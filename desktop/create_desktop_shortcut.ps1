# Create desktop shortcut for AutomatedSaiten PC app.
# Run: powershell -ExecutionPolicy Bypass -File ".\create_desktop_shortcut.ps1"

$ErrorActionPreference = "Stop"

$desktopDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$launchBat = Join-Path $desktopDir "launch.bat"
$shortcutPath = Join-Path ([Environment]::GetFolderPath("Desktop")) "AutomatedSaiten PC.lnk"

if (-not (Test-Path $launchBat)) {
    throw "Launcher not found: $launchBat"
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $launchBat
$shortcut.WorkingDirectory = $desktopDir
$shortcut.WindowStyle = 1
$shortcut.Description = "AutomatedSaiten PC"
$shortcut.Save()

Write-Host "Shortcut created:"
Write-Host $shortcutPath
