; Instalador de ClipStream (Inno Setup 6).
;   1) .venv\Scripts\pyinstaller installer\clipstream.spec --noconfirm --distpath build\dist --workpath build\work
;   2) "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" installer\clipstream.iss
; Resultado: build\installer\ClipStream-Setup-<versión>.exe
;
; Se instala por usuario (sin permisos de administrador) en %LOCALAPPDATA%\Programs\ClipStream.
; Los datos (clips, base de datos, modelos, runtime CUDA) viven aparte en %LOCALAPPDATA%\ClipStream
; y NO se borran al desinstalar: reinstalar no obliga a descargar otra vez ~3 GB.

#define AppName "ClipStream"
#define AppVersion "0.6.1"
#define AppExe "ClipStream.exe"

[Setup]
AppId={{6B1F3C2E-7A4D-4C1B-9F2E-C1A9B5D0E7F3}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=ClipStream (uso personal)
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir=..\build\installer
OutputBaseFilename={#AppName}-Setup-{#AppVersion}
SetupIconFile=clipstream.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes
WizardStyle=modern
CloseApplications=yes

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\build\dist\ClipStream\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Carpeta de datos de {#AppName}"; Filename: "{localappdata}\ClipStream"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[Messages]
spanish.WelcomeLabel2=Se instalará [name/ver] en tu equipo.%n%nLa primera vez que lo abras descargará los componentes pesados (runtime de NVIDIA CUDA, ffmpeg y modelos de IA, unos 3 GB) con una barra de progreso. Para títulos y traducciones con IA necesitarás además Ollama (ollama.com).
