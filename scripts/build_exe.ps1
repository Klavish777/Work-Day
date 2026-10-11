<#
.SYNOPSIS
    Сборка панели управления в один .exe (PyInstaller).

.DESCRIPTION
    Делает `MT5-Agent-Панель.exe` — файл, который можно запускать двойным щелчком,
    без PowerShell и без открытой консоли.

    Что важно понимать перед сборкой:

    * **.exe — это оболочка, а не «всё в одном».** Рядом с ним всё равно нужны
      папка проекта (config/, .env, models/), установленный терминал MT5 и вход
      в счёт. .exe только запускает код из того же каталога;
    * **антивирусы иногда ругаются** на упакованные PyInstaller'ом файлы
      (ложное срабатывание). Если это мешает, используйте ярлык на pythonw
      (scripts\start_gui.ps1 -CreateShortcut) — работает так же, без .exe;
    * **реальный счёт через .exe недоступен** — панель поддерживает только
      shadow/paper. Это сделано намеренно.

    Запуск:
        powershell -ExecutionPolicy Bypass -File .\scripts\build_exe.ps1
        powershell -ExecutionPolicy Bypass -File .\scripts\build_exe.ps1 -OneFile
#>

[CmdletBinding()]
param(
    [switch]$OneFile    # один файл вместо папки (для переноски; запускается медленнее)
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "[ОШИБКА] Нет .venv. Сначала выполни .\scripts\install.ps1" -ForegroundColor Red
    exit 1
}

Write-Host "==> Устанавливаю PyInstaller (только для сборки)" -ForegroundColor Cyan
& $python -m pip install --quiet --upgrade pyinstaller
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ОШИБКА] Не удалось установить PyInstaller." -ForegroundColor Red
    exit 1
}

$distDir = Join-Path $ProjectRoot "dist"
$buildDir = Join-Path $ProjectRoot "build"

$arguments = @(
    "--noconfirm",
    "--clean",
    "--windowed",                                  # без окна консоли
    "--name", "MT5-Agent",
    "--paths", (Join-Path $ProjectRoot "src"),     # чтобы нашёлся пакет mt5_agent
    "--collect-submodules", "mt5_agent",           # внутрь .exe попадает весь агент
    "--hidden-import", "MetaTrader5",              # без него .exe не сможет связаться с терминалом
    "--distpath", $distDir,
    "--workpath", $buildDir,
    "--specpath", $buildDir
)
if ($OneFile) { $arguments += "--onefile" }

# Исключаем тяжёлые библиотеки, которые панели не нужны (меньше размер и меньше ложных срабатываний антивируса)
foreach ($module in @("matplotlib", "scipy", "tkinter.test", "PyQt5", "PySide6", "notebook")) {
    $arguments += @("--exclude-module", $module)
}

$arguments += (Join-Path $ProjectRoot "tools\agent_gui.py")

Write-Host "==> Собираю приложение" -ForegroundColor Cyan
& $python -m PyInstaller @arguments
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ОШИБКА] Сборка не удалась. Смотри вывод выше." -ForegroundColor Red
    exit 1
}

$exeName = "MT5-Agent.exe"
if ($OneFile) {
    $result = Join-Path $distDir $exeName
} else {
    $result = Join-Path $distDir "MT5-Agent" $exeName
}

# Кладём .exe в корень проекта: тогда он найдёт config/.env/models рядом с собой
$target = Join-Path $ProjectRoot $exeName
if (Test-Path $result) {
    Copy-Item $result $target -Force
    Write-Host "[OK] Приложение собрано: $result" -ForegroundColor Green
    Write-Host "[OK] Скопировано в корень проекта: $target" -ForegroundColor Green
} else {
    Write-Host "[!] Не найден ожидаемый файл $result — посмотри папку dist." -ForegroundColor Yellow
}

Write-Host @"

Как пользоваться (два способа):
  A) Двойной щелчок по MT5-Agent.exe в корне проекта → открывается окно панели.
     Дальше: вкладка «Настройка» → реквизиты демо-счёта → «Сохранить в .env»
             вкладка «Быстрый старт» → «Запустить всё до бэктеста» → демо-торговля.
  B) Консольный режим того же файла (полный набор команд):
     .\MT5-Agent.exe --cli check
     .\MT5-Agent.exe --cli fetch --symbol AUDCAD --bars 20000 --save-specs
     .\MT5-Agent.exe --cli train --symbol AUDCAD
     .\MT5-Agent.exe --cli backtest --symbol AUDCAD --days 180
     .\MT5-Agent.exe --cli run --mode paper

Важно:
  * .exe НЕ самодостаточен: рядом должны лежать папка config\ (с config.yaml), файл .env
    и папка models\ (там появятся обученные модели). Поэтому запускай его из корня проекта.
  * Панель внутри .exe поддерживает только shadow/paper. Реальный счёт — только через
    консольный режим (--cli run --mode live --i-understand-live) и три подтверждения.
  * Если панель сообщает «нет модели» — сначала шаг train («Быстрый старт» или --cli train).
"@ -ForegroundColor Gray
