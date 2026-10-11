<#
.SYNOPSIS
    Сторож (watchdog) для агента: следит за heartbeat и перезапускает агента.

.DESCRIPTION
    Логика простая и намеренно тупая — сторож не должен сам быть источником
    проблем:

    1. Если есть KILL_SWITCH или агент остановлен вручную (halt) — ничего не делаю.
    2. Если процесса агента нет — запускаю его (через start_agent.ps1).
    3. Если процесс есть, но heartbeat-файл не обновлялся дольше N минут —
       процесс считается зависшим: завершаю его и запускаю заново.

    Запускать по расписанию (см. register_tasks.ps1) каждые 5 минут, либо
    вручную для разовой проверки:

        powershell -ExecutionPolicy Bypass -File .\scripts\watchdog.ps1
#>

[CmdletBinding()]
param(
    [int]$StaleMinutes = 10,        # сколько минут молчания считать «зависанием»
    [ValidateSet("paper", "shadow")]
    [string]$Mode = "paper",        # сторож НИКОГДА не запускает live сам
    [switch]$NoStart                # только проверять, не перезапускать
)

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$logsDir = Join-Path $ProjectRoot "logs"
if (-not (Test-Path $logsDir)) { New-Item -ItemType Directory -Path $logsDir | Out-Null }
$watchLog = Join-Path $logsDir "watchdog.log"

function Write-Log($text) {
    $stamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    Add-Content -Path $watchLog -Value "$stamp | $text" -Encoding UTF8
    Write-Host "$stamp | $text"
}

function Get-AgentProcesses {
    Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*mt5_agent*run*" }
}

$killSwitch = Join-Path $ProjectRoot "state\KILL_SWITCH"
if (Test-Path $killSwitch) {
    Write-Log "KILL_SWITCH активен — не вмешиваюсь."
    exit 0
}

$heartbeat = Join-Path $ProjectRoot "state\heartbeat.txt"
$processes = @(Get-AgentProcesses)

if ($processes.Count -eq 0) {
    Write-Log "Процесс агента не найден."
    if ($NoStart) { exit 0 }
    Write-Log "Запускаю агента в режиме $Mode."
    Start-Process -FilePath "powershell.exe" `
        -ArgumentList "-ExecutionPolicy", "Bypass", "-File", (Join-Path $PSScriptRoot "start_agent.ps1"), "-Mode", $Mode `
        -WindowStyle Hidden -WorkingDirectory $ProjectRoot
    exit 0
}

if (-not (Test-Path $heartbeat)) {
    Write-Log "Процесс есть, но heartbeat-файла нет (агент только стартовал?). Жду."
    exit 0
}

$age = (New-TimeSpan -Start (Get-Item $heartbeat).LastWriteTime -End (Get-Date)).TotalMinutes
if ($age -lt $StaleMinutes) {
    Write-Log ("Агент жив, heartbeat свежий ({0:N1} мин). Всё в порядке." -f $age)
    exit 0
}

Write-Log ("Агент НЕ обновлял heartbeat {0:N1} мин (порог {1} мин) — считаю зависшим." -f $age, $StaleMinutes)
foreach ($process in $processes) {
    try {
        Write-Log "Завершаю процесс PID $($process.ProcessId)"
        Stop-Process -Id $process.ProcessId -Force -ErrorAction Stop
    } catch {
        Write-Log "Не удалось завершить PID $($process.ProcessId): $_"
    }
}
Start-Sleep -Seconds 5

if (-not $NoStart) {
    Write-Log "Перезапускаю агента (режим $Mode). Открытые позиции защищены стоп-лоссами в терминале."
    Start-Process -FilePath "powershell.exe" `
        -ArgumentList "-ExecutionPolicy", "Bypass", "-File", (Join-Path $PSScriptRoot "start_agent.ps1"), "-Mode", $Mode `
        -WindowStyle Hidden -WorkingDirectory $ProjectRoot
}
exit 0
