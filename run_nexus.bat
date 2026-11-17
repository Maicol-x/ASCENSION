@echo off
cd /d "%~dp0"
echo ======================================================
echo   NEXUS / ASCENSION - Iniciando servidores locales
echo ======================================================
echo 1. Arrancando servidor proxy Python (puerto 8787)...
start /B "" "C:\Users\Janus\AppData\Local\Python\bin\python.exe" scripts\server.py > proxy_output.log 2>&1
echo 2. Arrancando servidor frontend Vite...
echo.
npm run dev
pause
