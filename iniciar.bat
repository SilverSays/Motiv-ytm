@echo off
setlocal
set "VENV=%LOCALAPPDATA%\YTMusicNativo\venv"
if not exist "%VENV%\Scripts\pythonw.exe" (
    call "%~dp0instalar.bat" || exit /b 1
)
rem Con pythonw no hay consola: si falta una dependencia la app no abriria y no
rem diria nada. Se comprueba antes con python.exe, que si puede mostrar el error.
"%VENV%\Scripts\python.exe" -c "import PySide6.QtWidgets, ytmusicapi, requests" 2>nul
if errorlevel 1 (
    echo Faltan dependencias en el entorno; se instalan ahora.
    call "%~dp0instalar.bat" || exit /b 1
)
cd /d "%~dp0"
start "" "%VENV%\Scripts\pythonw.exe" -m ytm_native
endlocal
