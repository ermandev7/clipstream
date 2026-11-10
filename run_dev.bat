@echo off
rem Desarrollo: motor en http://127.0.0.1:8765 y Vite en http://localhost:5173
cd /d "%~dp0"
start "ClipStream API" cmd /k ".venv\Scripts\python.exe -m app.main"
cd frontend && npm run dev
