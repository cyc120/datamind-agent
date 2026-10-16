@echo off
chcp 65001 >nul
setlocal EnableExtensions
title DataMind Agent local demo (portable: no Docker / no Python / no DB install)
cd /d "%~dp0"

rem ===========================================================================
rem  IMPORTANT: keep THIS FILE pure ASCII.
rem
rem  A cmd.exe batch file saved as UTF-8 WITHOUT a BOM is parsed unreliably on a
rem  Chinese Windows: with `chcp 65001` plus multi-byte characters in the file,
rem  the parser jumps to wrong file offsets and executes byte fragments of
rem  unrelated lines, e.g.  'Web' is not recognized as an internal or external
rem  command.  Then the whole control flow (including :broken) goes wrong.
rem  Reproduced 3/3 on the previous package: a banner line reading "echo    Web <CN>"
rem  produced exactly that error and the script fell through into :broken.
rem  Chinese guidance for the teacher therefore lives in separate .txt files
rem  and is shown with `type` - never as a batch command line.
rem  See docs/DELIVERY.md section 12.  Do not "restore" Chinese into this file.
rem ===========================================================================

set "PKG=%~dp0"
if "%PKG:~-1%"=="\" set "PKG=%PKG:~0,-1%"
set "PS=powershell -NoProfile -ExecutionPolicy Bypass -File"
set "STACK=%PKG%\tools\dmstack.ps1"
set "LOGS=%PKG%\data\logs"
set "APILOG=%LOGS%\api_console.log"
set "PGPORT=55432"
set "QPORT=56333"
set "APIPORT=8000"

echo ============================================================
echo    DataMind Agent -- local demo (portable package)
echo ============================================================
echo    Package dir  : %PKG%
echo    PostgreSQL   : portable 16.4, port %PGPORT%   (avoids 5432, no clash)
echo    Qdrant       : portable 1.12.4, port %QPORT%   (avoids 6333)
echo    Web service  : port %APIPORT%
echo ------------------------------------------------------------
echo    No Docker / Python / database needed: every runtime is in this folder.
echo ------------------------------------------------------------
echo.

rem -- Step 0: package integrity ---------------------------------------------
if not exist "%PKG%\runtime\python\python.exe"        goto :broken
if not exist "%PKG%\runtime\pylibs\uvicorn"           goto :broken
if not exist "%PKG%\runtime\pgsql\bin\pg_ctl.exe"     goto :broken
if not exist "%PKG%\runtime\qdrant\qdrant.exe"        goto :broken
if not exist "%PKG%\data\pgdata\PG_VERSION"           goto :broken
if not exist "%PKG%\app\datamind\api.py"              goto :broken
if not exist "%PKG%\app\web\vue\index.html"           goto :broken
echo [0/6] Package content check passed.

rem -- Step 1: port 8000 ------------------------------------------------------
set "APISTATE="
for /f "delims=" %%A in ('%PS% "%STACK%" -Action check-api -Port %APIPORT% -Quiet 2^>nul') do set "APISTATE=%%A"
if /i "%APISTATE%"=="OURAPP" (
    echo [1/6] The service is already running; opening the browser directly.
    echo         URL: http://127.0.0.1:%APIPORT%/
    start "" "http://127.0.0.1:%APIPORT%/"
    echo.
    echo         To stop the service, double-click stop.bat in this folder.
    call :zh "note_running.txt"
    pause
    exit /b 0
)
if /i "%APISTATE%"=="OTHER" (
    echo [1/6] ERROR: port %APIPORT% is already used by another program.
    echo         This demo needs port %APIPORT%. Close that program and try again.
    echo         To see the owner: netstat -ano ^| findstr :%APIPORT%
    echo.
    pause
    exit /b 1
)
echo [1/6] Port %APIPORT% is free.

rem -- Step 2: PostgreSQL -----------------------------------------------------
set "PORTBUSY="
for /f "delims=" %%A in ('%PS% "%STACK%" -Action check-port -Port %PGPORT% -Quiet 2^>nul') do set "PORTBUSY=%%A"
if /i "%PORTBUSY%"=="BUSY" (
    echo [2/6] ERROR: port %PGPORT% is already in use
    echo         ^(leftover from a previous run, or another program^).
    echo         Double-click stop.bat first, or reboot, then try again.
    echo.
    pause
    exit /b 1
)
echo [2/6] Starting PostgreSQL (data is preloaded; usually 3-10 seconds)...
%PS% "%STACK%" -Action start-postgres -Quiet >nul 2>&1
set "PGREADY="
for /f "delims=" %%A in ('%PS% "%STACK%" -Action wait-postgres -Seconds 90 -Quiet 2^>nul') do set "PGREADY=%%A"
if /i not "%PGREADY%"=="READY" (
    echo         ERROR: PostgreSQL did not become ready within 90 seconds.
    echo         Log: %LOGS%\postgres.log
    echo.
    pause
    exit /b 1
)
echo        PostgreSQL ready.

rem -- Step 3: Qdrant ---------------------------------------------------------
set "PORTBUSY="
for /f "delims=" %%A in ('%PS% "%STACK%" -Action check-port -Port %QPORT% -Quiet 2^>nul') do set "PORTBUSY=%%A"
if /i "%PORTBUSY%"=="BUSY" (
    echo [3/6] ERROR: port %QPORT% is already in use.
    echo         Double-click stop.bat first, or reboot, then try again.
    echo.
    pause
    exit /b 1
)
echo [3/6] Starting Qdrant vector store (vectors are preloaded; usually 2-5 seconds)...
%PS% "%STACK%" -Action start-qdrant -Quiet >nul 2>&1
set "QREADY="
for /f "delims=" %%A in ('%PS% "%STACK%" -Action wait-qdrant -Seconds 90 -Quiet 2^>nul') do set "QREADY=%%A"
if /i not "%QREADY%"=="READY" (
    echo         ERROR: Qdrant did not become ready within 90 seconds.
    echo         Log: %LOGS%\qdrant.err.log
    echo.
    pause
    exit /b 1
)
echo        Qdrant ready.

rem -- Step 4: Web service ----------------------------------------------------
rem  DO NOT wrap this call in `for /f` or a pipe.  `start-api` launches a
rem  long-running uvicorn; anything that waits for that pipe's EOF waits for the
rem  API to exit, i.e. forever - that was the "[4/6] hangs" defect.  The API's
rem  own stdout/stderr are redirected to files inside dmstack.ps1, and the PID is
rem  written to data\logs\api.pid.  run.bat never captures this call's output.
echo [4/6] Starting the Web service (keep this window open)...
echo        Service log file : %APILOG%
echo        A separate log window will also show these lines live.
%PS% "%STACK%" -Action start-api -Quiet

set "APIPID="
if exist "%LOGS%\api.pid" for /f "usebackq delims=" %%A in ("%LOGS%\api.pid") do set "APIPID=%%A"
if defined APIPID echo        Web service started (python PID %APIPID%).
if not defined APIPID echo        (note) PID file not found; continuing to wait for /health.

rem  Live log window: `start` gives it its own console, so nothing here is
rem  captured and nothing can block.  A new window may be blocked by policy
rem  (then this line just fails and the log file above is still there).
set "TAILTITLE=DataMind Agent service log"
start "%TAILTITLE%" /min %PS% "%STACK%" -Action start-api-tail -Quiet

rem -- Step 5: wait for /health ----------------------------------------------
echo.
echo [5/6] Waiting for the service (first start loads logs and vectors; about 10-40 seconds)...
set "HEALTHSTATE="
for /f "delims=" %%A in ('%PS% "%STACK%" -Action wait-api -Seconds 180 -Quiet 2^>nul') do set "HEALTHSTATE=%%A"
if /i "%HEALTHSTATE%"=="TIMEOUT" (
    echo         ERROR: the Web service did not become ready within 180 seconds.
    echo         Log: %APILOG%
    echo.
    pause
    exit /b 1
)
if /i "%HEALTHSTATE%"=="DEGRADED" (
    echo         WARNING: the database is ready but the vector store is not,
    echo         so document Q&A will be degraded.
    echo         Please check %LOGS%\qdrant.err.log
)
if /i "%HEALTHSTATE%"=="READY" echo         Service ready: database and vector store are both fine.

rem -- Step 6: open the browser ----------------------------------------------
echo [6/6] Opening the browser: http://127.0.0.1:%APIPORT%/
start "" "http://127.0.0.1:%APIPORT%/"

echo.
echo ============================================================
echo    You can start using it now.  Please read:
echo      1. This window IS the service window: do NOT close it
echo         (closing it stops the service).
echo      2. On the first start antivirus / firewall may prompt;
echo         choose "Allow" (it only listens on 127.0.0.1).
echo      3. To really call the LLM, fill in your API Key and model
echo         name under "Model Access" on the left of the page.
echo         Settings are stored in the preloaded database and are
echo         never written outside this folder.
echo      4. To stop: press any key in this window, or double-click
echo         stop.bat. The service log window can just be closed.
echo ============================================================
echo.
call :zh "note_running.txt"
echo Press any key to stop the service and exit (or just close this window)...
pause >nul

echo.
echo Stopping the service...
%PS% "%STACK%" -Action stop -Quiet
rem ??? 4 ?????????????????????????
taskkill /fi "WINDOWTITLE eq DataMind Agent service log*" /f >nul 2>&1
echo Stopped. Thank you for using DataMind Agent.
timeout /t 3 >nul
exit /b 0

rem -- show a Chinese note file if it is present (purely cosmetic) -------------
:zh
if exist "%~1" (
    echo ------------------------------------------------------------
    type "%~1"
    echo ------------------------------------------------------------
)
exit /b 0

:broken
echo.
echo [ERROR] The delivery package is incomplete:
echo         one of the runtime / app files checked above is missing.
echo         Common causes: antivirus deleted it during extraction, or only
echo         the outer folder was unzipped.
echo         Please extract the whole archive into one complete folder and
echo         double-click run.bat again.
echo.
call :zh "note_broken.txt"
pause
exit /b 1
