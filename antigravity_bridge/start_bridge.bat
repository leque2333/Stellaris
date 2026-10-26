@echo off
chcp 65001 >nul
title Antigravity Host Bridge
cd /d "D:\机器人\antigravity_bridge"
echo [Antigravity Bridge] 正在启动宿主机反重力桥梁服务...
"C:\Users\user\AppData\Local\Python\pythoncore-3.14-64\python.exe" server.py
pause
