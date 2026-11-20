; Instalador de Motiv para Windows (Inno Setup 6.3 o posterior).
;
; Empaqueta la carpeta que genera PyInstaller (ytm_native.spec): el equipo de
; destino no necesita Python. Lo normal es compilarlo con
; construir_instalador.bat, que le pasa la versión y las rutas:
;
;   ISCC /DAppVersion=0.1.0 /DAppSource=<carpeta YTMusicNativo> ^
;        /DOutputDir=<salida> /DIconFile=<YTMusicNativo.ico> installer.iss
;
; Se instala por usuario (sin pedir permisos de administrador) en
; %LOCALAPPDATA%\Programs; el asistente deja elegir instalar para todos.
; Los datos (biblioteca, credenciales, yt-dlp/deno/mpv) viven aparte, en
; %LOCALAPPDATA%\YTMusicNativo: al desinstalar se pregunta si borrarlos.

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#ifndef AppSource
  #define AppSource "dist\YTMusicNativo"
#endif
#ifndef OutputDir
  #define OutputDir "dist"
#endif

#define AppName "Motiv"
#define AppExe "YTMusicNativo.exe"
#define AppURL "https://github.com/SilverSays/Proyecto-YT-Music-Nativo"
; Misma clave que usa la app para «Iniciar con Windows» (ui/tray.py).
#define RunValue "YTMusicNativo"

[Setup]
AppId={{8C7E3F52-4A1B-4E7A-9C3D-2B5F6A7D9E10}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=SilverSays
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
LicenseFile=LICENSE
OutputDir={#OutputDir}
OutputBaseFilename=Motiv-{#AppVersion}-instalador
#ifdef IconFile
SetupIconFile={#IconFile}
#endif
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Si la app está abierta (también en la bandeja), se cierra antes de copiar.
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#AppSource}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; Una versión anterior pudo dejar módulos que esta ya no trae.
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep <> usPostUninstall then
    exit;
  { «Iniciar con Windows» apuntaba a este ejecutable: ya no existe. }
  RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', '{#RunValue}');
  DataDir := ExpandConstant('{localappdata}\YTMusicNativo');
  if UninstallSilent or not DirExists(DataDir) then
    exit;
  if MsgBox('¿Borrar también tus datos de Motiv?' + #13#10 + #13#10 +
            'Biblioteca, listas locales, historial, credenciales y los programas ' +
            'descargados (yt-dlp, deno, mpv), en:' + #13#10 + DataDir + #13#10 + #13#10 +
            'Si vas a volver a instalar la app, elige «No» para conservarlos.',
            mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
    DelTree(DataDir, True, True, True);
end;
