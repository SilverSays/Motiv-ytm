@echo off
setlocal
set "VENV=%LOCALAPPDATA%\YTMusicNativo\venv"
if not exist "%VENV%\Scripts\pythonw.exe" (
    call "%~dp0instalar.bat" || exit /b 1
)
cd /d "%~dp0"
start "" "%VENV%\Scripts\pythonw.exe" -m ytm_native
endlocal
