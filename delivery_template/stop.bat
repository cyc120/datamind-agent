@echo off
chcp 65001 >nul
setlocal EnableExtensions
title DataMind Agent —— 停止服务
cd /d "%~dp0"

set "PKG=%~dp0"
if "%PKG:~-1%"=="\" set "PKG=%PKG:~0,-1%"
set "PS=powershell -NoProfile -ExecutionPolicy Bypass -File"
set "STACK=%PKG%\tools\dmstack.ps1"

echo 正在停止 DataMind Agent 的便携服务（PostgreSQL / Qdrant / Web 服务 / 队列 worker）...
echo.
%PS% "%STACK%" -Action status
echo.
%PS% "%STACK%" -Action stop
echo.
echo 再确认一次状态：
%PS% "%STACK%" -Action status
echo.
echo 如果 PostgreSQL 显示「运行中」，说明它没能干净关闭；可以重启电脑，
echo 或打开任务管理器结束路径在 %PKG%\runtime\pgsql 下的 postgres.exe。
echo.
pause
exit /b 0
