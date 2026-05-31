; Inno Setup script for MakoSync.
; Compile with ISCC.exe (Inno Setup 6). Produces an installer .exe in dist/.
; Requires the PyInstaller .exe already built (build\build_exe.ps1).

#define MyAppName "MakoSync"
; CI passes the real version from the git tag via /DMyAppVersion=x.y.z.
; The fallback only applies to ad-hoc local builds.
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0-dev"
#endif
#define MyAppPublisher "Makos Swim"
#define MyAppExeName "MakoSync.exe"

[Setup]
; New AppId for the MakoSync identity (was Makos DolphinSync). A fresh GUID means
; this installs as a distinct app rather than upgrading the old one.
AppId={{8F2A6C14-3B9E-4D77-AE51-9C0F2B7A4D63}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Per-user install: lands in %LocalAppData%\Programs, never prompts for admin
; (keeps it friction-free on Windows 11 Home where the user isn't an admin).
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=MakoSync-Setup-{#MyAppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\src\makosync\assets\mako.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
; In-app self-update hands off to a detached helper that waits for MakoSync to
; fully exit before running this installer, so the files are never locked. We
; keep CloseApplications=yes as a backstop for a manual reinstall over a running
; app; RestartApplications=no because the helper does the relaunch.
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked
Name: "autostart";   Description: "Start automatically when I log in"; GroupDescription: "Startup:"; Flags: unchecked

[Files]
; --onedir build: ship the whole PyInstaller folder (MakoSync.exe + _internal\).
Source: "..\dist\MakoSync\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\docs\ingest-contract.md"; DestDir: "{app}\docs"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: autostart

[Run]
; Interactive install: offer the usual "Launch MakoSync" checkbox. (Silent installs
; are driven by the self-update helper, which relaunches MakoSync itself, so no
; WizardSilent [Run] here, or we'd double-launch.)
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  // A running MakoSync.exe locks its own file, and Restart Manager can't reliably
  // close our tkinter window (it threw "couldn't close applications" + "DeleteFile
  // failed; Access is denied"). So close it ourselves before copying files: a
  // graceful WM_CLOSE first (lets it save settings + stop cleanly), then a hard
  // kill as a backstop. Harmless during a self-update, where it has already exited.
  Exec('taskkill.exe', '/IM MakoSync.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(1500);
  Exec('taskkill.exe', '/F /IM MakoSync.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(500);
  Result := '';
end;
