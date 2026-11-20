import os
import sys
import time
from pathlib import Path

import pytest

from ytm_native.playback.mpv_ipc import MpvPlayer, make_pipe_name

# La ventana de prueba (con la red simulada) es la de las pruebas del inicio.
from test_home_search import animations_on, net, window  # noqa: F401

FAKE = str(Path(__file__).with_name("fake_mpv.py"))


def wait_until(qapp, cond, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def mpv(qapp):
    player = MpvPlayer(mpv_path=Path(sys.executable), pre_args=[FAKE])
    events = []
    player.ready.connect(lambda: events.append(("ready",)))
    player.crashed.connect(lambda m: events.append(("crashed", m)))
    player.gave_up.connect(lambda m: events.append(("gave_up", m)))
    player.file_started.connect(lambda e: events.append(("started", e)))
    player.file_loaded.connect(lambda: events.append(("loaded",)))
    player.file_ended.connect(lambda e, r, err: events.append(("ended", e, r, err)))
    player.position_changed.connect(lambda p: events.append(("pos", p)))
    player.duration_changed.connect(lambda d: events.append(("dur", d)))
    player.pause_changed.connect(lambda p: events.append(("pause", p)))
    player.media_key.connect(lambda k: events.append(("media", k)))
    player.events = events
    player.start()
    assert wait_until(qapp, player.is_ready), events
    yield player
    player.shutdown()


def test_pipe_name_is_a_windows_named_pipe_on_windows():
    name = make_pipe_name(1)
    if sys.platform == "win32":
        assert name.startswith("\\\\.\\pipe\\ytmn-mpv-")


def test_load_reports_start_load_duration_and_position(qapp, mpv):
    mpv.load("https://example.invalid/normal", "UA/1.0")
    assert wait_until(qapp, lambda: ("pos", 0.5) in mpv.events), mpv.events
    assert ("started", 1) in mpv.events and ("loaded",) in mpv.events
    assert ("dur", 100.0) in mpv.events and mpv.duration == 100.0
    mpv.seek(42)
    assert wait_until(qapp, lambda: ("pos", 42.0) in mpv.events)
    assert mpv.last_time_pos == 42.0
    mpv.set_pause(True)
    assert wait_until(qapp, lambda: ("pause", True) in mpv.events)


def test_end_of_file_and_errors_are_reported_but_stop_is_not(qapp, mpv):
    mpv.load("https://example.invalid/auto")
    assert wait_until(qapp, lambda: ("ended", 1, "eof", "") in mpv.events), mpv.events
    mpv.load("https://example.invalid/fail")
    assert wait_until(qapp, lambda: ("ended", 2, "error", "loading failed") in mpv.events)
    mpv.load("https://example.invalid/normal")
    assert wait_until(qapp, lambda: ("started", 3) in mpv.events)
    before = len([e for e in mpv.events if e[0] == "ended"])
    mpv.stop()
    wait_until(qapp, lambda: False, timeout=0.3)
    assert len([e for e in mpv.events if e[0] == "ended"]) == before   # "stop" se ignora


def test_crash_is_detected_and_mpv_is_relaunched(qapp, mpv):
    mpv.load("https://example.invalid/crash")
    assert wait_until(qapp, lambda: any(e[0] == "crashed" for e in mpv.events)), mpv.events
    assert wait_until(qapp, lambda: mpv.events.count(("ready",)) == 2), mpv.events
    mpv.load("https://example.invalid/normal")
    assert wait_until(qapp, lambda: ("loaded",) in mpv.events)


def test_missing_binary_reports_instead_of_crashing(qapp, tmp_path):
    player = MpvPlayer(mpv_path=tmp_path / "no-existe.exe")
    messages = []
    player.gave_up.connect(messages.append)
    player.start()
    assert messages and "no-existe.exe" in messages[0]


def test_media_keys_are_bound_and_forwarded(qapp, mpv):
    """Las teclas multimedia de Windows llegan a mpv: siguiente/anterior se reenvían."""
    mpv.command("keypress", "NEXT")
    mpv.command("keypress", "PREV")
    assert wait_until(qapp, lambda: ("media", "prev") in mpv.events), mpv.events
    assert mpv.events.index(("media", "next")) < mpv.events.index(("media", "prev"))
    mpv.command("keypress", "PLAYPAUSE")
    assert wait_until(qapp, lambda: ("pause", True) in mpv.events)


# -- cierre de la app ----------------------------------------------------------


def test_shutdown_cancels_a_pending_relaunch_after_a_crash(qapp, mpv):
    mpv.load("https://example.invalid/crash")
    assert wait_until(qapp, lambda: any(e[0] == "crashed" for e in mpv.events)), mpv.events
    mpv.shutdown()                                  # el relanzamiento (300 ms) aún no ocurrió
    wait_until(qapp, lambda: False, timeout=0.8)
    assert mpv.events.count(("ready",)) == 1
    assert mpv._proc is None and not mpv.is_ready()


def test_start_after_shutdown_is_ignored(qapp, mpv):
    mpv.shutdown()
    mpv.start()
    wait_until(qapp, lambda: False, timeout=0.3)
    assert mpv._proc is None and not mpv.is_ready()
    mpv.shutdown()                                  # cerrar dos veces no falla


def test_closing_the_window_stops_children_even_if_saving_the_session_fails(window, monkeypatch):
    calls = []

    def broken_save():
        raise RuntimeError("database is locked")

    monkeypatch.setattr(window.session, "save", broken_save)
    monkeypatch.setattr(window.player, "stop", lambda: calls.append("player"))
    monkeypatch.setattr(window.mpv, "shutdown", lambda: calls.append("mpv"))
    monkeypatch.setattr(window.extractor, "shutdown", lambda: calls.append("extractor"))
    window.close()
    assert calls == ["player", "mpv", "extractor"]


# -- procesos hijos atados a la app (Windows) ----------------------------------

_PARENT = r"""
import subprocess, sys, time
sys.path.insert(0, sys.argv[1])
from ytm_native.core import proc_guard
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
print(child.pid, proc_guard.adopt(child.pid), flush=True)
time.sleep(60)
"""


@pytest.mark.skipif(sys.platform != "win32", reason="Job Objects solo existen en Windows")
def test_children_die_when_the_app_is_killed(tmp_path):
    import ctypes
    import subprocess
    from ctypes import wintypes

    root = str(Path(__file__).resolve().parent.parent)
    script = tmp_path / "padre.py"
    script.write_text(_PARENT, encoding="utf-8")
    parent = subprocess.Popen([sys.executable, str(script), root], stdout=subprocess.PIPE, text=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    k32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    child = None
    try:
        pid, adopted = parent.stdout.readline().split()
        assert adopted == "True"
        child = k32.OpenProcess(0x00100000 | 0x0001, False, int(pid))   # SYNCHRONIZE | TERMINATE
        assert child
        assert k32.WaitForSingleObject(child, 0) == 0x102              # sigue vivo (WAIT_TIMEOUT)
        parent.kill()                                                  # la app muere de golpe
        assert k32.WaitForSingleObject(child, 5000) == 0               # y el hijo con ella
    finally:
        if child:
            k32.TerminateProcess(child, 1)
            k32.CloseHandle(child)
        parent.kill()
        parent.wait(5)


def test_adopt_is_harmless_for_a_process_that_already_ended():
    from ytm_native.core import proc_guard

    assert proc_guard.adopt(0) is False
    assert proc_guard.adopt(2**31 - 7) is False



# -- cargas confirmadas y reconexión (Fase 4) ------------------------------------


def test_each_load_is_acknowledged_with_its_entry(qapp, mpv):
    acks = []
    mpv.load_acked.connect(lambda token, entry, ok: (acks.append((token, entry, ok)),
                                                     mpv.events.append(("acked", token, entry))))
    assert mpv.load("https://example.invalid/normal", token=7) > 0
    assert wait_until(qapp, lambda: ("loaded",) in mpv.events), mpv.events
    assert acks == [(7, 1, True)]
    # La respuesta llega antes que el inicio del archivo.
    assert mpv.events.index(("acked", 7, 1)) < mpv.events.index(("started", 1))
    mpv.load("https://example.invalid/normal", token=8)
    assert wait_until(qapp, lambda: (8, 2, True) in acks)


def test_a_cut_pipe_reconnects_without_relaunching_mpv(qapp, mpv):
    reconnected = []
    mpv.reconnected.connect(lambda: reconnected.append(True))
    pid = mpv._proc.processId()
    mpv.command("ytmn-test-drop")
    assert wait_until(qapp, lambda: reconnected), mpv.events
    assert mpv.is_ready() and mpv._proc.processId() == pid
    assert mpv.events.count(("ready",)) == 1 and not any(e[0] == "crashed" for e in mpv.events)
    # Vuelve a observar las propiedades: llegan posición y duración.
    mpv.events.clear()
    mpv.load("https://example.invalid/normal")
    assert wait_until(qapp, lambda: ("pos", 0.5) in mpv.events), mpv.events
    assert ("dur", 100.0) in mpv.events


def test_orders_without_connection_are_dropped_with_one_warning(qapp, caplog):
    player = MpvPlayer(mpv_path=Path(sys.executable), pre_args=[FAKE])   # sin arrancar
    with caplog.at_level("DEBUG", logger="ytmn.mpv"):
        assert player.load("https://example.invalid/normal") == -1
        assert player.command("stop") == -1
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1 and "loadfile" in warnings[0].getMessage()
    assert player._pending_loads == {}


# -- volumen igualado (filtros de audio) ---------------------------------------


def record_commands(player):
    sent = []
    original = player.command

    def command(*args):
        sent.append(args)
        return original(*args)

    player.command = command
    return sent


def filter_orders(sent):
    return [args[2] for args in sent if args[:2] == ("set_property", "af")]


def test_igualar_el_volumen_se_aplica_en_marcha_sin_cortar_la_pista(qapp, mpv, caplog):
    sent = record_commands(mpv)
    mpv.load("https://example.invalid/normal")
    assert wait_until(qapp, lambda: ("pos", 0.5) in mpv.events), mpv.events
    pid = mpv._proc.processId()
    with caplog.at_level("WARNING", logger="ytmn.mpv"):
        mpv.set_normalization("loudnorm")
        mpv.set_normalization("loudnorm")                 # lo mismo otra vez: nada que enviar
        mpv.set_normalization("dynaudnorm")
        mpv.set_normalization(None)
        assert wait_until(qapp, lambda: mpv._filter_request == -1)   # mpv contestó
    assert filter_orders(sent) == ["loudnorm=I=-16:TP=-1.5:LRA=11", "dynaudnorm=f=150:g=15", ""]
    assert not [r for r in caplog.records if "filtro" in r.getMessage()]
    # Ni se relanzó mpv ni terminó la pista.
    assert mpv._proc.processId() == pid and mpv.is_ready()
    assert not any(e[0] in ("ended", "crashed") for e in mpv.events)


def test_el_filtro_elegido_antes_de_conectar_se_aplica_al_conectar(qapp):
    player = MpvPlayer(mpv_path=Path(sys.executable), pre_args=[FAKE])
    sent = record_commands(player)
    player.set_normalization("loudnorm")
    assert filter_orders(sent) == []                       # sin conexión no se pierde ni se avisa
    player.start()
    try:
        assert wait_until(qapp, player.is_ready)
        assert filter_orders(sent) == ["loudnorm=I=-16:TP=-1.5:LRA=11"]
    finally:
        player.shutdown()


def test_un_mpv_relanzado_recupera_el_filtro(qapp, mpv):
    mpv.set_normalization("loudnorm")
    sent = record_commands(mpv)
    mpv.load("https://example.invalid/crash")
    assert wait_until(qapp, lambda: mpv.events.count(("ready",)) == 2), mpv.events
    assert filter_orders(sent) == ["loudnorm=I=-16:TP=-1.5:LRA=11"]


def test_la_opcion_del_menu_guarda_y_aplica_el_filtro(window, db):
    assert not window.equalize_action.isChecked()          # desactivado por defecto
    assert window.mpv.audio_filter == ""
    window.equalize_action.setChecked(True)
    assert db.get_bool("equalize_volume") and window.mpv.audio_filter.startswith("loudnorm=")
    window.equalize_action.setChecked(False)
    assert not db.get_bool("equalize_volume", True) and window.mpv.audio_filter == ""


REAL_MPV = Path(os.environ.get("LOCALAPPDATA", "")) / "YTMusicNativo" / "bin" / "mpv.exe"


@pytest.mark.skipif(not REAL_MPV.is_file(), reason="sin mpv instalado")
def test_mpv_real_acepta_los_filtros_y_sigue_sonando(qapp, caplog):
    """Con el mpv de verdad, en silencio (--ao=null) y un tono generado."""
    player = MpvPlayer(mpv_path=REAL_MPV, pre_args=["--ao=null"])
    positions = []
    ended = []
    player.position_changed.connect(positions.append)
    player.file_ended.connect(lambda *a: ended.append(a))
    player.start()
    try:
        assert wait_until(qapp, player.is_ready, timeout=15)
        player.load("av://lavfi:sine=frequency=440:duration=60")
        assert wait_until(qapp, lambda: positions and positions[-1] > 0.5, timeout=15)
        with caplog.at_level("WARNING", logger="ytmn.mpv"):
            for mode in ("loudnorm", "dynaudnorm", None):
                player.set_normalization(mode)
                assert wait_until(qapp, lambda: player._filter_request == -1, timeout=10)
                before = positions[-1]
                assert wait_until(qapp, lambda: positions[-1] > before + 0.5, timeout=10)
            assert not [r for r in caplog.records if "filtro" in r.getMessage()]
            # Un filtro que no existe sí se rechaza (y la comprobación no es de adorno).
            player._normalization_filter = "filtro_que_no_existe"
            player._apply_audio_filter()
            assert wait_until(qapp, lambda: player._filter_request == -1, timeout=10)
        assert any("rechazó el filtro" in r.getMessage() for r in caplog.records)
        assert not ended
    finally:
        player.shutdown()
