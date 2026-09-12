# Build a single distributable SpokenGo.exe with PyInstaller.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
python -m venv .venv-build
$py = ".\.venv-build\Scripts\python.exe"
& $py -m pip install --upgrade pip
& $py -m pip install -e ".[runtime]" pyinstaller
& $py -m PyInstaller --noconfirm --onefile --noconsole `
    --name SpokenGo `
    --icon src\spokengo\assets\spokengo.ico `
    --collect-submodules spokengo `
    --add-data "src\spokengo\assets\spokengo.ico;spokengo/assets" `
    scripts\pyi_entry.py
Write-Host "Built dist\SpokenGo.exe"
