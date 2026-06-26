# Make SpokenGo start automatically at login (no console window).
# Enable:   powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1
# Disable:  powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1 -Remove
param([switch]$Remove)

$ErrorActionPreference = "Stop"
$root    = Split-Path -Parent $PSScriptRoot
$startup = [Environment]::GetFolderPath("Startup")
$lnkPath = Join-Path $startup "SpokenGo.lnk"

if ($Remove) {
    if (Test-Path $lnkPath) { Remove-Item $lnkPath; Write-Host "Autostart disabled." -ForegroundColor Green }
    else { Write-Host "No autostart entry found." }
    return
}

$pyw  = Join-Path $root ".venv\Scripts\pythonw.exe"   # windowed = no console
$icon = Join-Path $root "src\spokengo\assets\spokengo.ico"
if (-not (Test-Path $pyw)) {
    throw "Не найден $pyw. Сначала запустите scripts\install.ps1 (создаёт .venv)."
}

$ws  = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut($lnkPath)
$lnk.TargetPath       = $pyw
$lnk.Arguments        = "-m spokengo gui"
$lnk.WorkingDirectory = $root
$lnk.IconLocation     = $icon
$lnk.Description       = "SpokenGo - start at login"
$lnk.Save()

Write-Host "SpokenGo will now start automatically at login (windowed, no console)." -ForegroundColor Green
Write-Host "To undo:  powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1 -Remove"
