"""Inicio de sesión asistido con Firefox (Tarea 8.2), sin Firefox ni red:
un ``QProcess`` falso, un ``cookies.sqlite`` de prueba y una comprobación de
sesión simulada."""

import hashlib
import http.cookiejar
import json
import sqlite3
import sys
import time

import pytest
from PySide6.QtCore import QObject, Signal

from ytm_native.core import credentials, paths
from ytm_native.services import auth_assistant
from ytm_native.services.auth_assistant import (
    APP_PATHS_KEY, START_URL, BrowserCookie, FirefoxLogin, LoginError, browser_headers, capture_session,
    cookie_header, find_firefox_executable, firefox_arguments, firefox_user_agent, netscape_cookies,
    prepare_profile, read_cookies,
)
from ytm_native.ui import main_window as mw
from ytm_native.ui.dialogs import NO_FIREFOX, WHY_NOT_CHROME, FirefoxLoginDialog

from conftest import FakeRegistry
from test_home_search import animations_on, net, window  # noqa: F401

windows = pytest.mark.skipif(sys.platform != "win32", reason="las credenciales se cifran con DPAPI")

FUTURE = int(time.time()) + 365 * 86400
SESSION_ROWS = [
    # host, name, value, expiry, secure, httponly
    (".youtube.com", "SAPISID", "sapisid_secreto", FUTURE, 1, 0),
    (".youtube.com", "__Secure-3PAPISID", "papisid_secreto", FUTURE, 1, 0),
    (".youtube.com", "SID", "sid_secreto", FUTURE, 0, 0),
    (".youtube.com", "__Secure-3PSID", "psid_secreto", FUTURE, 1, 1),
    (".youtube.com", "HSID", "hsid", FUTURE, 0, 1),
    (".youtube.com", "SSID", "ssid", FUTURE, 1, 1),
    (".youtube.com", "APISID", "apisid", FUTURE, 0, 0),
    (".youtube.com", "LOGIN_INFO", "login", FUTURE, 1, 1),
    ("music.youtube.com", "PREF", "f6=40000000", FUTURE, 1, 0),
    (".youtube.com", "PREF", "f6=global", FUTURE, 1, 0),          # el de music. gana
    (".youtube.com", "VIEJA", "caducada", int(time.time()) - 60, 1, 0),
    (".google.com", "SID", "de_google", FUTURE, 1, 1),             # otro dominio: fuera
    (".notyoutube.com", "SAPISID", "trampa", FUTURE, 1, 0),        # tampoco
]


def make_cookie_db(profile, rows=SESSION_ROWS, version="137.0.1_20250601000000/20250601000000"):
    profile.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(profile / "cookies.sqlite")
    conn.execute(
        "CREATE TABLE moz_cookies (id INTEGER PRIMARY KEY, originAttributes TEXT NOT NULL DEFAULT '', "
        "name TEXT, value TEXT, host TEXT, path TEXT, expiry INTEGER, lastAccessed INTEGER, "
        "creationTime INTEGER, isSecure INTEGER, isHttpOnly INTEGER, inBrowserElement INTEGER DEFAULT 0, "
        "sameSite INTEGER DEFAULT 0, rawSameSite INTEGER DEFAULT 0, schemeMap INTEGER DEFAULT 0)"
    )
    conn.executemany(
        "INSERT INTO moz_cookies (host, name, value, path, expiry, isSecure, isHttpOnly) "
        "VALUES (?, ?, ?, '/', ?, ?, ?)",
        [(h, n, v, e, s, ho) for h, n, v, e, s, ho in rows],
    )
    conn.commit()
    conn.close()
    (profile / "compatibility.ini").write_text(f"[Compatibility]\nLastVersion={version}\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Encontrar Firefox
# ---------------------------------------------------------------------------
def test_firefox_se_encuentra_por_el_registro(tmp_path):
    exe = tmp_path / "Mi Firefox" / "firefox.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    reg = FakeRegistry()
    reg.HKEY_LOCAL_MACHINE = "HKLM"
    reg.keys[("HKLM", APP_PATHS_KEY)] = {"": f'"{exe}"'}
    assert find_firefox_executable(reg, environ={}) == exe


def test_firefox_se_encuentra_en_program_files(tmp_path):
    exe = tmp_path / "Program Files" / "Mozilla Firefox" / "firefox.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    assert find_firefox_executable(FakeRegistry(), environ={"ProgramFiles": str(tmp_path / "Program Files")}) == exe


def test_sin_firefox_no_hay_ruta(tmp_path):
    reg = FakeRegistry()
    reg.keys[("HKCU", APP_PATHS_KEY)] = {"": str(tmp_path / "no-existe" / "firefox.exe")}
    assert find_firefox_executable(reg, environ={"ProgramFiles": str(tmp_path)}) is None


# ---------------------------------------------------------------------------
# Perfil y lectura de cookies
# ---------------------------------------------------------------------------
def test_perfil_nuevo_y_argumentos_de_firefox(tmp_path):
    profile = tmp_path / "perfil"
    (profile / "resto").mkdir(parents=True)               # quedaba uno de antes
    prepare_profile(profile)
    assert [p.name for p in profile.iterdir()] == ["user.js"]
    assert "browser.shell.checkDefaultBrowser" in (profile / "user.js").read_text(encoding="utf-8")
    args = firefox_arguments(profile)
    assert args[:2] == ["-profile", str(profile)]
    assert "-no-remote" in args and "-wait-for-browser" in args and args[-1] == START_URL


def test_se_leen_solo_las_cookies_vigentes_de_youtube_sin_tocar_el_archivo(tmp_path):
    profile = tmp_path / "perfil"
    make_cookie_db(profile)
    db_file = profile / "cookies.sqlite"
    before = hashlib.sha256(db_file.read_bytes()).hexdigest()
    cookies = read_cookies(profile)
    assert hashlib.sha256(db_file.read_bytes()).hexdigest() == before        # solo lectura
    names = {(c.host, c.name) for c in cookies}
    assert (".youtube.com", "SAPISID") in names and ("music.youtube.com", "PREF") in names
    assert not any(c.name == "VIEJA" for c in cookies)
    assert not any(c.host in (".google.com", ".notyoutube.com") for c in cookies)


def test_cabecera_cookie_y_cookies_txt(tmp_path):
    profile = tmp_path / "perfil"
    make_cookie_db(profile)
    cookies = read_cookies(profile)
    header = cookie_header(cookies)
    assert "SAPISID=sapisid_secreto" in header and "PREF=f6=40000000" in header
    assert header.count("PREF=") == 1
    jar_file = tmp_path / "cookies.txt"
    jar_file.write_bytes(netscape_cookies(cookies).encode())
    jar = http.cookiejar.MozillaCookieJar(str(jar_file))
    jar.load(ignore_discard=True, ignore_expires=True)                     # yt-dlp lo lee igual
    loaded = {(c.domain, c.name): c.value for c in jar}
    assert loaded[(".youtube.com", "__Secure-3PSID")] == "psid_secreto"   # también las HttpOnly


def test_cabeceras_de_ytmusicapi_con_firma_de_navegador(tmp_path):
    from requests.structures import CaseInsensitiveDict
    from ytmusicapi.auth.auth_parse import determine_auth_type
    from ytmusicapi.auth.types import AuthType

    profile = tmp_path / "perfil"
    make_cookie_db(profile)
    headers = browser_headers(read_cookies(profile), firefox_user_agent(profile))
    assert headers["x-goog-authuser"] == "0" and headers["authorization"].startswith("SAPISIDHASH ")
    assert "Firefox/137.0" in headers["user-agent"]
    assert determine_auth_type(CaseInsensitiveDict(headers)) is AuthType.BROWSER


def test_sin_sesion_de_google_se_rechaza_con_un_mensaje_claro(tmp_path):
    profile = tmp_path / "perfil"
    make_cookie_db(profile, rows=[(".youtube.com", "PREF", "x", FUTURE, 1, 0),
                                  (".youtube.com", "VISITOR_INFO1_LIVE", "y", FUTURE, 1, 1)])
    with pytest.raises(LoginError) as info:
        browser_headers(read_cookies(profile), "UA")
    assert "SAPISID" in str(info.value) and "Inicia sesión" in str(info.value)
    with pytest.raises(LoginError):
        read_cookies(tmp_path / "vacio")                     # Firefox no llegó a guardar nada


def test_capturar_borra_el_perfil_tambien_si_falla(tmp_path):
    good = tmp_path / "bueno"
    make_cookie_db(good)
    session = capture_session(good)
    assert not good.exists() and "SAPISID" in session.headers["cookie"]
    bad = tmp_path / "malo"
    make_cookie_db(bad, rows=[])
    with pytest.raises(LoginError):
        capture_session(bad)
    assert not bad.exists()


# ---------------------------------------------------------------------------
# El proceso completo con un Firefox falso
# ---------------------------------------------------------------------------
class FakeFirefox(QObject):
    """Lo que la app usa de ``QProcess``. ``close_window()`` simula que el
    usuario cierra Firefox."""

    finished = Signal(int)
    errorOccurred = Signal(object)
    started: list = []

    def __init__(self, parent=None):
        super().__init__(parent)
        self.program, self.args, self.running, self.killed = None, None, False, False
        FakeFirefox.started.append(self)

    def start(self, program, args):
        self.program, self.args, self.running = program, args, True

    def state(self):
        from PySide6.QtCore import QProcess

        return QProcess.ProcessState.Running if self.running else QProcess.ProcessState.NotRunning

    def kill(self):
        self.killed = True

    def waitForFinished(self, _ms):
        self.close_window()
        return True

    def close_window(self):
        self.running = False
        self.finished.emit(0)


def sync_runner(fn, done):
    try:
        result = fn()
    except Exception as exc:  # noqa: BLE001
        done(None, exc)
    else:
        done(result, None)


@pytest.fixture
def login(qapp, isolated_home, tmp_path):
    FakeFirefox.started = []
    firefox = tmp_path / "firefox.exe"
    firefox.write_bytes(b"")
    checked = []

    def validate(headers):
        checked.append(headers)
        if validate.reject:
            raise LoginError("YouTube Music no aceptó la sesión: vuelve a intentarlo.")
        return "Cuenta secundaria"

    validate.reject = False
    flow = FirefoxLogin(firefox=firefox, process_factory=FakeFirefox, runner=sync_runner, validate=validate)
    flow.checked = checked
    flow.validate = validate
    flow.results = []
    flow.finished.connect(lambda ok, message: flow.results.append((ok, message)))
    return flow


@windows
def test_inicio_de_sesion_completo_guarda_cifrado_y_borra_el_perfil(login):
    assert login.start()
    process = FakeFirefox.started[0]
    assert process.program.endswith("firefox.exe") and process.args == firefox_arguments(login.profile)
    assert (login.profile / "user.js").is_file()
    make_cookie_db(login.profile)                        # el usuario inicia sesión...
    process.close_window()                               # ...y cierra la ventana
    assert login.results == [(True, "Sesión iniciada como Cuenta secundaria. "
                                    "Las credenciales se guardaron cifradas.")]
    assert not login.profile.exists()
    assert login.checked and "SAPISID=sapisid_secreto" in login.checked[0]["cookie"]
    saved = json.loads(credentials.read_text(paths.browser_json()))
    assert saved["cookie"] == login.checked[0]["cookie"]
    assert "SAPISID\tsapisid_secreto" in credentials.read_text(paths.cookies_txt())
    everything = b"".join(p.read_bytes() for p in paths.app_home().rglob("*") if p.is_file())
    assert b"sapisid_secreto" not in everything          # nada en claro en la carpeta de la app
    assert not login.busy


def test_una_sesion_que_youtube_rechaza_no_se_guarda(login):
    login.validate.reject = True
    login.start()
    make_cookie_db(login.profile)
    FakeFirefox.started[0].close_window()
    assert login.results == [(False, "YouTube Music no aceptó la sesión: vuelve a intentarlo.")]
    assert not paths.browser_json().exists() and not paths.cookies_txt().exists()
    assert not login.profile.exists()


def test_cerrar_firefox_sin_iniciar_sesion(login):
    login.start()
    FakeFirefox.started[0].close_window()                 # sin cookies.sqlite
    ok, message = login.results[0]
    assert not ok and "inicia sesión" in message.lower()
    assert login.checked == [] and not login.profile.exists()


def test_cancelar_cierra_firefox_y_borra_el_perfil(login):
    login.start()
    make_cookie_db(login.profile)
    login.cancel()
    assert FakeFirefox.started[0].killed
    assert login.results == [(False, "Inicio de sesión cancelado.")]
    assert login.checked == [] and not login.profile.exists()
    assert not paths.browser_json().exists()


def test_si_firefox_no_arranca_tambien_se_limpia(login):
    from PySide6.QtCore import QProcess

    login.start()
    FakeFirefox.started[0].errorOccurred.emit(QProcess.ProcessError.FailedToStart)
    assert login.results == [(False, "No se pudo abrir Firefox.")]
    assert not login.profile.exists() and not login.busy


def test_sin_firefox_no_se_lanza_nada(qapp, isolated_home):
    FakeFirefox.started = []
    flow = FirefoxLogin(process_factory=FakeFirefox, runner=sync_runner)
    flow.firefox = None
    results = []
    flow.finished.connect(lambda ok, message: results.append((ok, message)))
    assert not flow.available and not flow.start()
    assert results and not results[0][0] and FakeFirefox.started == []


# ---------------------------------------------------------------------------
# Diálogo y ventana
# ---------------------------------------------------------------------------
def test_el_dialogo_explica_por_que_no_chrome_y_sigue_el_proceso(login):
    dialog = FirefoxLoginDialog(login)
    texts = " ".join(label.text() for label in dialog.findChildren(type(dialog.status)))
    assert WHY_NOT_CHROME in texts and "Device Bound Session Credentials" in texts and "v20" in texts
    changed = []
    dialog.credentials_changed.connect(lambda: changed.append(True))
    login.validate.reject = False
    dialog.open_btn.click()
    assert not dialog.open_btn.isEnabled() and dialog.close_btn.text() == "Cancelar"
    make_cookie_db(login.profile)
    FakeFirefox.started[0].close_window()
    if sys.platform == "win32":
        assert changed and dialog.close_btn.text() == "Cerrar"
        assert "Sesión iniciada" in dialog.status.text()


def test_cerrar_el_dialogo_a_mitad_cancela(login):
    dialog = FirefoxLoginDialog(login)
    dialog.open_btn.click()
    dialog.reject()
    assert FakeFirefox.started[0].killed and not login.busy and not login.profile.exists()


def test_sin_firefox_el_dialogo_lleva_a_la_importacion_manual(qapp, isolated_home):
    flow = FirefoxLogin(process_factory=FakeFirefox, runner=sync_runner)
    flow.firefox = None
    dialog = FirefoxLoginDialog(flow)
    assert not dialog.open_btn.isEnabled() and dialog.status.text() == NO_FIREFOX
    asked = []
    dialog.manual_requested.connect(lambda: asked.append(True))
    dialog.manual_btn.click()
    assert asked


def test_sin_credenciales_el_aviso_ofrece_iniciar_sesion_con_firefox(window, monkeypatch):
    monkeypatch.setattr(mw.bootstrap, "missing_binaries", lambda: [])    # el aviso de binarios va antes
    window._refresh_banner()
    assert window.banner_button.text() == "Iniciar sesión"
    assert window._banner_action == window._open_firefox_login
    texts = [action.text() for action in window.app_menu.actions()]
    assert "Iniciar sesión con Firefox…" in texts


def test_el_validador_real_no_se_usa_en_las_pruebas():
    with pytest.raises(AssertionError):
        auth_assistant.validate_session({"cookie": "x"})
    assert isinstance(BrowserCookie(".youtube.com", "a", "b"), BrowserCookie)
