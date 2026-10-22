#define MyAppName "抖店自动上款"
#define MyAppBuild "v49"
#define MyAppVersion "2026.08.19.49"
#define MyAppExeName "DouyinDraftAutomation.exe"

[Setup]
AppId={{D3D1C0D8-286B-46F0-9347-52D171BB83A2}
AppName={#MyAppName}
AppVerName={#MyAppName} {#MyAppBuild}
AppVersion={#MyAppVersion}
AppPublisher=Try-onApp
DefaultDirName={localappdata}\Programs\DouyinDraftAutomation
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
OutputDir=release
OutputBaseFilename=抖店自动上款安装包-{#MyAppBuild}-{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupLogging=yes

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："; Flags: checkedonce

[Files]
Source: "dist\DouyinDraftAutomation\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "config.example.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "config.example.json"; DestDir: "{app}"; DestName: "config.json"; Flags: onlyifdoesntexist
Source: "assets\wash-lable.jpg"; DestDir: "{app}\assets"; Flags: ignoreversion

[Dirs]
Name: "{app}\work"
Name: "{app}\edge-profile"
Name: "{app}\edge-profile-shop2"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "运行 {#MyAppName}"; Flags: nowait postinstall skipifsilent
