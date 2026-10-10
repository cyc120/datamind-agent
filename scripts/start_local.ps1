<#
.SYNOPSIS
    Windows 启动脚本 —— scripts/start_local.sh 的等价实现。

.DESCRIPTION
    在 Windows 上把 DataMind Agent 的本地栈拉起来：PostgreSQL + Qdrant 容器、
    16 个元数据迁移、零售/Chinook/Catalog/评测集种子、文档摄取、工业底座，
    最后启动 127.0.0.1:8000 的 API。

    与 start_local.sh 的差异都源于平台，各有对应处理：
      1. PYTHONUTF8=1。项目在 macOS/Linux 上开发，隐含假设 open()/read_text()
         默认是 UTF-8；Windows 默认是 ANSI 代码页（本机 GBK），会让 73 处未显式
         指定编码的文件读写直接抛 UnicodeDecodeError。
      2. docker CLI 可能不在当前进程的 PATH 上。Docker Desktop 是用户级安装，
         改 PATH 不影响已启动的进程，因此这里显式探测。
      3. 用 PowerShell 原生的 HTTP 探测替代 curl（Windows 自带 curl.exe 的参数
         与 macOS 不同，不能照搬 -fsS --noproxy）。

.PARAMETER SqlOnly
    只初始化问数所需服务，跳过 Qdrant、文档摄取与工业底座。

.PARAMETER Background
    后台启动 API（日志 .cache/api/server.log），立即返回。

.EXAMPLE
    pwsh -File scripts/start_local.ps1
    pwsh -File scripts/start_local.ps1 -SqlOnly
    pwsh -File scripts/start_local.ps1 -Background
#>
[CmdletBinding()]
param(
    [switch]$SqlOnly,
    [switch]$Background
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

# ── 0. 平台前提 ───────────────────────────────────────────────────────────────
$env:PYTHONUTF8 = '1'          # 见文件头说明 1

function Resolve-Docker {
    $cmd = Get-Command docker -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($candidate in @(
            "$env:ProgramFiles\Docker\Docker\resources\bin\docker.exe",
            "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin\docker.exe")) {
        if (Test-Path $candidate) { return $candidate }
    }
    throw 'docker 未找到：请安装 Docker Desktop。'
}

$script:Docker = Resolve-Docker
function Invoke-Docker { & $script:Docker @args }

& $script:Docker info --format '{{.ServerVersion}}' *> $null
if ($LASTEXITCODE -ne 0) { throw 'Docker 守护进程未运行：请先启动 Docker Desktop。' }
Write-Host "docker: $script:Docker"

$Python = Join-Path $Root '.venv\Scripts\python.exe'
function Invoke-Python {
    & $Python @args
    if ($LASTEXITCODE -ne 0) { throw "python 退出码 $LASTEXITCODE：$args" }
}

# ── 1. 虚拟环境与依赖 ─────────────────────────────────────────────────────────
if (-not (Test-Path $Python)) {
    Write-Host '创建 .venv …'
    python -m venv .venv
}

& $Python scripts/check_env.py sql *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host '安装 SQL 依赖 …'
    Invoke-Python -m pip install -r requirements-sql.txt
}

if (-not $SqlOnly) {
    # 依赖探针放在 scripts/check_env.py：PowerShell 5.1 会把内联 python -c 里的
    # 引号和分号拆坏，写成文件才能可靠判断。
    & $Python scripts/check_env.py rag *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host '安装 RAG/OCR 依赖 …'
        Invoke-Python -m pip install -r requirements-ocr.txt
    }
}

# ── 2. PostgreSQL ─────────────────────────────────────────────────────────────
Invoke-Docker compose -f deploy/compose.yaml up -d postgres
$postgresReady = $false
foreach ($try in 1..30) {
    & $script:Docker exec datamind-postgres pg_isready -U postgres -d datamind_meta -q *> $null
    if ($LASTEXITCODE -eq 0) { $postgresReady = $true; break }
    Start-Sleep -Seconds 1
}
if (-not $postgresReady) { throw 'PostgreSQL 未在 30 秒内就绪。' }

function Invoke-Psql([string]$Sql, [string]$Database = 'datamind_meta') {
    $out = & $script:Docker exec datamind-postgres psql -U postgres -d $Database -tAc $Sql
    if ($LASTEXITCODE -ne 0) { throw "psql 失败：$Sql" }
    return ($out | Out-String).Trim()
}

Invoke-Psql 'CREATE SCHEMA IF NOT EXISTS demo_industrial AUTHORIZATION datamind_app; GRANT USAGE ON SCHEMA demo_industrial TO readonly_user;' | Out-Null
Write-Host '应用迁移 …'
Invoke-Python db/migrate.py

if ((Invoke-Psql "SELECT count(*) FROM pg_database WHERE datname='chinook'" 'postgres') -eq '0') {
    Write-Host '加载 Chinook …'
    Invoke-Docker cp db/chinook/Chinook_PostgreSql.sql datamind-postgres:/tmp/chinook.sql
    Invoke-Docker exec datamind-postgres psql -U postgres -q -f /tmp/chinook.sql
    Invoke-Psql 'GRANT CONNECT ON DATABASE chinook TO readonly_user, datamind_app; GRANT USAGE ON SCHEMA public TO readonly_user, datamind_app; GRANT SELECT ON ALL TABLES IN SCHEMA public TO readonly_user, datamind_app;' 'chinook' | Out-Null
}
if ((Invoke-Psql 'SELECT count(*) FROM orders' 'demo_retail') -eq '0') {
    Write-Host '装载零售演示库 …'
    Invoke-Python db/seed_demo_retail.py
}
if ((Invoke-Psql 'SELECT count(*) FROM schema_snapshots') -eq '0') {
    Write-Host '构建 Catalog …'
    Invoke-Python db/build_catalog.py
}
if ((Invoke-Psql 'SELECT count(*) FROM evaluation_cases') -eq '0') {
    Write-Host '登记评测集 …'
    Invoke-Python db/seed_eval_cases.py
}

# ── 3. 向量库、文档摄取与工业底座 ─────────────────────────────────────────────
if (-not $SqlOnly) {
    if (-not (Test-Path (Join-Path $Root '.cache/ocr_models/ch_PP-OCRv5_rec_server.onnx'))) {
        Write-Host '表头模型尚未安装：运行 .venv\Scripts\python.exe scripts\setup_ocr_models.py；缺失表头将保留为解析缺口。'
    }
    $formulaPython = if ($env:DATAMIND_FORMULA_PYTHON) { $env:DATAMIND_FORMULA_PYTHON } else { Join-Path $Root '.venv-formula/Scripts/python.exe' }
    if (-not (Test-Path $formulaPython)) {
        Write-Host '公式识别尚未安装：运行 .venv\Scripts\python.exe scripts\setup_formula.py --python <python3.10-3.12>；未识别图片将保留解析缺口。'
    }

    Invoke-Docker compose -f deploy/compose.yaml up -d qdrant
    $qdrantReady = $false
    foreach ($try in 1..30) {
        try {
            Invoke-WebRequest -Uri 'http://127.0.0.1:6333/readyz' -UseBasicParsing -TimeoutSec 2 | Out-Null
            $qdrantReady = $true; break
        } catch { Start-Sleep -Seconds 1 }
    }
    if (-not $qdrantReady) { throw 'Qdrant 未在 30 秒内就绪。' }

    Write-Host '摄取文档（未变化的文档会跳过）…'
    Invoke-Python ingestion/ingest.py
    Invoke-Python db/seed_industrial.py
    Invoke-Python db/build_industrial_catalog.py
}

# ── 4. 启动 API ───────────────────────────────────────────────────────────────
function Test-Api {
    try {
        $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 2
        return $health.agent -in @('sql-only', 'sql+rag')
    } catch { return $false }
}

if (Test-Api) {
    Write-Host 'DataMind Agent 已运行：http://127.0.0.1:8000/（代码更新后请重启原服务进程）'
    exit 0
}

Write-Host '启动 DataMind Agent：http://127.0.0.1:8000/'
if ($Background) {
    Invoke-Python scripts/start_api_background.py
} else {
    Invoke-Python -m uvicorn datamind.api:app --host 127.0.0.1 --port 8000
}
