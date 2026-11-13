import sys
import time
from pathlib import Path

import pytest

from ytm_native.playback.mpv_ipc import MpvPlayer, make_pipe_name

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
