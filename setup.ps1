param([string]$Python)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path '.venv\Scripts\python.exe')) {
    if ($Python) { & $Python -m venv .venv }
    elseif (Get-Command py -ErrorAction SilentlyContinue) { py -3.12 -m venv .venv }
    elseif (Get-Command python -ErrorAction SilentlyContinue) { python -m venv .venv }
    else { throw 'Install Python 3.12, or run setup.ps1 -Python C:\path\python.exe' }
    if ($LASTEXITCODE) { throw 'Could not create virtual environment' }
}
& '.\.venv\Scripts\python.exe' -m pip install -r requirements-lock.txt
if ($LASTEXITCODE) { throw 'Dependency installation failed' }
& '.\.venv\Scripts\python.exe' -m pip check
if ($LASTEXITCODE) { throw 'Dependency check failed' }
& '.\.venv\Scripts\python.exe' -X utf8 tests\acceptance.py
if ($LASTEXITCODE) { throw 'Acceptance checks failed; see qa-results\acceptance' }
Write-Host 'Offline checks passed. Review DEPLOYMENT.md before connecting the bot.'
