# Run yf-scanner natively on Windows, through Windows' own Bluetooth (no WSL, no usbipd).
# Sets up Python and a venv the first time, then runs the scanner with whatever arguments you give.
#
#   powershell -ExecutionPolicy Bypass -File yf\setup\windows.ps1 --adapters
#   powershell -ExecutionPolicy Bypass -File yf\setup\windows.ps1 --port ble:AA:BB:CC:DD:EE:FF --report
#
# Works from a normal clone or straight from a WSL checkout (\\wsl.localhost\Ubuntu\home\...). The venv
# lives in %LOCALAPPDATA%\yf-scanner so it never collides with a WSL .venv. Logs go to yf\logs as usual.
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$venv = Join-Path $env:LOCALAPPDATA 'yf-scanner\venv'
$py = Join-Path $venv 'Scripts\python.exe'
$req = Join-Path $repo 'yf\requirements.txt'
$stamp = Join-Path $venv 'requirements.stamp'

function Find-Python {
    foreach ($c in @('py', 'python')) {
        $cmd = Get-Command $c -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source -notmatch 'WindowsApps') { return $c }  # skip the Store alias stub
    }
    return $null
}

if (-not (Test-Path $py)) {
    $base = Find-Python
    if (-not $base) {
        Write-Host 'Python not found; installing it with winget...'
        winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
        Write-Host 'Python installed. Open a new PowerShell window and run this again.'
        exit 0
    }
    Write-Host "Creating venv in $venv"
    New-Item -ItemType Directory -Force (Split-Path $venv) | Out-Null
    if ($base -eq 'py') { & py -3 -m venv $venv } else { & python -m venv $venv }
}

$want = (Get-FileHash $req).Hash
if (-not (Test-Path $stamp) -or (Get-Content $stamp) -ne $want) {
    & $py -m pip install -q --upgrade pip
    & $py -m pip install -q -r $req
    Set-Content $stamp $want
}

$bt = Get-PnpDevice -Class Bluetooth -Status OK -ErrorAction SilentlyContinue
if (-not $bt) { Write-Warning 'No working Bluetooth radio found. Turn Bluetooth on in Settings (or is it attached to WSL by usbipd?).' }

$scanArgs = @($args)
if ($scanArgs.Count -eq 0) { $scanArgs = @('--port', 'DEMO', '--report'); Write-Host '(no arguments: running the DEMO report)' }
& $py (Join-Path $repo 'yf\yf_scan.py') @scanArgs
exit $LASTEXITCODE
