<#
.SYNOPSIS
    Запуск графической панели управления MT5 AI-Agent.

.DESCRIPTION
    Открывает панель на встроенном в Python tkinter — устанавливать ничего не нужно.
    Панель запускает агента отдельным процессом, поэтому её закрытие агента не
    останавливает (и наоборот).

    Примеры:
        powershell -ExecutionPolicy Bypass -File .\scripts\start_gui.ps1
        powershell -ExecutionPolicy Bypass -File .\scripts\start_gui.ps1 -CreateShortcut
        powershell -ExecutionPolicy Bypass -File .\scripts\start_gui.ps1 -Console
#>

[CmdletBinding()]
param(
    [switch]$CreateShortcut,   # создать ярлык «MT5 AI-Agent (панель)» на рабочем столе
    [switch]$Console           # запустить с окном консоли (удобно для отладки)
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$pythonw = Join-Path $ProjectRoot ".venv\Scripts\pythonw.exe"
$script = Join-Path $ProjectRoot "tools\agent_gui.py"

if (-not (Test-Path $python)) {
    Write-Host "[ОШИБКА] Нет виртуального окружения. Сначала выполни:" -ForegroundColor Red
    Write-Host "         powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1" -ForegroundColor Yellow
    exit 1
}
if (-not (Test-Path $script)) {
    Write-Host "[ОШИБКА] Не найден $script" -ForegroundColor Red
    exit 1
}

# Проверяем, что в этой сборке Python есть tkinter (на официальном установщике Windows он есть)
$tkCheck = & $python -c "import tkinter; print('ok')" 2>&1
if ($tkCheck -notmatch "ok") {
    Write-Host "[ОШИБКА] В этой сборке Python нет tkinter — панель недоступна." -ForegroundColor Red
    Write-Host "Реши так: переустанови Python с python.org, оставив галочку «tcl/tk and IDLE»," -ForegroundColor Yellow
    Write-Host "затем пересоздай окружение: Remove-Item .venv -Recurse -Force; .\scripts\install.ps1" -ForegroundColor Yellow
    Write-Host "Альтернатива: работать из консоли — python -m mt5_agent status / run" -ForegroundColor Gray
    exit 1
}

if ($CreateShortcut) {
    $desktop = [Environment]::GetFolderPath("Desktop")
    $shortcutPath = Join-Path $desktop "MT5 AI-Agent (панель).lnk"
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = if (Test-Path $pythonw) { $pythonw } else { $python }
    $shortcut.Arguments = "`"$script`""
    $shortcut.WorkingDirectory = $ProjectRoot
    $shortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,167"
    $shortcut.Description = "Панель управления MT5 AI-Agent (наблюдение и демо-режим)"
    $shortcut.Save()
    Write-Host "[OK] Ярлык создан: $shortcutPath" -ForegroundColor Green
    Write-Host "     Реальный счёт через ярлык недоступен — он запускается только из консоли (docs/SAFETY.md)" -ForegroundColor Gray
}

Write-Host "Открываю панель управления…" -ForegroundColor Cyan
if ($Console -or -not (Test-Path $pythonw)) {
    & $python $script
} else {
    # pythonw = без окна консоли, как обычное приложение Windows
    Start-Process -FilePath $pythonw -ArgumentList "`"$script`"" -WorkingDirectory $ProjectRoot
}
exit 0
