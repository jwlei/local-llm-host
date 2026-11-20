# Creates a "local-llm-host" shortcut on the Desktop and in the Start menu that runs start.pyw: starts the app
# (port 8810) with no console window and opens it in your browser. Safe to re-run.
#   powershell -ExecutionPolicy Bypass -File scripts\create_shortcut.ps1
#   Add -Startup to put it in the Startup folder instead (headless). scripts\enable_startup.ps1 does this for you,
#   and scripts\disable_startup.ps1 undoes it.

param([switch]$Startup)

$root = Split-Path -Parent $PSScriptRoot
$launcher = Join-Path $root 'start.pyw'

# pythonw / pyw run Python without a console window.
$pythonw = (Get-Command pyw -ErrorAction SilentlyContinue).Source
if (-not $pythonw) { $pythonw = (Get-Command pythonw).Source }

$shell = New-Object -ComObject WScript.Shell
$folders = if ($Startup) { @([Environment]::GetFolderPath('Startup')) } else { @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs')) }
foreach ($folder in $folders) {
    $shortcut = $shell.CreateShortcut((Join-Path $folder 'local-llm-host.lnk'))
    $shortcut.TargetPath = $pythonw
    $shortcut.Arguments = "`"$launcher`"" + $(if ($Startup) { ' --headless' } else { '' })
    $shortcut.WorkingDirectory = $root
    $shortcut.Description = if ($Startup) { 'Start local-llm-host without opening a browser' } else { 'Open local-llm-host' }
    # An icon from Windows' own icon library.
    $shortcut.IconLocation = Join-Path $env:SystemRoot 'System32\imageres.dll,15'
    $shortcut.Save()
    Write-Output "Created $(Join-Path $folder 'local-llm-host.lnk')"
}
