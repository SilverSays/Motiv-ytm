@echo off
rem Crea el entorno de Python en %LOCALAPPDATA% (fuera de la carpeta del proyecto) e instala las dependencias.
setlocal
set "APPHOME=%LOCALAPPDATA%\YTMusicNativo"
set "VENV=%APPHOME%\venv"
if not exist "%APPHOME%" mkdir "%APPHOME%"
if not exist "%VENV%\Scripts\python.exe" (
    py -3 -m venv "%VENV%" || (echo No se pudo crear el entorno. Instala Python 3.10+ desde python.org & exit /b 1)
)
"%VENV%\Scripts\python.exe" -m pip install --disable-pip-version-check -r "%~dp0requirements.txt" || exit /b 1
echo.
echo Listo. Ejecuta iniciar.bat para abrir la aplicacion.
endlocal
