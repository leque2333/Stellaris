# uninstall_autostart.ps1
$taskName = "AntigravityBridge"
$startupShortcut = [System.IO.Path]::Combine($env:APPDATA, "Microsoft\Windows\Start Menu\Programs\Startup\AntigravityBridge.lnk")

Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
if (Test-Path $startupShortcut) {
    Remove-Item $startupShortcut -Force -ErrorAction SilentlyContinue
}
Write-Host "已成功移除 Antigravity 宿主机桥梁自启动任务。" -ForegroundColor Green
