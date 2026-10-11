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
    "--name", "MT5-Agent-Панель",
    "--paths", (Join-Path $ProjectRoot "src"),     # чтобы нашёлся пакет mt5_agent
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

$result = if ($OneFile) { Join-Path $distDir "MT5-Agent-Панель.exe" } else { Join-Path $distDir "MT5-Agent-Панель" }
Write-Host "[OK] Готово: $result" -ForegroundColor Green
Write-Host @"

Как пользоваться:
  1. Запусти MT5, войди в ДЕМО-счёт, включи AutoTrading.
  2. Запусти MT5-Agent-Панель.exe (двойным щелчком).
  3. В панели: «Проверить один цикл» → «Демо-торговля».

Важно:
  * .exe читает config\.env\models из СВОЕЙ папки проекта. Если переносишь .exe
    отдельно от проекта, он не найдёт настройки — держи его рядом с проектом
    (или запускай из папки проекта).
  * Если панель сообщает «нет модели» — сначала выполни train (вкладка «Задачи»).
  * Реальный счёт через панель запустить нельзя: только консоль + три подтверждения.
"@ -ForegroundColor Gray
