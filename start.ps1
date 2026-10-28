$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$vaultPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $vaultPython)) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python environment creation failed.' }
}
& $vaultPython -c 'import flask, PIL, waitress' 2>$null
if ($LASTEXITCODE -ne 0) {
    & $vaultPython -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
}
Write-Host 'Open http://127.0.0.1:8765 in your browser. Press Ctrl+C to stop.'
& $vaultPython run.py
