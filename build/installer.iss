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

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked
Name: "autostart";   Description: "Start automatically when I log in"; GroupDescription: "Startup:"; Flags: unchecked

[Files]
Source: "..\dist\MakoSync.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\docs\ingest-contract.md";    DestDir: "{app}\docs"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: autostart

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
