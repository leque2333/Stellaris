@echo off
chcp 65001 >nul
title Stellaris · 记忆与人格中枢控制台
cd /d %~dp0
echo ------------------------------------------------------------
echo 正在启动 Stellaris 记忆与人格中枢控制台...
echo 控制台地址: http://localhost:18895
echo 局域网访问: http://127.0.0.1:18895
echo ------------------------------------------------------------
py server.py
pause
