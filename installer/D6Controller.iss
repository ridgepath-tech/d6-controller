#ifndef AppVersion
  #define AppVersion "0.3.0"
#endif
#ifndef SourceDir
  #error SourceDir must point to the PyInstaller bundle.
#endif
#ifndef OutputDir
  #define OutputDir "."
#endif

[Setup]
AppId={{8B3CFD26-6F53-4B68-9A94-4E8C7FEA6E1D}
AppName=RidgePath D6 Controller
AppVersion={#AppVersion}
AppPublisher=RidgePath Technologies
AppPublisherURL=https://github.com/ridgepath-tech/d6-controller
AppSupportURL=https://github.com/ridgepath-tech/d6-controller/issues
DefaultDirName={localappdata}\Programs\RidgePath\D6 Controller
DefaultGroupName=RidgePath D6 Controller
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=D6ControllerSetup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=RidgePath D6 Controller
Uninstallable=yes
CloseApplications=no

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\D6 Controller"; Filename: "{app}\D6Controller.exe"; Parameters: "--open-browser"
Name: "{userdesktop}\D6 Controller"; Filename: "{app}\D6Controller.exe"; Parameters: "--open-browser"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ""{app}\register-startup.ps1"""; StatusMsg: "Registering D6 Controller startup..."; Flags: runhidden waituntilterminated
Filename: "{app}\D6Controller.exe"; Parameters: "--open-browser"; Description: "Launch D6 Controller"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "powershell.exe"; Parameters: "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ""{app}\unregister-startup.ps1"""; Flags: runhidden waituntilterminated; RunOnceId: "RemoveD6ControllerStartup"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\_internal"
