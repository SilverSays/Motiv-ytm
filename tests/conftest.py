import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


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
