#define MyAppName "Flowing Piano"
#define MyAppExeName "AI-Piano-Studio.exe"

#ifndef Version
  #define Version "0.0.0"
#endif

#ifndef PortableDir
  #define PortableDir "..\dist\AI-Piano-Studio"
#endif

#ifndef OutputDir
  #define OutputDir "..\dist\release"
#endif

[Setup]
AppId={{B5E6E3B9-4E6E-4E50-9E80-3E0A7D2F5A11}
AppName={#MyAppName}
SetupIconFile=..\src\ai_piano_studio\resources\brand\flowing-piano.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
AppVersion={#Version}
AppPublisher=AI Piano Studio
DefaultDirName={localappdata}\Programs\AI Piano Studio
DefaultGroupName=AI Piano Studio
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=AI-Piano-Studio-Setup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
Uninstallable=yes
UninstallDisplayName=AI Piano Studio

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "{#PortableDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
#include "obsolete-runtime-files.iss"
; Remove only obsolete executable/runtime files from a verified prior install.
; Local artwork and user documents are deliberately retained.
Type: files; Name: "{app}\_internal\PySide6\Qt6WebEngine*.dll"; Check: IsOwnedUpgrade
Type: files; Name: "{app}\_internal\PySide6\Qt6Pdf*.dll"; Check: IsOwnedUpgrade
Type: files; Name: "{app}\_internal\PySide6\Qt6WebView*.dll"; Check: IsOwnedUpgrade
Type: files; Name: "{app}\_internal\PySide6\qml\QtWebEngine\*.dll"; Check: IsOwnedUpgrade
Type: files; Name: "{app}\_internal\PySide6\qml\QtWebEngine\ControlsDelegates\*.dll"; Check: IsOwnedUpgrade
Type: files; Name: "{app}\_internal\PySide6\qml\QtWebView\*.dll"; Check: IsOwnedUpgrade
Type: files; Name: "{app}\_internal\PySide6\qml\QtQuick\Pdf\*.dll"; Check: IsOwnedUpgrade
Type: files; Name: "{app}\_internal\PySide6\plugins\imageformats\qpdf.dll"; Check: IsOwnedUpgrade
Type: files; Name: "{app}\_internal\PySide6\plugins\webview\*.dll"; Check: IsOwnedUpgrade
Type: files; Name: "{app}\_internal\PySide6\QtWebEngineProcess.exe"; Check: IsOwnedUpgrade
Type: files; Name: "{app}\_internal\api-ms-win-*.dll"; Check: IsOwnedUpgrade
Type: files; Name: "{app}\_internal\ucrtbase.dll"; Check: IsOwnedUpgrade
Type: files; Name: "{app}\_internal\icuuc.dll"; Check: IsOwnedUpgrade
Type: files; Name: "{app}\_internal\icudt78.dll"; Check: IsOwnedUpgrade
Type: files; Name: "{app}\_internal\PySide6\icuuc.dll"; Check: IsOwnedUpgrade
Type: files; Name: "{app}\_internal\PySide6\icudt78.dll"; Check: IsOwnedUpgrade

[Icons]
Name: "{group}\Flowing Piano"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\Flowing Piano"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch AI Piano Studio"; Flags: nowait postinstall skipifsilent unchecked

[Code]
var
  OwnedUpgrade: Boolean;

function PlainDirectory(const Path: String): Boolean;
var
  Entry: TFindRec;
begin
  Result := not FileExists(Path);
  if not DirExists(Path) then exit;
  Result := False;
  if FindFirst(Path, Entry) then begin
    try
      Result := (Entry.Attributes and FILE_ATTRIBUTE_REPARSE_POINT) = 0;
    finally
      FindClose(Entry);
    end;
  end;
end;

function IsOwnedUpgrade: Boolean;
begin
  Result := OwnedUpgrade;
end;

function CanRemoveObsoleteRuntime(const RelativeFile: String): Boolean;
var
  AppPath, ParentPath: String;
begin
  Result := False;
  if not OwnedUpgrade then exit;
  AppPath := ExpandFileName(ExpandConstant('{app}'));
  ParentPath := ExtractFileDir(AddBackslash(AppPath) + RelativeFile);
  while CompareText(AddBackslash(ParentPath), AddBackslash(AppPath)) <> 0 do begin
    if Length(ParentPath) <= Length(AppPath) then exit;
    if not PlainDirectory(ParentPath) then exit;
    ParentPath := ExtractFileDir(ParentPath);
  end;
  Result := PlainDirectory(AppPath);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  PreviousPath, AppPath: String;
begin
  Result := '';
  OwnedUpgrade := False;
  AppPath := ExpandConstant('{app}');
  if not RegQueryStringValue(HKCU,
    'Software\Microsoft\Windows\CurrentVersion\Uninstall\{B5E6E3B9-4E6E-4E50-9E80-3E0A7D2F5A11}_is1',
    'InstallLocation', PreviousPath) then exit;
  if CompareText(AddBackslash(ExpandFileName(PreviousPath)), AddBackslash(ExpandFileName(AppPath))) <> 0 then exit;
  if not FileExists(AddBackslash(AppPath) + '{#MyAppExeName}') then exit;
  if not (PlainDirectory(AppPath)
    and PlainDirectory(AppPath + '\_internal')
    and PlainDirectory(AppPath + '\_internal\PySide6')
    and PlainDirectory(AppPath + '\_internal\PySide6\qml')
    and PlainDirectory(AppPath + '\_internal\PySide6\qml\QtWebEngine')
    and PlainDirectory(AppPath + '\_internal\PySide6\qml\QtWebEngine\ControlsDelegates')
    and PlainDirectory(AppPath + '\_internal\PySide6\qml\QtWebView')
    and PlainDirectory(AppPath + '\_internal\PySide6\qml\QtQuick')
    and PlainDirectory(AppPath + '\_internal\PySide6\qml\QtQuick\Pdf')
    and PlainDirectory(AppPath + '\_internal\PySide6\plugins')
    and PlainDirectory(AppPath + '\_internal\PySide6\plugins\imageformats')
    and PlainDirectory(AppPath + '\_internal\PySide6\plugins\webview')) then begin
    Result := 'The previous installation contains directory links. Install to a new directory to preserve them.';
    exit;
  end;
  OwnedUpgrade := True;
  Log('Validated prior application directory for obsolete runtime cleanup: ' + AppPath);
end;
