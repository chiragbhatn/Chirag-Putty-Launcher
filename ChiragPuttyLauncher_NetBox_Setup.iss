; Inno Setup package for the NetBox-enabled Chirag Putty Launcher only.

#define AppName "Chirag Putty Launcher"
#define AppVersion "1.0.0"
#define AppPublisher "Chirag Putty Launcher"
#define AppExeName "ChiragPuttyLauncher_NetBox.exe"

[Setup]
AppId={{8E5A1F9A-79C7-4475-9C82-C131BDA5A1D4}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\Chirag Putty Launcher
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=installer_output
OutputBaseFilename=ChiragPuttyLauncher_NetBox_Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
UninstallDisplayName={#AppName} (NetBox)

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "staged_netbox_v7\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
