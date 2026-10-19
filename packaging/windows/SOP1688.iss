#ifndef SourceDir
  #error SourceDir is required. Run build-installer.ps1 instead of compiling this file directly.
#endif

#ifndef OutputDir
  #define OutputDir "."
#endif

#ifndef AppVersion
  #define AppVersion "0.2.0"
#endif

#define AppName "SOP 1688 选品工作台"
#define AppPublisher "SOP"
#define AppExeName "runtime\python\pythonw.exe"

[Setup]
AppId={{BE74943E-1D5E-4EC8-AD57-9C9B358D5A87}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\SOP1688
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=SOP1688-Setup-x64
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupLogging=yes
CloseApplications=no
RestartApplications=no
UninstallDisplayName={#AppName}
VersionInfoVersion={#AppVersion}
VersionInfoDescription={#AppName} 安装程序
VersionInfoCompany={#AppPublisher}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："; Flags: checkedonce
Name: "autostart"; Description: "登录 Windows 后自动启动工作台"; GroupDescription: "自动启动："; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\启动 SOP 1688 工作台"; Filename: "{app}\{#AppExeName}"; Parameters: """{app}\launcher.py"" --start"; WorkingDir: "{app}"
Name: "{group}\打开 SOP 1688 工作台"; Filename: "http://127.0.0.1:8765/"
Name: "{group}\停止 SOP 1688 工作台"; Filename: "{app}\{#AppExeName}"; Parameters: """{app}\launcher.py"" --stop"; WorkingDir: "{app}"
Name: "{group}\卸载 SOP 1688 工作台"; Filename: "{uninstallexe}"
Name: "{autodesktop}\SOP 1688 工作台"; Filename: "{app}\{#AppExeName}"; Parameters: """{app}\launcher.py"" --start"; WorkingDir: "{app}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "SOP1688Workbench"; ValueData: """{app}\{#AppExeName}"" ""{app}\launcher.py"" --start --quiet"; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\{#AppExeName}"; Parameters: """{app}\launcher.py"" --start"; WorkingDir: "{app}"; Description: "启动 SOP 1688 工作台"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\{#AppExeName}"; Parameters: """{app}\launcher.py"" --stop --quiet"; WorkingDir: "{app}"; Flags: runhidden waituntilterminated; RunOnceId: "StopSOP1688Workbench"

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
  LauncherPath: String;
  PythonwPath: String;
begin
  Result := '';
  LauncherPath := ExpandConstant('{app}\launcher.py');
  PythonwPath := ExpandConstant('{app}\{#AppExeName}');
  if FileExists(LauncherPath) and FileExists(PythonwPath) then
  begin
    Exec(PythonwPath, '"' + LauncherPath + '" --stop --quiet', ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;
end;
