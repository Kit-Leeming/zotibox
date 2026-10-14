param([string]$IconPath, [string]$TargetPath, [string]$DesktopPath)
$ErrorActionPreference = 'Stop'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut((Join-Path $DesktopPath 'ZotiBox.lnk'))
$shortcut.TargetPath = $TargetPath
$shortcut.WorkingDirectory = Split-Path -Parent $TargetPath
$shortcut.IconLocation = "$IconPath,0"
$shortcut.Save()
