<#
启动中枢：日志轮转（重启不丢历史）。

每轮启动前把上一份日志改名成 *.prev，再重定向到主日志。
控制台的「运行日志」页会同时读主日志和 .prev。

用法：
    powershell -ExecutionPolicy Bypass -File scripts\start_hub.ps1
    powershell -ExecutionPolicy Bypass -File scripts\start_hub.ps1 -Root "D:\Stellaris_test" -Port 18895
#>

param(
    [string]$Root = "D:\Stellaris_test",
    [int]$Port = 18895,
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"

# 解释器：优先用 py 启动器解析，其次 PATH 里的 python（跳过商店占位符）
function Resolve-Python([string]$Given) {
    if ($Given) { return $Given }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $resolved = & py -c "import sys; print(sys.executable)" 2>$null
        if ($resolved -and (Test-Path $resolved)) { return $resolved }
    }
    foreach ($name in @('python', 'python3')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source -and $cmd.Source -notlike '*WindowsApps*') { return $cmd.Source }
    }
    return ""
}

$Python = Resolve-Python $Python

if (-not (Test-Path $Root)) { Write-Error "目录不存在：$Root" }
if (-not $Python -or -not (Test-Path $Python)) { Write-Error "找不到 Python，请用 -Python 指定解释器路径" }

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