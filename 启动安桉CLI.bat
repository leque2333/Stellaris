@echo off
chcp 65001 >nul
title 助手 CLI 自由无审查交互终端
cd /d "%~dp0"

echo 正在连接助手底层智能体中枢...
"C:\Users\user\AppData\Local\Python\bin\python.exe" -m agent_core.cli

if errorlevel 1 (
    echo.
    echo 终端退出异常，按任意键关闭窗口...
    pause >nul
)
