<#
.SYNOPSIS
    Установка MT5 AI-Agent на Windows-ПК или Windows VPS.

.DESCRIPTION
    Скрипт делает ровно одно: готовит окружение.
    1. Проверяет Python 3.10–3.13 (для MetaTrader5 нужен именно Windows-колесо).
    2. Создаёт виртуальное окружение .venv внутри папки проекта.
    3. Ставит зависимости, включая официальный пакет MetaTrader5.
    4. Копирует .env.example -> .env, если .env ещё нет.
    5. Показывает, что делать дальше.

    Ничего не торгует и никуда не подключается. Запускать нужно в PowerShell
    (не от имени администратора) из папки проекта:

        powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1

.NOTES
    Если PowerShell блокирует запуск скриптов — используй -ExecutionPolicy Bypass,
    как в примере выше, а не меняй политику машины глобально.
#>

[CmdletBinding()]
param(
    [string]$PythonCommand = "",   # например "py -3.11" или путь к python.exe
    [switch]$SkipVenv
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

# Определяем папку проекта (на уровень выше scripts\)
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

function Write-Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Write-Ok($text)   { Write-Host "    [OK] $text" -ForegroundColor Green }
function Write-Warn($text) { Write-Host "    [!]  $text" -ForegroundColor Yellow }
function Fail($text)       { Write-Host "`n[ОШИБКА] $text" -ForegroundColor Red; exit 1 }

Write-Host "======================================================" -ForegroundColor White
Write-Host " MT5 AI-Agent — установка окружения" -ForegroundColor White
Write-Host "======================================================" -ForegroundColor White

# --- 1. Проверка, что мы на Windows и в папке проекта -------------------------
if ($PSVersionTable.Platform -and $PSVersionTable.Platform -ne "Win32") {
    Fail "Скрипт предназначен для Windows (MetaTrader5 существует только под Windows)."
}
if (-not (Test-Path (Join-Path $ProjectRoot "config\config.yaml"))) {
    Fail "Не найден config\config.yaml. Запускай скрипт из папки проекта: .\scripts\install.ps1"
}

# --- 2. Поиск Python ----------------------------------------------------------
Write-Step "Ищу Python"
$python = $null
$candidates = @()
if ($PythonCommand) { $candidates += ,($PythonCommand -split '\s+') }
$candidates += ,@("py", "-3.13")
$candidates += ,@("py", "-3.12")
$candidates += ,@("py", "-3.11")
$candidates += ,@("py", "-3.10")
$candidates += ,@("python")

foreach ($candidate in $candidates) {
    $exe = $candidate[0]
    $exeArgs = @()
    if ($candidate.Length -gt 1) { $exeArgs = $candidate[1..($candidate.Length - 1)] }
    try {
        $versionText = & $exe @exeArgs -c "import sys; print('.'.join(map(str, sys.version_info[:3])))" 2>$null
        if ($LASTEXITCODE -eq 0 -and $versionText) {
            $parts = $versionText.Trim().Split('.')
            $major = [int]$parts[0]; $minor = [int]$parts[1]
            if ($major -eq 3 -and $minor -ge 10 -and $minor -le 13) {
                $python = @{ Exe = $exe; Args = $exeArgs; Version = $versionText.Trim() }
                break
            } elseif ($major -eq 3 -and ($minor -lt 10 -or $minor -gt 13)) {
                Write-Warn "Найден Python $versionText — для MetaTrader5 нужен 3.10–3.13. Пропускаю."
            }
        }
    } catch { }
}

if (-not $python) {
    Fail @"
Python 3.10–3.13 не найден.

Установи Python (галочка «Add python.exe to PATH»):
    https://www.python.org/downloads/windows/
Затем закрой и снова открой PowerShell и повтори запуск скрипта.

Важно: MetaTrader5 — это Windows-колесо с привязкой к версии Python, поэтому
важно, чтобы версия была в диапазоне 3.10–3.13 (рекомендуем 3.11 или 3.12).
"@
}
Write-Ok ("Python {0} ({1} {2})" -f $python.Version, $python.Exe, ($python.Args -join " "))

# --- 3. Виртуальное окружение -------------------------------------------------
$venvPath = Join-Path $ProjectRoot ".venv"
if (-not $SkipVenv) {
    Write-Step "Создаю виртуальное окружение .venv"
    if (-not (Test-Path $venvPath)) {
        & $python.Exe @($python.Args) -m venv $venvPath
        if ($LASTEXITCODE -ne 0) { Fail "Не удалось создать .venv" }
        Write-Ok "Создано: $venvPath"
    } else {
        Write-Ok "Уже существует: $venvPath"
    }
}

$venvPython = Join-Path $venvPath "Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Fail "Не найден $venvPython. Пересоздай окружение: Remove-Item .venv -Recurse -Force"
}

# --- 4. Зависимости -----------------------------------------------------------
Write-Step "Обновляю pip"
& $venvPython -m pip install --quiet --upgrade pip
Write-Ok "pip обновлён"

Write-Step "Ставлю зависимости (это может занять пару минут)"
& $venvPython -m pip install --quiet -r (Join-Path $ProjectRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) { Fail "Не удалось установить зависимости. Смотри текст ошибки выше." }
Write-Ok "Зависимости установлены"

Write-Step "Проверяю MetaTrader5"
$mt5check = & $venvPython -c "import MetaTrader5 as m; print(m.__version__)" 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Ok "MetaTrader5 установлен, версия $mt5check"
} else {
    Write-Warn "MetaTrader5 не установился: $mt5check"
    Write-Warn "Скорее всего версия Python не совпадает с колесом. Пересоздай .venv на Python 3.11 или 3.12."
}

# --- 5. Файл секретов ---------------------------------------------------------
Write-Step "Проверяю .env"
$envPath = Join-Path $ProjectRoot ".env"
if (-not (Test-Path $envPath)) {
    Copy-Item (Join-Path $ProjectRoot ".env.example") $envPath
    Write-Ok ".env создан из .env.example"
    Write-Warn "ОТКРОЙ .env и впиши реквизиты ДЕМО-счёта (MT5_LOGIN, MT5_PASSWORD, MT5_SERVER)."
} else {
    Write-Ok ".env уже есть — не трогаю (в нём твои секреты)"
}

# --- 6. Итог --------------------------------------------------------------------
Write-Host "`n======================================================" -ForegroundColor White
Write-Host " Что дальше" -ForegroundColor White
Write-Host "======================================================" -ForegroundColor White
Write-Host @"
1) Открой .env и заполни реквизиты ДЕМО-счёта MetaTrader 5.
2) Запусти терминал MT5, войди в ДЕМО-счёт, включи кнопку AutoTrading.
3) Проверь связь и окружение:
       .\.venv\Scripts\python.exe -m mt5_agent check
4) Скачай данные с брокера (бары + параметры символов):
       .\.venv\Scripts\python.exe -m mt5_agent fetch --bars 20000 --save-specs
5) Обучи модель и посмотри честный отчёт:
       .\.venv\Scripts\python.exe -m mt5_agent train --symbol EURUSD
6) Прогони бэктест и прочитай ВЕРДИКТ:
       .\.venv\Scripts\python.exe -m mt5_agent backtest --symbol EURUSD --days 180
7) Только если вердикт положительный — запусти демо-торговлю:
       powershell -ExecutionPolicy Bypass -File .\scripts\start_agent.ps1 -Mode paper

Помни: цель первых 1–3 месяцев — собрать статистику на демо, а не заработать.
"@ -ForegroundColor Gray
