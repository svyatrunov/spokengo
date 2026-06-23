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
    --collect-submodules spokengo `
    src\spokengo\__main__.py
Write-Host "Built dist\SpokenGo.exe"
