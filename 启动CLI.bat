@echo off
chcp 65001 >nul
title Stellaris CLI
cd /d "%~dp0"

echo 正在连接中枢...
set PY=py
where py >nul 2>nul
if errorlevel 1 set PY=python
"%PY%" -m agent_core.cli

if errorlevel 1 (
    echo.
    echo 终端退出异常，按任意键关闭窗口...
    pause >nul
)
