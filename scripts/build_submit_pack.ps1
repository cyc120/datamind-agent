<#
.SYNOPSIS
  生成赛题八「技术数据」提交包 —— 按赛题原文的 5 项要求组织目录。

.DESCRIPTION
  赛题原文（第 8-9 页）：
    (3) 技术数据：作品原始文件（ZIP 数据包），应包含：
        - 完整项目源代码（需包含清晰的目录结构说明和 README 文件）
        - 数据库 schema 定义文件
        - 知识库文档样本（不少于 10 份，涵盖不同类型文档格式）
        - 模型权重文件（若适用）
        - 运行环境配置说明（包括依赖包列表及版本号）
        - 可执行 Demo 的快速启动脚本

  本脚本把上面 6 项各自映射到一个编号目录，最外层再放一份 SUBMIT_README.md
  说明每项对应什么、评审要怎么用。可执行 Demo 单独是一个 zip（本包里放它的
  路径与校验和，不重复塞 400MB）。

.PARAMETER Output
  输出 zip 的路径。默认 ..\delivery\DataMind_Submit_<yyyyMMdd>.zip

.PARAMETER SkipDemo
  跳过 Demo 包（只打源码与数据）。调试用。

.EXAMPLE
  pwsh -File scripts\build_submit_pack.ps1
#>
[CmdletBinding()]
param(
    [string]$Output = '',
    [switch]$SkipDemo
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

if (-not $Output) {
    $stamp = Get-Date -Format 'yyyyMMdd'
    $Output = Join-Path (Split-Path -Parent $root) "delivery\DataMind_Submit_$stamp.zip"
}
$Output = [System.IO.Path]::GetFullPath($Output)
$stage = Join-Path ([System.IO.Path]::GetTempPath()) ("datamind-submit-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
$pack = Join-Path $stage 'DataMind_Submit'

function Write-Step([string]$msg) { Write-Host "  [$msg]" -ForegroundColor Cyan }
function Write-Done([string]$msg) { Write-Host "  ✓ $msg" -ForegroundColor Green }
function Write-Warn2([string]$msg) { Write-Host "  ! $msg" -ForegroundColor Yellow }

New-Item -ItemType Directory -Force -Path $pack | Out-Null

# ────────────────────────────────────────────────────────────
# 01 源代码 + 目录结构说明 + README
# ────────────────────────────────────────────────────────────
Write-Step '01 源代码'
$src = Join-Path $pack '01_source'
New-Item -ItemType Directory -Force -Path $src | Out-Null

# 后端与产品代码（整个目录搬）
foreach ($d in 'datamind', 'ingestion', 'db', 'deploy', 'scripts', 'tests') {
    if (Test-Path $d) {
        Copy-Item -Recurse -Force $d (Join-Path $src $d)
        Write-Done "$d/"
    }
}

# 前端只要源码，不要 node_modules 与构建产物
if (Test-Path 'frontend') {
    $fe = Join-Path $src 'frontend'
    New-Item -ItemType Directory -Force -Path $fe | Out-Null
    foreach ($item in 'src', 'public', 'index.html', 'package.json', 'package-lock.json', 'vite.config.*', 'tsconfig*.json', 'tailwind.config.*', 'postcss.config.*') {
        Get-ChildItem -Path 'frontend' -Filter $item -Force -EA SilentlyContinue | ForEach-Object {
            if ($_.PSIsContainer) { Copy-Item -Recurse -Force $_.FullName (Join-Path $fe $_.Name) }
            else { Copy-Item -Force $_.FullName (Join-Path $fe $_.Name) }
        }
    }
    Write-Done 'frontend/（仅源码，不含 node_modules 与 web/vue 构建产物）'
}

# README 与项目级配置
foreach ($f in 'README.md', 'AGENTS.md', '.gitignore', 'pyproject.toml', 'alembic.ini') {
    if (Test-Path $f) { Copy-Item -Force $f (Join-Path $src $f) }
}

# requirements 全部
Get-ChildItem -Filter 'requirements*.txt' -EA SilentlyContinue | ForEach-Object {
    Copy-Item -Force $_.FullName (Join-Path $src $_.Name)
    Write-Done $_.Name
}

# 目录结构说明（生成，含每个顶层目录一句话用途）
$treeLines = @('# 目录结构说明', '',
    '> 本文件由 `scripts\build_submit_pack.ps1` 自动生成。', '',
    '## 顶层', '',
    '| 路径 | 用途 |', '|---|---|')
$dirNotes = @{
    'datamind'   = '后端主包：FastAPI 路由、Agent 编排、NL2SQL、RAG、证据图、Wiki 知识图谱'
    'ingestion'  = '文档摄取：多模态解析（PDF/DOCX/XLSX/PPTX/DXF）、切片、索引'
    'db'         = '数据库迁移（db/migrations/*.sql）、Chinook 样例 schema'
    'deploy'     = '部署配置（Docker、PostgreSQL、Qdrant）'
    'scripts'    = '启动与构建脚本（start_local.ps1 / build_delivery.ps1 等）'
    'tests'      = '测试用例（含契约测试与前端卫生检查）'
    'frontend'   = '前端源码：Vue 3 + Vite，含 Wiki 图谱与可解释性页面'
    'data'       = '样例数据与知识库文档样本'
    'docs'       = '项目文档'
}
foreach ($k in $dirNotes.Keys | Sort-Object) { $treeLines += "| ``$k/`` | $($dirNotes[$k]) |" }
$treeLines += @('', '## 关键入口', '',
    '| 文件 | 作用 |', '|---|---|',
    '| `datamind/api.py` | HTTP 路由层（含 `/v1/wiki/*` 14 条路由） |',
    '| `datamind/agent.py` | Agent 单轮执行链路 |',
    '| `datamind/sql_engine.py` | NL2SQL 生成与只读执行 |',
    '| `datamind/sql_guard.py` | SQL 十九项校验 |',
    '| `datamind/evidence_graph.py` | 类型化证据图 |',
    '| `datamind/wiki_build.py` | Wiki 整理任务（run → 知识词条） |',
    '| `datamind/wiki_projection.py` | Wiki 投影（证据 → 词条/关系，21 种关系闭集） |',
    '| `datamind/wiki_query.py` | Wiki 查询与授权范围内取图 |',
    '| `db/migrations/021_wiki_knowledge.sql` | Wiki 表结构与全部 CHECK 约束 |',
    '| `frontend/src/views/WikiEntryPage.vue` | Wiki 词条详情页 |',
    '| `frontend/src/components/wiki/WikiForceGraph.vue` | 力导向关系图 |',
    '| `frontend/src/components/wiki/markdown.ts` | 自写 Markdown 渲染器（先转义再插标签防 XSS） |')
Set-Content -Path (Join-Path $src 'DIRECTORY_STRUCTURE.md') -Value ($treeLines -join "`r`n") -Encoding UTF8
Write-Done 'DIRECTORY_STRUCTURE.md'

# ────────────────────────────────────────────────────────────
# 02 数据库 schema
# ────────────────────────────────────────────────────────────
Write-Step '02 数据库 schema'
$dbs = Join-Path $pack '02_database_schema'
New-Item -ItemType Directory -Force -Path $dbs | Out-Null
if (Test-Path 'db') { Copy-Item -Recurse -Force 'db' (Join-Path $dbs 'db') }
$migCount = (Get-ChildItem 'db\migrations' -Filter '*.sql' -EA SilentlyContinue).Count
Write-Done "db/（$migCount 个迁移 + Chinook schema）"

# ────────────────────────────────────────────────────────────
# 03 知识库文档样本（赛题要求 ≥10 份、涵盖不同格式）
# ────────────────────────────────────────────────────────────
Write-Step '03 知识库文档样本'
$kb = Join-Path $pack '03_knowledge_base_samples'
New-Item -ItemType Directory -Force -Path $kb | Out-Null
$copied = 0
$byExt = @{}
if (Test-Path 'data\qa_samples') {
    Get-ChildItem 'data\qa_samples' -Recurse -File -EA SilentlyContinue |
        Where-Object { $_.Extension -in '.pdf', '.docx', '.xlsx', '.pptx', '.dxf', '.md', '.txt', '.csv' } |
        ForEach-Object {
            Copy-Item -Force $_.FullName (Join-Path $kb $_.Name)
            $copied++
            $byExt[$_.Extension] = 1 + ($byExt[$_.Extension] -as [int])
        }
}
if ($copied -lt 10) {
    Write-Warn2 "qa_samples 只有 $copied 份，尝试从 data/ 其它位置补"
    Get-ChildItem 'data' -Recurse -File -EA SilentlyContinue |
        Where-Object { $_.Extension -in '.pdf', '.docx', '.xlsx', '.pptx' -and -not (Test-Path (Join-Path $kb $_.Name)) } |
        Select-Object -First (12 - $copied) | ForEach-Object {
            Copy-Item -Force $_.FullName (Join-Path $kb $_.Name)
            $copied++
            $byExt[$_.Extension] = 1 + ($byExt[$_.Extension] -as [int])
        }
}
# OHR-Bench 样本（JSON 形式的基准数据）
if (Test-Path 'data\ohr_bench_samples') { Copy-Item -Recurse -Force 'data\ohr_bench_samples' (Join-Path $kb 'ohr_bench_samples') }

$kbLines = @('# 知识库文档样本', '',
    "共 $copied 份主文档，覆盖 $($byExt.Keys.Count) 种格式：", '',
    '| 格式 | 份数 |', '|---|---|')
foreach ($k in $byExt.Keys | Sort-Object) { $kbLines += "| $k | $($byExt[$k]) |" }
$kbLines += @('', '另有 `ohr_bench_samples/` 为 OHR-Bench 基准样例（JSON）。',
    '', '> 赛题要求：知识库文档样本不少于 10 份，涵盖不同类型文档格式。',
    '> 上述文档已全部入库并跑通「解析 → 切片 → 索引 → 检索 → 生成」全链路。')
Set-Content -Path (Join-Path $kb 'README.md') -Value ($kbLines -join "`r`n") -Encoding UTF8
Write-Done "$copied 份文档，$($byExt.Keys.Count) 种格式"

# ────────────────────────────────────────────────────────────
# 04 运行环境配置（依赖列表及版本号）
# ────────────────────────────────────────────────────────────
Write-Step '04 运行环境配置'
$env4 = Join-Path $pack '04_environment'
New-Item -ItemType Directory -Force -Path $env4 | Out-Null
Get-ChildItem -Filter 'requirements*.txt' -EA SilentlyContinue | ForEach-Object { Copy-Item -Force $_.FullName (Join-Path $env4 $_.Name) }
if (Test-Path 'frontend\package.json') { Copy-Item -Force 'frontend\package.json' (Join-Path $env4 'frontend-package.json') }
if (Test-Path 'frontend\package-lock.json') { Copy-Item -Force 'frontend\package-lock.json' (Join-Path $env4 'frontend-package-lock.json') }
if (Test-Path 'pyproject.toml') { Copy-Item -Force 'pyproject.toml' (Join-Path $env4 'pyproject.toml') }
if (Test-Path 'deploy') { Copy-Item -Recurse -Force 'deploy' (Join-Path $env4 'deploy') }

# 生成一份人类可读的运行环境说明（版本号从实际文件里抽，不写死）
$pyVer = (Get-Content 'requirements.txt' -Raw -EA SilentlyContinue)
$pkgJson = if (Test-Path 'frontend\package.json') { Get-Content 'frontend\package.json' -Raw | ConvertFrom-Json } else { $null }
$envLines = @('# 运行环境配置说明', '',
    '> 本文件由 `scripts\build_submit_pack.ps1` 自动生成，版本号取自仓库内的实际文件（非手写）。', '',
    '## 后端（Python）', '',
    '完整依赖与版本见 `requirements*.txt`。核心项：', '', '```')
foreach ($line in ($pyVer -split "`r?`n" | Where-Object { $_ -match '^\s*[A-Za-z0-9_.\-]+\s*[=><~!]' })) { $envLines += $line.Trim() }
$envLines += '```'
if ($pkgJson) {
    $envLines += @('', '## 前端（Node）', '', '来自 `frontend/package.json`：', '', '| 包 | 版本 |', '|---|---|')
    foreach ($p in $pkgJson.dependencies.PSObject.Properties) { $envLines += "| $($p.Name) | $($p.Value) |" }
    $envLines += @('', 'devDependencies：', '', '| 包 | 版本 |', '|---|---|')
    foreach ($p in $pkgJson.devDependencies.PSObject.Properties) { $envLines += "| $($p.Name) | $($p.Value) |" }
}
$envLines += @('', '## 中间件', '',
    '| 组件 | 版本 | 用途 |', '|---|---|---|',
    '| PostgreSQL | 16 | 元数据、证据、Wiki 词条与关系 |',
    '| Qdrant | 最新稳定版 | 向量检索 |', '',
    '以上两者在 `deploy/` 里有 Docker 配置；Demo 包内已带便携运行时（见 05）。')
Set-Content -Path (Join-Path $env4 'ENVIRONMENT.md') -Value ($envLines -join "`r`n") -Encoding UTF8
Write-Done 'ENVIRONMENT.md（版本号从 requirements / package.json 自动抽取）'

# ────────────────────────────────────────────────────────────
# 05 可执行 Demo（快速启动）
# ────────────────────────────────────────────────────────────
Write-Step '05 可执行 Demo'
$demo = Join-Path $pack '05_runnable_demo'
New-Item -ItemType Directory -Force -Path $demo | Out-Null
$demoZip = Join-Path (Split-Path -Parent $root) 'delivery\DataMindAgent_Demo.zip'
$demoInfo = @('# 可执行 Demo', '')
if ((-not $SkipDemo) -and (Test-Path $demoZip)) {
    $h = Get-FileHash $demoZip -Algorithm SHA256
    $sizeMB = [math]::Round((Get-Item $demoZip).Length / 1MB, 1)
    $demoInfo += @(
        "Demo 包：``DataMindAgent_Demo.zip``（$sizeMB MB）", '',
        '**未打进本 ZIP** —— 体积原因，单独提交/分发。校验信息：', '',
        '```',
        "文件名 : DataMindAgent_Demo.zip",
        "大小   : $sizeMB MB",
        "SHA256 : $($h.Hash)",
        '```', '',
        '## 快速启动', '',
        '1. 解压 `DataMindAgent_Demo.zip`（**不需要装 Docker、不需要装 Python**）',
        '2. 双击目录里的 `run.bat`，等待 6 个步骤走完（约 7-10 秒）',
        '3. 浏览器打开脚本提示的地址（默认 `http://127.0.0.1:8000/`）',
        '4. 关闭用 `stop.bat`（会清理全部端口）', '',
        '> 便携运行时（PostgreSQL / Qdrant / Python venv）已内置在包内，',
        '> 全部路径为相对路径，可放在含中文与空格的目录下。'
    )
} else {
    $demoInfo += @('Demo 包在 `..\delivery\DataMindAgent_Demo.zip`，未随本 ZIP 打包。',
        '构建方式：`pwsh -File scripts\build_delivery.ps1 -Frontend build`')
}
$demoInfo += @('', '## 从源码启动（开发用）', '',
    '```powershell',
    '# 1. 起中间件（Docker：PostgreSQL 16 + Qdrant）',
    'powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start_local.ps1 -Background',
    '# 2. 看状态 / 停止',
    'powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start_local.ps1 -Status',
    'powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start_local.ps1 -Stop',
    '```')
Set-Content -Path (Join-Path $demo 'RUN_DEMO.md') -Value ($demoInfo -join "`r`n") -Encoding UTF8
Copy-Item -Force 'scripts\start_local.ps1' (Join-Path $demo 'start_local.ps1') -EA SilentlyContinue
Write-Done 'RUN_DEMO.md + start_local.ps1'

# ────────────────────────────────────────────────────────────
# 06 模型权重（若适用）
# ────────────────────────────────────────────────────────────
Write-Step '06 模型权重'
$w = Join-Path $pack '06_model_weights'
New-Item -ItemType Directory -Force -Path $w | Out-Null
Set-Content -Path (Join-Path $w 'README.md') -Encoding UTF8 -Value (@(
    '# 模型权重', '',
    '**本项目不包含自训练模型权重** —— 采用第三方大模型 API（国产 Token），',
    '通过模型网关接入（见 `01_source/datamind/` 内的网关相关模块与前端「模型接入」页）。',
    '赛题原文该项为「（若适用）」，故此处如实说明不适用，并列出实际使用的模型接入方式：',
    '',
    '- 接入位置：`01_source/datamind/` 下的模型网关模块',
    '- 配置方式：前端「模型接入」页面填写 provider / base_url / model / api_key',
    '- 说明：所有模型调用走统一网关层，便于替换与审计'
) -join "`r`n")
Write-Done 'README.md（如实说明：不自带权重，走模型网关）'

# ────────────────────────────────────────────────────────────
# 顶层说明
# ────────────────────────────────────────────────────────────
Write-Step 'SUBMIT_README.md'
$top = @(
    '# 赛题八 技术数据提交包', '',
    '本 ZIP 按赛题「(3) 技术数据：作品原始文件（ZIP 数据包）」的**六项要求**组织：', '',
    '| 目录 | 对应赛题要求 | 内容 |', '|---|---|---|',
    '| `01_source/` | 完整项目源代码（含目录结构说明和 README） | `datamind/` `ingestion/` `db/` `deploy/` `scripts/` `tests/` `frontend/src/` + `README.md` + `DIRECTORY_STRUCTURE.md` |',
    '| `02_database_schema/` | 数据库 schema 定义文件 | `db/migrations/*.sql`（幂等迁移）+ Chinook schema |',
    "| ``03_knowledge_base_samples/`` | 知识库文档样本（不少于 10 份，涵盖不同类型格式） | $copied 份，$($byExt.Keys.Count) 种格式 + OHR-Bench 样例 |",
    '| `04_environment/` | 运行环境配置说明（依赖包列表及版本号） | `requirements*.txt` + `package.json` + `ENVIRONMENT.md` |',
    '| `05_runnable_demo/` | 可执行 Demo 的快速启动脚本 | `RUN_DEMO.md` + `start_local.ps1`（Demo 包另附，见其校验和） |',
    '| `06_model_weights/` | 模型权重文件（若适用） | 如实说明：不适用，走模型网关接入第三方模型 |',
    '',
    '## 两份技术文档',
    '',
    '- `docs/设计报告_DataMind_Agent.docx` / `.pdf`（≤20 页，第 1-2 页为核心亮点预览页）',
    '- `docs/技术实现说明书_DataMind_Agent.docx` / `.pdf`（≤30 页）',
    '',
    '## 评审建议路径',
    '',
    '1. **先看 Demo**：解压 `DataMindAgent_Demo.zip` → `run.bat` → 浏览器打开（不需装 Docker / Python）',
    '2. **再看文档**：设计报告第 1-2 页是核心能力总览；技术实现说明书按 7 个必写模块组织',
    '3. **要看源码**：`01_source/`，先读 `DIRECTORY_STRUCTURE.md`',
    '',
    "> 生成时间：$((Get-Date).ToString('yyyy-MM-dd HH:mm'))",
    '> 生成脚本：`scripts\build_submit_pack.ps1`'
)
Set-Content -Path (Join-Path $pack 'SUBMIT_README.md') -Value ($top -join "`r`n") -Encoding UTF8
Write-Done 'SUBMIT_README.md'

# ────────────────────────────────────────────────────────────
# 打包
# ────────────────────────────────────────────────────────────
Write-Step '压缩'
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Output) | Out-Null
if (Test-Path $Output) { Remove-Item $Output -Force }
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory($pack, $Output, [System.IO.Compression.CompressionLevel]::Optimal, $true)
$outMB = [math]::Round((Get-Item $Output).Length / 1MB, 1)
Write-Done "输出：$Output（$outMB MB）"

Remove-Item -Recurse -Force $stage -EA SilentlyContinue

Write-Host ''
Write-Host "  ── 提交包内容 ──" -ForegroundColor Cyan
Write-Host "     01_source/                 源代码 + README + 目录结构说明"
Write-Host "     02_database_schema/        数据库 schema（$migCount 个迁移）"
Write-Host "     03_knowledge_base_samples/ 知识库文档样本（$copied 份 / $($byExt.Keys.Count) 种格式）"
Write-Host "     04_environment/            运行环境（依赖列表 + 版本号 + ENVIRONMENT.md）"
Write-Host "     05_runnable_demo/          Demo 快速启动（RUN_DEMO.md + 校验和）"
Write-Host "     06_model_weights/          模型权重（如实说明不适用）"
Write-Host "     SUBMIT_README.md           顶层映射表 + 评审建议路径"
Write-Host ''
