# Hand the laptop's Bluetooth radio to WSL2 (usbipd-win), so yf-scanner in WSL can reach a BLE adapter.
# Windows loses Bluetooth while the radio is attached. Undo with -Detach, or unplug/replug a dongle.
#
#   powershell -ExecutionPolicy Bypass -File setup\wsl-attach-bluetooth.ps1
#   powershell -ExecutionPolicy Bypass -File setup\wsl-attach-bluetooth.ps1 -Detach
#
# Then, in WSL: setup/setup.sh   (BlueZ, LE-only mode, venv)
# Prefer not to give up Windows Bluetooth? Run the scanner on Windows instead: setup\windows.ps1
param([switch]$Detach, [string]$BusId)
$ErrorActionPreference = 'Stop'

$me = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not (New-Object Security.Principal.WindowsPrincipal $me).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $argList = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    if ($Detach) { $argList += ' -Detach' }
    if ($BusId) { $argList += " -BusId $BusId" }
    Start-Process powershell -Verb RunAs -ArgumentList $argList
    exit
}

$usbipd = (Get-Command usbipd -ErrorAction SilentlyContinue).Source
if (-not $usbipd) { $usbipd = 'C:\Program Files\usbipd-win\usbipd.exe' }
if (-not (Test-Path $usbipd)) {
    Write-Host 'usbipd-win not found; installing it with winget...'
    winget install -e --id dorssel.usbipd-win --accept-package-agreements --accept-source-agreements
    $usbipd = 'C:\Program Files\usbipd-win\usbipd.exe'
}

$list = & $usbipd list
if (-not $BusId) {
    $line = $list | Where-Object { $_ -match '^\s*\d+-[\d.]+\s' -and $_ -match 'Bluetooth' } | Select-Object -First 1
    if (-not $line) {
        $list | Write-Host
        Write-Warning 'No USB Bluetooth device listed. A built-in radio not on USB can''t be passed to WSL; use a USB dongle or setup\windows.ps1.'
        Read-Host 'Enter to close'; exit 1
    }
    $BusId = ($line.Trim() -split '\s+')[0]
    Write-Host "Bluetooth radio: $line"
}

if ($Detach) {
    & $usbipd detach --busid $BusId
    Write-Host "Detached $BusId; Windows has its Bluetooth back."
} else {
    wsl.exe -e true | Out-Null   # make sure a WSL distro is running to attach to
    & $usbipd bind --busid $BusId
    & $usbipd attach --wsl --busid $BusId
    Start-Sleep -Seconds 3
    & $usbipd list | Write-Host
    Write-Host "`nAttached $BusId to WSL. In WSL run: setup/setup.sh"
}
Read-Host 'Enter to close'
