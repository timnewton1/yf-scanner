# Run yf-scanner natively on Windows, through Windows' own Bluetooth (no WSL, no usbipd).
# Installs uv the first time, syncs the venv (uv fetches Python itself if needed), then runs the
# scanner with whatever arguments you give.
#
#   powershell -ExecutionPolicy Bypass -File setup\windows.ps1 --adapters
#   powershell -ExecutionPolicy Bypass -File setup\windows.ps1 --report   (the adapter is found once, then saved in .adapter)
#
# Works from a normal clone or straight from a WSL checkout (\\wsl.localhost\Ubuntu\home\...). The venv
# lives in %LOCALAPPDATA%\yf-scanner so it never collides with a WSL .venv. Logs go to logs\ as usual.
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$venv = Join-Path $env:LOCALAPPDATA 'yf-scanner\venv'
$py = Join-Path $venv 'Scripts\python.exe'

$env:Path = "$(Join-Path $env:USERPROFILE '.local\bin');$env:Path"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host 'uv not found; installing it...'
    powershell -ExecutionPolicy Bypass -c 'irm https://astral.sh/uv/install.ps1 | iex'
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Write-Host 'uv installed. Open a new PowerShell window and run this again.'
        exit 0
    }
}

$env:UV_PROJECT_ENVIRONMENT = $venv
Push-Location $repo
try { & uv sync --quiet; if ($LASTEXITCODE) { exit $LASTEXITCODE } } finally { Pop-Location }

$bt = Get-PnpDevice -Class Bluetooth -Status OK -ErrorAction SilentlyContinue
if (-not $bt) { Write-Warning 'No working Bluetooth radio found. Turn Bluetooth on in Settings (or is it attached to WSL by usbipd?).' }

$scanArgs = @($args)
if ($scanArgs.Count -eq 0) { $scanArgs = @('--port', 'DEMO', '--report'); Write-Host '(no arguments: running the DEMO report)' }
& $py (Join-Path $repo 'scan.py') @scanArgs
exit $LASTEXITCODE
