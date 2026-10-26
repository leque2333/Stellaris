# install_autostart.ps1
$ErrorActionPreference = "Stop"

$taskName = "AntigravityBridge"
$pywPath = "C:\Users\user\AppData\Local\Microsoft\WindowsApps\pyw.exe"
if (-not (Test-Path $pywPath)) {
    $pywPath = (Get-Command pyw.exe).Source
}

$scriptPath = "D:\机器人\antigravity_bridge\server.py"
$workDir = "D:\机器人\antigravity_bridge"
$startupFolder = [System.IO.Path]::Combine($env:APPDATA, "Microsoft\Windows\Start Menu\Programs\Startup")
$shortcutPath = [System.IO.Path]::Combine($startupFolder, "AntigravityBridge.lnk")

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  正在配置 Antigravity 宿主机桥梁隐形自启" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

# 1. 注册 Windows 计划任务（登录即以最高管理员权限隐形启动，且崩溃自动重启）
Write-Host "`n[1/2] 配置 Windows 计划任务 ($taskName)..." -ForegroundColor Yellow
$action = New-ScheduledTaskAction -Execute $pywPath -Argument "`"$scriptPath`"" -WorkingDirectory $workDir
$trigger = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -Hidden -ExecutionTimeLimit (New-TimeSpan -Days 0)

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Write-Host "  ✅ 计划任务注册成功（RunLevel: Highest, 隐藏窗体）" -ForegroundColor Green

# 2. 在 Startup 目录创建快捷方式作为双重兜底保障
Write-Host "`n[2/2] 创建 Startup 启动快捷方式..." -ForegroundColor Yellow
$wsh = New-Object -ComObject WScript.Shell
$shortcut = $wsh.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pywPath
$shortcut.Arguments = "`"$scriptPath`""
$shortcut.WorkingDirectory = $workDir
$shortcut.WindowStyle = 7 # 7 = 最小化/隐形启动，不弹出窗口
$shortcut.Description = "Antigravity Host Bridge for Anan (Invisible Background)"
$shortcut.Save()
Write-Host "  ✅ 快捷方式已写入 Startup 目录: $shortcutPath" -ForegroundColor Green

Write-Host "`n==========================================" -ForegroundColor Cyan
Write-Host "  配置完成！服务已具备开机/登录隐形自动启动" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Cyan
