import faulthandler
import os
import sys
import threading
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Las pruebas comprueban los textos en español (los del código), sea cual
# sea el idioma de Windows de quien las ejecuta.
os.environ["YTMN_LANG"] = "es"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# Tope por prueba (preparación, prueba y recogida): una regresión que deje un
# hilo bloqueado o una espera que no acaba no cuelga toda la batería. Pasado
# el tope se enseñan las pilas de todos los hilos y la ejecución termina con
# error. Con pytest-timeout instalado manda el suyo; YTMN_TEST_TIMEOUT=0 lo
# desactiva (para depurar con pausas).
TEST_TIMEOUT_S = float(os.environ.get("YTMN_TEST_TIMEOUT", "120"))


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    if TEST_TIMEOUT_S <= 0 or item.config.pluginmanager.hasplugin("timeout"):
        yield
        return
    done = threading.Event()

    def watch() -> None:
        if done.wait(TEST_TIMEOUT_S):
            return
        capture = item.config.pluginmanager.getplugin("capturemanager")
        try:
            if capture is not None:
                capture.suspend_global_capture(in_=True)    # que se vea en la consola
        except Exception:  # noqa: BLE001 - se corta igual
            pass
        sys.stderr.write(f"\n\n*** {item.nodeid} lleva más de {TEST_TIMEOUT_S:g} s: "
                         "se corta la batería. Pilas de los hilos: ***\n")
        sys.stderr.flush()
        faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
        sys.stderr.flush()
        os._exit(3)

    threading.Thread(target=watch, name="vigia-de-pruebas", daemon=True).start()
    try:
        yield
    finally:
        done.set()


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Cada test usa su propia carpeta de datos: nunca toca la instalación real."""
    monkeypatch.setenv("YTMN_HOME", str(tmp_path / "home"))
    yield tmp_path / "home"


class FakeRegistry:
    """Lo justo de ``winreg`` para el inicio con Windows, en memoria."""

    HKEY_CURRENT_USER = "HKCU"
    KEY_READ, KEY_SET_VALUE = 1, 2
    REG_SZ = 1

    def __init__(self):
        self.keys: dict[tuple, dict] = {}
        self.fail = False

    class _Key:
        def __init__(self, values: dict):
            self.values = values

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    def OpenKey(self, root, path, reserved=0, access=1):  # noqa: N802 - API de winreg
        if self.fail:
            raise PermissionError("acceso denegado")
        if (root, path) not in self.keys:
            raise FileNotFoundError(path)
        return self._Key(self.keys[(root, path)])

    def CreateKeyEx(self, root, path, reserved=0, access=2):  # noqa: N802
        if self.fail:
            raise PermissionError("acceso denegado")
        return self._Key(self.keys.setdefault((root, path), {}))

    def QueryValueEx(self, key, name):  # noqa: N802
        if name not in key.values:
            raise FileNotFoundError(name)
        return key.values[name], self.REG_SZ

    def SetValueEx(self, key, name, reserved, kind, value):  # noqa: N802
        key.values[name] = value

    def DeleteValue(self, key, name):  # noqa: N802
        if name not in key.values:
            raise FileNotFoundError(name)
        del key.values[name]


@pytest.fixture(autouse=True)
def fake_registry(monkeypatch):
    """Ninguna prueba escribe en el registro de Windows de verdad."""
    from ytm_native.ui import tray

    registry = FakeRegistry()
    monkeypatch.setattr(tray, "_default_registry", lambda: registry)
    return registry


@pytest.fixture(autouse=True)
def no_external_services(monkeypatch):
    """Ninguna prueba habla con Discord, Last.fm ni GitHub de verdad: las que
    los usan pasan sus dobles; si alguna se escapa, falla en vez de salir."""
    from ytm_native.services import discord_rpc, lastfm, updater

    def blocked(*_args, **_kwargs):
        raise AssertionError("una prueba intentó usar un servicio externo real")

    monkeypatch.setattr(lastfm, "_requests_http", blocked)
    monkeypatch.setattr(updater, "_requests_fetch", blocked)
    monkeypatch.setattr(discord_rpc.PipeTransport, "open", classmethod(blocked))
    # Ni un Firefox real ni una comprobación de sesión contra YouTube.
    from ytm_native.services import auth_assistant

    monkeypatch.setattr(auth_assistant, "QProcess", blocked)
    monkeypatch.setattr(auth_assistant, "validate_session", blocked)


@pytest.fixture(autouse=True)
def light_theme(monkeypatch):
    """Las pruebas no dependen del tema de Windows de quien las ejecuta: el
    sistema "dice" claro, y una prueba que cambie al oscuro lo deja como estaba."""
    from PySide6.QtWidgets import QApplication

    from ytm_native.ui import art, theme

    monkeypatch.setattr(theme, "system_prefers_dark", lambda: False)
    app = QApplication.instance()
    saved = (app.styleSheet(), app.palette()) if app is not None else None
    yield
    if theme.scheme() != "light":
        theme.use_scheme("light")
        art.on_theme_changed()
    if app is not None and saved is not None and app.styleSheet() != saved[0]:
        app.setStyleSheet(saved[0])
        app.setPalette(saved[1])


@pytest.fixture
def db(isolated_home):
    from ytm_native.core.db import Database

    return Database(isolated_home / "test.sqlite3")


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication

    app = QCoreApplication.instance() or QApplication([])
    yield app
