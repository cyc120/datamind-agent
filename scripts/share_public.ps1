<#
.SYNOPSIS
    把本机 DataMind Agent 通过 Cloudflare 快速隧道发布成一个公网 https 链接。

.DESCRIPTION
    做四件事，顺序不能换：
      1. 起 API，绑定 0.0.0.0 并带 --no-proxy-headers；
      2. 起 cloudflared，**指向本机局域网地址而不是 127.0.0.1**；
      3. 从隧道输出里取出本次分配的 https 域名；
      4. 把该域名写进 DATAMIND_ALLOWED_ORIGINS，重启 API 使其生效。

    第 2 步为什么不能用 127.0.0.1：`demo_allowed()` 把「TCP 对端是回环」当作
    「本机操作者」，会给它自动管理员身份。隧道若连回环，每个外部访客都会被当成
    本机管理员，能改模型密钥、审批权威规则、上传文件。走局域网地址则对端是
    10.x，非回环，访客拿不到管理员。
    `--no-proxy-headers` 是另一半：uvicorn 默认信任来自回环的 X-Forwarded-For，
    开着它访客就能伪造 127.0.0.1 冒充本机。

    公开范围仅限问答：DATAMIND_PUBLIC_CHAT=1 只放行 /v1/chat 与 /v1/chat/stream，
    且仍强制同源检查；其余写接口（模型配置、权威规则、上传、快照、审批）继续要求
    会话。任何拿到链接的人都能发起提问，会消耗你的模型额度。

.PARAMETER Port
    API 端口，默认 8000。

.PARAMETER Stop
    停止由本脚本启动的 API 与隧道。

.PARAMETER Protocol
    隧道传输协议：auto（默认，让 cloudflared 自选，通常 QUIC 更稳）、quic、http2。
    某些网络封 UDP 7844，此时 QUIC 建不起来，需要显式 -Protocol http2。

.EXAMPLE
    pwsh -File scripts/share_public.ps1
    pwsh -File scripts/share_public.ps1 -Protocol http2
    pwsh -File scripts/share_public.ps1 -Stop
#>
[CmdletBinding()]
param(
    [int]$Port = 8000,
    [ValidateSet('auto', 'quic', 'http2')]
    [string]$Protocol = 'auto',
    [switch]$Stop,
    #: 只检查链接是否还活着，不做任何改动。
    [switch]$Status
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$env:PYTHONUTF8 = '1'

$Tools = Join-Path (Split-Path -Parent $Root) '.tools'
$Cloudflared = Join-Path $Tools 'cloudflared.exe'
$ApiLog = Join-Path $Root '.cache/api/share_api.log'
$TunnelLog = Join-Path $Tools 'tunnel.log'
$PidFile = Join-Path $Root '.cache/api/share.pids'

function Get-ShareProcesses {
    if (-not (Test-Path $PidFile)) { return @() }
    $ids = (Get-Content $PidFile) | Where-Object { $_ -match '^\d+$' } | ForEach-Object { [int]$_ }
    $ids | Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue }
}

if ($Stop) {
    $stopped = 0
    foreach ($id in Get-ShareProcesses) {
        Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
        $stopped++
    }
    if (Test-Path $PidFile) { Remove-Item $PidFile -Force }
    Write-Host "已停止 $stopped 个进程（API / 隧道）"
    exit 0
}

if ($Status) {
    # 隧道会静默挂死：cloudflared 进程还在，但它到 Cloudflare 边缘的流已经断了，
    # 访客看到的是 Error 1033 / HTTP 530。所以进程存活不足以说明链接可用，
    # 必须真的请求一次公网地址。
    $alive = @(Get-ShareProcesses)
    Write-Host "进程：$(if ($alive) { "$($alive.Count) 个存活 (PID $($alive -join ', '))" } else { '无' })"
    $apiOk = $false
    try { $apiOk = (Invoke-RestMethod "http://127.0.0.1:$Port/health" -TimeoutSec 3).status -eq 'ok' } catch { }
    Write-Host "本机 API：$(if ($apiOk) { '就绪' } else { '无响应' })"
    $url = $null
    if (Test-Path $TunnelLog) {
        $match = Select-String -Path $TunnelLog -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($match) { $url = [regex]::Match($match.Line, 'https://[a-z0-9-]+\.trycloudflare\.com').Value }
    }
    if (-not $url) { Write-Host '公网地址：未知（尚无隧道日志）'; exit 1 }
    Write-Host "公网地址：$url/"
    $live = $false
    try { $live = (Invoke-RestMethod "$url/health" -TimeoutSec 10).status -eq 'ok' } catch { }
    if ($live) { Write-Host '公网可达：是 ✓'; exit 0 }
    Write-Host '公网可达：否 ✗（链接已失效，重新跑一次本脚本会换一个新地址）'
    exit 1
}

if (-not (Test-Path $Cloudflared)) {
    throw "cloudflared 未找到：$Cloudflared`n先下载：Invoke-WebRequest https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe -OutFile '$Cloudflared'"
}
if (-not (Test-Path (Join-Path $Root '.venv\Scripts\python.exe'))) { throw '未找到 .venv，先跑 scripts/start_local.ps1 初始化。' }
New-Item -ItemType Directory -Force -Path (Split-Path $ApiLog) | Out-Null

# 已在本脚本管理下运行则先停掉，避免端口与 pid 文件冲突。
foreach ($id in Get-ShareProcesses) { Stop-Process -Id $id -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2

# ── 选一个非回环的本机地址给隧道连接 ─────────────────────────────────────────
# 优先取「实际出网用的那块网卡」：向外部地址做一次 UDP connect 不会发包，只是让
# 系统选出源地址。这样不会误选 Radmin VPN / WSL / 虚拟网卡——那些地址在虚拟网卡
# 断开时会让隧道连不上。
$lanIp = $null
try {
    $probe = New-Object System.Net.Sockets.UdpClient
    $probe.Connect('1.1.1.1', 65530)
    $lanIp = $probe.Client.LocalEndPoint.Address.ToString()
    $probe.Close()
} catch { }
if (-not $lanIp -or $lanIp -like '127.*') {
    $fallback = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' -and
                       $_.InterfaceAlias -notmatch 'Loopback|WSL|vEthernet|VPN|Radmin' } |
        Select-Object -First 1
    if ($fallback) { $lanIp = $fallback.IPAddress }
}
if (-not $lanIp) { throw '找不到可用的局域网 IPv4 地址。' }
$lanBase = "http://${lanIp}:$Port"
Write-Host "隧道将连接：$lanBase"

# ── 起隧道并取回本次分配的域名 ───────────────────────────────────────────────
# 用 UseShellExecute 启动：不继承任何句柄，脚本能立刻继续。
# 另外两种写法都会卡住：Start-Process 配 -RedirectStandardOutput 会让脚本持有子进程
# 的流句柄；`cmd /c start /b ... | Out-Null` 的管道会被子进程继承。
function Start-Detached([string]$File, [string[]]$Arguments) {
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $File
    $info.Arguments = ($Arguments | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }) -join ' '
    $info.UseShellExecute = $true
    $info.WindowStyle = 'Hidden'
    return [System.Diagnostics.Process]::Start($info)
}

if (Test-Path $TunnelLog) { Remove-Item $TunnelLog -Force }
$tunnelArgs = @('tunnel', '--url', $lanBase, '--no-autoupdate', '--logfile', $TunnelLog)
if ($Protocol -ne 'auto') { $tunnelArgs += @('--protocol', $Protocol) }
$tunnel = Start-Detached $Cloudflared $tunnelArgs
Write-Host '等待隧道分配域名…'
$publicUrl = $null
foreach ($try in 1..40) {
    Start-Sleep -Milliseconds 750
    if (Test-Path $TunnelLog) {
        $match = Select-String -Path $TunnelLog -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($match) { $publicUrl = [regex]::Match($match.Line, 'https://[a-z0-9-]+\.trycloudflare\.com').Value; break }
    }
    if ($tunnel.HasExited) { throw "cloudflared 提前退出，查看 $TunnelLog" }
}
if (-not $publicUrl) { throw "40 次轮询内未取得隧道域名，查看 $TunnelLog" }
Write-Host "隧道域名：$publicUrl"

# ── 用该域名起 API ───────────────────────────────────────────────────────────
$env:DATAMIND_PUBLIC_CHAT = '1'
$origins = New-Object System.Collections.Generic.List[string]
foreach ($base in @("http://127.0.0.1:$Port", "http://localhost:$Port", $lanBase, $publicUrl)) { $origins.Add($base) }
$env:DATAMIND_ALLOWED_ORIGINS = ($origins | Select-Object -Unique) -join ','

$python = Join-Path $Root '.venv\Scripts\python.exe'
$api = Start-Detached $python @('-m', 'uvicorn', 'datamind.api:app', '--host', '0.0.0.0',
                                '--port', "$Port", '--no-proxy-headers')

Start-Sleep -Seconds 2
@($tunnel.Id, $api.Id) | Where-Object { $_ } | Sort-Object -Unique | Set-Content -Path $PidFile

# ── 等 API 就绪并自检公网可达 ────────────────────────────────────────────────
$ready = $false
foreach ($try in 1..30) {
    Start-Sleep -Milliseconds 700
    try { if ((Invoke-RestMethod "http://127.0.0.1:$Port/health" -TimeoutSec 2).status -eq 'ok') { $ready = $true; break } } catch { }
}
if (-not $ready) { throw "API 未在 20 秒内就绪，查看 $ApiLog" }

$public = $null
foreach ($try in 1..20) {
    try { $public = Invoke-RestMethod "$publicUrl/health" -TimeoutSec 8; break } catch { Start-Sleep -Seconds 2 }
}

Write-Host ''
Write-Host '════════════════════════════════════════════════════════'
if ($public) {
    Write-Host "  公网链接（可直接发给任何人）："
    Write-Host "    $publicUrl/"
    Write-Host "  本机访问： http://127.0.0.1:$Port/"
    Write-Host "  局域网：   $lanBase/"
    Write-Host ''
    Write-Host "  公开范围：仅问答（/v1/chat）。模型配置、权威规则、"
    Write-Host "  上传、快照、审批仍需登录，外部访客拿不到管理员。"
    Write-Host '  注意：任何拿到链接的人都能提问，会消耗你的模型额度。'
} else {
    Write-Host "  公网自检未通过：$publicUrl"
    Write-Host "  隧道可能仍在就绪中，稍后再试；或查看 $TunnelLog"
}
Write-Host "  停止：pwsh -File scripts/share_public.ps1 -Stop"
Write-Host '════════════════════════════════════════════════════════'
