#ifndef DreamVersion
  #error Version required
#endif
[Setup]
AppId={{DDDD1989-902E-4A28-AB61-D2EAC2D8AA12}
AppName=造梦室
AppVersion={#DreamVersion}
AppPublisher=蒋啊讲
DefaultDirName={localappdata}\Programs\DreamRoom
DefaultGroupName=造梦室
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#ReleaseDir}
OutputBaseFilename=DreamStudio-Setup-x64
SetupIconFile={#ProductDir}\resources\app\brand\dreamroom.ico
UninstallDisplayIcon={app}\DreamRoom.exe
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
DisableProgramGroupPage=yes
[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
[Files]
Source: "{#ProductDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{autoprograms}\造梦室"; Filename: "{app}\DreamRoom.exe"; AppUserModelID: "com.jiangajiang.dreamroom"
Name: "{autodesktop}\造梦室"; Filename: "{app}\DreamRoom.exe"; AppUserModelID: "com.jiangajiang.dreamroom"
[Run]
Filename: "{app}\DreamRoom.exe"; Description: "启动造梦室"; Flags: nowait postinstall skipifsilent
