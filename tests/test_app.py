"""Arranque de la app: errores sin atrapar visibles con pythonw e instancia única."""

import logging
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

ROOT = str(Path(__file__).resolve().parent.parent)


def wait_until(qapp, cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def app_logging(isolated_home):
    """Monta el registro de la app en la carpeta de prueba y lo desmonta al acabar."""
    from ytm_native import app

    root = logging.getLogger()
    before = list(root.handlers)
    app.setup_logging()
    yield isolated_home / "logs" / "ytmn.log"
    for handler in root.handlers[:]:
        if handler not in before:
            root.removeHandler(handler)
            handler.close()


def _log_text(isolated_home) -> str:
    from ytm_native.core import paths

    return (paths.log_dir() / "ytmn.log").read_text(encoding="utf-8")


def test_uncaught_error_is_logged_and_shown(isolated_home, app_logging, monkeypatch):
    from ytm_native import app

    shown = []
    monkeypatch.setattr(app, "_show_crash_dialog", shown.append)
    monkeypatch.setattr(app, "_crash_dialogs", 0)
    try:
        raise RuntimeError("Prueba de fallo de inicio")
    except RuntimeError:
        app.report_crash(*sys.exc_info())
    text = _log_text(isolated_home)
    assert "error sin atrapar" in text and "Traceback" in text
    assert "RuntimeError: Prueba de fallo de inicio" in text
    assert len(shown) == 1 and "Prueba de fallo de inicio" in shown[0] and "ytmn.log" in shown[0]


def test_repeated_errors_do_not_flood_the_screen(isolated_home, app_logging, monkeypatch):
    from ytm_native import app

    shown = []
    monkeypatch.setattr(app, "_show_crash_dialog", shown.append)
    monkeypatch.setattr(app, "_crash_dialogs", 0)
    for _ in range(10):
        app.report_crash(ValueError, ValueError("otra vez"), None)
    assert len(shown) == 3
    assert _log_text(isolated_home).count("ValueError: otra vez") == 10


def test_error_before_logging_is_set_up_still_reaches_the_log(isolated_home, monkeypatch):
    from ytm_native import app

    monkeypatch.setattr(logging.getLogger(), "handlers", [])
    monkeypatch.setattr(app, "_show_crash_dialog", lambda message: None)
    monkeypatch.setattr(app, "_crash_dialogs", 0)
    app.report_crash(OSError, OSError("disco lleno"), None)
    assert "OSError: disco lleno" in _log_text(isolated_home)


_CRASHING_START = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, sys.argv[1])
    from ytm_native import app

    app._show_crash_dialog = lambda message: print("DIALOGO:", message, flush=True)

    def build(qapp):
        raise RuntimeError("Prueba de fallo de inicio")

    app.build = build
    sys.exit(app.main())
    """
)


def test_a_crash_while_starting_writes_the_log_and_warns(isolated_home, tmp_path):
    """Lo que pasaría con pythonw: el error sale de ``main`` sin que nadie lo
    atrape y llega al gancho, que lo anota y avisa."""
    script = tmp_path / "arranque.py"
    script.write_text(_CRASHING_START, encoding="utf-8")
    env = dict(os.environ, YTMN_HOME=str(isolated_home), QT_QPA_PLATFORM="offscreen")
    out = subprocess.run(
        [sys.executable, str(script), ROOT], capture_output=True, text=True, timeout=60, env=env,
        encoding="utf-8", errors="replace",
    )
    assert out.returncode != 0
    assert "DIALOGO:" in out.stdout and "Prueba de fallo de inicio" in out.stdout
    log = (isolated_home / "logs" / "ytmn.log").read_text(encoding="utf-8")
    assert "RuntimeError: Prueba de fallo de inicio" in log
    # El cerrojo de instancia se soltó: la app puede volver a abrirse.
    from ytm_native.core.instance import SingleInstance

    again = SingleInstance(isolated_home)
    assert again.acquire()
    again.release()


# -- instancia única -----------------------------------------------------------


def test_second_instance_hands_focus_to_the_first_and_leaves(qapp, isolated_home):
    from ytm_native.core.instance import SingleInstance

    first = SingleInstance(isolated_home)
    assert first.acquire()
    calls = []
    first.activated.connect(lambda: calls.append(True))
    try:
        second = SingleInstance(isolated_home)
        assert not second.acquire()
        assert wait_until(qapp, lambda: calls)
    finally:
        first.release()
    third = SingleInstance(isolated_home)
    assert third.acquire()                          # al soltarlo, se puede volver a abrir
    third.release()


_SECOND_PROCESS = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, sys.argv[1])
    from pathlib import Path
    from PySide6.QtCore import QCoreApplication
    from ytm_native.core.instance import SingleInstance

    app = QCoreApplication([])
    print(SingleInstance(Path(sys.argv[2])).acquire(), flush=True)
    """
)


def test_second_instance_in_another_process(qapp, isolated_home, tmp_path):
    from ytm_native.core.instance import SingleInstance

    script = tmp_path / "segunda.py"
    script.write_text(_SECOND_PROCESS, encoding="utf-8")
    first = SingleInstance(isolated_home)
    assert first.acquire()
    calls = []
    first.activated.connect(lambda: calls.append(True))
    try:
        proc = subprocess.Popen(
            [sys.executable, str(script), ROOT, str(isolated_home)],
            stdout=subprocess.PIPE, text=True,
            env=dict(os.environ, QT_QPA_PLATFORM="offscreen"),
        )
        assert wait_until(qapp, lambda: proc.poll() is not None and calls, timeout=30)
        assert proc.stdout.read().strip() == "False"
        assert proc.returncode == 0
    finally:
        first.release()


def test_different_data_folders_do_not_block_each_other(qapp, tmp_path):
    from ytm_native.core.instance import SingleInstance

    a, b = SingleInstance(tmp_path / "a"), SingleInstance(tmp_path / "b")
    try:
        assert a.acquire() and b.acquire()
    finally:
        a.release()
        b.release()


def test_lock_left_by_a_dead_process_does_not_block(qapp, isolated_home):
    """Si la app murió de golpe, su cerrojo queda en disco pero no cuenta."""
    from ytm_native.core.instance import SingleInstance

    script = (
        "import sys; sys.path.insert(0, sys.argv[1]); from pathlib import Path; "
        "from PySide6.QtCore import QCoreApplication; "
        "from ytm_native.core.instance import SingleInstance; "
        "app = QCoreApplication([]); s = SingleInstance(Path(sys.argv[2])); "
        "print(s.acquire(), flush=True); import os; os._exit(0)"
    )
    out = subprocess.run(
        [sys.executable, "-c", script, ROOT, str(isolated_home)],
        capture_output=True, text=True, timeout=60,
        env=dict(os.environ, QT_QPA_PLATFORM="offscreen"),
    )
    assert out.stdout.strip() == "True"
    assert (isolated_home / "ytmn.lock").exists()     # quedó en disco al morir sin soltarlo
    fresh = SingleInstance(isolated_home)
    assert fresh.acquire()
    fresh.release()
