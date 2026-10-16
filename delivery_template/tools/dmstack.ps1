<#
  dmstack.ps1 —— 便携演示包的服务编排（启动 / 等待 / 停止 / 状态）

  这个脚本只做「本包里的事」，不读写 %ProgramFiles%、注册表或系统服务：
    runtime\pgsql   便携版 PostgreSQL 16.4（数据目录 data\pgdata，端口 55432）
    runtime\qdrant  便携版 Qdrant 1.12.4（存储 data\qdrant_storage，端口 56333）
    runtime\python  嵌入式 Python 3.14.5 + runtime\pylibs
    app             项目源码（含 web\vue 前端构建产物）

  run.bat / stop.bat 是给老师的入口，本脚本被它们调用；也可以单独调用：
    powershell -NoProfile -ExecutionPolicy Bypass -File tools\dmstack.ps1 -Action status

  端口刻意避开 5432 / 6333 / 8000 之外的常用端口，以免和老师机器上已装的
  PostgreSQL / Qdrant 冲突；8000 是本应用固定端口（前端同源检查白名单写死了它）。

  环境变量：所有数据库 / 向量库地址都通过环境变量注入给应用子进程。应用侧代码把
  DSN 默认值硬编码在多个模块里（datamind/settings_store.py:33、sql_engine.py:357、
  agent.py:47-54 等），这些模块**不修改**，一律用它们已经支持的 os.environ 覆盖。
  注意：不设置 DATAMIND_LOCAL_NO_LOGIN（那是全局认证旁路，回环访问默认已是 demo
  admin，见 docs/DEPLOYMENT_BOUNDARIES.md 第 1 节）；uvicorn 必须带
  --no-proxy-headers（同文档第 2 节）。
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('check-port', 'check-api', 'start-postgres', 'wait-postgres', 'start-qdrant',
                 'wait-qdrant', 'start-api', 'start-api-tail', 'wait-api', 'health', 'stop', 'status',
                 'watchdog', 'show-env')]
    [string]$Action,
    [int]$Port = 8000,
    [int]$Seconds = 60,
    [int]$ApiPid = 0,
    [int]$TimeoutMinutes = 720,
    [switch]$Quiet
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Root = Split-Path -Parent $PSScriptRoot
$Runtime = Join-Path $Root 'runtime'
$AppDir = Join-Path $Root 'app'
$DataDir = Join-Path $Root 'data'
$LogDir = Join-Path $DataDir 'logs'
$PgDir = Join-Path $DataDir 'pgdata'
$QdrantStore = Join-Path $DataDir 'qdrant_storage'
$PgBin = Join-Path $Runtime 'pgsql\bin'
$PgCtl = Join-Path $PgBin 'pg_ctl.exe'
$PgIsReady = Join-Path $PgBin 'pg_isready.exe'
$QdrantExe = Join-Path $Runtime 'qdrant\qdrant.exe'
$Python = Join-Path $Runtime 'python\python.exe'
$PyLibs = Join-Path $Runtime 'pylibs'

$PGPORT = 55432
$QHTTP = 56333
$QGRPC = 56334
$APIPORT = 8000

# ── 端口覆盖（可选，只为「本机已有一个实例占着默认端口」的场景）─────────────
# 交付包的默认端口是写死的（前端同源检查白名单里有 8000），老师那台机器上直接双击
# run.bat 走的就是默认值。但打包验证时本机常常已经跑着一个开发实例占住 8000，
# 这时可以用环境变量把整套端口错开，在不打扰那个实例的前提下把包跑起来。
# 只认这四个变量，不读任何其它配置，默认值不变。
function Get-PortOverride([string]$Name, [int]$Default) {
    $raw = [Environment]::GetEnvironmentVariable($Name, 'Process')
    if (-not $raw) { return $Default }
    $parsed = 0
    if ([int]::TryParse($raw.Trim(), [ref]$parsed) -and $parsed -gt 0 -and $parsed -lt 65536) { return $parsed }
    return $Default
}
$PGPORT = Get-PortOverride 'DATAMIND_PG_PORT' $PGPORT
$QHTTP = Get-PortOverride 'DATAMIND_QDRANT_HTTP_PORT' $QHTTP
$QGRPC = Get-PortOverride 'DATAMIND_QDRANT_GRPC_PORT' $QGRPC
$APIPORT = Get-PortOverride 'DATAMIND_API_PORT' $APIPORT

function Say([string]$Text, [string]$Color = 'Gray') {
    if (-not $Quiet) { Write-Host $Text -ForegroundColor $Color }
}
function Quote-Arg([string]$Text) {
    # Start-Process 拼命令行时**不会**替参数加引号，含空格的路径会被拆成多个 token：
    # `-D D:\dmtest\中文 测试目录\...\pgdata` 让 pg_ctl 只吃到 "测试目录\...\pgdata"，
    # 直接报 `unrecognized operation mode`。老师把包解压到「桌面\新建文件夹」这种带空格
    # 的路径太常见，所以凡是可能含空格的路径参数都要过这一层。
    if ($Text -match '\s') { return '"' + $Text + '"' }
    return $Text
}
function Ensure-LogDir { if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null } }

# ── 端口与进程探测 ───────────────────────────────────────────────────────────
function Test-TcpPort([int]$Number, [int]$TimeoutMs = 500) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect('127.0.0.1', $Number, $null, $null)
        if ($async.AsyncWaitHandle.WaitOne($TimeoutMs)) { $client.EndConnect($async); return $true }
        return $false
    } catch { return $false } finally { $client.Close() }
}

function Get-PackageProcesses {
    # 只认「可执行文件位于本包目录内」的进程：不会误杀老师机器上的其它程序。
    $prefix = $Root.TrimEnd('\') + '\'
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($prefix, 'OrdinalIgnoreCase') }
}

function Get-Health([int]$Port = 0) {
    if ($Port -le 0) { $Port = $APIPORT }
    try {
        return Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 3
    } catch { return $null }
}

function Set-AppEnvironment {
    # 应用的实际配置来源：全部是既有代码已经支持的 os.environ 键名（见文件头说明）。
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    if (Test-Path $PyLibs) { $env:PYTHONPATH = $PyLibs }

    $appDsn = "host=127.0.0.1 port=$PGPORT dbname=datamind_meta user=datamind_app password=datamind_app_dev"
    $roDsn = "host=127.0.0.1 port=$PGPORT dbname=datamind_meta user=readonly_user password=readonly_dev"
    $env:DATAMIND_META_WRITE_DSN = $appDsn
    $env:DATAMIND_WRITE_DSN = $appDsn
    $env:DATAMIND_META_DSN = $roDsn
    $env:DATAMIND_WIKI_WRITE_DSN = $appDsn
    $env:DATAMIND_WIKI_READ_DSN = $roDsn
    # 不设 DATAMIND_QUERY_DSN：sql_engine 会拒绝「一个 DSN 复用给不同 catalog 库」。
    $env:DATAMIND_DB_HOST = '127.0.0.1'
    $env:DATAMIND_DB_PORT = "$PGPORT"
    $env:DATAMIND_QUERY_USER = 'readonly_user'
    $env:DATAMIND_QUERY_PASSWORD = 'readonly_dev'
    $env:DATAMIND_QDRANT_URL = "http://127.0.0.1:$QHTTP"

    # 可选的模型密钥文件（默认不存在）：data\model_config.env，每行 KEY=VALUE。
    # 打包时不带任何真实密钥，老师可自行放入或在前端「模型接入」页面填写。
    $keyFile = Join-Path $DataDir 'model_config.env'
    if (Test-Path $keyFile) {
        foreach ($line in (Get-Content $keyFile -Encoding UTF8)) {
            $text = $line.Trim()
            if (-not $text -or $text.StartsWith('#')) { continue }
            $idx = $text.IndexOf('=')
            if ($idx -lt 1) { continue }
            $name = $text.Substring(0, $idx).Trim()
            $value = $text.Substring($idx + 1).Trim().Trim('"')
            [Environment]::SetEnvironmentVariable($name, $value, 'Process')
        }
        Say "  已载入模型配置：$keyFile" 'DarkGray'
    }
}

function Wait-Ready([scriptblock]$Probe, [int]$TimeoutSeconds) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (& $Probe) { return $true }
        Start-Sleep -Milliseconds 700
    }
    return $false
}

switch ($Action) {

    'check-port' {
        if (Test-TcpPort $Port) { 'BUSY' } else { 'FREE' }
    }

    'check-api' {
        # OURAPP：端口上已经是本应用（/health 回 database=ready）；NONE：没人监听。
        if (-not (Test-TcpPort $Port)) { 'NONE'; break }
        $health = Get-Health $Port
        if ($health -and $health.database -eq 'ready') { 'OURAPP' } else { 'OTHER' }
    }

    'start-postgres' {
        if (Test-TcpPort $PGPORT) { Say "  PostgreSQL 端口 $PGPORT 已在监听，跳过启动。" 'Yellow'; 'ALREADY'; break }
        if (-not (Test-Path $PgCtl)) { throw "缺少便携版 PostgreSQL：$PgCtl" }
        if (-not (Test-Path (Join-Path $PgDir 'PG_VERSION'))) { throw "缺少预置数据目录：$PgDir" }
        Ensure-LogDir
        $log = Join-Path $LogDir 'postgres.log'
        # -o 后面是一整条 postgres 选项串，**必须自带引号**：Start-Process 不会替我们加，
        # pg_ctl 只吃掉紧邻的一个 token，于是 "-c"/端口被当成「操作模式」直接报
        # `pg_ctl: unrecognized operation mode`。这条踩过，别再改回去。
        $pgArgs = @('-D', (Quote-Arg $PgDir), '-l', (Quote-Arg $log), '-o',
                    "`"-p $PGPORT -c listen_addresses=127.0.0.1`"", '-w', 'start')
        # 不要把 pg_ctl 的输出接进管道：postgres 会继承句柄，管道永远不结束。
        # -Wait 与重定向到文件是安全的（文件句柄由 Start-Process 自己管）。
        $proc = Start-Process -FilePath $PgCtl -ArgumentList $pgArgs -NoNewWindow -PassThru `
            -RedirectStandardOutput (Join-Path $LogDir 'pg_ctl.out.log') `
            -RedirectStandardError (Join-Path $LogDir 'pg_ctl.err.log')
        # 自己等 + 上限：Start-Process -Wait 在句柄异常时没有超时可言，一旦挂住就是
        # 「双击 run.bat 之后什么都不发生」。这里最多等 60 秒，超时就报中文错误。
        # 刻意不用 `$proc.HasExited` / `$proc.ExitCode`：配了 -RedirectStandardOutput
        # 之后这两个属性在本机实测不可靠（HasExited 立刻为真、ExitCode 读到空值，
        # 于是明明启动成功却被判成失败）。Wait-Process 是 cmdlet，等待与取值都稳。
        $timedOut = $false
        try { Wait-Process -Id $proc.Id -Timeout 60 -ErrorAction Stop }
        catch { $timedOut = $true }
        if ($timedOut) {
            try { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue } catch { }
            throw "pg_ctl start 超过 60 秒没有返回，见 $log"
        }
        $pgCtlExit = 0
        try { $pgCtlExit = [int](Get-Process -Id $proc.Id -ErrorAction Stop).ExitCode } catch { $pgCtlExit = 0 }
        if ($pgCtlExit -ne 0) { throw "pg_ctl start 失败（退出码 $pgCtlExit），见 $log" }
        'STARTED'
    }

    'wait-postgres' {
        $ok = Wait-Ready -TimeoutSeconds $Seconds -Probe {
            & $PgIsReady -h 127.0.0.1 -p $PGPORT -q *> $null
            return ($LASTEXITCODE -eq 0)
        }
        if ($ok) { 'READY' } else { 'TIMEOUT' }
    }

    'start-qdrant' {
        if (Test-TcpPort $QHTTP) { Say "  Qdrant 端口 $QHTTP 已在监听，跳过启动。" 'Yellow'; 'ALREADY'; break }
        if (-not (Test-Path $QdrantExe)) { throw "缺少便携版 Qdrant：$QdrantExe" }
        Ensure-LogDir
        if (-not (Test-Path $QdrantStore)) { New-Item -ItemType Directory -Path $QdrantStore -Force | Out-Null }
        $env:QDRANT__STORAGE__STORAGE_PATH = $QdrantStore
        $env:QDRANT__SERVICE__HTTP_PORT = "$QHTTP"
        $env:QDRANT__SERVICE__GRPC_PORT = "$QGRPC"
        $env:QDRANT__TELEMETRY_DISABLED = 'true'
        # 完全脱离启动（UseShellExecute=$true）：父脚本一个子进程句柄都不持有，
        # 输出让 cmd 自己重定向到文件。**不要**改成
        # `Start-Process -RedirectStandardOutput`：那个组合会让父脚本等一个永远不来的
        # 流 EOF，表现是「双击 run.bat 之后卡住、界面上什么都不再发生」——本项目在
        # share_public.ps1 上踩过，交付包构建脚本也栽在同一处。
        $outLog = Join-Path $LogDir 'qdrant.out.log'
        $errLog = Join-Path $LogDir 'qdrant.err.log'
        $info = New-Object System.Diagnostics.ProcessStartInfo
        $info.FileName = 'cmd.exe'
        $info.Arguments = '/c ""{0}" --disable-telemetry > "{1}" 2> "{2}""' -f $QdrantExe, $outLog, $errLog
        $info.UseShellExecute = $true
        $info.WindowStyle = 'Hidden'
        $info.WorkingDirectory = (Split-Path -Parent $QdrantExe)
        $proc = [System.Diagnostics.Process]::Start($info)
        # 记的是 cmd 包装进程的 pid：停止时按「可执行文件在本包内」精确匹配 qdrant.exe，
        # 不靠这个 pid（见 stop 分支）。
        Set-Content -Path (Join-Path $LogDir 'qdrant.pid') -Value $proc.Id -Encoding ASCII
        'STARTED'
    }

    'wait-qdrant' {
        $ok = Wait-Ready -TimeoutSeconds $Seconds -Probe {
            try {
                $r = Invoke-WebRequest "http://127.0.0.1:$QHTTP/readyz" -UseBasicParsing -TimeoutSec 2
                return ($r.StatusCode -eq 200)
            } catch { return $false }
        }
        if ($ok) { 'READY' } else { 'TIMEOUT' }
    }

    'start-api' {
        if (-not (Test-Path $Python)) { throw "缺少便携版 Python：$Python" }
        Ensure-LogDir
        Set-AppEnvironment

        # 1) 两个队列 worker：上传解析与 Wiki 整理都只由 worker 推进，
        #    没有它们任务会永远停在 queued（docs/DEPLOYMENT_BOUNDARIES.md 第 6 节）。
        foreach ($worker in @(
                @{ Name = 'upload_worker'; Args = @('-m', 'ingestion.upload_worker', '--watch') },
                @{ Name = 'wiki_worker'; Args = @('scripts/wiki_build_worker.py', '--watch') })) {
            $pidFile = Join-Path $LogDir "$($worker.Name).pid"
            $alive = $false
            if (Test-Path $pidFile) {
                $old = (Get-Content $pidFile -First 1) -as [int]
                if ($old -and (Get-Process -Id $old -ErrorAction SilentlyContinue)) { $alive = $true }
            }
            if ($alive) { Say "  $($worker.Name) 已在运行（PID $old）" 'DarkGray'; continue }
            $wp = Start-Process -FilePath $Python -ArgumentList $worker.Args -WorkingDirectory $AppDir `
                -WindowStyle Hidden -PassThru `
                -RedirectStandardOutput (Join-Path $LogDir "$($worker.Name).log") `
                -RedirectStandardError (Join-Path $LogDir "$($worker.Name).err.log")
            Set-Content -Path $pidFile -Value $wp.Id -Encoding ASCII
            Say "  已启动 $($worker.Name)（PID $($wp.Id)）" 'DarkGray'
        }

        # 2) API 本体。
        #
        # ⚠ 这里的三条约束是「run.bat 冷启动永久卡死在 [4/6]」这个致命缺陷换来的，
        #   改动前请先读 docs/DELIVERY.md 第 12 节：
        #
        #   (a) stdout / stderr 必须重定向到文件（两个**不同**的文件）。
        #       Windows PowerShell 5.1 的 Start-Process 在 RedirectStandardOutput 与
        #       RedirectStandardError 指向同一个文件时直接报
        #       「"RedirectStandardOutput" and "RedirectStandardError" are same」，
        #       所以拆成 api_console.log（stdout）与 api_console.err.log（stderr）。
        #   (b) uvicorn 是常驻进程，绝不能让它继承调用方的 stdout 管道。
        #       修复前这里是裸的 `-NoNewWindow -PassThru`：uvicorn 继承了
        #       run.bat 第 108 行 `for /f` 的管道并一直持有，而 `for /f` 必须等到
        #       管道 EOF（= API 退出）才结束循环 —— 老师双击后看到的就是一个永远
        #       不动的黑窗口，api_console.log 也永远不会生成。
        #   (c) 整个 start-api 分支**不许再往 stdout 写任何东西**，只把 PID 落盘
        #       （api.pid）。这是双保险：即使有人把本分支重新包进 `for /f` 或管道，
        #       也不会有任何东西被挡住。run.bat 读 api.pid，不再捕获本脚本的输出。
        $apiLog = Join-Path $LogDir 'api_console.log'
        $apiErrLog = Join-Path $LogDir 'api_console.err.log'
        # 每次启动先清空：老师看到的就是「这一次」的日志，而不是上一次的残留
        # （残留内容还会让排查时误判服务已经起来）。
        Remove-Item $apiLog, $apiErrLog -Force -ErrorAction SilentlyContinue
        $apiArgs = @('-m', 'uvicorn', 'datamind.api:app', '--host', '127.0.0.1',
                     '--port', "$APIPORT", '--no-proxy-headers')
        $proc = Start-Process -FilePath $Python -ArgumentList $apiArgs -WorkingDirectory $AppDir `
            -NoNewWindow -PassThru `
            -RedirectStandardOutput $apiLog -RedirectStandardError $apiErrLog
        Set-Content -Path (Join-Path $LogDir 'api.pid') -Value $proc.Id -Encoding ASCII
        # 只落盘、不回显：见上面 (c)。这里刻意不写 "APIPID=..." 到 stdout。

        # 3) 看门狗：老师直接关掉窗口时，API 会随控制台一起退出，但 PostgreSQL / Qdrant /
        #    worker 是独立进程；看门狗在 API 退出后把它们收干净，避免端口被占住。
        try {
            $watch = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Action watchdog -ApiPid $($proc.Id) -TimeoutMinutes $TimeoutMinutes"
            Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $watch } | Out-Null
        } catch {
            Say "  （提示）看门狗启动失败，窗口关闭后请用 stop.bat 停止服务：$($_.Exception.Message)" 'Yellow'
        }
    }

    'start-api-tail' {
        # 「服务日志」窗口：API 的 stdout/stderr 现在落在文件里（见 start-api 的 (a)），
        # 老师要看到 uvicorn 的 INFO 行就得有人把文件跟出来。这个分支由 run.bat 用
        # `start`（独立、新的控制台）拉起，自己只负责 tail，退出码与主流程无关。
        $apiLog = Join-Path $LogDir 'api_console.log'
        $apiErrLog = Join-Path $LogDir 'api_console.err.log'
        $errSeen = Join-Path $LogDir '.api_console.err.seen'
        # stderr 的「已读到哪」游标每次开窗都归零，免得读到上一轮的位置
        Set-Content -LiteralPath $errSeen -Value 0 -Encoding ASCII
        $host.UI.RawUI.WindowTitle = 'DataMind Agent 服务日志（可最小化，勿关）'
        Write-Host 'DataMind Agent 服务日志 —— 这个窗口只显示日志，关掉它不影响服务。' -ForegroundColor DarkGray
        Write-Host '（stop.bat 不会关这个窗口，用完自己关掉即可。）' -ForegroundColor DarkGray
        Write-Host ''
        $seen = 0
        while ($true) {
            # $null -eq $f 的写法是刻意的：Set-StrictMode -Version Latest 下访问
            # $null.Length 会直接抛错，而 StrictMode 是本脚本文件顶部设的，改不掉。
            $f = $null
            if (Test-Path -LiteralPath $apiLog) { $f = Get-Item -LiteralPath $apiLog -ErrorAction SilentlyContinue }
            if ($null -ne $f -and $f.Length -gt $seen) {
                $stream = $null
                try {
                    # FileShare::ReadWrite 必须显式给：默认 FileShare.Read 会与
                    # uvicorn 的写句柄冲突，tail 直接抛「文件正在被另一进程使用」。
                    $stream = New-Object System.IO.FileStream($apiLog, [System.IO.FileMode]::Open,
                        [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
                    $stream.Seek($seen, [System.IO.SeekOrigin]::Begin) | Out-Null
                    $reader = New-Object System.IO.StreamReader($stream, [System.Text.Encoding]::UTF8)
                    $chunk = $reader.ReadToEnd()
                    $seen = $stream.Position
                    $reader.Dispose()
                    if ($chunk) { Write-Host $chunk -NoNewline }
                } catch {
                    # 读不到就下一轮再试，绝不因为一次 IO 竞争把日志窗口打死
                } finally {
                    if ($null -ne $stream) { $stream.Dispose() }
                }
            }
            # stderr（例如启动期 traceback）单独标一下，便于老师截图报错
            $fe = $null
            if (Test-Path -LiteralPath $apiErrLog) { $fe = Get-Item -LiteralPath $apiErrLog -ErrorAction SilentlyContinue }
            if ($null -ne $fe -and $fe.Length -gt 0) {
                $mark = 0
                $rawMark = Get-Content -LiteralPath $errSeen -First 1 -ErrorAction SilentlyContinue
                if ($rawMark) { $null = [int]::TryParse("$rawMark".Trim(), [ref]$mark) }
                if ($fe.Length -gt $mark) {
                    try {
                        $stream2 = New-Object System.IO.FileStream($apiErrLog, [System.IO.FileMode]::Open,
                            [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
                        $stream2.Seek($mark, [System.IO.SeekOrigin]::Begin) | Out-Null
                        $reader2 = New-Object System.IO.StreamReader($stream2, [System.Text.Encoding]::UTF8)
                        $chunk2 = $reader2.ReadToEnd()
                        $newMark = $stream2.Position
                        $reader2.Dispose()
                        $stream2.Dispose()
                        Set-Content -LiteralPath $errSeen -Value $newMark -Encoding ASCII
                        if ($chunk2) { Write-Host $chunk2 -NoNewline -ForegroundColor DarkYellow }
                    } catch { }
                }
            }
            Start-Sleep -Milliseconds 500
        }
    }

    'wait-api' {
        $deadline = (Get-Date).AddSeconds($Seconds)
        $state = 'TIMEOUT'
        while ((Get-Date) -lt $deadline) {
            $h = Get-Health $APIPORT
            if ($h -and $h.database -eq 'ready' -and $h.vector_store -eq 'ready') { $state = 'READY'; break }
            if ($h -and $h.database -eq 'ready') { $state = 'DEGRADED' }
            Start-Sleep -Milliseconds 700
        }
        $state
    }

    'health' {
        $h = Get-Health $APIPORT
        if ($h) { $h | ConvertTo-Json -Depth 5 } else { 'UNREACHABLE' }
    }

    'show-env' {
        Set-AppEnvironment
        Get-ChildItem Env: | Where-Object { $_.Name -like 'DATAMIND_*' -or $_.Name -like 'NEW_API_*' -or $_.Name -in @('PYTHONUTF8', 'PYTHONPATH') } |
            Sort-Object Name | ForEach-Object { "{0}={1}" -f $_.Name, $_.Value }
    }

    'stop' {
        Ensure-LogDir
        $stopped = @()

        # 1) 先停 API 与 worker（都在本包的 runtime\python 下运行）。
        foreach ($p in (Get-PackageProcesses | Where-Object { $_.ExecutablePath -like "$Runtime\python\*" })) {
            Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
            $stopped += "python PID $($p.ProcessId)"
        }
        # 2) 再停 Qdrant。
        foreach ($p in (Get-PackageProcesses | Where-Object { $_.ExecutablePath -like "$Runtime\qdrant\*" })) {
            Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
            $stopped += "qdrant PID $($p.ProcessId)"
        }
        # 3) 最后停 PostgreSQL：优先走 pg_ctl（干净关库，不需要恢复）。
        #    不用 -Wait：Start-Process -Wait 在句柄异常时没有超时，一旦挂住 stop.bat
        #    就永远不返回。这里自己等，最多 60 秒。
        if (Test-TcpPort $PGPORT) {
            $stopProc = Start-Process -FilePath $PgCtl -ArgumentList @('-D', (Quote-Arg $PgDir), '-m', 'fast', '-w', 'stop') `
                -NoNewWindow -PassThru -RedirectStandardOutput (Join-Path $LogDir 'pg_ctl_stop.out.log') `
                -RedirectStandardError (Join-Path $LogDir 'pg_ctl_stop.err.log')
            $stopTimedOut = $false
            try { Wait-Process -Id $stopProc.Id -Timeout 60 -ErrorAction Stop } catch { $stopTimedOut = $true }
            if ($stopTimedOut) { Say "  （注意）pg_ctl stop 超过 60 秒未返回，改为强制结束 postgres" 'Yellow' }
            $stopped += 'postgresql'
        }
        Start-Sleep -Milliseconds 500
        foreach ($p in (Get-PackageProcesses | Where-Object { $_.ExecutablePath -like "$Runtime\pgsql\*" })) {
            Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
            $stopped += "postgres PID $($p.ProcessId)"
        }
        foreach ($name in @('api', 'upload_worker', 'wiki_worker', 'qdrant')) {
            Remove-Item (Join-Path $LogDir "$name.pid") -Force -ErrorAction SilentlyContinue
        }
        if ($stopped.Count -eq 0) { 'NOTHING-RUNNING' }
        else { 'STOPPED: ' + ($stopped -join ', ') }
    }

    'status' {
        $health = Get-Health $APIPORT
        $lines = @()
        $lines += "包目录            : $Root"
        $lines += "PostgreSQL  $PGPORT : " + $(if (Test-TcpPort $PGPORT) { '运行中' } else { '未运行' })
        $lines += "Qdrant      $QHTTP : " + $(if (Test-TcpPort $QHTTP) { '运行中' } else { '未运行' })
        $lines += "API         $APIPORT : " + $(if ($health) { '运行中' } else { '未运行' })
        if ($health) {
            $lines += "  database      : $($health.database)"
            $lines += "  vector_store  : $($health.vector_store)"
            $lines += "  knowledge     : $($health.knowledge)（已发布文档 $($health.document_count) 篇）"
            $lines += "  worker        : $($health.worker)"
        }
        $lines -join [Environment]::NewLine
    }

    'watchdog' {
        # 等待 API 进程退出，然后收拾它留下的独立子进程。本进程由 WMI 创建，
        # 不挂在老师的控制台上，所以关窗口不会把它一起带走。
        if ($ApiPid -le 0) { return }
        $deadline = (Get-Date).AddMinutes($TimeoutMinutes)
        while ((Get-Date) -lt $deadline) {
            if (-not (Get-Process -Id $ApiPid -ErrorAction SilentlyContinue)) {
                Start-Sleep -Seconds 2
                $null = & $PSCommandPath -Action stop -Quiet
                return
            }
            Start-Sleep -Seconds 2
        }
    }
}
