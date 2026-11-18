"""Rutas de la aplicación.

Todo lo que cambia en tiempo de ejecución (binarios auto-actualizables, base de
datos, credenciales, caché) vive en ``%LOCALAPPDATA%\\YTMusicNativo`` y no junto
al código: así la auto-actualización de ``yt-dlp.exe`` nunca choca con permisos
de ``Program Files`` y las credenciales no terminan sincronizadas en OneDrive.

``YTMN_HOME`` permite redirigir todo a otra carpeta (lo usan los tests).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "YTMusicNativo"
IS_WINDOWS = sys.platform == "win32"
_EXE = ".exe" if IS_WINDOWS else ""


def app_home() -> Path:
    override = os.environ.get("YTMN_HOME")
    if override:
        return Path(override)
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_DIR_NAME
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / APP_DIR_NAME


def bin_dir() -> Path:
    # YTMN_BIN_DIR / YTMN_CRED_DIR permiten probar con una carpeta de datos
    # desechable (YTMN_HOME) reutilizando los binarios y credenciales reales.
    override = os.environ.get("YTMN_BIN_DIR")
    return Path(override) if override else app_home() / "bin"


def cred_dir() -> Path:
    override = os.environ.get("YTMN_CRED_DIR")
    return Path(override) if override else app_home() / "credenciales"


def cache_dir() -> Path:
    return app_home() / "cache"


def log_dir() -> Path:
    return app_home() / "logs"


def fase0_dir() -> Path:
    return app_home() / "fase0"


def db_path() -> Path:
    return app_home() / "biblioteca.sqlite3"


def ytdlp_exe() -> Path:
    return bin_dir() / f"yt-dlp{_EXE}"


def ytdlp_bak() -> Path:
    return bin_dir() / "yt-dlp.bak"


def deno_exe() -> Path:
    return bin_dir() / f"deno{_EXE}"


def mpv_exe() -> Path:
    return bin_dir() / f"mpv{_EXE}"


def browser_json() -> Path:
    """Cabeceras de ``ytmusicapi`` (antes llamado ``headers_auth.json``)."""
    return cred_dir() / "browser.json"


def cookies_txt() -> Path:
    """Cookies en formato Netscape para ``yt-dlp``."""
    return cred_dir() / "cookies.txt"


def firefox_login_profile() -> Path:
    """Perfil de Firefox desechable del inicio de sesión asistido."""
    return cache_dir() / "firefox_auth_profile"


def ensure_dirs() -> None:
    dirs = [app_home(), cache_dir(), log_dir()]
    # Las carpetas redirigidas por variable de entorno son ajenas: no se crean.
    if not os.environ.get("YTMN_BIN_DIR"):
        dirs.append(bin_dir())
    if not os.environ.get("YTMN_CRED_DIR"):
        dirs.append(cred_dir())
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
    # Cookies en claro que un cierre brusco dejó a medio borrar (las de un
    # yt-dlp de la app que siga vivo no se tocan).
    from ytm_native.core.credentials import cleanup_ephemeral

    cleanup_ephemeral(cred_dir())


def secure_credentials() -> None:
    """Al arrancar: cifra las credenciales que sigan en claro y borra el perfil
    de Firefox que un inicio de sesión interrumpido pudo dejar (tiene las
    cookies de la cuenta en claro).

    Con ``YTMN_CRED_DIR`` la carpeta es ajena (pruebas, otra instalación): se
    lee pero no se reescribe."""
    import shutil

    from ytm_native.core.credentials import migrate_plaintext

    if not os.environ.get("YTMN_CRED_DIR"):
        migrate_plaintext([browser_json(), cookies_txt()])
    leftover = firefox_login_profile()
    if leftover.exists():
        shutil.rmtree(leftover, ignore_errors=True)
