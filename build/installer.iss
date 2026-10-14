#define AppName "ZotiBox"
#define AppVersion "0.5.0"

[Setup]
AppId={{B2AD5EB7-0914-41C6-9482-ED8187149302}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=ZotiBox
DefaultDirName={localappdata}\Programs\ZotiBox
DefaultGroupName=ZotiBox
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir=..\dist
OutputBaseFilename=ZotiBox-Setup-0.5.0
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\ZotiBox.exe
SetupIconFile=generated\app.ico
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "..\.build-work\distribution\ZotiBox\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\ZotiBox"; Filename: "{app}\ZotiBox.exe"
Name: "{autodesktop}\ZotiBox"; Filename: "{app}\ZotiBox.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ZotiBox.exe"; Description: "Open ZotiBox"; Flags: nowait postinstall skipifsilent
