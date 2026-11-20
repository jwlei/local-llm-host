# Starts local-llm-host by itself when you sign in to Windows, headless: in the background, with no console window
# and no browser opening. It puts a "local-llm-host" shortcut in your Startup folder. Safe to re-run.
#   powershell -ExecutionPolicy Bypass -File scripts\enable_startup.ps1
#   Add -Now to also start it now, the same way.
# Undo with scripts\disable_startup.ps1. Stop a running one with Shut down in the app's menu.

param([switch]$Now)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

& (Join-Path $PSScriptRoot 'create_shortcut.ps1') -Startup

# Who it will let in: with APP_PASSWORD it listens for your other devices too (home network and Tailscale).
$envFile = Join-Path $root '.env'
$protected = (Test-Path $envFile) -and (Select-String -Path $envFile -Pattern '^\s*APP_PASSWORD\s*=\s*\S' -Quiet)
if ($protected) {
    Write-Output 'It will open to your other devices too (APP_PASSWORD is set), at port 8810.'
} else {
    Write-Output 'It will answer only this PC: set APP_PASSWORD in .env to let your other devices in.'
}

if ($Now) {
    $pythonw = (Get-Command pyw -ErrorAction SilentlyContinue).Source
    if (-not $pythonw) { $pythonw = (Get-Command pythonw).Source }
    Start-Process -FilePath $pythonw -ArgumentList "`"$(Join-Path $root 'start.pyw')`"", '--headless' -WorkingDirectory $root
    Write-Output 'Started: open http://localhost:8810'
}
