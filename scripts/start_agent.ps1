<#
.SYNOPSIS
    Запуск агента MT5 AI-Agent с авто-перезапуском при падении.

.DESCRIPTION
    Скрипт запускает `python -m mt5_agent run` и следит за ним:

    * если процесс упал по технической причине — перезапускает с задержкой;
    * если агент остановился по БЕЗОПАСНОСТИ (код 3: реальный счёт, ограничения) —
      НЕ перезапускает и сообщает об этом;
    * если включён KILL_SWITCH — не запускает вообще;
    * все сообщения дублируются в logs\console.log.

    Примеры:
        powershell -ExecutionPolicy Bypass -File .\scripts\start_agent.ps1
        powershell -ExecutionPolicy Bypass -File .\scripts\start_agent.ps1 -Mode shadow -Once
        powershell -ExecutionPolicy Bypass -File .\scripts\start_agent.ps1 -Mode live -AllowLive
#>

[CmdletBinding()]
param(
    [ValidateSet("paper", "shadow", "live")]
    [string]$Mode = "paper",

    [switch]$Once,              # один цикл и выход (удобно для проверки)
    [switch]$DryRun,            # считать всё, но ордера не отправлять
    [switch]$AllowLive,         # подтверждение запуска на реальном счёте
    [int]$RestartDelaySec = 30,
    [int]$MaxRestarts = 20
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "[ОШИБКА] Нет виртуального окружения. Сначала: .\scripts\install.ps1" -ForegroundColor Red
    exit 1
}

$logsDir = Join-Path $ProjectRoot "logs"
if (-not (Test-Path $logsDir)) { New-Item -ItemType Directory -Path $logsDir | Out-Null }
$consoleLog = Join-Path $logsDir "console.log"

$killSwitch = Join-Path $ProjectRoot "state\KILL_SWITCH"
if (Test-Path $killSwitch) {
    Write-Host "[СТОП] Активен KILL_SWITCH ($killSwitch)." -ForegroundColor Red
    Write-Host "Снять: .\.venv\Scripts\python.exe -m mt5_agent unkill" -ForegroundColor Yellow
    exit 1
}

if ($Mode -eq "live" -and -not $AllowLive) {
    Write-Host "[СТОП] Режим live требует явного подтверждения." -ForegroundColor Red
    Write-Host "Запусти так: .\scripts\start_agent.ps1 -Mode live -AllowLive" -ForegroundColor Yellow
    Write-Host "И помни: перед этим нужны три барьера (демо 1-3 месяца, отдельный счёт," -ForegroundColor Yellow
    Write-Host "флаг I_UNDERSTAND_REAL_MONEY_RISK=TRUE и safety.demo_only=false)." -ForegroundColor Yellow
    exit 1
}

$arguments = @("-m", "mt5_agent", "run", "--mode", $Mode)
if ($Once)   { $arguments += "--once" }
if ($DryRun) { $arguments += "--dry-run" }
if ($Mode -eq "live" -and $AllowLive) { $arguments += "--i-understand-live" }

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

function Write-Log($text) {
    $stamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    $line = "$stamp | $text"
    Write-Host $line
    Add-Content -Path $consoleLog -Value $line -Encoding UTF8
}

Write-Log "Запуск агента в режиме '$Mode' (once=$($Once.IsPresent), dryRun=$($DryRun.IsPresent))"

$attempt = 0
while ($attempt -lt $MaxRestarts) {
    $attempt++
    if (Test-Path $killSwitch) {
        Write-Log "Обнаружен KILL_SWITCH — останавливаюсь и не перезапускаю."
        exit 0
    }

    Write-Log "Попытка #$attempt : $python $($arguments -join ' ')"
    & $python @arguments 2>&1 | ForEach-Object { Write-Log $_ }
    $code = $LASTEXITCODE
    Write-Log "Агент завершился с кодом $code"

    switch ($code) {
        0 {
            if ($Once) { Write-Log "Одиночный цикл выполнен." ; exit 0 }
            Write-Log "Штатное завершение (0). Остановлено."
            exit 0
        }
        3 {
            Write-Log "ОСТАНОВКА ПО БЕЗОПАСНОСТИ. Перезапуск НЕ выполняю — сначала разберись в причине."
            exit 3
        }
        2 {
            Write-Log "Ошибка конфигурации или окружения. Перезапуск НЕ поможет — исправь и запусти снова."
            exit 2
        }
        130 {
            Write-Log "Прервано пользователем (Ctrl+C)."
            exit 0
        }
        default {
            Write-Log "Технический сбой. Пауза $RestartDelaySec сек. и перезапуск."
            Start-Sleep -Seconds $RestartDelaySec
        }
    }
}

Write-Log "Исчерпан лимит перезапусков ($MaxRestarts). Останавливаюсь."
exit 1
