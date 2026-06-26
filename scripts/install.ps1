# SpokenGo installer (Windows). Creates an isolated environment, installs the
# app, and puts a clickable shortcut on the Desktop and in the Start Menu — so
# you launch it like any normal app, no terminal, no shipped .exe.
#
#   Right-click -> Run with PowerShell      (or)
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Step($n, $msg) { Write-Host "[$n/5] $msg" -ForegroundColor Cyan }

Step 1 "Checking Python..."
$py = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $py) { throw "Python 3.10+ not found. Install it from https://www.python.org/downloads/ (tick 'Add to PATH')." }

Step 2 "Creating virtual environment (.venv)..."
python -m venv .venv
$venvPy  = Join-Path $root ".venv\Scripts\python.exe"
$venvPyw = Join-Path $root ".venv\Scripts\pythonw.exe"   # windowed = no console

Step 3 "Installing SpokenGo + runtime dependencies..."
& $venvPy -m pip install --upgrade pip | Out-Null
& $venvPy -m pip install -e ".[runtime]"

Step 4 "Setting up your Groq API key..."
# Skip the prompt if a key is already stored (Credential Manager persists it
# across installs) — pressing Enter previously kept it, but asking again is noise.
& $venvPy -c "import sys; from spokengo.secrets_store import get_key; sys.exit(0 if get_key('groq') else 1)" 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "    A Groq API key is already saved — keeping it. Change it anytime in the app's Settings."
} else {
    Write-Host "    Get a free key at https://console.groq.com/keys"
    $key = Read-Host "    Paste your Groq API key (or press Enter to set it later in the app)"
    if ($key) { & $venvPy -m spokengo set-key groq $key }
}

Step 5 "Creating shortcuts..."
$icon = Join-Path $root "src\spokengo\assets\spokengo.ico"
function New-Shortcut($path) {
  $ws  = New-Object -ComObject WScript.Shell
  $lnk = $ws.CreateShortcut($path)
  $lnk.TargetPath       = $venvPyw
  $lnk.Arguments        = "-m spokengo gui"
  $lnk.WorkingDirectory = $root
  $lnk.IconLocation     = $icon
  $lnk.Description       = "SpokenGo - voice input into any field"
  $lnk.Save()
}
$desktop   = [Environment]::GetFolderPath("Desktop")
$startMenu = Join-Path ([Environment]::GetFolderPath("StartMenu")) "Programs"
New-Shortcut (Join-Path $desktop   "SpokenGo.lnk")
New-Shortcut (Join-Path $startMenu "SpokenGo.lnk")

Write-Host ""
Write-Host "Done. A 'SpokenGo' icon is now on your Desktop and in the Start Menu." -ForegroundColor Green
Write-Host "Launch it, then press your hotkey (default Ctrl+Space) in any text field."
Write-Host ""
Write-Host "Tip: to auto-start at login, copy the Desktop shortcut into the Startup"
Write-Host "     folder (press Win+R, type 'shell:startup', paste the shortcut)."
