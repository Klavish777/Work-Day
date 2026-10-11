<#
.SYNOPSIS
    Регистрация задач планировщика Windows для VPS-режима (24/7).

.DESCRIPTION
    Создаёт две задачи:

    * MT5-Agent-Watchdog  — каждые 5 минут проверяет heartbeat и при необходимости
      перезапускает агента (см. watchdog.ps1);
    * MT5-Agent-Dashboard — веб-дашборд на localhost:8787 при старте системы.

    Скрипт НЕ создаёт задачу «запустить торговлю на реале»: живой реал запускается
    вручную и осознанно. Автоматизирован только контроль демо/paper-агента.

    Запуск ТРЕБУЕТ прав администратора:

        powershell -ExecutionPolicy Bypass -File .\scripts\register_tasks.ps1

    Удалить задачи:
        powershell -ExecutionPolicy Bypass -File .\scripts\register_tasks.ps1 -Remove
#>

[CmdletBinding()]
param(
    [ValidateSet("paper", "shadow")]
    [string]$Mode = "paper",
    [int]$WatchdogIntervalMinutes = 5,
    [switch]$Remove,
    [switch]$SkipDashboard
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "[ОШИБКА] Нужны права администратора (запусти PowerShell от имени администратора)." -ForegroundColor Red
    exit 1
}

$taskNames = @("MT5-Agent-Watchdog", "MT5-Agent-Dashboard")

if ($Remove) {
    foreach ($name in $taskNames) {
        if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $name -Confirm:$false
            Write-Host "Удалена задача: $name" -ForegroundColor Yellow
        }
    }
    exit 0
}

$python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "[ОШИБКА] Нет .venv. Сначала выполни .\scripts\install.ps1" -ForegroundColor Red
    exit 1
}

# --- Задача 1: watchdog -------------------------------------------------------
$watchdogAction = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-ExecutionPolicy Bypass -WindowStyle Hidden -File `"$PSScriptRoot\watchdog.ps1`" -Mode $Mode" `
    -WorkingDirectory $ProjectRoot
$watchdogTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $WatchdogIntervalMinutes) `
    -RepetitionDuration ([TimeSpan]::MaxValue)
$watchdogSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName "MT5-Agent-Watchdog" -Action $watchdogAction `
    -Trigger $watchdogTrigger -Settings $watchdogSettings `
    -Description "Сторож MT5 AI-Agent: следит за heartbeat и перезапускает агента (режим $Mode)" `
    -Force | Out-Null
Write-Host "[OK] Задача MT5-Agent-Watchdog: каждые $WatchdogIntervalMinutes мин (режим $Mode)" -ForegroundColor Green

# --- Задача 2: дашборд ---------------------------------------------------------
if (-not $SkipDashboard) {
    $dashboardAction = New-ScheduledTaskAction -Execute $python `
        -Argument "`"$ProjectRoot\tools\dashboard.py`" --host 127.0.0.1 --port 8787" `
        -WorkingDirectory $ProjectRoot
    $dashboardTrigger = New-ScheduledTaskTrigger -AtStartup
    Register-ScheduledTask -TaskName "MT5-Agent-Dashboard" -Action $dashboardAction `
        -Trigger $dashboardTrigger -Settings $watchdogSettings `
        -Description "Локальный дашборд MT5 AI-Agent (только чтение, http://127.0.0.1:8787)" `
        -Force | Out-Null
    Write-Host "[OK] Задача MT5-Agent-Dashboard: при старте системы, http://127.0.0.1:8787" -ForegroundColor Green
}

Write-Host @"

Дальше:
  * Агент запускается сторожем в режиме '$Mode'. Первый запуск произойдёт в течение
    $WatchdogIntervalMinutes минут после включения задачи (если агент ещё не работает).
  * Если нужно, чтобы агент работал сразу после загрузки VPS, добавь задачу
    на старт системы вручную или выполни:
        powershell -ExecutionPolicy Bypass -File .\scripts\start_agent.ps1 -Mode $Mode
  * РЕАЛЬНАЯ торговля сюда не входит специально: её запускают руками и осознанно.
"@ -ForegroundColor Gray
