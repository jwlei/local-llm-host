# Stops local-llm-host from starting by itself when you sign in to Windows: removes the shortcut that
# scripts\enable_startup.ps1 put in your Startup folder. A copy running now keeps running: stop it with Shut down
# in the app's menu.
#   powershell -ExecutionPolicy Bypass -File scripts\disable_startup.ps1

$link = Join-Path ([Environment]::GetFolderPath('Startup')) 'local-llm-host.lnk'
if (Test-Path $link) {
    Remove-Item -LiteralPath $link
    Write-Output "Removed ${link}: it no longer starts when you sign in."
} else {
    Write-Output 'It was not set to start when you sign in.'
}
