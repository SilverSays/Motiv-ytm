@echo off
rem Construye Motiv como programa autonomo (PyInstaller) y su
rem instalador (Inno Setup). El resultado no necesita Python en el equipo
rem donde se instale.
rem
rem Requisitos solo para construir: Python 3.10+ (lanzador "py") e Inno Setup
rem 6.3+ (https://jrsoftware.org/isdl.php). Sin Inno Setup se queda en la
rem version portable (la carpeta con YTMusicNativo.exe).
setlocal
cd /d "%~dp0"

rem Todo lo temporal va fuera de la carpeta del proyecto (y de OneDrive).
set "BUILDHOME=%LOCALAPPDATA%\YTMusicNativo-construccion"
set "BVENV=%BUILDHOME%\venv"
set "WORK=%BUILDHOME%\trabajo"
set "DIST=%BUILDHOME%\dist"
set "OUT=%~dp0dist"

echo [1/4] Entorno de construccion en "%BVENV%"
if not exist "%BVENV%\Scripts\python.exe" (
    py -3 -m venv "%BVENV%"
    if errorlevel 1 goto :sin_python
)
"%BVENV%\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt "pyinstaller>=6.10"
if errorlevel 1 goto :sin_dependencias

echo [2/4] Empaquetando con PyInstaller
"%BVENV%\Scripts\python.exe" -m PyInstaller --noconfirm --clean --workpath "%WORK%" --distpath "%DIST%" ytm_native.spec
if errorlevel 1 goto :fallo_pyinstaller

set "VERSION="
rem "call" evita que cmd quite las comillas de la ruta al ejecutar la orden.
for /f "delims=" %%v in ('call "%BVENV%\Scripts\python.exe" -c "import ytm_native; print(ytm_native.__version__)"') do set "VERSION=%%v"
if not defined VERSION goto :sin_version

echo [3/4] Buscando Inno Setup
set "ISCC="
for %%p in ("%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles%\Inno Setup 6\ISCC.exe" "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe") do (
    if exist "%%~p" set "ISCC=%%~p"
)
if not defined ISCC (
    for /f "delims=" %%p in ('where ISCC.exe 2^>nul') do set "ISCC=%%p"
)
if not defined ISCC goto :sin_inno

echo [4/4] Compilando el instalador
if not exist "%OUT%" mkdir "%OUT%"
"%ISCC%" /Q "/DAppVersion=%VERSION%" "/DAppSource=%DIST%\YTMusicNativo" "/DOutputDir=%OUT%" "/DIconFile=%WORK%\ytm_native\YTMusicNativo.ico" installer.iss
if errorlevel 1 goto :fallo_inno

echo.
echo Listo: "%OUT%\Motiv-%VERSION%-instalador.exe"
echo Version portable: "%DIST%\YTMusicNativo"
endlocal
exit /b 0

:sin_python
echo.
echo No se pudo crear el entorno de construccion. Instala Python 3.10+ desde python.org
goto :fallo

:sin_dependencias
echo.
echo No se pudieron instalar las dependencias o PyInstaller: revisa el error de pip
echo de arriba y la conexion a internet.
goto :fallo

:fallo_pyinstaller
echo.
echo PyInstaller no pudo empaquetar la app: revisa el error de arriba.
goto :fallo

:sin_version
echo.
echo No se pudo leer la version de ytm_native.
goto :fallo

:sin_inno
echo.
echo No se encontro Inno Setup 6 ^(ISCC.exe^): no se genera el instalador.
echo La version portable ya esta lista en "%DIST%\YTMusicNativo".
echo Para el instalador, instala Inno Setup desde https://jrsoftware.org/isdl.php
echo y vuelve a ejecutar este archivo.
endlocal
exit /b 0

:fallo_inno
echo.
echo Inno Setup no pudo compilar el instalador: revisa el error de arriba.
goto :fallo

:fallo
pause
endlocal
exit /b 1
