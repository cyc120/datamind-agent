<#
.SYNOPSIS
    DataMind Agent 便携交付包打包脚本（可重复执行）。

.DESCRIPTION
    产出 D:\ICT中国电子杯\delivery\DataMindAgent_Demo\：一个免 Docker、免 Python、
    免安装数据库的演示包 —— 老师解压后双击 run.bat 即可用浏览器使用。

    与其他「压一次 zIP」的做法不同，本脚本的核心目标是**可重复**：
      * 运行时压缩包按 sha256 缓存（delivery_cache/），重复执行不重下；
      * 运行时解压、pip 依赖安装各自带 stamp，内容没变就跳过；
      * 打开 app/ 文件夹是**每次都重新同步**的，所以前端重新构建后
        再跑一次本脚本，包里的前端就是新的；
      * 数据库预置数据默认复用（-RebuildData 强制重建）。

    四个阶段：
      1) 运行时  解压 PostgreSQL / Qdrant / 嵌入式 Python，装 pylibs
      2) 应用    把仓库源码 + 前端构建产物 + 模型缓存同步进 app/
      3) 数据    控制台 initdb + 迁移 + 种子 + 文档摄取，产出 data/pgdata、data/qdrant_storage
      4) 打包    写入 run.bat / stop.bat / README.txt 等，可选打 zip

    关于中文路径（实测结论，详见 docs/DELIVERY.md）：
      * PostgreSQL 的 initdb **要求安装目录与数据目录都是纯 ASCII 路径**，
        否则报 `FATAL: invalid byte sequence for encoding "UTF8"`；
      * 但已经建好的数据目录，postgres 服务器本身在中文路径下**能正常运行**。
      所以本脚本只在「建库」这一段用 subst 映射出一个 ASCII 盘符
      （物理文件仍在本目录内），建完就撤掉；交付包本身不依赖 subst。

.PARAMETER OutDir
    交付包输出根目录，默认 <仓库上一级>\delivery。

.PARAMETER CacheDir
    运行时可下载物缓存目录，默认 <仓库上一级>\delivery_cache。

.PARAMETER Proxy
    下载与 pip 走的 HTTP 代理，默认 http://127.0.0.1:7890；-Direct 表示直连。

.PARAMETER Frontend
    copy（默认）：直接用仓库里现成的 web/vue 构建产物；
    build：先在前端工程里跑 npm run deploy 再打包（前端改了就选它）；
    skip：不动 frontend 源码目录（只带 web/vue 产物）。

.PARAMETER RebuildRuntime
    重新解压运行时并重装 pylibs（默认按压缩包 sha256 与依赖清单复用）。

.PARAMETER RebuildData
    重新 initdb + 迁移 + 种子 + 文档摄取（默认复用已有的 data/pgdata）。

.PARAMETER EmbedModelKeyFile
    可选：把指定的 KEY=VALUE 文件复制成包内 data/model_config.env。
    **默认不写任何密钥**；只有你显式传这个参数，密钥才会进包。

.PARAMETER NoZip
    只产出文件夹，不打 zip。

.EXAMPLE
    pwsh -File scripts/build_delivery.ps1                       # 全量（首次）
    pwsh -File scripts/build_delivery.ps1 -Frontend build        # 前端改动后重新打包
    pwsh -File scripts/build_delivery.ps1 -RebuildData           # 连数据库一起重建
#>
[CmdletBinding()]
param(
    [string]$OutDir,
    [string]$CacheDir,
    [string]$Proxy = 'http://127.0.0.1:7890',
    [switch]$Direct,
    [ValidateSet('copy', 'build', 'skip')]
    [string]$Frontend = 'copy',
    [switch]$RebuildRuntime,
    [switch]$RebuildData,
    [switch]$NoZip,
    [string]$EmbedModelKeyFile,
    [switch]$SkipFetch,
    # 构建期「无进展」看门狗上限（分钟）：超过这么久没有任何一步推进，脚本自己带
    # 退出码 2 结束，并打印最后推进到哪一步。任何等待都不允许无声挂起。
    [int]$WatchdogMinutes = 15,
    # 单个外部命令的硬上限（分钟）：pip 装依赖、应用脚本（迁移/种子/摄取）都用它兜底。
    [int]$StepTimeoutMinutes = 40
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$RepoRoot = Split-Path -Parent $PSScriptRoot
$BaseRoot = Split-Path -Parent $RepoRoot
if (-not $OutDir) { $OutDir = Join-Path $BaseRoot 'delivery' }
if (-not $CacheDir) { $CacheDir = Join-Path $BaseRoot 'delivery_cache' }
$OutDir = [System.IO.Path]::GetFullPath($OutDir)
$CacheDir = [System.IO.Path]::GetFullPath($CacheDir)

$PkgName = 'DataMindAgent_Demo'
$PkgDir = Join-Path $OutDir $PkgName
$BuildDir = Join-Path $OutDir '_build'
$TemplateDir = Join-Path $RepoRoot 'delivery_template'
$StampFile = Join-Path $BuildDir 'build_stamps.json'
$LogDir = Join-Path $BuildDir 'logs'
$BuildLog = Join-Path $LogDir 'build_steps.log'
$HeartbeatFile = Join-Path $BuildDir 'build_heartbeat.txt'
$HangFile = Join-Path $LogDir 'build_hang_report.txt'

$PgPort = 55432
$QdrantHttp = 56333
$QdrantGrpc = 56334
$ApiPort = 8000
$PythonVersion = '3.14.5'

$script:StepNo = 0
$script:StartedAt = Get-Date

function Write-Phase([string]$Text) {
    $script:StepNo++
    Write-BuildLog "===== 阶段 $($script:StepNo)：$Text =====" 'PHASE'
    Write-Host ''
    Write-Host ("=== [{0}] {1}" -f $script:StepNo, $Text) -ForegroundColor Cyan
}
function Write-Info([string]$Text) { Write-Host "    $Text" }
function Write-Ok([string]$Text) { Write-Host "    [OK] $Text" -ForegroundColor Green }
function Write-Warn2([string]$Text) { Write-Host "    [注意] $Text" -ForegroundColor Yellow }

function Assert-Path([string]$Path, [string]$What) {
    if (-not (Test-Path $Path)) { throw "缺少$What：$Path" }
}

# ── 分步心跳日志 ────────────────────────────────────────────────────────────
# 为什么需要：脚本曾经在某一步无声挂起 18 分钟，事后只能靠文件 mtime 反推。
# 现在每一步、每一次等待、每一个外部命令都往 build_steps.log 写带毫秒时间戳的
# 一行，同时刷新 build_heartbeat.txt；看门狗进程读心跳，超时就带着「最后一行」
# 报错并自杀。所以「挂在哪一行」永远是查得到的。
function Write-BuildLog([string]$Text, [string]$Level = 'STEP') {
    $stamp = Get-Date -Format 'HH:mm:ss.fff'
    $line = "[$stamp] [$Level] $Text"
    if ($BuildLog) {
        try { Add-Content -Path $BuildLog -Value $line -Encoding UTF8 } catch { }
        try { Set-Content -Path $HeartbeatFile -Value $line -Encoding UTF8 } catch { }
    }
    if ($Level -eq 'FAIL') { Write-Host "    $line" -ForegroundColor Red }
}

# 每一步/每个外部命令前调一次：写了日志，超时就能精确指到这一步。
function Write-Step([string]$Text) { Write-BuildLog $Text 'STEP' }

# 长任务期间给看门狗一个「我在动」的信号（下载/安装这类没有子步骤的阶段）。
function Send-Heartbeat([string]$Text) { Write-BuildLog $Text 'TICK' }

# ── 进程工具：一律带超时，一律不留孤儿 ──────────────────────────────────────
<#
为什么不用 `Start-Process -Wait -RedirectStandardOutput`：
Windows PowerShell 5.1 里这两个参数组合会让父脚本一直等重定向流的 EOF。被启动的
进程（pg_ctl / cmd / 应用脚本）会把自己的句柄复制给子进程，子进程不退，EOF 就永远
不到，父脚本就永久挂住 —— 这正是本项目在 share_public.ps1 上踩过的坑，也是交付包
构建脚本会挂起的根因。这里改成两条互不干扰的路子：

  * Wait-ProgramExit  —— 直接 .NET Process 启动、句柄继承控制台（不经过管道/文件流），
                         用 WaitForExit(毫秒) 兜底，超时就杀进程树；
  * Start-Detached    —— 后台常驻服务（Qdrant）用 UseShellExecute=$true 的完全脱离
                         启动，输出由 cmd 自己写文件，父脚本一个句柄都不持有。
#>

function Get-DescendantProcessIds([int]$RootId) {
    # 返回到叶子的顺序（先杀子、后杀父），避免杀父进程后子进程变成孤儿。
    $result = New-Object System.Collections.Generic.List[int]
    $all = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.ParentProcessId -and $_.ProcessId } |
        Select-Object ProcessId, ParentProcessId)
    function Add-Children([int]$Parent) {
        foreach ($p in $all) {
            if ([int]$p.ParentProcessId -eq $Parent -and $result -notcontains [int]$p.ProcessId) {
                Add-Children ([int]$p.ProcessId)
                $result.Add([int]$p.ProcessId)
            }
        }
    }
    Add-Children $RootId
    return $result
}

function Stop-ProcessTree([int]$RootId) {
    foreach ($child in (Get-DescendantProcessIds $RootId)) {
        Stop-Process -Id $child -Force -ErrorAction SilentlyContinue
    }
    Stop-Process -Id $RootId -Force -ErrorAction SilentlyContinue
}

function Invoke-NativeChecked {
    # 跑一条外部命令，带硬超时；输出直接继承本控制台（用户能实时看到进度），
    # 超时则杀掉整个进程树并抛中文错误。
    #
    # 为什么不用 Start-Job 包一层：实测两个坑都踩过 ——
    #   1) Windows PowerShell 5.1 的 job 子进程不继承父作用域变量，$Exe 是 $null；
    #   2) 即使用 -ArgumentList 把参数传进去，job 的输出流仍会被引到当前控制台，
    #      同一份输出打印两遍，退出码数字还会混进正文。
    # 直接用 .NET Process + UseShellExecute=$false（不重定向任何流）就没有这两个问题：
    # 子进程继承控制台句柄，实时输出，父脚本只负责等待和超时。
    param([string]$Exe, [string[]]$Arguments, [string]$What, [int]$TimeoutSeconds = ($StepTimeoutMinutes * 60))
    # 允许直接给 PATH 上的裸命令名（如 npm.cmd、subst）；其余一律要求真实路径存在，
    # 免得「命令名拼错」被报成一句看不懂的启动异常。
    $resolved = $Exe
    if (-not (Test-Path -LiteralPath $Exe)) {
        $cmd = Get-Command $Exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($cmd) { $resolved = $cmd.Source } else { throw "找不到可执行文件（$What）：$Exe" }
    }
    Write-Info $What
    Write-Step "$What ← $resolved $($Arguments -join ' ')"
    $started = Get-Date
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $resolved
    # 只给「含空格」的参数补引号，调用方自己带的引号原样保留：
    # 一律加引号会把 D:\ICT中国电子杯\... 变成 \"...\"，被程序当成含转义引号的路径。
    $info.Arguments = ($Arguments | ForEach-Object {
        if ($_ -match '^".*"$') { $_ }
        elseif ($_ -match '\s') { '"' + $_ + '"' }
        else { $_ }
    }) -join ' '
    $info.UseShellExecute = $false
    $info.WorkingDirectory = (Get-Location).Path
    $proc = $null
    try {
        $proc = [System.Diagnostics.Process]::Start($info)
    } catch {
        throw "$What 无法启动：$Exe（$($_.Exception.Message)）"
    }
    $code = Wait-ProgramExit -Process $proc -TimeoutSeconds $TimeoutSeconds -What $What
    Write-BuildLog ("$What 用时 {0:N1} 秒，退出码 {1}" -f ((Get-Date) - $started).TotalSeconds, $code) 'TIME'
    if ($code -ne 0) { throw "$What 失败（退出码 $code）：$Exe $($Arguments -join ' ')" }
    return $code
}

function Start-Detached([string]$File, [string[]]$Arguments, [string]$OutLog, [string]$ErrLog) {
    # 完全脱离：不继承任何句柄（UseShellExecute = $true），输出交给 cmd 自己重定向
    # 到文件。父脚本手里只有 Process 对象，没有任何会阻塞的流。
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = 'cmd.exe'
    $info.Arguments = '/c ""{0}" {1} > "{2}" 2> "{3}""' -f $File, ($Arguments -join ' '), $OutLog, $ErrLog
    $info.UseShellExecute = $true
    $info.WindowStyle = 'Hidden'
    $info.WorkingDirectory = (Split-Path -Parent $File)
    return [System.Diagnostics.Process]::Start($info)
}

function Quote-Arg([string]$Text) {
    # Start-Process（以及 ProcessStartInfo.Arguments）拼命令行时**不会**替参数加引号。
    # 交付目录路径里一旦有空格（老师解压到「桌面\新建文件夹」很常见），
    # `-D D:\中文 测试目录\...\pgdata` 会被拆成两个 token，pg_ctl 报
    # `unrecognized operation mode` 并拒绝启动。含空格的路径参数一律过这一层。
    if ($Text -match '\s') { return '"' + $Text + '"' }
    return $Text
}

function Wait-ProgramExit {
    param([System.Diagnostics.Process]$Process, [int]$TimeoutSeconds, [string]$What, [string]$KillExe = '')
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    while (-not $Process.HasExited) {
        if ($sw.Elapsed.TotalSeconds -ge $TimeoutSeconds) {
            Write-Warn2 "$What 超过 $TimeoutSeconds 秒未退出，强制结束"
            if ($KillExe) {
                Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
                    Where-Object { $_.ExecutablePath -and $_.ExecutablePath -like $KillExe } |
                    ForEach-Object { Stop-ProcessTree ([int]$_.ProcessId) }
            }
            try { Stop-ProcessTree $Process.Id } catch { }
            while (-not $Process.HasExited -and $sw.Elapsed.TotalSeconds -lt ($TimeoutSeconds + 20)) { Start-Sleep -Milliseconds 200 }
            throw "$What 超时（$TimeoutSeconds 秒）未结束，已强制结束。日志：$BuildLog"
        }
        Start-Sleep -Milliseconds 200
    }
    # 用了重定向的 Process，ExitCode 必须先 WaitForExit() 才有效（.NET 把「排空异步
    # 输出流」算在 WaitForExit 里），否则调用方读到的是空值。
    try { $Process.WaitForExit() } catch { }
    return $Process.ExitCode
}

function Wait-WithDeadline {
    # 通用等待循环：必须有 deadline，超时返回 $false，由调用方抛中文错误。
    param([scriptblock]$Probe, [int]$TimeoutSeconds, [string]$What)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $tries = 0
    while ((Get-Date) -lt $deadline) {
        $tries++
        if (& $Probe) { return $true }
        Start-Sleep -Milliseconds 700
        if ($tries % 15 -eq 0) { Send-Heartbeat "$What：已等待 $([int]((Get-Date) - ($deadline.AddSeconds(-$TimeoutSeconds))).TotalSeconds) 秒 / 上限 $TimeoutSeconds 秒" }
    }
    return $false
}

function Stop-PackageQdrant {
    # 只杀「可执行文件位于本交付目录内」的 qdrant.exe，不碰老师/本机其它 Qdrant。
    $prefix = $OutDir.TrimEnd('\') + '\'
    $killed = @()
    foreach ($p in (Get-CimInstance Win32_Process -Filter "Name='qdrant.exe'" -ErrorAction SilentlyContinue)) {
        if ($p.ExecutablePath -and $p.ExecutablePath.StartsWith($prefix, 'OrdinalIgnoreCase')) {
            Stop-ProcessTree ([int]$p.ProcessId)
            $killed += $p.ProcessId
        }
    }
    if ($killed.Count -gt 0) { Write-BuildLog "已结束包内 qdrant.exe：PID $($killed -join ', ')" } else { Write-BuildLog '没有发现包内 qdrant.exe 在跑' }
    return $killed
}

function Clear-StalePostmasterPid([string]$DataDir) {
    # 数据目录被强杀（或上一次构建中途被打断）后会留下 postmaster.pid，下一次
    # pg_ctl start 会拒绝启动。这里只在「里面记的 PID 其实已经不存在」时才删，
    # 绝不删一个真在跑的实例的锁文件。
    $pidFile = Join-Path $DataDir 'postmaster.pid'
    if (-not (Test-Path $pidFile)) { return $false }
    $firstLine = (Get-Content $pidFile -First 1 -ErrorAction SilentlyContinue)
    $owner = 0
    if (-not [int]::TryParse("$firstLine".Trim(), [ref]$owner)) { return $false }
    if (Get-Process -Id $owner -ErrorAction SilentlyContinue) {
        Write-BuildLog "postmaster.pid 指向仍在运行的 PID $owner，保留" 'STEP'
        return $false
    }
    Write-Warn2 "数据目录里残留 postmaster.pid（PID $owner 已不存在），清掉后重新启动"
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
    return $true
}

function Start-PackagePostgres([string]$DataDir, [int]$Port, [string]$PgCtl, [string]$LogFile, [string]$What) {
    # 启动便携 PostgreSQL 并**等到真的能连**为止。
    # 三个关键点，都是踩出来的：
    #   1) pg_ctl 的输出既不接管道也不合并流，一律重定向到文件 —— postgres 会继承
    #      父进程句柄，接管道会让调用方永久等待（本项目的经典坑）；
    #   2) 每个含空格的参数都要自带引号：Start-Process 不替我们加，交付目录路径里
    #      一旦有空格（D:\dmtest\中文 测试目录\...），-D 只吃到后半段，pg_ctl 直接报
    #      `unrecognized operation mode`；
    #   3) -o 后面是一整条 postgres 选项串，同理必须自带引号。
    Clear-StalePostmasterPid $DataDir | Out-Null
    $started = Start-Process -FilePath $PgCtl `
        -ArgumentList @('-D', (Quote-Arg $DataDir), '-l', (Quote-Arg $LogFile), '-o', "`"-p $Port`"", '-w', 'start') `
        -NoNewWindow -PassThru `
        -RedirectStandardOutput ($LogFile + '.ctl.out') -RedirectStandardError ($LogFile + '.ctl.err')
    $code = Wait-ProgramExit -Process $started -TimeoutSeconds 120 -What $What `
        -KillExe ((Split-Path -Parent $PgCtl) + '\*')
    $ready = Wait-WithDeadline -TimeoutSeconds 90 -What "$What（等 pg_isready）" -Probe {
        & (Join-Path (Split-Path -Parent $PgCtl) 'pg_isready.exe') -h 127.0.0.1 -p $Port -q *> $null
        return ($LASTEXITCODE -eq 0)
    }
    if (-not $ready) {
        # 把 pg_ctl 自己的 stderr 也带出来：它才是「为什么没起来」的第一现场。
        $tail = ''
        if (Test-Path $LogFile) { $tail = ((Get-Content $LogFile -Tail 10 -ErrorAction SilentlyContinue) -join ' / ') }
        $ctlErr = ''
        if (Test-Path ($LogFile + '.ctl.err')) { $ctlErr = ((Get-Content ($LogFile + '.ctl.err') -ErrorAction SilentlyContinue) -join ' / ') }
        throw "$What 后 PostgreSQL 仍未就绪（pg_ctl 退出码 $code）。pg_ctl 报：$ctlErr ；$LogFile 尾部：$tail"
    }
    return $code
}

function Wait-PortFree([int]$Port, [int]$TimeoutSeconds, [string]$What) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $busy = $false
        try { $busy = [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) } catch { $busy = $false }
        if (-not $busy) { return $true }
        Start-Sleep -Milliseconds 400
    }
    return $false
}

function Write-Phase([string]$Text) {
    $script:StepNo++
    Write-BuildLog "===== 阶段 $($script:StepNo)：$Text =====" 'PHASE'
    Write-Host ''
    Write-Host ("=== [{0}] {1}" -f $script:StepNo, $Text) -ForegroundColor Cyan
}

function Invoke-Native([string]$Exe, [string[]]$Arguments, [string]$What) {
    # 仍然走「直接调用 + 控制台继承」，但带超时兜底；pg_ctl / psql / subst 都用它。
    Invoke-NativeChecked -Exe $Exe -Arguments $Arguments -What $What
}

function Copy-Tree {
    param([string]$Source, [string]$Destination,
          [string[]]$ExcludeDirs = @(), [string[]]$ExcludeFiles = @(),
          [switch]$Mirror)
    if (-not (Test-Path $Source)) { throw "复制源不存在：$Source" }
    if (-not (Test-Path $Destination)) { New-Item -ItemType Directory -Path $Destination -Force | Out-Null }
    $robocopy = Join-Path $env:SystemRoot 'System32\robocopy.exe'
    $rcArgs = @($Source, $Destination, '/E', '/NFL', '/NDL', '/NJH', '/NJS', '/NP', '/R:1', '/W:1')
    if ($ExcludeDirs.Count -gt 0) { $rcArgs += '/XD'; $rcArgs += $ExcludeDirs }
    if ($ExcludeFiles.Count -gt 0) { $rcArgs += '/XF'; $rcArgs += $ExcludeFiles }
    if ($Mirror) {
        # /PURGE：删掉目标里「源已经没有」的文件。app\ 必须用它 —— 否则前端每重新
        # 构建一次，vite 的哈希文件名就多攒一套（实测一次重建多 36 个文件），旧
        # assets 永远不会清掉，包只会越滚越大，而 index.html 只引用新的那套。
        # 只对 app\ 用：data\ 下的 pgdata / qdrant_storage 不是从模板来的，绝不能被清。
        $rcArgs += '/PURGE'
    }
    Write-BuildLog ("robocopy 开始：源=[{0}] 目标=[{1}] 可执行=[{2}] 排除目录={3} 排除文件={4} 镜像清理={5}" -f `
        $Source, $Destination, $robocopy, $ExcludeDirs.Count, $ExcludeFiles.Count, [bool]$Mirror) 'COPY'
    & $robocopy @rcArgs | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy 失败（退出码 $LASTEXITCODE）：$Source -> $Destination" }
    Write-BuildLog "robocopy 完成（退出码 $LASTEXITCODE）：$Source" 'COPY'
    $global:LASTEXITCODE = 0
}

function Expand-Zip([string]$Archive, [string]$Destination) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    if (Test-Path $Destination) { Remove-Item $Destination -Recurse -Force }
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    [System.IO.Compression.ZipFile]::ExtractToDirectory($Archive, $Destination, [System.Text.Encoding]::UTF8)
}

function Get-Sha256([string]$Path) { (Get-FileHash -Path $Path -Algorithm SHA256).Hash.ToLowerInvariant() }

function Get-FolderSize([string]$Path) {
    if (-not (Test-Path $Path)) { return 0 }
    (Get-ChildItem $Path -Recurse -File -Force -ErrorAction SilentlyContinue |
        Measure-Object -Sum Length).Sum
}

function Format-Size([double]$Bytes) {
    if ($Bytes -ge 1GB) { return ('{0:N2} GB' -f ($Bytes / 1GB)) }
    return ('{0:N1} MB' -f ($Bytes / 1MB))
}

function Read-Stamps {
    $table = @{ pgsql = $null; qdrant = $null; python = $null; pylibs = $null }
    if (-not (Test-Path $StampFile)) { return $table }
    try {
        $raw = Get-Content $StampFile -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($entry in $raw) { $table[$entry.key] = $entry.value }
    } catch { }
    return $table
}
function Save-Stamps($Table) {
    if (-not (Test-Path $BuildDir)) { New-Item -ItemType Directory -Path $BuildDir -Force | Out-Null }
    $list = @()
    foreach ($key in ($Table.Keys | Sort-Object)) {
        $list += [pscustomobject]@{ key = $key; value = $Table[$key] }
    }
    Set-Content -Path $StampFile -Value (ConvertTo-Json -InputObject @($list) -Depth 6) -Encoding UTF8
}

function Get-FreeDriveLetter {
    $used = (Get-PSDrive -PSProvider FileSystem).Name
    foreach ($letter in @('W', 'X', 'Y', 'Z', 'V', 'U', 'T', 'S', 'R', 'Q', 'P', 'O', 'N', 'M', 'L', 'K')) {
        if ($used -contains $letter) { continue }
        if (Test-Path "${letter}:\") { continue }
        return $letter
    }
    return $null
}

function Start-BuildWatchdog([int]$StagnationMinutes) {
    # 「无声挂起」的兜底：一个独立进程盯着心跳文件，只要 StagnationMinutes 分钟内
    # 没有任何一行新日志，就写一份 build_hang_report.txt（含最后一行的精确内容），
    # 然后结束构建进程。挂起最多持续这么久，而且永远查得到挂在哪一行。
    $watchdogJob = Start-Job -ScriptBlock {
        $buildPid = $using:PID
        $file = $using:HeartbeatFile
        $report = $using:HangFile
        $limit = $using:StagnationMinutes
        while ($true) {
            Start-Sleep -Seconds 20
            if (-not (Get-Process -Id $buildPid -ErrorAction SilentlyContinue)) { return 'BUILD-EXITED' }
            if (-not (Test-Path $file)) { continue }
            $item = Get-Item $file -ErrorAction SilentlyContinue
            if (-not $item) { continue }
            $idle = ((Get-Date) - $item.LastWriteTime).TotalMinutes
            if ($idle -ge $limit) {
                $last = ''
                try { $last = (Get-Content $file -Raw -Encoding UTF8).Trim() } catch { }
                $lines = @(
                    '构建脚本「无进展」超时报告',
                    "报告时间     : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')",
                    "构建进程 PID : $buildPid",
                    "判定阈值     : $limit 分钟没有任何一步推进",
                    "实际无进展   : $([math]::Round($idle,1)) 分钟",
                    '',
                    '最后一行心跳（就是挂住的位置）：',
                    "  $last",
                    '',
                    "完整分步日志：$file",
                    '提示：若最后一行是外部命令（pip / python / pg_ctl），说明该命令卡住；',
                    '      请把 build_steps.log 尾部贴出来定位。'
                )
                try { Set-Content -Path $report -Value ($lines -join "`r`n") -Encoding UTF8 } catch { }
                Stop-Process -Id $buildPid -Force -ErrorAction SilentlyContinue
                return 'KILLED'
            }
        }
    }
    return $watchdogJob
}

function Stop-BuildWatchdog($Job) {
    if (-not $Job) { return }
    # 刻意不用 Stop-Job：Windows PowerShell 5.1 的 Stop-Job 会把承载 job 的
    # powershell.exe 直接结束，实测会连带把本进程（甚至外层调用方）一起带走。
    # 这里按「父进程 = 我 + 最近创建」精确找到那个子 powershell.exe 再杀。
    try {
        $cutoff = (Get-Date).AddMinutes(-30)
        Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
            Where-Object {
                [int]$_.ParentProcessId -eq $PID -and $_.CreationDate -and $_.CreationDate -ge $cutoff
            } | ForEach-Object { Stop-ProcessTree ([int]$_.ProcessId) }
    } catch { }
    try { Remove-Job -Job $Job -Force -ErrorAction SilentlyContinue } catch { }
}

# ═══════════════════════════════════════════════════════════════════════════
$script:QdrantStopped = $false
$script:ExitCode = 0
$script:Watchdog = $null
try {
Write-Host ''
Write-Host '============================================================' -ForegroundColor White
Write-Host ' DataMind Agent 便携交付包打包' -ForegroundColor White
Write-Host '============================================================' -ForegroundColor White
Write-Info "仓库      : $RepoRoot"
Write-Info "输出      : $PkgDir"
Write-Info "缓存      : $CacheDir"
Write-Info "前端      : $Frontend"
Write-Info "重建运行时: $RebuildRuntime    重建数据: $RebuildData"

foreach ($folder in @($OutDir, $CacheDir, $BuildDir, $LogDir)) {
    if (-not (Test-Path $folder)) { New-Item -ItemType Directory -Path $folder -Force | Out-Null }
}
# 打包过程用的临时目录也放在构建目录里：一是自带清理、不污染系统 %TEMP%，二是某些
# 受限环境下系统临时目录不可写（get-pip / pip 会在 tempfile 上直接失败）。
$TempDir = Join-Path $BuildDir 'tmp'
if (-not (Test-Path $TempDir)) { New-Item -ItemType Directory -Path $TempDir -Force | Out-Null }
$env:TMP = $TempDir
$env:TEMP = $TempDir
Assert-Path (Join-Path $RepoRoot 'datamind\api.py') '仓库源码（datamind/api.py）'
Assert-Path $TemplateDir '包内模板目录（delivery_template/）'

# 分步日志从这里开始滚动（每次运行先清空，避免上一轮的尾部误导定位）。
Set-Content -Path $BuildLog -Value ("[{0}] [BEGIN] 构建开始：{1}" -f (Get-Date -Format 'HH:mm:ss.fff'), ($MyInvocation.Line)) -Encoding UTF8
Remove-Item $HangFile -Force -ErrorAction SilentlyContinue
Write-BuildLog ("参数：Frontend=$Frontend RebuildRuntime=$RebuildRuntime RebuildData=$RebuildData NoZip=$NoZip SkipFetch=$SkipFetch WatchdogMinutes=$WatchdogMinutes StepTimeoutMinutes=$StepTimeoutMinutes") 'BEGIN'
Write-BuildLog "仓库=$RepoRoot 输出=$PkgDir 缓存=$CacheDir" 'BEGIN'
$script:Watchdog = Start-BuildWatchdog -StagnationMinutes $WatchdogMinutes
Write-BuildLog "无进展看门狗已启动（阈值 $WatchdogMinutes 分钟）" 'BEGIN'

# ── 0. 运行时压缩包 ─────────────────────────────────────────────────────────
Write-Phase '准备运行时压缩包（缓存复用）'
if (-not $SkipFetch) {
    & (Join-Path $PSScriptRoot 'fetch_runtime.ps1') -CacheDir $CacheDir -Proxy $Proxy -Direct:$Direct
    if ($LASTEXITCODE -ne 0) { throw 'fetch_runtime.ps1 失败' }
    $global:LASTEXITCODE = 0
}
$ArchivePython = Join-Path $CacheDir "python-$PythonVersion-embed-amd64.zip"
$ArchiveQdrant = Join-Path $CacheDir 'qdrant-x86_64-pc-windows-msvc.zip'
$ArchivePg = Join-Path $CacheDir 'postgresql-16.4-1-windows-x64-binaries.zip'
$GetPip = Join-Path $CacheDir 'get-pip.py'
foreach ($item in @($ArchivePython, $ArchiveQdrant, $ArchivePg, $GetPip)) { Assert-Path $item '运行时压缩包（先跑 scripts/fetch_runtime.ps1）' }

$stamps = Read-Stamps
$RuntimeDir = Join-Path $PkgDir 'runtime'
$PgHome = Join-Path $RuntimeDir 'pgsql'
$PythonHome = Join-Path $RuntimeDir 'python'
$PyLibs = Join-Path $RuntimeDir 'pylibs'
$QdrantHome = Join-Path $RuntimeDir 'qdrant'

$proxyArgs = @()
if (-not $Direct -and $Proxy) { $proxyArgs = @('--proxy', $Proxy) }

# ── 1. 便携运行时 ───────────────────────────────────────────────────────────
Write-Phase '装配便携运行时（PostgreSQL / Qdrant / 嵌入式 Python / pylibs）'
if (-not (Test-Path $RuntimeDir)) { New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null }

# 1a. PostgreSQL 16.4（只留运行必需：bin + lib + share）
$pgHash = Get-Sha256 $ArchivePg
if ($RebuildRuntime -or $stamps['pgsql'] -ne $pgHash -or -not (Test-Path (Join-Path $PgHome 'bin\pg_ctl.exe'))) {
    Write-Info "解压 PostgreSQL 16.4（$([math]::Round((Get-Item $ArchivePg).Length/1MB,1)) MB）..."
    $tmp = Join-Path $BuildDir 'pg_extract'
    Write-Step "解压 PostgreSQL 压缩包 → $tmp"
    Expand-Zip $ArchivePg $tmp
    Write-BuildLog 'PostgreSQL 压缩包已解开，开始搬运 bin/lib/share' 'COPY'
    Assert-Path (Join-Path $tmp 'pgsql\bin\pg_ctl.exe') 'PostgreSQL 解压结果'
    if (Test-Path $PgHome) { Remove-Item $PgHome -Recurse -Force }
    New-Item -ItemType Directory -Path $PgHome -Force | Out-Null
    foreach ($keep in @('bin', 'lib', 'share')) {
        Move-Item (Join-Path $tmp "pgsql\$keep") (Join-Path $PgHome $keep)
    }
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    # 去掉与运行无关的大件：pgAdmin 4（616 MB）、symbols、doc、include、StackBuilder、import 库
    foreach ($junk in @('bin\*.lib', 'lib\*.lib', 'bin\wx*.dll', 'bin\stackbuilder.exe',
                       'bin\pgAdmin4.exe', 'bin\pgcli*')) {
        Get-ChildItem (Join-Path $PgHome $junk) -ErrorAction SilentlyContinue |
            Remove-Item -Force -ErrorAction SilentlyContinue
    }
    # VC++ 2015-2022 运行库：postgres.exe 动态链接 VCRUNTIME140.dll，而 EDB 的 zip 不带它。
    # 老师机器上没装运行库时，PostgreSQL 会直接起不来，所以随包带上这两个 DLL
    # （微软可再发行组件清单内的文件，允许随应用一起分发）。
    $copied = @()
    foreach ($dll in @('vcruntime140.dll', 'vcruntime140_1.dll', 'msvcp140.dll')) {
        $src = Join-Path $env:SystemRoot "System32\$dll"
        if (Test-Path $src) {
            Copy-Item $src (Join-Path $PgHome 'bin') -Force
            $copied += $dll
        }
    }
    if ($copied.Count -eq 0) { Write-Warn2 '本机没有找到 VC++ 运行库 DLL，包内 PostgreSQL 可能在那台机器上需要另装 VC++ 2015-2022 运行库' }
    else { Write-Ok ("PostgreSQL 已就绪（随包附带 {0}）" -f ($copied -join ', ')) }
    $stamps['pgsql'] = $pgHash
    Save-Stamps $stamps
} else { Write-Ok 'PostgreSQL 运行时未变化，复用' }

# 1b. Qdrant 1.12.4（单文件）
$qdHash = Get-Sha256 $ArchiveQdrant
if ($RebuildRuntime -or $stamps['qdrant'] -ne $qdHash -or -not (Test-Path (Join-Path $QdrantHome 'qdrant.exe'))) {
    Write-Info '解压 Qdrant 1.12.4 ...'
    $tmp = Join-Path $BuildDir 'qdrant_extract'
    Write-Step "解压 Qdrant 压缩包 → $tmp"
    Expand-Zip $ArchiveQdrant $tmp
    if (Test-Path $QdrantHome) { Remove-Item $QdrantHome -Recurse -Force }
    New-Item -ItemType Directory -Path $QdrantHome -Force | Out-Null
    $exe = Get-ChildItem $tmp -Recurse -Filter 'qdrant.exe' | Select-Object -First 1
    if (-not $exe) { throw '压缩包里没找到 qdrant.exe' }
    Copy-Item $exe.FullName (Join-Path $QdrantHome 'qdrant.exe') -Force
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    $stamps['qdrant'] = $qdHash
    Save-Stamps $stamps
    Write-Ok 'Qdrant 已就绪'
} else { Write-Ok 'Qdrant 运行时未变化，复用' }

# 1c. 嵌入式 Python 3.14.5
$pyHash = Get-Sha256 $ArchivePython
if ($RebuildRuntime -or $stamps['python'] -ne $pyHash -or -not (Test-Path (Join-Path $PythonHome 'python.exe'))) {
    Write-Info "解压 CPython $PythonVersion 嵌入式发行版 ..."
    Write-Step '解压嵌入式 Python'
    if (Test-Path $PythonHome) { Remove-Item $PythonHome -Recurse -Force }
    Expand-Zip $ArchivePython $PythonHome
    Assert-Path (Join-Path $PythonHome 'python.exe') '嵌入式 Python'
    $stamps['python'] = $pyHash
    Save-Stamps $stamps
    Write-Ok '嵌入式 Python 已解压'
} else { Write-Ok 'Python 运行时未变化，复用' }

# 1c-2. ._pth：嵌入式发行版用它代替 sys.path；必须显式打开 site，并把 app / pylibs
# 以及 pip 自己的 site-packages 都写进去。注意：存在 ._pth 时 PYTHONPATH 会被忽略，
# 所有路径都要写在这里。
# **每次都重写**（而不是只在解压后写一次）：早期版本的脚本漏了 pywin32 那三条路径，
# 复用旧 runtime 的机器必须能靠重跑本脚本自动修好，不能要求用户先删 runtime。
$pthPath = Get-ChildItem $PythonHome -Filter 'python3*._pth' -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $pthPath) { throw "找不到嵌入式 Python 的 ._pth 文件（$PythonHome）" }
# pylibs\pywin32_system32 等三条为什么必须手写：嵌入式发行版走 ._pth 通道时
# 不会处理 pylibs 里的 *.pth（pywin32.pth 的那套 hackery 根本不执行），于是
# pywintypes314.dll / win32api.pyd 找不到，portalocker（qdrant_client 的依赖）
# 一 import 就 `ModuleNotFoundError: No module named 'pywintypes'`。
# 顺序要紧：win32\lib 里是 pywintypes.py 等纯 Python 包装，必须先于 win32。
$pthFile = $pthPath.FullName
$stdlibZip = (Split-Path -Leaf $pthFile) -replace '\._pth$', '.zip'
$pthLines = @(
    '# DataMind Agent portable runtime (generated by scripts/build_delivery.ps1)',
    '# NOTE: Python reads this file as UTF-8, so keep every line pure ASCII;',
    '# when this file exists CPython ignores PYTHONPATH, hence the paths below.',
    $stdlibZip,
    '.',
    'Lib\site-packages',
    '..\pylibs',
    '..\pylibs\pywin32_system32',
    '..\pylibs\win32\lib',
    '..\pylibs\win32',
    '..\..\app',
    'import site'
)
Set-Content -Path $pthFile -Value $pthLines -Encoding ASCII
Write-BuildLog "._pth 已写入：$pthFile"
Write-Ok "$(Split-Path -Leaf $pthFile) 已就绪（含 ..\pylibs、pywin32 与 ..\..\app）"

# 1d. 依赖库 pylibs（要求清单变了才重装）
$pylibsRequirements = Join-Path $BuildDir 'pylibs_requirements.txt'
$lockPath = Join-Path $RepoRoot 'requirements.lock'
Assert-Path $lockPath 'requirements.lock'
$pinned = Get-Content $lockPath | Where-Object { $_ -match '^[A-Za-z0-9_.\-]+==' }
# tzdata 不在 lock 里，但 sql_engine/agent 在 Windows 上要 zoneinfo("Asia/Shanghai")，
# requirements-sql.txt 也把它标成 win32 必需；这里对齐 .venv 里的实际版本。
# pywin32 同理：portalocker（qdrant-client 的依赖）在 Windows 上 `import pywintypes`，
# 而 lock 里没有它；不显式装的话 qdrant_client 直接 import 失败。版本对齐 .venv。
$extraPins = @('tzdata==2026.5', 'pywin32==312')
$allPins = @($pinned) + $extraPins
Set-Content -Path $pylibsRequirements -Value ($allPins -join "`n") -Encoding ASCII
$pylibsKey = (Get-FileHash $pylibsRequirements -Algorithm SHA256).Hash.ToLowerInvariant() + '|' + $pyHash

if ($RebuildRuntime -or $stamps['pylibs'] -ne $pylibsKey -or -not (Test-Path (Join-Path $PyLibs 'uvicorn'))) {
    $embedded = Join-Path $PythonHome 'python.exe'
    Write-Info '给嵌入式 Python 装 pip（只影响包内 runtime\python，不动系统）...'
    $pipCache = Join-Path $CacheDir 'pip'
    if (-not (Test-Path $pipCache)) { New-Item -ItemType Directory -Path $pipCache -Force | Out-Null }
    $env:PIP_CACHE_DIR = $pipCache
    $env:PYTHONUTF8 = '1'
    $env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
    $pipArgs = @()
    if (-not $Direct -and $Proxy) { $pipArgs = @('--proxy', $Proxy) }
    Invoke-NativeChecked -Exe $embedded -Arguments (@($GetPip, '--no-warn-script-location') + $pipArgs) `
        -What 'get-pip.py 给嵌入式 Python 装 pip' -TimeoutSeconds 900
    # 嵌入式发行版只带来 pip；antlr4-python3-runtime（omegaconf/RapidOCR 的依赖）在 PyPI
    # 上只有 sdist，需要 setuptools 才能构建，所以先给这个解释器装上构建后端。
    Invoke-NativeChecked -Exe $embedded -Arguments (@('-m', 'pip', 'install', '--no-warn-script-location',
        'setuptools', 'wheel') + $pipArgs) -What '安装 setuptools/wheel' -TimeoutSeconds 900

    Write-Info ("安装 {0} 个依赖包到 runtime\pylibs（首次会从网上下载，之后走本地 pip 缓存）..." -f $allPins.Count)
    if (Test-Path $PyLibs) { Remove-Item $PyLibs -Recurse -Force }
    New-Item -ItemType Directory -Path $PyLibs -Force | Out-Null
    # --prefer-binary：能用 wheel 就用 wheel（上述 sdist 包除外）。
    # --no-build-isolation：复用上一步装好的 setuptools，避免 pip 再去临时环境里找后端。
    Invoke-NativeChecked -Exe $embedded -Arguments (@('-m', 'pip', 'install', '--target', $PyLibs,
        '--no-warn-script-location', '--prefer-binary', '--no-input', '--no-build-isolation',
        '-r', $pylibsRequirements) + $pipArgs) -What '安装 pylibs 全部依赖' -TimeoutSeconds ($StepTimeoutMinutes * 60)
    $stamps['pylibs'] = $pylibsKey
    Save-Stamps $stamps
    Write-Ok ("pylibs 已就绪（{0}）" -f (Format-Size (Get-FolderSize $PyLibs)))
} else { Write-Ok ("pylibs 未变化，复用（{0}）" -f (Format-Size (Get-FolderSize $PyLibs))) }

# 1e. 依赖自检：用包内这套运行时把「应用真的会 import 的模块」全导一遍。
# 这一步存在的意义就是让「包里少了某个依赖」在打包阶段红着脸失败，而不是等审核
# 老师点到那个页面才发现 ImportError。历史教训：pylibs 里少了 pywin32 的路径，
# 结果 qdrant_client 一 import 就炸，直到建库阶段才暴露。
Write-Info '依赖自检（用包内嵌入式 Python 导入全部应用模块）...'
$importCheck = Join-Path $TemplateDir 'build_support\import_check.py'
Assert-Path $importCheck '依赖自检脚本（delivery_template/build_support/import_check.py）'
Invoke-NativeChecked -Exe (Join-Path $PythonHome 'python.exe') -Arguments @($importCheck) `
    -What '包内运行时依赖自检' -TimeoutSeconds 900
Write-Ok '包内运行时依赖自检通过'

# ── 2. 应用源码 + 前端产物 + 模型缓存 ───────────────────────────────────────
Write-Phase '同步应用源码、前端产物与模型缓存到 app/'
$AppDir = Join-Path $PkgDir 'app'
if (-not (Test-Path $AppDir)) { New-Item -ItemType Directory -Path $AppDir -Force | Out-Null }

if ($Frontend -eq 'build') {
    $frontendDir = Join-Path $RepoRoot 'frontend'
    Assert-Path (Join-Path $frontendDir 'package.json') '前端工程（frontend/package.json）'
    if (-not (Test-Path (Join-Path $frontendDir 'node_modules'))) {
        Invoke-Native 'npm.cmd' @('install') '安装前端依赖（npm install）'
    }
    Push-Location $frontendDir
    try { Invoke-Native 'npm.cmd' @('run', 'deploy') '构建前端（npm run deploy → web/vue）' }
    finally { Pop-Location }
}

$excludeDirs = @('.git', '.venv', '.venv-formula', '__pycache__', '.pytest_cache', '.mypy_cache',
                 '.ruff_cache', 'node_modules', '.vite', 'dist', 'tmp', 'output', 'reports',
                 '交付', '技术文档', '赛题', '.uploads', '.repo-state')
# -Mirror（robocopy /PURGE）：app/ 是从仓库派生的，必须与仓库「完全一致」。
# 不加的话前端重新构建后，旧的哈希 assets 会一直堆在包里（见 Copy-Tree 注释）。
foreach ($folder in @('datamind', 'db', 'ingestion', 'deploy', 'scripts', 'web', 'docs', 'data', 'tests')) {
    $src = Join-Path $RepoRoot $folder
    if (-not (Test-Path $src)) { Write-Warn2 "仓库里没有 $folder，跳过"; continue }
    Copy-Tree -Source $src -Destination (Join-Path $AppDir $folder) -ExcludeDirs $excludeDirs `
        -ExcludeFiles @('*.pyc', '*.pyo') -Mirror
    Write-Info "已同步 $folder\"
}
if ($Frontend -ne 'skip') {
    $src = Join-Path $RepoRoot 'frontend'
    if (Test-Path $src) {
        Copy-Tree -Source $src -Destination (Join-Path $AppDir 'frontend') `
            -ExcludeDirs @('node_modules', '.vite', 'dist', '__pycache__') -ExcludeFiles @('*.log') -Mirror
        Write-Info '已同步 frontend\（前端源码，不含 node_modules / dist）'
    }
}
foreach ($file in @('README.md', 'requirements.txt', 'requirements.lock', 'requirements-sql.txt',
                    'requirements-rag.txt', 'requirements-ocr.txt', 'requirements-cad.txt',
                    'requirements-formula.txt', 'requirements-office.txt', 'PROGRESS.md', '.gitignore')) {
    $src = Join-Path $RepoRoot $file
    if (Test-Path $src) { Copy-Item $src (Join-Path $AppDir $file) -Force }
}
Assert-Path (Join-Path $AppDir 'web\vue\index.html') '前端构建产物 app\web\vue\index.html（先运行 cd frontend && npm run deploy）'
Write-Ok '应用源码已同步'

# 模型缓存：knowledge.py:103 与 tables.py:13 把缓存路径硬编码成 ROOT/.cache/...，
# 没有环境变量可覆盖，所以必须随包放在 app\.cache\ 下，否则老师机器上要联网下载模型。
$appCache = Join-Path $AppDir '.cache'
if (-not (Test-Path $appCache)) { New-Item -ItemType Directory -Path $appCache -Force | Out-Null }
foreach ($cacheName in @('fastembed', 'ocr_models', 'formula_models')) {
    $src = Join-Path $RepoRoot ".cache\$cacheName"
    if (-not (Test-Path $src)) { continue }
    Copy-Tree -Source $src -Destination (Join-Path $appCache $cacheName) -ExcludeFiles @('*.lock')
    Write-Info ("已同步 .cache\{0}（{1}）" -f $cacheName, (Format-Size (Get-FolderSize (Join-Path $appCache $cacheName))))
}
foreach ($dir in @('api')) {
    $target = Join-Path $appCache $dir
    if (-not (Test-Path $target)) { New-Item -ItemType Directory -Path $target -Force | Out-Null }
}

# ── 3. 预置数据库与向量库 ───────────────────────────────────────────────────
Write-Phase '预置数据（initdb + 迁移 + 种子 + 文档摄取）'
$DataDir = Join-Path $PkgDir 'data'
$PgDataFinal = Join-Path $DataDir 'pgdata'
$QdrantStore = Join-Path $DataDir 'qdrant_storage'
if (-not (Test-Path $DataDir)) { New-Item -ItemType Directory -Path $DataDir -Force | Out-Null }

$embeddedPython = Join-Path $PythonHome 'python.exe'
$shimDir = Join-Path $BuildDir 'build_support'
if (-not (Test-Path $shimDir)) { New-Item -ItemType Directory -Path $shimDir -Force | Out-Null }
Copy-Item (Join-Path $TemplateDir 'build_support\sitecustomize.py') $shimDir -Force
Copy-Item (Join-Path $TemplateDir 'build_support\run_with_shim.py') $shimDir -Force

$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:DM_BUILD_PG_PORT = "$PgPort"
$env:DM_BUILD_QDRANT_URL = "http://127.0.0.1:$QdrantHttp"
$env:DM_BUILD_SHIM_LOG = Join-Path $LogDir 'shim_hits.log'
$appDsn = "host=127.0.0.1 port=$PgPort dbname=datamind_meta user=datamind_app password=datamind_app_dev"
$roDsn = "host=127.0.0.1 port=$PgPort dbname=datamind_meta user=readonly_user password=readonly_dev"

function Invoke-AppPython([string]$ScriptRelative, [string[]]$ScriptArgs = @(), [string]$What,
                          [int]$TimeoutSeconds = ($StepTimeoutMinutes * 60)) {
    # 用包内嵌入式 Python + pylibs 跑构建脚本：既完成建库，也顺带验证了「包里这套运行时
    # 真的能跑这个仓库的代码」。硬编码 5432/6333 的连接由 build_support 的 sitecustomize 改写。
    # 一律带超时（摄取那一步要 OCR + 向量化，给的时间最长），超时杀进程树并抛中文错误。
    $target = Join-Path $AppDir $ScriptRelative
    Assert-Path $target "构建脚本 $ScriptRelative"
    Invoke-NativeChecked -Exe $embeddedPython `
        -Arguments (@((Join-Path $shimDir 'run_with_shim.py'), $target) + $ScriptArgs) `
        -What $What -TimeoutSeconds $TimeoutSeconds
}

$needData = $RebuildData -or -not (Test-Path (Join-Path $PgDataFinal 'PG_VERSION'))
if (-not $needData) {
    Write-Ok '已存在预置数据目录，复用（要重建请加 -RebuildData）'
} else {
    if (Test-Path $PgDataFinal) { Remove-Item $PgDataFinal -Recurse -Force }
    if ($RebuildData -and (Test-Path $QdrantStore)) { Remove-Item $QdrantStore -Recurse -Force }
    if (-not (Test-Path $QdrantStore)) { New-Item -ItemType Directory -Path $QdrantStore -Force | Out-Null }

    # 3a. 用 subst 造一个纯 ASCII 盘符：initdb 要求安装目录与数据目录都是 ASCII。
    #     物理文件仍然全部在本交付目录内，subst 只是给它们起个别名。
    $letter = Get-FreeDriveLetter
    if (-not $letter) { throw '找不到可用盘符，无法为 initdb 建立 ASCII 路径别名（subst）' }
    $ascii = "${letter}:"
    Write-Info "建立 ASCII 路径别名：$ascii\ → $OutDir"
    & subst $ascii $OutDir
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path "$ascii\")) { throw "subst $ascii $OutDir 失败" }
    $asciiPkg = "$ascii\$PkgName"
    $asciiPgData = "$ascii\_build\pgdata"
    try {
        if (Test-Path "$ascii\_build\pgdata") { Remove-Item "$ascii\_build\pgdata" -Recurse -Force }
        Assert-Path "$asciiPkg\runtime\pgsql\bin\initdb.exe" '便携版 initdb.exe'

        # 3b. initdb：UTF8 + C locale（Docker 镜像的默认也是 C 系），scram 口令认证
        $pwFile = Join-Path $BuildDir 'pwfile.txt'
        Set-Content -Path $pwFile -Value 'datamind_dev' -NoNewline -Encoding ASCII
        Invoke-Native "$asciiPkg\runtime\pgsql\bin\initdb.exe" `
            @('-D', $asciiPgData, '-U', 'postgres', '-E', 'UTF8', '--locale=C',
              '-A', 'scram-sha-256', "--pwfile=$pwFile") 'initdb 初始化数据目录'

        # 3c. 端口与监听地址写进 postgresql.conf：数据目录自描述，老师那边不必额外传参
        $conf = Join-Path $asciiPgData 'postgresql.conf'
        $append = @(
            '',
            '# ---- DataMind Agent 便携包（由 scripts/build_delivery.ps1 写入） ----',
            "# 端口刻意避开 5432，避免与老师机器上已装的 PostgreSQL 冲突。",
            "port = $PgPort",
            "listen_addresses = '127.0.0.1'",
            "timezone = 'Asia/Shanghai'",
            "log_timezone = 'Asia/Shanghai'",
            'logging_collector = off',
            'max_connections = 60',
            'shared_buffers = 256MB',
            'lc_messages = ''C'''
        )
        Add-Content -Path $conf -Value $append -Encoding UTF8

        $pgCtl = "$asciiPkg\runtime\pgsql\bin\pg_ctl.exe"
        $psql = "$asciiPkg\runtime\pgsql\bin\psql.exe"
        $pgLog = Join-Path $LogDir 'build_postgres.log'
        $null = Start-PackagePostgres -DataDir $asciiPgData -Port $PgPort -PgCtl $pgCtl -LogFile $pgLog -What '启动便携版 PostgreSQL'
        Write-Ok "PostgreSQL 已在 $PgPort 就绪"

        try {
            $env:PGPASSWORD = 'datamind_dev'
            $env:PGCLIENTENCODING = 'UTF8'
            Invoke-Native $psql @('-h', '127.0.0.1', '-p', "$PgPort", '-U', 'postgres',
                '-c', 'CREATE DATABASE datamind_meta') '创建元数据库 datamind_meta'
            foreach ($sql in @('01_roles.sql', '02_metadata.sql', '03_demo_retail.sql', '04_industrial_schema.sql')) {
                $path = Join-Path $AppDir "deploy\init\$sql"
                Assert-Path $path "初始化 SQL $sql"
                Invoke-Native $psql @('-h', '127.0.0.1', '-p', "$PgPort", '-U', 'postgres',
                    '-d', 'datamind_meta', '-q', '-v', 'ON_ERROR_STOP=1', '-f', $path) "执行 deploy/init/$sql"
            }
            # Chinook 演示库（与 scripts/start_local.ps1 的步骤等价）
            Invoke-Native $psql @('-h', '127.0.0.1', '-p', "$PgPort", '-U', 'postgres', '-q',
                '-f', (Join-Path $AppDir 'db\chinook\Chinook_PostgreSql.sql')) '装载 Chinook 演示库'
            Invoke-Native $psql @('-h', '127.0.0.1', '-p', "$PgPort", '-U', 'postgres', '-d', 'chinook', '-q', '-c',
                'GRANT CONNECT ON DATABASE chinook TO readonly_user, datamind_app; GRANT USAGE ON SCHEMA public TO readonly_user, datamind_app; GRANT SELECT ON ALL TABLES IN SCHEMA public TO readonly_user, datamind_app;') 'Chinook 授权'
            Invoke-Native $psql @('-h', '127.0.0.1', '-p', "$PgPort", '-U', 'postgres', '-d', 'datamind_meta', '-q', '-c',
                'CREATE INDEX IF NOT EXISTS runs_recent_idx ON runs(started_at DESC, run_id DESC);') '补 runs 索引'

            # 3d. 应用迁移 + 种子（走应用自己的脚本，保证与仓库一致）
            $env:DATAMIND_META_WRITE_DSN = $appDsn
            $env:DATAMIND_WRITE_DSN = $appDsn
            $env:DATAMIND_META_DSN = $roDsn
            Invoke-AppPython 'db/migrate.py' @() '执行 db/migrations 全部迁移'
            Invoke-AppPython 'db/seed_demo_retail.py' @() '生成零售演示数据'
            Invoke-AppPython 'db/build_catalog.py' @() '构建语义 Catalog'

            # 3e. 向量库（摄取需要它，所以先起 Qdrant）
            # 两个硬要求，都是踩过坑换来的：
            #   1) 启动前确认端口是空的：构建期残留的 qdrant 会让「就绪探测」立刻通过，
            #      于是一路跑下去，直到最后才以「数据写到旧实例里」的形式暴露。
            #   2) 启动用 Start-Detached（UseShellExecute=$true），绝不持有子进程句柄；
            #      输出让 cmd 自己重定向到文件。Start-Process -RedirectStandardOutput
            #      会让父脚本等一个永远不来的流 EOF —— 那就是 18 分钟无声挂起的根因。
            if (-not (Wait-PortFree $QdrantHttp 15 "构建期 Qdrant 端口 $QdrantHttp")) {
                throw "端口 $QdrantHttp 被占用（可能是上次构建残留或开发用的 Qdrant）。请先结束占用进程再重新运行本脚本。"
            }
            $env:QDRANT__STORAGE__STORAGE_PATH = $QdrantStore
            $env:QDRANT__SERVICE__HTTP_PORT = "$QdrantHttp"
            $env:QDRANT__SERVICE__GRPC_PORT = "$QdrantGrpc"
            $env:QDRANT__TELEMETRY_DISABLED = 'true'
            $qdrantOut = Join-Path $LogDir 'build_qdrant.out.log'
            $qdrantErr = Join-Path $LogDir 'build_qdrant.err.log'
            Write-Step "启动便携版 Qdrant（detached，端口 $QdrantHttp）"
            $qdrantProc = Start-Detached (Join-Path $QdrantHome 'qdrant.exe') @('--disable-telemetry') $qdrantOut $qdrantErr
            $ready = Wait-WithDeadline -TimeoutSeconds 60 -What '等待 Qdrant 就绪' -Probe {
                try {
                    $r = Invoke-WebRequest "http://127.0.0.1:$QdrantHttp/readyz" -UseBasicParsing -TimeoutSec 2
                    return ($r.StatusCode -eq 200)
                } catch { return $false }
            }
            if (-not $ready) {
                Stop-PackageQdrant
                throw "Qdrant 未能在 60 秒内就绪，见 $qdrantErr"
            }
            Write-Ok 'Qdrant 已就绪'

            Invoke-AppPython 'db/seed_eval_cases.py' @() '登记评测集' 900
            Invoke-AppPython 'db/seed_industrial.py' @() '装载工业演示数据并解析工艺 PDF' ($StepTimeoutMinutes * 60)
            Invoke-AppPython 'db/build_industrial_catalog.py' @() '构建工业 Catalog' 1800
            $env:DM_BUILD_REQUIRE_QDRANT = '1'
            try {
                Invoke-AppPython 'ingestion/ingest.py' @() '摄取知识库文档（OCR + 向量化）' ($StepTimeoutMinutes * 60)
            } finally {
                Remove-Item Env:DM_BUILD_REQUIRE_QDRANT -ErrorAction SilentlyContinue
            }

            Write-Step '关闭构建期 PostgreSQL（数据在下次启动时才可搬运，先等它干净退出）'
        } finally {
            Write-Step "pg_ctl -m fast -w stop（数据目录 $asciiPgData）"
            Invoke-NativeChecked -Exe $pgCtl -Arguments @('-D', $asciiPgData, '-m', 'fast', '-w', 'stop') `
                -What '关闭构建期 PostgreSQL' -TimeoutSeconds 180
        }

        # 3e-2. 先把数据库目录搬到位，再收拾 Qdrant。
        # 为什么把搬运提前到关 Qdrant 之前：qdrant 是「随时可以再起的缓存」，pgdata 是
        # 「跑了几十分钟才建出来的成果」。先保住成果，后面的步骤即使失败也不至于白跑。
        Write-Step "搬运数据目录到 $PgDataFinal"
        Write-Info "搬运数据目录到 $PgDataFinal"
        if (-not (Test-Path $DataDir)) { New-Item -ItemType Directory -Path $DataDir -Force | Out-Null }
        if (Test-Path $PgDataFinal) { Remove-Item $PgDataFinal -Recurse -Force }
        Move-Item "$ascii\_build\pgdata" $PgDataFinal
        Assert-Path (Join-Path $PgDataFinal 'PG_VERSION') '搬运后的 PG_VERSION'
        Write-Ok 'PostgreSQL 预置数据已就位'

        # 3e-3. 关掉构建期的 Qdrant：按可执行文件路径精确匹配，不碰别人的 Qdrant。
        Write-Step '关闭构建期 Qdrant'
        Write-Info '停止构建期的 Qdrant ...'
        Stop-PackageQdrant | Out-Null
        if (-not (Wait-PortFree $QdrantHttp 30 "Qdrant 停止后释放端口 $QdrantHttp")) {
            Write-Warn2 "Qdrant 停止后端口 $QdrantHttp 仍被占用，稍后如果打包失败请手工检查"
        }
        $script:QdrantStopped = $true

        Start-Sleep -Seconds 1
        foreach ($p in (Get-CimInstance Win32_Process -Filter "Name='postgres.exe'" -ErrorAction SilentlyContinue)) {
            if ($p.ExecutablePath -and $p.ExecutablePath.StartsWith(($PkgDir + '\'), 'OrdinalIgnoreCase')) {
                Stop-ProcessTree ([int]$p.ProcessId)
            }
        }
    } finally {
        if (-not $script:QdrantStopped) { Stop-PackageQdrant | Out-Null }
        if ($ascii) {
            Write-Step "撤销 subst $ascii"
            Invoke-NativeChecked -Exe 'subst' -Arguments @("$ascii", '/D') -What "撤销 ASCII 路径别名 $ascii" -TimeoutSeconds 60
        }
        Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    }

    # 3g. 自检：预置数据到底有没有
    # 这一步踩过两个坑，都写在这里免得再犯：
    #   * pg_ctl 的输出**不能接管道**（postgres 会继承句柄，管道永不结束，实测直接把
    #     调用方挂住 3 分钟以上）—— 一律重定向到文件，再用「真实进程退出码」判断；
    #   * 不要拿 `pg_ctl -w start` 的退出当就绪信号，也不要假定 psql 一定有输出：
    #     用 pg_isready 轮询到真的能连，并且把空结果当成一条明确的中文错误。
    $env:PGPASSWORD = 'datamind_dev'
    $psql = Join-Path $PgHome 'bin\psql.exe'
    Write-Info '重新拉起预置数据库做自检统计 ...'
    $verifyCode = Start-PackagePostgres -DataDir $PgDataFinal -Port $PgPort -PgCtl (Join-Path $PgHome 'bin\pg_ctl.exe') `
        -LogFile (Join-Path $LogDir 'build_postgres_verify.log') -What '自检用 pg_ctl start'
    Write-BuildLog "自检用 pg_ctl start 退出码 $verifyCode" 'STEP'
    try {
        # 自检只断言「构建流程真的产出、且必须非空」的东西。
        # 曾经这里还卡 evidence_nodes >= 10，那是错的：evidence_nodes 由
        # datamind/agent.py:220 与 datamind/evidence_graph.py:514 在**用户提问时**写入，
        # 构建期一条都不该有 —— 拿它当门槛只会让刚建好的库自检必挂。
        # 知识库的「真的摄取进去了」由 document_assets + chunks 证明（两者都由
        # ingestion/ingest.py 写），chinook 由构建期 psql 装载，两者都受构建控制。
        $countSql = 'SELECT (SELECT count(*) FROM document_assets), (SELECT count(*) FROM chunks), ' +
                    '(SELECT count(*) FROM evaluation_cases)'
        $counts = (& $psql -h 127.0.0.1 -p $PgPort -U postgres -d datamind_meta -tAc $countSql 2>&1 | Out-String).Trim()
        Write-Info "document_assets / chunks / evaluation_cases = $counts"
        if (-not $counts) { throw "自检查询没有返回任何结果（psql 退出码 $LASTEXITCODE）：$countSql" }
        $parts = @($counts -split '\|')
        if ($parts.Count -lt 3) { throw "自检结果格式不符合预期（应为 a|b|c）：$counts" }
        if ([int]$parts[0] -lt 10) { throw "知识库文档太少（document_assets=$($parts[0])，应 >= 10）：$counts" }
        if ([int]$parts[1] -lt 50) { throw "知识库分块太少（chunks=$($parts[1])，应 >= 50）：$counts" }
        if ([int]$parts[2] -lt 10) { throw "评测集没登记（evaluation_cases=$($parts[2])，应 >= 10）：$counts" }
        $qd = (& $psql -h 127.0.0.1 -p $PgPort -U postgres -d demo_retail -tAc 'SELECT count(*) FROM orders' 2>&1 | Out-String).Trim()
        Write-Info "demo_retail.orders = $qd"
        if (-not $qd) { throw '零售演示库自检查询没有返回结果' }
        if ([int]$qd -lt 100) { throw "零售演示库没有数据：orders=$qd" }
        $ch = (& $psql -h 127.0.0.1 -p $PgPort -U postgres -d chinook -tAc 'SELECT count(*) FROM "Track"' 2>&1 | Out-String).Trim()
        Write-Info "chinook.Track = $ch"
        if (-not $ch) { throw 'Chinook 演示库自检查询没有返回结果' }
        if ([int]$ch -lt 100) { throw "Chinook 演示库没有数据：tracks=$ch" }
    } finally {
        $verifyStop = Start-Process -FilePath (Join-Path $PgHome 'bin\pg_ctl.exe') `
            -ArgumentList @('-D', (Quote-Arg $PgDataFinal), '-m', 'fast', '-w', 'stop') -NoNewWindow -PassThru `
            -RedirectStandardOutput (Join-Path $LogDir 'pg_ctl_verify_stop.out.log') `
            -RedirectStandardError (Join-Path $LogDir 'pg_ctl_verify_stop.err.log')
        $null = Wait-ProgramExit -Process $verifyStop -TimeoutSeconds 180 -What '自检用 pg_ctl stop' `
            -KillExe ((Join-Path $PgHome 'bin') + '\*')
        Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    }
    Write-Ok '预置数据自检通过'
}

if (-not (Test-Path $QdrantStore)) { New-Item -ItemType Directory -Path $QdrantStore -Force | Out-Null }

# ── 4. 包内文件（启动脚本、说明、日志目录） ─────────────────────────────────
Write-Phase '写入包内启动脚本与说明'
foreach ($file in @('run.bat', 'stop.bat', 'README.txt', '启动说明.txt',
                    'note_running.txt', 'note_broken.txt')) {
    Copy-Item (Join-Path $TemplateDir $file) (Join-Path $PkgDir $file) -Force
}
Copy-Tree -Source (Join-Path $TemplateDir 'tools') -Destination (Join-Path $PkgDir 'tools')
Write-BuildLog ("包内文件阶段变量：TemplateDir=[{0}] PkgDir=[{1}] DataDir=[{2}] SystemRoot=[{3}]" -f $TemplateDir, $PkgDir, $DataDir, $env:SystemRoot) 'STEP'
# 下面两条模板目录：路径先落到局部变量再传给 Copy-Tree，不在实参位置直接写
# `(Join-Path $TemplateDir 'data_files')`。原因见 docs/DELIVERY.md「已知坑」：
# 在这台机器的 Windows PowerShell 5.1 上，实参位置的 Join-Path 求值偶发失败，
# 报出的却是一句与现场毫无关系的
# 「Cannot process command because of one or more missing mandatory parameters: ChildPath」
# （实测变量已确认非空、类型是 System.String，Copy-Tree 函数体第一行却从未执行到）。
# 先赋值再传参绕开了这个求值时机问题，出错日志也能直接看到真实路径。
$dataFilesSrc = Join-Path -Path $TemplateDir -ChildPath 'data_files'
$dataFilesDst = $DataDir
Write-BuildLog "复制模板 data_files：源=[$dataFilesSrc] 目标=[$dataFilesDst]" 'STEP'
Copy-Tree -Source $dataFilesSrc -Destination $dataFilesDst
$pkgLogDir = Join-Path $DataDir 'logs'
if (-not (Test-Path $pkgLogDir)) { New-Item -ItemType Directory -Path $pkgLogDir -Force | Out-Null }
$appCacheDir = Join-Path $DataDir '.cache'
if (-not (Test-Path $appCacheDir)) { New-Item -ItemType Directory -Path $appCacheDir -Force | Out-Null }

if ($EmbedModelKeyFile) {
    Assert-Path $EmbedModelKeyFile '指定的模型密钥文件'
    Copy-Item $EmbedModelKeyFile (Join-Path $DataDir 'model_config.env') -Force
    Write-Warn2 "已按 -EmbedModelKeyFile 把密钥写入 data\model_config.env：这个包从此带着真实密钥，请勿转发"
} else {
    Write-Info '未写入任何模型密钥（默认行为）。老师可启动后在「模型接入」页面填写。'
}

# 文本文件编码：.bat 需要**纯 ASCII 且无 BOM**（配合 chcp 65001），.ps1/.txt 用 UTF-8 带 BOM
# （Windows PowerShell 5.1 与记事本都靠 BOM 认 UTF-8，否则中文会变乱码）。
# ⚠ .bat 为什么不能只是「UTF-8 无 BOM」而是「纯 ASCII」：见下面那条断言与
#   docs/DELIVERY.md 第 12 节 —— 含多字节字符的批处理会被 cmd.exe 错位执行。
function Set-TextEncoding([string]$Path, [bool]$WithBom) {
    $text = [System.IO.File]::ReadAllText($Path, [System.Text.UTF8Encoding]::new($false))
    $text = ($text -replace "`r`n", "`n") -replace "`n", "`r`n"
    [System.IO.File]::WriteAllText($Path, $text, [System.Text.UTF8Encoding]::new($WithBom))
}
foreach ($file in @('run.bat', 'stop.bat')) {
    Set-TextEncoding (Join-Path $PkgDir $file) $false
}
# ── 两个 .bat 的语法断言（都是实测踩出来的，详见 docs/DELIVERY.md 第 12 节）──────
#  (1) run.bat 必须**纯 ASCII**：它用 `for /f` 调 PowerShell，而 cmd.exe 把
#      「UTF-8 无 BOM + chcp 65001 + 多字节字符」的批处理按单字节读，遇到多字节字符会
#      从行中间开始解析，把不相干的行名当成命令执行（实测报
#      `'Web' is not recognized as an internal or external command`，3/3 复现），
#      严重时连 :broken 分支都会被走穿。中文提示因此由 note_*.txt 承担（用 type 显示）。
#  (2) 两个文件都不许有 UTF-8 BOM（cmd 会把 BOM 当命令）。
#  (3) 两个文件都不许在 `if ... (` / `for ... (` 块内的 echo 里写**未转义**的括号：
#      未转义的 ) 会提前结束该块，后面的行被当成块外命令执行，报
#      `. was unexpected at this time.`，整个分支被静默跳过 —— 现场就是「双击后卡在
#      [1/6] 与 [2/6] 之间、什么都不说」。必须写成 ^( 和 ^)。
function Assert-BatchSyntax([string]$Bat, [bool]$RequirePureAscii) {
    $batPath = Join-Path $PkgDir $Bat
    $batBytes = [System.IO.File]::ReadAllBytes($batPath)
    if ($batBytes.Length -gt 0 -and $batBytes[0] -eq 0xEF -and $batBytes[1] -eq 0xBB -and $batBytes[2] -eq 0xBF) {
        throw "$Bat 不能被写成 UTF-8 带 BOM（cmd.exe 会把 BOM 当命令）；当前文件带 BOM。"
    }
    $highBytes = @($batBytes | Where-Object { $_ -ge 0x80 })
    if ($RequirePureAscii -and $highBytes.Count -gt 0) {
        throw ("{0} 里出现了 {1} 个非 ASCII 字节（偏移 {2}...）。它要用 for /f 调 PowerShell，必须保持纯 ASCII：中文提示请写进 note_*.txt 再用 type 显示。" -f `
            $Bat, $highBytes.Count, ($highBytes[0..([math]::Min(5, $highBytes.Count - 1))] -join ','))
    }
    $batText = [System.IO.File]::ReadAllText($batPath, [System.Text.UTF8Encoding]::new($false))
    $depth = 0
    $lineNo = 0
    foreach ($line in @($batText -split "`r?`n")) {
        $lineNo++
        $opens = ([regex]::Matches($line, '(?<!\^)\(')).Count
        $closes = ([regex]::Matches($line, '(?<!\^)\)')).Count
        if ($depth -gt 0 -and $line -match '^\s*echo') {
            # 既认半角 () 也认全角（）：原版 run.bat 第 64 行写的是
            # `echo [2/6] 错误：端口 %PGPORT% 已被占用（可能是上一次没退干净，或另有程序在用）。`
            # —— 全角 ）同样会提前结束 if 块（本包那次「双击后什么都不发生」就是它）。
            $bare = $line -replace '\^\(', '' -replace '\^\)', ''
            if ($bare -match '[(（)）]') {
                throw ("{0}:{1} 在 if/for 块内的 echo 里有未转义的括号：「{2}」。半角请写成 ^( 和 ^)；全角括号不要出现在块内的 echo 里（挪到 note_*.txt 用 type 显示）。否则 cmd 会提前结束该块，报「was unexpected at this time.」并静默跳过整个分支。" -f `
                    $Bat, $lineNo, $line.Trim())
            }
        }
        $depth += ($opens - $closes)
        if ($depth -lt 0) { $depth = 0 }
    }
    $note = if ($RequirePureAscii) { '纯 ASCII' } else { "$($highBytes.Count) 个非 ASCII 字节（中文提示，不含 for /f，已实测安全）" }
    Write-BuildLog "$Bat 语法检查通过：无 BOM、块内 echo 括号已转义、$note（$($batBytes.Length) 字节）" 'STEP'
}
Assert-BatchSyntax 'run.bat' $true
Assert-BatchSyntax 'stop.bat' $false
foreach ($file in @('README.txt', '启动说明.txt', (Join-Path 'tools' 'dmstack.ps1'))) {
    Set-TextEncoding (Join-Path $PkgDir $file) $true
}
# note_*.txt 是 run.bat 用 `type` 直接打到控制台的（控制台是 chcp 65001），
# **必须无 BOM**：带 BOM 时那一行开头会多出一个不可见的 U+FEFF，老师看到的就是
# 「﻿提示（中文）：」这种多一个怪字的样子。它们不经过 PowerShell 读取，
# 所以不需要 BOM 来防乱码。
foreach ($file in @('note_running.txt', 'note_broken.txt')) {
    Set-TextEncoding (Join-Path $PkgDir $file) $false
    $nb = [System.IO.File]::ReadAllBytes((Join-Path $PkgDir $file))
    if ($nb.Length -gt 2 -and $nb[0] -eq 0xEF -and $nb[1] -eq 0xBB -and $nb[2] -eq 0xBF) {
        throw "$file 不该有 UTF-8 BOM（会用 type 显示，BOM 会变成一个多余字符）。"
    }
    Write-BuildLog "$file 已确认 UTF-8 无 BOM（$($nb.Length) 字节）" 'STEP'
}

# 包信息（不参与运行，只用于支持与追溯）
$buildInfo = @()
$buildInfo += "DataMind Agent 便携交付包"
$buildInfo += "构建时间   : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')"
$buildInfo += "构建机器   : $env:COMPUTERNAME"
$buildInfo += "源码仓库   : $RepoRoot"
$gitHead = (& git -C $RepoRoot rev-parse --short HEAD 2>$null)
if ($LASTEXITCODE -eq 0 -and $gitHead) { $buildInfo += "源码提交   : $gitHead" }
$buildInfo += "Python     : $PythonVersion（嵌入式）"
$buildInfo += "PostgreSQL : 16.4（便携）  端口 $PgPort"
$buildInfo += "Qdrant     : 1.12.4（便携） 端口 $QdrantHttp"
$buildInfo += "Web service: port $ApiPort"
$buildInfo += "前端产物   : web\vue（来自 frontend 的 npm run deploy）"
$buildInfo += ""
$buildInfo += "包内各部分大小（构建结束时）："
foreach ($part in @('runtime\pgsql', 'runtime\qdrant', 'runtime\python', 'runtime\pylibs',
                    'app', 'data\pgdata', 'data\qdrant_storage')) {
    $buildInfo += ("  {0,-22} {1,10}" -f $part, (Format-Size (Get-FolderSize (Join-Path $PkgDir $part))))
}
$buildInfo += ("  {0,-22} {1,10}" -f '合计', (Format-Size (Get-FolderSize $PkgDir)))
Set-Content -Path (Join-Path $PkgDir 'BUILD_INFO.txt') -Value ($buildInfo -join "`r`n") -Encoding UTF8
Set-TextEncoding (Join-Path $PkgDir 'BUILD_INFO.txt') $true

# ── 5. zip ─────────────────────────────────────────────────────────────────
$zipPath = Join-Path $OutDir "$PkgName.zip"
if (-not $NoZip) {
    Write-Phase '打包 zip'
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
    Write-Info "压缩 $PkgDir → $zipPath（这一步最慢，通常 1-3 分钟）..."
    [System.IO.Compression.ZipFile]::CreateFromDirectory(
        $PkgDir, $zipPath, [System.IO.Compression.CompressionLevel]::Optimal, $true)
    Write-Ok ("zip 已生成：{0}" -f (Format-Size (Get-Item $zipPath).Length))
}

# ── 6. 汇总 ────────────────────────────────────────────────────────────────
$stamps['last_build'] = (Get-Date).ToUniversalTime().ToString('o')
Save-Stamps $stamps

Write-Phase '结果汇总'
$rows = @()
foreach ($part in @('runtime\pgsql', 'runtime\qdrant', 'runtime\python', 'runtime\pylibs',
                    'app', 'data\pgdata', 'data\qdrant_storage')) {
    $rows += [pscustomobject]@{ 部分 = $part; 大小 = (Format-Size (Get-FolderSize (Join-Path $PkgDir $part))) }
}
$rows | Format-Table -AutoSize | Out-String -Width 200 | Write-Host
$total = Get-FolderSize $PkgDir
Write-Host ("交付包总大小：{0}   目录：{1}" -f (Format-Size $total), $PkgDir) -ForegroundColor Cyan
if (-not $NoZip) { Write-Host ("zip        ：{0}   {1}" -f (Format-Size (Get-Item $zipPath).Length), $zipPath) -ForegroundColor Cyan }
Write-Host ("本次耗时    ：{0:N1} 分钟" -f ((Get-Date) - $script:StartedAt).TotalMinutes) -ForegroundColor Cyan
Write-Host ''
Write-Host '下一步：把 zip 解压到一个干净目录，双击 run.bat 验证（或者直接双击目录里的 run.bat）。' -ForegroundColor White

}
catch {
    $script:ExitCode = 1
    $where = ''
    if ($_.InvocationInfo) { $where = "$($_.InvocationInfo.ScriptName):$($_.InvocationInfo.ScriptLineNumber)" }
    Write-BuildLog "构建失败：$($_.Exception.Message)（$where）" 'FAIL'
    Write-Host ''
    Write-Host '============================================================' -ForegroundColor Red
    Write-Host ' 构建失败（脚本没有挂死，是明确报错退出）' -ForegroundColor Red
    Write-Host '============================================================' -ForegroundColor Red
    Write-Host " 位置 : $where"
    Write-Host " 错误 : $($_.Exception.Message)"
    Write-Host " 分步日志（含时间戳，可精确定位到最后一步）: $BuildLog"
    Write-Host ' 结束语：本次运行已经把所有子进程收干净；修掉上面这条错误后重跑即可。'
    Write-Host '============================================================' -ForegroundColor Red
}
finally {
    Stop-BuildWatchdog $script:Watchdog
    if (-not $script:QdrantStopped) { Stop-PackageQdrant | Out-Null }
    Write-BuildLog ("构建结束：退出码 $($script:ExitCode)，总耗时 {0:N1} 分钟" -f ((Get-Date) - $script:StartedAt).TotalMinutes) ($(if ($script:ExitCode -eq 0) { 'DONE' } else { 'FAIL' }))
}
exit $script:ExitCode
