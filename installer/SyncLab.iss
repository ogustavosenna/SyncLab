; SyncLab Inno Setup Script
; Generates: SyncLab_v1.3.1_Setup.exe
;
; Prerequisites:
;   1. Install Inno Setup 6 from https://jrsoftware.org/isdl.php
;   2. Build with PyInstaller first: python -m PyInstaller synclab.spec --noconfirm
;   3. Compile this script: ISCC.exe installer\SyncLab.iss
;      Or open in Inno Setup GUI and click Build > Compile

#define MyAppName "SyncLab"
#define MyAppVersion "1.3.1"
#define MyAppPublisher "SyncLab"
#define MyAppURL "https://github.com/synclab"
#define MyAppExeName "SyncLab.exe"

[Setup]
; App identity
AppId={{B7E3F8A1-5C2D-4E6F-9A1B-3D7C8E2F4A5B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}

; Install location
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes

; Output
OutputDir=..\dist
OutputBaseFilename=SyncLab_v{#MyAppVersion}_Setup
Compression=lzma2/ultra64
SolidCompression=yes

; Visual
SetupIconFile=..\synclab\app\static\img\icon.ico
WizardStyle=modern

; Privileges (per-user install, no admin required)
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

; Misc
AllowNoIcons=yes
LicenseFile=
UninstallDisplayIcon={app}\{#MyAppExeName}

; Minimum Windows version (Windows 10+)
MinVersion=10.0

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Copy the entire PyInstaller dist folder
Source: "..\dist\SyncLab\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Start Menu shortcut
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
; Desktop shortcut (optional)
Name: "{commondesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
; Uninstall shortcut
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"

[Run]
; Option to launch after install
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Clean up any temp/cache files created at runtime
Type: filesandordirs; Name: "{app}\temp"
Type: filesandordirs; Name: "{app}\__pycache__"
