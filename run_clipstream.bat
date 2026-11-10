@echo off
rem Abre ClipStream en su ventana de escritorio.
cd /d "%~dp0"
".venv\Scripts\pythonw.exe" -m app.desktop
