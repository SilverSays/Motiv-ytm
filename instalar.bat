@echo off
rem Crea el entorno de Python en %LOCALAPPDATA% (fuera de la carpeta del proyecto) e instala las dependencias.
setlocal
set "APPHOME=%LOCALAPPDATA%\YTMusicNativo"
set "VENV=%APPHOME%\venv"
if not exist "%APPHOME%" mkdir "%APPHOME%"
if not exist "%VENV%\Scripts\python.exe" (
    py -3 -m venv "%VENV%"
    if errorlevel 1 (
        echo.
        echo No se pudo crear el entorno. Instala Python 3.10+ desde python.org
        pause
        exit /b 1
    )
)
"%VENV%\Scripts\python.exe" -m pip install --disable-pip-version-check -r "%~dp0requirements.txt"
if errorlevel 1 (
    echo.
    echo No se pudieron instalar las dependencias: revisa el error de pip de arriba
    echo y la conexion a internet, y vuelve a ejecutar instalar.bat.
    pause
    exit /b 1
)
"%VENV%\Scripts\python.exe" -c "import PySide6.QtWidgets, ytmusicapi, requests"
if errorlevel 1 (
    echo.
    echo Las dependencias se instalaron pero no se pueden cargar ^(error de arriba^).
    echo Borra la carpeta "%VENV%" y vuelve a ejecutar instalar.bat.
    pause
    exit /b 1
)
echo.
echo Listo. Ejecuta iniciar.bat para abrir la aplicacion.
endlocal
