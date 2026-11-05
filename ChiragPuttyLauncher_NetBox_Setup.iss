; Inno Setup package for Chirag Putty Launcher.
; Build the EXE first (pyinstaller ChiragPuttyLauncher_NetBox.spec), then compile this script.
; The AppId and EXE name are unchanged from 1.x so this upgrades an existing install in place.

#define AppName "Chirag Putty Launcher"
#define AppVersion "2.0.0"
#define AppPublisher "Chirag Putty Launcher"
#define AppExeName "ChiragPuttyLauncher_NetBox.exe"

[Setup]
AppId={{8E5A1F9A-79C7-4475-9C82-C131BDA5A1D4}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\Chirag Putty Launcher
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=installer_output
OutputBaseFilename=ChiragPuttyLauncher_Setup_{#AppVersion}
SetupIconFile=launcher_ui\app.ico
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
UninstallDisplayName={#AppName}
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "dist\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[Code]
const
  WebView2Key = 'Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  WebView2Download = 'https://go.microsoft.com/fwlink/p/?LinkId=2124703';

function HasWebView2(): Boolean;
var
  Version: String;
begin
  Result :=
    (RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\' + WebView2Key, 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0')) or
    (RegQueryStringValue(HKLM, 'SOFTWARE\' + WebView2Key, 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0')) or
    (RegQueryStringValue(HKCU, 'Software\' + WebView2Key, 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0'));
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ErrorCode: Integer;
begin
  if (CurStep = ssPostInstall) and not HasWebView2() and not WizardSilent() then
  begin
    if MsgBox('Chirag Putty Launcher needs the Microsoft Edge WebView2 Runtime, which is not installed on this PC.' + #13#10#13#10 +
              'Open the Microsoft download page now?', mbConfirmation, MB_YESNO) = IDYES then
      ShellExec('open', WebView2Download, '', '', SW_SHOWNORMAL, ewNoWait, ErrorCode);
  end;
end;
