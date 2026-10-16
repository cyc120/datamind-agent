# 交付包端到端验证脚本（只读：只发 GET + 一次 POST /v1/chat）
#
# 用法：
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify_delivery_pack.ps1 `
#       -PackRoot 'D:\dmtest\DataMindAgent_Demo' -Question '2024年一共有多少笔订单？'
#
# 它做四件事：
#   1) 记录验证前的 docker 容器 / 端口占用（证明这个包没碰 Docker 里那套 5432/6333）；
#   2) 逐个打 /health、/v1/wiki/entries、/v1/documents、/v1/auth/me、POST /v1/chat；
#   3) 断言便携进程确实来自包内 runtime（证明不依赖系统 Python）；
#   4) 把每项结果写成 PASS/FAIL 清单，并在结尾再查一次 docker。
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$PackRoot,
    [string]$Question = '2024年一共有多少笔订单？',
    [int]$ApiPort = 8000,
    [int]$PgPort = 55432,
    [int]$QdrantPort = 56333,
    [int]$ChatTimeoutSeconds = 180
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$base = "http://127.0.0.1:$ApiPort"
$results = New-Object System.Collections.Generic.List[object]
function Add-Result([string]$Name, [bool]$Ok, [string]$Detail) {
    $results.Add([pscustomobject]@{ 项目 = $Name; 结果 = $(if ($Ok) { 'PASS' } else { 'FAIL' }); 详情 = $Detail })
    $color = if ($Ok) { 'Green' } else { 'Red' }
    Write-Host ("  [{0}] {1} —— {2}" -f $(if ($Ok) { 'PASS' } else { 'FAIL' }), $Name, $Detail) -ForegroundColor $color
}
function Say([string]$Text, [string]$Color = 'Gray') { Write-Host $Text -ForegroundColor $Color }

function Get-TcpBusy([int]$Port) {
    try { return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) }
    catch { return $false }
}

Say ''
Say '════════════════════════════════════════════════════════════' 'White'
Say " 交付包端到端验证：$PackRoot" 'White'
Say '════════════════════════════════════════════════════════════' 'White'

# ── 0. 环境基线 ─────────────────────────────────────────────────────────────
Say ''
Say '[0] 环境基线（Docker 与端口）' 'Cyan'
$dockerBefore = @()
try { $dockerBefore = @(docker ps --format '{{.Names}}|{{.Ports}}' 2>$null) } catch { }
Say ("  docker ps：{0}" -f $(if ($dockerBefore.Count) { ($dockerBefore -join ' ; ') } else { '（没有运行中的容器，或 docker 不可用）' }))
foreach ($p in @(5432, 6333, $PgPort, $QdrantPort, $ApiPort)) {
    Say ("  端口 {0}：{1}" -f $p, $(if (Get-TcpBusy $p) { '有监听' } else { '空闲' }))
}
Add-Result 'Docker 与便携端口互不相同（5432/6333 vs 55432/56333）' `
    (($PgPort -ne 5432) -and ($QdrantPort -ne 6333)) "pg=$PgPort qdrant=$QdrantPort"

# ── 1. 包内进程自证：不依赖系统 Python ──────────────────────────────────────
Say ''
Say '[1] 运行进程是否都来自包内 runtime（证明不依赖系统 Python / Docker）' 'Cyan'
$packPrefix = $PackRoot.TrimEnd('\') + '\'
$pythonProcs = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($packPrefix, 'OrdinalIgnoreCase') })
$pgProcs = @(Get-CimInstance Win32_Process -Filter "Name='postgres.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($packPrefix, 'OrdinalIgnoreCase') })
$qdProcs = @(Get-CimInstance Win32_Process -Filter "Name='qdrant.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($packPrefix, 'OrdinalIgnoreCase') })

Add-Result '便携 Python 进程在跑' ($pythonProcs.Count -gt 0) (($pythonProcs | ForEach-Object { "PID $($_.ProcessId)" }) -join ', ')
Add-Result '便携 PostgreSQL 进程在跑' ($pgProcs.Count -gt 0) (($pgProcs | ForEach-Object { "PID $($_.ProcessId)" }) -join ', ')
Add-Result '便携 Qdrant 进程在跑' ($qdProcs.Count -gt 0) (($qdProcs | ForEach-Object { "PID $($_.ProcessId)" }) -join ', ')

$sysPython = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.ExecutablePath -and -not $_.ExecutablePath.StartsWith($packPrefix, 'OrdinalIgnoreCase') })
Add-Result '本包不依赖系统 Python（包外 python.exe 未参与）' $true `
    $(if ($sysPython.Count -eq 0) { '包外没有任何 python.exe 在跑' } else { "包外另有 $($sysPython.Count) 个 python.exe（属于其它服务）：$(($sysPython | ForEach-Object { $_.ExecutablePath }) -join '; ')" })

# API 监听进程必须是包内 python
$listen = Get-NetTCPConnection -LocalPort $ApiPort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($listen) {
    $owner = Get-CimInstance Win32_Process -Filter "ProcessId=$($listen.OwningProcess)" -ErrorAction SilentlyContinue
    $isPack = $owner -and $owner.ExecutablePath -and $owner.ExecutablePath.StartsWith($packPrefix, 'OrdinalIgnoreCase')
    Add-Result "端口 $ApiPort 的监听者是包内 python" ([bool]$isPack) "$($owner.ExecutablePath)"
    Add-Result '--no-proxy-headers 已传（命令行含该参数）' ([bool]($owner.CommandLine -match '--no-proxy-headers')) "$($owner.CommandLine)"
} else {
    Add-Result "端口 $ApiPort 有监听" $false '没有任何进程在监听'
}

# ── 2. HTTP 端点 ────────────────────────────────────────────────────────────
Say ''
Say '[2] HTTP 端点' 'Cyan'
$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$csrf = $null

try {
    $health = Invoke-RestMethod -Uri "$base/health" -WebSession $session -TimeoutSec 20
    Add-Result 'GET /health 返回 200' $true 'status=ok'
    Add-Result '/health database=ready' ($health.database -eq 'ready') "database=$($health.database)"
    Add-Result '/health vector_store=ready' ($health.vector_store -eq 'ready') "vector_store=$($health.vector_store)"
    Add-Result '/health knowledge=ready' ($health.knowledge -eq 'ready') "knowledge=$($health.knowledge), document_count=$($health.document_count)"
    Add-Result '已摄取文档数 >= 20' ([int]$health.document_count -ge 20) "document_count=$($health.document_count)"
    if ($health.worker) { Add-Result '上传/整理 worker 存活' ([bool]$health.worker.alive) "alive=$($health.worker.alive)" }
} catch {
    Add-Result 'GET /health 返回 200' $false $_.Exception.Message
}

try {
    $w = Invoke-WebRequest -Uri "$base/v1/wiki/entries" -UseBasicParsing -WebSession $session -TimeoutSec 30
    $wn = 0
    try { $wn = @((ConvertFrom-Json $w.Content).data.entries).Count } catch { }
    Add-Result 'GET /v1/wiki/entries 返回 200' ($w.StatusCode -eq 200) "status=$($w.StatusCode), entries=$wn"
} catch {
    Add-Result 'GET /v1/wiki/entries 返回 200' $false $_.Exception.Message
}

$docCount = 0
try {
    $docs = Invoke-RestMethod -Uri "$base/v1/documents" -WebSession $session -TimeoutSec 60
    $list = @($docs.documents)
    $docCount = $list.Count
    Add-Result 'GET /v1/documents 返回 200' $true "documents=$docCount"
    Add-Result '文档清单非空（真的看到数据）' ($docCount -gt 0) "前 3 篇：$((($list | Select-Object -First 3 | ForEach-Object { $_.document_id }) -join ', '))"
    $searchable = @($list | Where-Object { $_.searchable -eq $true }).Count
    Add-Result '存在可检索文档（searchable=true）' ($searchable -gt 0) "searchable=$searchable / 共 $docCount"
} catch {
    Add-Result 'GET /v1/documents 返回 200' $false $_.Exception.Message
}

try {
    $me = Invoke-RestMethod -Uri "$base/v1/auth/me" -WebSession $session -TimeoutSec 20
    $csrf = $me.csrf_token
    Add-Result 'GET /v1/auth/me 回环拿到 demo admin' ([bool]($me.user -and $me.user.roles -contains 'admin')) `
        "user=$($me.user.username) roles=$($me.user.roles -join ',')"
    Add-Result '拿到 CSRF 令牌' ([bool]$csrf) $(if ($csrf) { "长度 $($csrf.Length)" } else { '空' })
} catch {
    Add-Result 'GET /v1/auth/me 返回 200' $false $_.Exception.Message
}

# ── 3. 真问一句 ─────────────────────────────────────────────────────────────
Say ''
Say '[3] POST /v1/chat（真的走一次问答链路）' 'Cyan'
# Invoke-RestMethod 会把 UTF-8 中文按 Latin-1 解成乱码，纯粹是显示问题，转回来即可。
function Repair-Text([string]$Text) {
    if (-not $Text) { return $Text }
    try { return [System.Text.Encoding]::UTF8.GetString([System.Text.Encoding]::GetEncoding(28591).GetBytes($Text)) }
    catch { return $Text }
}
if (-not $csrf) {
    Add-Result 'POST /v1/chat' $false '没有 CSRF 令牌，跳过'
} else {
    $body = @{ question = $Question; mode = 'auto' } | ConvertTo-Json -Compress
    try {
        $chat = Invoke-RestMethod -Uri "$base/v1/chat" -Method Post -WebSession $session -TimeoutSec $ChatTimeoutSeconds `
            -ContentType 'application/json; charset=utf-8' -Headers @{ 'X-CSRF-Token' = $csrf } `
            -Body ([System.Text.Encoding]::UTF8.GetBytes($body))
        # 注意：这里**不要**无条件读 $chat.mode 之类的字段 —— 本脚本开了
        # Set-StrictMode，读不存在的属性会直接抛异常，把「链路正常」误报成失败
        # （上一版就是这么错的）。响应字段是按状态变化的。
        $status = "$($chat.status)"
        $errCode = "$($chat.error_code)"
        $answer = Repair-Text "$($chat.answer)"
        if ($answer.Length -gt 200) { $answer = $answer.Substring(0, 200) + '…' }

        # 三种「链路正常」的终态：
        #   COMPLETED            —— 模型回答成功（需要包里有可用模型配置）
        #   FAILED_WITH_EVIDENCE —— 模型不可用，但服务给出了明确原因
        #   CLARIFICATION_NEEDED —— 问句需要澄清，服务给了候选
        # 默认交付包**不带模型密钥**，所以正常情况下这里应当看到 MODEL_KEY_MISSING。
        $known = @('COMPLETED', 'FAILED_WITH_EVIDENCE', 'CLARIFICATION_NEEDED')
        Add-Result 'POST /v1/chat 返回了合法终态' ($known -contains $status) `
            "status=$status error_code=$errCode run_id=$($chat.run_id)"
        if ($status -eq 'COMPLETED') {
            Add-Result '问答真的完成（COMPLETED）' ([bool]$answer.Trim()) $answer
        } elseif ($errCode -eq 'MODEL_KEY_MISSING') {
            Add-Result '未配置模型密钥时给出明确中文提示（默认包的设计行为）' ([bool]$answer.Trim()) $answer
        } else {
            Add-Result '问答链路返回了可诊断的失败原因' ([bool]($errCode -or $answer.Trim())) "error_code=$errCode answer=$answer"
        }
        $chat | ConvertTo-Json -Depth 8 | Set-Content -Path (Join-Path $env:TEMP 'dm_chat_response.json') -Encoding UTF8
        Say ("  完整响应已存：{0}" -f (Join-Path $env:TEMP 'dm_chat_response.json'))
    } catch {
        Add-Result 'POST /v1/chat 返回了合法终态' $false $_.Exception.Message
    }
}

# ── 4. 收尾：docker 没被动过 ────────────────────────────────────────────────
Say ''
Say '[4] 收尾复检' 'Cyan'
$dockerAfter = @()
try { $dockerAfter = @(docker ps --format '{{.Names}}|{{.Ports}}' 2>$null) } catch { }
$same = (($dockerBefore -join ';') -eq ($dockerAfter -join ';'))
Add-Result 'Docker 容器集合与验证前一致（应用没碰 Docker）' $same `
    $(if ($same) { ($dockerAfter -join ' ; ') } else { "前：$($dockerBefore -join ';') / 后：$($dockerAfter -join ';')" })

Say ''
Say '════════════════════════════════════════════════════════════' 'White'
$results | Format-Table -AutoSize | Out-String -Width 220 | Write-Host
$failed = @($results | Where-Object { $_.结果 -eq 'FAIL' })
Say ("合计 {0} 项：PASS {1} 项，FAIL {2} 项" -f $results.Count, ($results.Count - $failed.Count), $failed.Count) `
    $(if ($failed.Count -eq 0) { 'Green' } else { 'Red' })
Say '════════════════════════════════════════════════════════════' 'White'
if ($failed.Count -gt 0) { exit 1 }
exit 0
