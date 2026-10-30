@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0.."

set PY=python
where python >nul 2>nul
if errorlevel 1 (
  set PY=C:\Users\user\AppData\Local\Python\bin\python.exe
)

echo ============================================================
echo  助手中枢 x AstrBot 一键连接
echo ============================================================
echo.
echo  将要：自动找到 AstrBot 的 data 目录，把中枢写成它的默认模型提供商。
echo  会先备份 cmd_config.json；重复运行只更新不重复添加。
echo.

set /p ANSWER=继续？[Y/n]
if /i "%ANSWER%"=="n" goto :cancel
if /i "%ANSWER%"=="no" goto :cancel

"%PY%" scripts\connect_astrbot.py %*
set RC=%ERRORLEVEL%

if %RC% neq 0 (
  echo.
  echo 连接未完成，请按上面的提示处理（常见原因：中枢没启动 / AstrBot 路径不对）。
  pause
  exit /b %RC%
)

echo.
echo 连接完成。重启 AstrBot 后生效：
echo   docker restart astrbot
echo.
echo 想连 P4 薄壳工具的话，用：
echo   python scripts\connect_astrbot.py --p4 --restart
echo.
pause
exit /b 0

:cancel
echo 已取消。
pause
exit /b 0
