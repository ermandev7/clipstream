@echo off
rem Compila ClipStream y su instalador: build\installer\ClipStream-Setup-<versión>.exe
setlocal
cd /d "%~dp0\.."

echo [1/4] Tests
".venv\Scripts\python.exe" -m pytest -q || goto :error

echo [2/4] Interfaz
pushd frontend
call npm run build || (popd & goto :error)
popd

echo [3/4] Ejecutable (PyInstaller)
".venv\Scripts\pyinstaller.exe" installer\clipstream.spec --noconfirm --distpath build\dist --workpath build\work || goto :error
"build\dist\ClipStream\ClipStream.exe" --selftest || (echo El autodiagnostico ha fallado: revisa %%LOCALAPPDATA%%\ClipStream\logs & goto :error)

echo [4/4] Instalador (Inno Setup)
set ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe
if not exist "%ISCC%" set ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe
"%ISCC%" installer\clipstream.iss || goto :error

echo.
echo Listo: build\installer
exit /b 0

:error
echo.
echo Error en la compilacion.
exit /b 1
