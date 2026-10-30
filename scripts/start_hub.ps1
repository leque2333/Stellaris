<#
启动中枢：日志轮转（重启不丢历史）。

每轮启动前把上一份日志改名成 *.prev，再重定向到主日志。
控制台的「运行日志」页会同时读主日志和 .prev。

用法：
    powershell -ExecutionPolicy Bypass -File scripts\start_hub.ps1
    powershell -ExecutionPolicy Bypass -File scripts\start_hub.ps1 -Root "D:\机器人_完整版" -Port 18895
#>

param(
    [string]$Root = "D:\机器人_完整版",
    [int]$Port = 18895,
    [string]$Python = "C:\Users\user\AppData\Local\Python\bin\python.exe"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $Root)) { Write-Error "目录不存在：$Root" }
if (-not (Test-Path $Python)) { Write-Error "Python 不存在：$Python" }

$logDir = Join-Path $Root "logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }

$outFile = Join-Path $logDir "hub-$Port.out.log"
$errFile = Join-Path $logDir "hub-$Port.err.log"

$existing = Get-CimInstance Win32_Process -Filter "Name like '%python%'" |
    Where-Object { $_.CommandLine -like "*server.py $Port*" } |
    Select-Object -First 1
if ($existing) {
    Stop-Process -Id $existing.ProcessId -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}

# 轮转：保留上一轮，避免重启清空
if (Test-Path $outFile) { Move-Item -LiteralPath $outFile -Destination "$outFile.prev" -Force }
if (Test-Path $errFile) { Move-Item -LiteralPath $errFile -Destination "$errFile.prev" -Force }

Start-Process -FilePath $Python `
    -ArgumentList "console\server.py", $Port `
    -WorkingDirectory $Root `
    -WindowStyle Hidden `
    -RedirectStandardOutput $outFile `
    -RedirectStandardError $errFile

Start-Sleep -Seconds 6
$listening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listening) {
    Write-Host "中枢已启动: http://localhost:$Port  (日志: $outFile, 上一轮: $outFile.prev)"
} else {
    Write-Error "启动失败，看日志：$outFile"
}