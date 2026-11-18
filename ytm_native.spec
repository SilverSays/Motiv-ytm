# -*- mode: python ; coding: utf-8 -*-
"""Especificación de PyInstaller: YT Music Nativo como programa autónomo.

Genera una carpeta ``YTMusicNativo`` con ``YTMusicNativo.exe`` y todo lo que
necesita (Python, PySide6, ytmusicapi, requests y los paquetes WinRT de los
controles multimedia): el equipo de destino no necesita Python.

Se construye con ``construir_instalador.bat`` o a mano:

    python -m PyInstaller --noconfirm --clean ytm_native.spec

Lo que NO va dentro, a propósito:

* ``yt-dlp``, ``deno`` y ``mpv``: se descargan en el primer arranque a
  ``%LOCALAPPDATA%\\YTMusicNativo\\bin`` (el aviso «Descargar ahora»).
  ``yt-dlp`` se actualiza solo y necesita una carpeta con escritura, que
  ``Program Files`` no da; y así el instalador pesa decenas de MB, no cientos.
* Credenciales, base de datos y cachés: viven en ``%LOCALAPPDATA%``.

El icono del ejecutable se dibuja aquí mismo con el código de la app (el mismo
que el de la bandeja), a todos los tamaños de Windows.
"""

import struct
import subprocess
import sys
from pathlib import Path

from PyInstaller import config as pyi_config
from PyInstaller.utils.hooks import collect_data_files, collect_submodules
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo,
)

ROOT = Path(SPECPATH)  # noqa: F821 - lo define PyInstaller
WORK = Path(pyi_config.CONF["workpath"])
APP_NAME = "YTMusicNativo"
sys.path.insert(0, str(ROOT))
from ytm_native import __version__  # noqa: E402

ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)

# Dibuja cada tamaño con Qt en un proceso aparte (sin ventanas) y escribe los
# PNG; aquí se juntan en un .ico.
_RENDER_ICON = r"""
import sys
from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QGuiApplication
app = QGuiApplication(sys.argv[:1])
from ytm_native.ui.tray import _icon_pixmap
out = sys.argv[1]
for px in map(int, sys.argv[2:]):
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    _icon_pixmap(px).toImage().save(buf, "PNG")
    buf.close()
    with open(f"{out}/{px}.png", "wb") as fh:
        fh.write(bytes(data))
"""


def build_icon(target: Path) -> Path:
    frames = target.parent / "icono"
    frames.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, "-c", _RENDER_ICON, str(frames), *map(str, ICON_SIZES)],
        cwd=ROOT, check=True,
    )
    images = [(px, (frames / f"{px}.png").read_bytes()) for px in ICON_SIZES]
    # .ico con imágenes PNG (Windows Vista en adelante): cabecera, directorio y datos.
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    directory, blobs = b"", b""
    for px, png in images:
        side = 0 if px >= 256 else px
        directory += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
        blobs += png
    target.write_bytes(header + directory + blobs)
    return target


def version_info() -> VSVersionInfo:
    numbers = tuple(int(n) for n in __version__.split("."))
    numbers = (numbers + (0, 0, 0, 0))[:4]
    text = ".".join(map(str, numbers))
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=numbers, prodvers=numbers),
        kids=[
            StringFileInfo([StringTable("0C0A04B0", [
                StringStruct("FileDescription", "YT Music Nativo"),
                StringStruct("FileVersion", text),
                StringStruct("InternalName", APP_NAME),
                StringStruct("OriginalFilename", f"{APP_NAME}.exe"),
                StringStruct("ProductName", "YT Music Nativo"),
                StringStruct("ProductVersion", __version__),
                StringStruct("LegalCopyright", "GPL-3.0"),
            ])]),
            VarFileInfo([VarStruct("Translation", [0x0C0A, 1200])]),
        ],
    )


WORK.mkdir(parents=True, exist_ok=True)
icon = build_icon(WORK / f"{APP_NAME}.ico")

# ytmusicapi lleva sus traducciones como datos; los paquetes WinRT (traen su
# propio gancho de PyInstaller) se recogen enteros por si un módulo solo se
# importa de forma perezosa.
datas = collect_data_files("ytmusicapi") + [(str(ROOT / "LICENSE"), ".")]
hiddenimports = collect_submodules("winrt") + collect_submodules("ytmusicapi")

a = Analysis(  # noqa: F821
    [str(ROOT / "ytm_native" / "__main__.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "_pytest", "IPython", "matplotlib", "numpy", "PySide6.QtWebEngineCore"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    icon=str(icon),
    version=version_info(),
    console=False,          # app de ventana: sin consola, como pythonw
    upx=False,              # UPX dispara falsos positivos de los antivirus
)
coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    name=APP_NAME,
    upx=False,
)
