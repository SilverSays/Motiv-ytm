"""Inicio de sesión asistido con un perfil de Firefox desechable.

En lugar de copiar cabeceras desde las herramientas de desarrollo:

1. Se abre Firefox con un perfil nuevo y vacío (``-profile <carpeta>
   -no-remote``) en music.youtube.com. Es una ventana aparte: no ve ni toca las
   pestañas, cuentas o extensiones del Firefox habitual del usuario.
2. El usuario inicia sesión con la cuenta secundaria y cierra esa ventana.
3. La app lee ``cookies.sqlite`` del perfil en solo lectura, toma las cookies
   de ``youtube.com``, **borra el perfil entero** (también si algo falla o se
   cancela: un perfil reutilizado seguiría rotando las cookies por su cuenta e
   invalidaría la copia de la app) y comprueba la sesión con ytmusicapi.
4. Si vale, guarda las cabeceras de ytmusicapi y el ``cookies.txt`` de yt-dlp,
   cifrados con DPAPI (``core/credentials.py``).

Por qué Firefox y no Chrome: las versiones recientes de Chrome ligan la sesión
al dispositivo (*Device Bound Session Credentials*: cookies de vida corta que
solo ese Chrome puede renovar) y cifran sus cookies con una clave que solo
Chrome puede usar (cifrado *app-bound*, DPAPI «v20»). Una sesión sacada de
Chrome deja de valer en pocas horas fuera de él.
"""

from __future__ import annotations

import configparser
import json
import logging
import os
import shutil
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QObject, QProcess, Signal

from ytm_native.core import credentials, paths
from ytm_native.core.i18n import tr

log = logging.getLogger("ytmn.auth")

START_URL = "https://music.youtube.com"
ORIGIN = "https://music.youtube.com"
APP_PATHS_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\firefox.exe"
# Las de la sesión de Google; ``__Secure-3PAPISID`` es imprescindible:
# ytmusicapi firma cada petición con ella.
ESSENTIAL_COOKIES = ("SAPISID", "__Secure-3PAPISID", "HSID", "SSID", "APISID", "SID",
                     "__Secure-3PSID", "LOGIN_INFO")
REQUIRED_COOKIES = ("__Secure-3PAPISID", "SAPISID")
DEFAULT_FIREFOX_VERSION = "140.0"
_NOT_RUNNING = QProcess.ProcessState.NotRunning
_FAILED_TO_START = QProcess.ProcessError.FailedToStart

# Perfil ligero: sin pantallas de bienvenida, sin comprobar el navegador por
# defecto, sin telemetría ni sincronización, sin guardar contraseñas.
_USER_PREFS = {
    "browser.shell.checkDefaultBrowser": False,
    "browser.startup.homepage_override.mstone": "ignore",
    "startup.homepage_welcome_url": "",
    "startup.homepage_welcome_url.additional": "",
    "browser.aboutwelcome.enabled": False,
    "datareporting.policy.dataSubmissionEnabled": False,
    "datareporting.healthreport.uploadEnabled": False,
    "toolkit.telemetry.reportingpolicy.firstRun": False,
    "app.update.enabled": False,
    "identity.fxaccounts.enabled": False,
    "signon.rememberSignons": False,
    "extensions.pocket.enabled": False,
    "browser.sessionstore.resume_from_crash": False,
    "browser.privatebrowsing.autostart": False,
}


class LoginError(Exception):
    """El inicio de sesión no dio una sesión válida (mensaje para el usuario)."""


# ---------------------------------------------------------------------------
# Encontrar Firefox
# ---------------------------------------------------------------------------
def _default_registry():
    if sys.platform != "win32":
        return None
    import winreg

    return winreg


def find_firefox_executable(registry=None, environ: Optional[dict] = None) -> Optional[Path]:
    """``firefox.exe`` según el registro de Windows (App Paths, del usuario y
    del equipo) o en sus carpetas de instalación habituales."""
    reg = registry if registry is not None else _default_registry()
    env = os.environ if environ is None else environ
    candidates: list[Path] = []
    if reg is not None:
        for root in (getattr(reg, "HKEY_CURRENT_USER", None), getattr(reg, "HKEY_LOCAL_MACHINE", None)):
            if root is None:
                continue
            try:
                with reg.OpenKey(root, APP_PATHS_KEY) as key:
                    value, _kind = reg.QueryValueEx(key, "")
                if isinstance(value, str) and value.strip():
                    candidates.append(Path(os.path.expandvars(value.strip().strip('"'))))
            except OSError:
                continue
    for var in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = env.get(var)
        if base:
            candidates.append(Path(base) / "Mozilla Firefox" / "firefox.exe")
    for path in candidates:
        if path.name.lower() == "firefox.exe" and path.is_file():
            return path
    return None


# ---------------------------------------------------------------------------
# Perfil desechable
# ---------------------------------------------------------------------------
def prepare_profile(folder: Path) -> Path:
    """Un perfil vacío (si quedaba uno de antes, se borra primero)."""
    destroy_profile(folder)
    folder.mkdir(parents=True)
    lines = [f"user_pref({json.dumps(name)}, {json.dumps(value)});" for name, value in _USER_PREFS.items()]
    (folder / "user.js").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return folder


def destroy_profile(folder: Path) -> None:
    """Borra el perfil entero; si Windows retiene algún archivo un instante
    (Firefox acaba de cerrar), se reintenta un par de veces."""
    for attempt in range(4):
        if not folder.exists():
            return
        shutil.rmtree(folder, ignore_errors=True)
        if not folder.exists():
            return
        time.sleep(0.25 * (attempt + 1))
    log.warning("el perfil de Firefox del inicio de sesión no se pudo borrar del todo; se borrará al arrancar")


def firefox_arguments(profile: Path) -> list[str]:
    # -no-remote y -new-instance: una instancia propia aunque el Firefox
    # habitual esté abierto. -wait-for-browser: el proceso lanzado espera a
    # que se cierre la ventana (si no, el lanzador de Firefox sale enseguida).
    return ["-profile", str(profile), "-no-remote", "-new-instance", "-wait-for-browser", START_URL]


# ---------------------------------------------------------------------------
# Leer la sesión
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class BrowserCookie:
    host: str
    name: str
    value: str
    path: str = "/"
    expiry: int = 0
    secure: bool = True
    http_only: bool = False


def _youtube_host(host: str) -> bool:
    bare = host.lstrip(".").lower()
    return bare == "youtube.com" or bare.endswith(".youtube.com")


def read_cookies(profile: Path, now: Optional[float] = None) -> list[BrowserCookie]:
    """Las cookies vigentes de youtube.com en ``cookies.sqlite``, abierto en
    solo lectura (``mode=ro``: el archivo no se modifica)."""
    db_path = profile / "cookies.sqlite"
    if not db_path.is_file():
        raise LoginError(tr("Firefox no guardó ninguna cookie: inicia sesión antes de cerrar la ventana."))
    now = time.time() if now is None else now
    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5)
    except sqlite3.Error as exc:
        raise LoginError(tr("No se pudieron leer las cookies de Firefox.")) from exc
    try:
        rows = conn.execute(
            "SELECT host, name, value, path, expiry, isSecure, isHttpOnly FROM moz_cookies "
            "WHERE host LIKE '%youtube.com'"
        ).fetchall()
    except sqlite3.Error as exc:
        raise LoginError(tr("No se pudieron leer las cookies de Firefox.")) from exc
    finally:
        conn.close()
    found = []
    for host, name, value, path, expiry, secure, http_only in rows:
        if not _youtube_host(str(host)) or not name:
            continue
        expiry = int(expiry or 0)
        if expiry > 10**11:          # algunas versiones lo guardan en milisegundos
            expiry //= 1000
        if expiry and expiry < now:
            continue
        found.append(BrowserCookie(str(host), str(name), str(value or ""), str(path or "/"),
                                   expiry, bool(secure), bool(http_only)))
    return found


def cookie_header(cookies: list[BrowserCookie]) -> str:
    """Cabecera ``Cookie`` para music.youtube.com: una entrada por nombre (la
    del dominio más concreto gana, como haría el navegador)."""
    chosen: dict[str, BrowserCookie] = {}
    for cookie in sorted(cookies, key=lambda c: len(c.host.lstrip("."))):
        chosen[cookie.name] = cookie
    return "; ".join(f"{c.name}={c.value}" for c in chosen.values())


def netscape_cookies(cookies: list[BrowserCookie]) -> str:
    """Las cookies en formato Netscape, el que lee yt-dlp."""
    lines = ["# Netscape HTTP Cookie File", "# Generado por Motiv", ""]
    for c in cookies:
        host = c.host if not c.http_only else f"#HttpOnly_{c.host}"
        sub = "TRUE" if c.host.startswith(".") else "FALSE"
        lines.append("\t".join([host, sub, c.path or "/", "TRUE" if c.secure else "FALSE",
                                str(c.expiry), c.name, c.value]))
    return "\n".join(lines) + "\n"


def firefox_user_agent(profile: Path) -> str:
    """El User-Agent de la versión de Firefox que creó el perfil: las
    peticiones de la app se parecen a las del navegador de la sesión."""
    version = DEFAULT_FIREFOX_VERSION
    parser = configparser.ConfigParser()
    try:
        parser.read(profile / "compatibility.ini", encoding="utf-8")
        raw = parser.get("Compatibility", "LastVersion", fallback="")
        major = raw.split("_", 1)[0].split(".", 1)[0]
        if major.isdigit():
            version = f"{major}.0"
    except (configparser.Error, OSError):
        pass
    return f"Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:{version}) Gecko/20100101 Firefox/{version}"


def browser_headers(cookies: list[BrowserCookie], user_agent: str) -> dict:
    """Las cabeceras que guarda ``browser.json`` (las mismas que daría pegar
    las de una petición «browse»)."""
    names = {c.name for c in cookies}
    missing = [name for name in REQUIRED_COOKIES if name not in names]
    if missing:
        raise LoginError(
            tr("No se encontró la sesión de Google en Firefox (falta {names}). Inicia sesión en "
               "YouTube Music y espera a ver tu biblioteca antes de cerrar la ventana.",
               names=", ".join(missing))
        )
    from ytmusicapi.helpers import get_authorization

    header = cookie_header(cookies)
    sapisid = next(c.value for c in cookies if c.name == "__Secure-3PAPISID")
    from ytm_native.services.sync import sanitize_browser_headers

    return sanitize_browser_headers({
        "accept": "*/*",
        "accept-language": "es-ES,es;q=0.9",
        "content-type": "application/json",
        "cookie": header,
        "origin": ORIGIN,
        "x-origin": ORIGIN,
        "x-goog-authuser": "0",
        "user-agent": user_agent,
        # ytmusicapi reconoce la autenticación de navegador por esta cabecera y
        # la recalcula en cada petición a partir de la cookie.
        "authorization": get_authorization(f"{sapisid} {ORIGIN}"),
    })


@dataclass(frozen=True)
class CapturedSession:
    headers: dict
    cookies_txt: str
    cookie_count: int
    account: str = ""


def capture_session(profile: Path) -> CapturedSession:
    """Lee la sesión del perfil y lo destruye, pase lo que pase."""
    try:
        cookies = read_cookies(profile)
        headers = browser_headers(cookies, firefox_user_agent(profile))
        return CapturedSession(headers, netscape_cookies(cookies), len(cookies))
    finally:
        destroy_profile(profile)


def validate_session(headers: dict) -> str:
    """Comprueba con ytmusicapi que la sesión funciona; devuelve el nombre
    de la cuenta."""
    from ytmusicapi import YTMusic

    try:
        info = YTMusic(auth=dict(headers)).get_account_info()
    except Exception as exc:  # noqa: BLE001 - cualquier fallo es «sesión no válida»
        raise LoginError(tr("YouTube Music no aceptó la sesión: vuelve a intentarlo.")) from exc
    name = info.get("accountName") if isinstance(info, dict) else None
    if not name:
        raise LoginError(tr("YouTube Music no reconoce una cuenta con esa sesión."))
    return str(name)


def save_session(session: CapturedSession) -> None:
    """Guarda las cabeceras y las cookies, cifradas."""
    credentials.write_text(paths.browser_json(), json.dumps(session.headers, ensure_ascii=False, indent=1))
    credentials.write_text(paths.cookies_txt(), session.cookies_txt)


# ---------------------------------------------------------------------------
# El proceso completo
# ---------------------------------------------------------------------------
def _default_runner(fn: Callable, done: Callable) -> None:
    from ytm_native.ui.widgets import run_async

    run_async(fn, done)


class FirefoxLogin(QObject):
    """Lanza Firefox con el perfil desechable y, al cerrarse, recoge y guarda
    la sesión. ``finished(ok, mensaje)`` llega siempre una vez por intento."""

    waiting = Signal()               # Firefox abierto: el usuario inicia sesión
    checking = Signal()              # Firefox cerrado: se comprueba la sesión
    finished = Signal(bool, str)

    def __init__(
        self,
        firefox: Optional[Path] = None,
        profile: Optional[Path] = None,
        process_factory: Optional[Callable[[QObject], QProcess]] = None,
        runner: Optional[Callable[[Callable, Callable], None]] = None,
        validate: Optional[Callable[[dict], str]] = None,
        save: Optional[Callable[[CapturedSession], None]] = None,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self.firefox = firefox if firefox is not None else find_firefox_executable()
        self.profile = profile or paths.firefox_login_profile()
        # Lo que no se pasa se busca al usarlo (las pruebas lo sustituyen).
        self._make_process = process_factory
        self._run = runner or _default_runner
        self._validate = validate
        self._save = save
        self._process: Optional[QProcess] = None
        self._cancelled = False
        self.busy = False

    @property
    def available(self) -> bool:
        return self.firefox is not None

    def start(self) -> bool:
        if self.busy:
            return False
        if self.firefox is None:
            self.finished.emit(False, tr("No se encontró Firefox en este equipo."))
            return False
        self._cancelled = False
        try:
            prepare_profile(self.profile)
        except OSError as exc:
            log.warning("no se pudo preparar el perfil de Firefox: %s", exc)
            self.finished.emit(False, tr("No se pudo preparar la ventana de Firefox."))
            return False
        process = (self._make_process or QProcess)(self)
        process.finished.connect(self._on_finished)
        process.errorOccurred.connect(self._on_error)
        self._process = process
        self.busy = True
        process.start(str(self.firefox), firefox_arguments(self.profile))
        self.waiting.emit()
        return True

    def cancel(self) -> None:
        """Cierra la ventana de Firefox de este intento y borra su perfil."""
        if not self.busy:
            return
        self._cancelled = True
        process = self._process
        if process is not None and process.state() != _NOT_RUNNING:
            process.kill()
            process.waitForFinished(3000)
        if self.busy:                     # sin aviso de fin (ya estaba parado)
            self._finish(False, tr("Inicio de sesión cancelado."))

    def _on_error(self, error) -> None:
        if error == _FAILED_TO_START and self.busy:
            log.warning("Firefox no arrancó: %s", error)
            self._finish(False, tr("No se pudo abrir Firefox."))

    def _on_finished(self, *_args) -> None:
        if not self.busy:
            return
        if self._cancelled:
            self._finish(False, tr("Inicio de sesión cancelado."))
            return
        self.checking.emit()
        profile = self.profile
        validate = self._validate or validate_session
        save = self._save or save_session

        def work() -> CapturedSession:
            session = capture_session(profile)        # el perfil se borra aquí, pase lo que pase
            account = validate(session.headers)
            session = CapturedSession(session.headers, session.cookies_txt, session.cookie_count, account)
            save(session)
            return session

        self._run(work, self._captured)

    def _captured(self, session: Optional[CapturedSession], error) -> None:
        if error is None and session is not None:
            who = tr(" como {account}", account=session.account) if session.account else ""
            self._finish(True, tr("Sesión iniciada{who}. Las credenciales se guardaron cifradas.", who=who))
            return
        if isinstance(error, LoginError):
            message = str(error)
        else:
            log.warning("no se pudo completar el inicio de sesión con Firefox: %s", type(error).__name__)
            message = tr("No se pudo guardar la sesión de Firefox.")
        self._finish(False, message)

    def _finish(self, ok: bool, message: str) -> None:
        self.busy = False
        self._process = None
        destroy_profile(self.profile)     # también al cancelar o si Firefox no arrancó
        self.finished.emit(ok, message)
