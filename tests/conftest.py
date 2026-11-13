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
