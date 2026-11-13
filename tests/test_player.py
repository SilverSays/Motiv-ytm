import gc
import threading
import time

import pytest
from PySide6.QtCore import QObject, Signal

from ytm_native.core import config
from ytm_native.core.db import Track
from ytm_native.core.errors import ErrorKind
from ytm_native.playback.extractor import ResolveResult
from ytm_native.playback.player import LOADING, PAUSED, PLAYING, STOPPED, PlaybackController


class FakeMpv(QObject):
    ready = Signal()
    crashed = Signal(str)
    gave_up = Signal(str)
    position_changed = Signal(float)
    duration_changed = Signal(float)
    pause_changed = Signal(bool)
    file_started = Signal(int)
    file_loaded = Signal()
    file_ended = Signal(int, str, str)
    media_key = Signal(str)

    def __init__(self):
        super().__init__()
        self.loaded, self.seeks, self.pauses = [], [], []
        self.titles = []
        self.stops = 0
        self.duration = 0.0
        self.last_time_pos = 0.0
        self.entry = 0

    def load(self, url, user_agent=""):
        self.loaded.append(url)
        self.duration = 0.0

    def set_media_title(self, title):
        self.titles.append(title)

    def stop(self):
        self.stops += 1

    def set_pause(self, paused):
        self.pauses.append(paused)

    def seek(self, seconds):
        self.seeks.append(seconds)

    def set_volume(self, volume):
        pass

    # -- ayudas para simular a mpv --
    def begin(self, duration=100.0):
        self.entry += 1
        self.file_started.emit(self.entry)
        self.file_loaded.emit()
        self.duration = duration
        self.duration_changed.emit(duration)
        return self.entry

    def at(self, pos):
        self.last_time_pos = pos
        self.position_changed.emit(pos)


class FakeExtractor:
    def __init__(self):
        self.calls = []                 # (video_id, purpose)
        self.failures = {}              # video_id -> ErrorKind
        self.lock = threading.Lock()
        self.expire_in = 6 * 3600
        self.gates = {}                 # video_id -> Event: la resolución espera a que se abra

    def resolve(self, video_id, cancel=None, purpose="play"):
        with self.lock:
            self.calls.append((video_id, purpose))
            n = sum(1 for v, _ in self.calls if v == video_id)
        gate = self.gates.get(video_id)
        if gate is not None:
            gate.wait(5)
        kind = self.failures.get(video_id)
        if kind is not None:
            return ResolveResult(video_id=video_id, kind=kind, message=kind.value,
                                 blocked_until=time.time() + 60 if kind is ErrorKind.BOT_BLOCK else 0)
        now = time.time()
        return ResolveResult(video_id=video_id, url=f"url://{video_id}/{n}", resolved_at=now,
                             expire_ts=now + self.expire_in, duration_s=100)

    def count(self, video_id=None, purpose=None):
        return sum(1 for v, p in self.calls
                   if (video_id is None or v == video_id) and (purpose is None or p == purpose))


def wait_until(qapp, cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.005)
    return False


def settle(qapp, seconds=0.15):
    wait_until(qapp, lambda: False, timeout=seconds)


@pytest.fixture
def env(qapp, db, monkeypatch):
    # Las pruebas no esperan a que la URL "madure" salvo la que lo comprueba.
    monkeypatch.setattr(config, "FIRST_LOAD_MIN_URL_AGE_S", 0.0)
    # Sin resolución por adelantado salvo en las pruebas que la activan.
    monkeypatch.setattr(config, "PREFETCH_AHEAD", 0)
    monkeypatch.setattr(config, "PREFETCH_START_DELAY_MS", 0)
    tracks =[Track(f"v{i}", f"Pista {i}", "Artista", "Álbum", 100) for i in range(6)]
    for t in tracks:
        db.upsert_track(t)
    mpv, extractor = FakeMpv(), FakeExtractor()
    radio_calls = []

    def radio(video_id):
        radio_calls.append(video_id)
        return [Track(f"r{i}", f"Radio {i}", "Otro", "", 100) for i in range(3)] + [tracks[0]]

    player = PlaybackController(db, extractor, mpv, radio_fetcher=radio)
    player.radio_calls = radio_calls
    notices, blocked = [], []
    player.notice.connect(notices.append)
    player.blocked.connect(blocked.append)
    yield type("Env", (), dict(qapp=qapp, db=db, tracks=tracks, mpv=mpv, ex=extractor,
                               player=player, notices=notices, blocked=blocked))
    # Los objetos Qt deben morir en este hilo: si la última referencia la suelta
    # un hilo de trabajo (o el recolector corre en él), Qt destruye temporizadores
    # desde otro hilo y la siguiente prueba puede caerse con un access violation.
    player.stop()
    for gate in extractor.gates.values():
        gate.set()
    for thread in threading.enumerate():
        if thread.name.startswith(("resolve-", "radio")):
            thread.join(5)
    qapp.processEvents()
    del player, mpv
    gc.collect()


def start_playing(env, index=0):
    env.player.play_tracks(env.tracks, index)
    assert wait_until(env.qapp, lambda: len(env.mpv.loaded) >= 1)
    entry = env.mpv.begin()
    assert env.player.state == PLAYING
    return entry


def test_play_resolves_and_loads(env):
    start_playing(env)
    assert env.mpv.loaded == ["url://v0/1"]
    assert env.ex.calls == [("v0", "play")]
    assert env.player.current_track().video_id == "v0"


def test_rapid_skips_launch_a_single_resolution(env):
    start_playing(env)
    for _ in range(4):
        env.player.next_track()
        settle(env.qapp, 0.03)
    assert env.player.current_track().video_id == "v4"       # la GUI ya muestra la pista final
    assert env.ex.count() == 1                                # aún no se lanzó nada
    assert wait_until(env.qapp, lambda: env.ex.count() == 2, timeout=3)
    settle(env.qapp, 0.6)
    assert env.ex.calls == [("v0", "play"), ("v4", "play")]


def cached(env, video_id):
    return env.player._cached(video_id) is not None


def test_prefetch_resolves_the_next_tracks_as_soon_as_playback_starts(env, monkeypatch):
    monkeypatch.setattr(config, "PREFETCH_AHEAD", 2)
    entry = start_playing(env)
    assert wait_until(env.qapp, lambda: cached(env, "v1") and cached(env, "v2"))
    settle(env.qapp)
    # De una en una, en orden, y nada más allá de las dos siguientes.
    assert env.ex.calls == [("v0", "play"), ("v1", "prefetch"), ("v2", "prefetch")]
    env.mpv.at(99.5)
    env.mpv.file_ended.emit(entry, "eof", "")
    assert wait_until(env.qapp, lambda: len(env.mpv.loaded) == 2)
    assert env.mpv.loaded[1] == "url://v1/1"                  # usa la URL guardada...
    assert env.ex.count("v1") == 1                            # ...sin otra resolución


def test_nothing_is_prefetched_until_the_track_plays(env, monkeypatch):
    monkeypatch.setattr(config, "PREFETCH_AHEAD", 2)
    env.player.play_tracks(env.tracks, 0)
    assert wait_until(env.qapp, lambda: len(env.mpv.loaded) == 1)
    settle(env.qapp, 0.2)                                     # mpv aún no confirmó la carga
    assert env.ex.calls == [("v0", "play")]


def test_prefetched_urls_survive_queue_changes(env, monkeypatch):
    monkeypatch.setattr(config, "PREFETCH_AHEAD", 1)
    start_playing(env)
    assert wait_until(env.qapp, lambda: cached(env, "v1"))
    env.player.set_shuffle(True)
    env.player.cycle_repeat()
    assert cached(env, "v1")                                  # la URL sigue valiendo para esa pista
    # La nueva "siguiente" (la que haya tocado al barajar) también se resuelve.
    nxt = env.player.upcoming(1)[0].video_id
    assert wait_until(env.qapp, lambda: cached(env, nxt))


def test_expired_prefetch_is_not_used_nor_retried_in_a_loop(env, monkeypatch):
    monkeypatch.setattr(config, "PREFETCH_AHEAD", 1)
    env.ex.expire_in = config.URL_EXPIRY_MARGIN_S - 10       # URL a punto de caducar
    entry = start_playing(env)
    assert wait_until(env.qapp, lambda: env.ex.count("v1", "prefetch") == 1)
    settle(env.qapp, 0.3)
    assert env.ex.count("v1") == 1                            # no insiste aunque no sirva
    env.mpv.at(99.5)
    env.mpv.file_ended.emit(entry, "eof", "")
    assert wait_until(env.qapp, lambda: env.ex.count("v1") == 2)
    assert wait_until(env.qapp, lambda: len(env.mpv.loaded) == 2)
    assert env.mpv.loaded[1] == "url://v1/2"                  # se resolvió de nuevo


def test_returning_to_a_track_reuses_its_cached_url(env):
    start_playing(env)
    env.player.play_tracks(env.tracks, 3)
    assert wait_until(env.qapp, lambda: len(env.mpv.loaded) == 2)
    env.mpv.begin()
    env.player.play_tracks(env.tracks, 0)                     # vuelve a la primera
    assert wait_until(env.qapp, lambda: len(env.mpv.loaded) == 3)
    assert env.mpv.loaded[2] == "url://v0/1"
    assert env.ex.count("v0") == 1


def test_skipping_to_a_cached_track_uses_the_short_debounce(env, monkeypatch):
    monkeypatch.setattr(config, "PREFETCH_AHEAD", 1)
    monkeypatch.setattr(config, "DEBOUNCE_MS", 5000)
    monkeypatch.setattr(config, "DEBOUNCE_CACHED_MS", 20)
    start_playing(env)
    assert wait_until(env.qapp, lambda: cached(env, "v1"))
    env.player.next_track()
    assert wait_until(env.qapp, lambda: env.mpv.loaded[-1] == "url://v1/1", timeout=1)


def test_play_adopts_a_prefetch_already_in_flight(env, monkeypatch):
    monkeypatch.setattr(config, "PREFETCH_AHEAD", 1)
    monkeypatch.setattr(config, "DEBOUNCE_MS", 20)
    env.ex.gates["v1"] = threading.Event()
    start_playing(env)
    assert wait_until(env.qapp, lambda: ("v1", "prefetch") in env.ex.calls)
    env.player.next_track()                                   # la pista que se está resolviendo
    settle(env.qapp, 0.1)
    env.ex.gates["v1"].set()
    assert wait_until(env.qapp, lambda: len(env.mpv.loaded) == 2)
    assert env.mpv.loaded[1] == "url://v1/1"
    assert env.ex.count("v1") == 1                            # una sola resolución


def test_play_does_not_cancel_a_prefetch_in_flight(env, monkeypatch):
    monkeypatch.setattr(config, "PREFETCH_AHEAD", 1)
    env.ex.gates["v1"] = threading.Event()
    start_playing(env)
    assert wait_until(env.qapp, lambda: ("v1", "prefetch") in env.ex.calls)
    env.player.play_tracks(env.tracks, 4)                     # clic en otra pista
    assert wait_until(env.qapp, lambda: len(env.mpv.loaded) == 2)   # no espera al prefetch
    assert env.mpv.loaded[1] == "url://v4/1"
    env.ex.gates["v1"].set()
    assert wait_until(env.qapp, lambda: cached(env, "v1"))   # y su resultado se aprovecha


def test_stale_cached_url_that_fails_to_open_is_resolved_again_at_once(env, monkeypatch):
    monkeypatch.setattr(config, "LOAD_RETRY_MAX_URL_AGE_S", 0.0)   # toda URL cuenta como vieja
    env.player.play_tracks(env.tracks, 0)
    assert wait_until(env.qapp, lambda: len(env.mpv.loaded) == 1)
    env.mpv.entry += 1
    env.mpv.file_started.emit(env.mpv.entry)
    env.mpv.file_ended.emit(env.mpv.entry, "error", "loading failed")
    assert wait_until(env.qapp, lambda: len(env.mpv.loaded) == 2)
    assert env.mpv.loaded[1] == "url://v0/2"                  # sin reintentar la URL caducada


def test_ready_url_is_loaded_without_the_maturity_wait(env, monkeypatch):
    monkeypatch.setattr(config, "FIRST_LOAD_MIN_URL_AGE_S", 30.0)
    probed = []

    def await_playable(res, cancel=None):
        probed.append(res.video_id)
        res.ready = True
        return True

    env.ex.await_playable = await_playable
    env.player.play_tracks(env.tracks, 0)
    assert wait_until(env.qapp, lambda: len(env.mpv.loaded) == 1, timeout=2)
    assert probed == ["v0"]


def test_premature_end_re_resolves_and_resumes_position(env):
    entry = start_playing(env)
    env.mpv.at(30)
    env.mpv.file_ended.emit(entry, "eof", "")                 # "eof" a los 30 s de 100
    assert wait_until(env.qapp, lambda: len(env.mpv.loaded) == 2)
    assert env.mpv.loaded[1] == "url://v0/2"                  # misma pista, URL fresca
    assert env.player.current_track().video_id == "v0"
    env.mpv.begin()
    assert env.mpv.seeks == [30]


def test_stream_error_gives_up_after_bounded_retries(env):
    entry = start_playing(env)
    env.mpv.at(10)
    for n in range(config.MAX_RERESOLVE_PER_TRACK):
        env.mpv.file_ended.emit(entry, "error", "stream broke")     # a mitad de pista
        assert wait_until(env.qapp, lambda: len(env.mpv.loaded) == n + 2)
        entry = env.mpv.begin()
        env.mpv.at(10)
    env.mpv.file_ended.emit(entry, "error", "stream broke")
    assert wait_until(env.qapp, lambda: env.player.current_track().video_id == "v1")
    assert env.ex.count("v0") == 1 + config.MAX_RERESOLVE_PER_TRACK


def test_open_failure_retries_the_same_url_before_resolving_again(env, monkeypatch):
    """403 pasajero de una URL recién resuelta: se reintenta la misma URL."""
    monkeypatch.setattr(config, "LOAD_RETRY_DELAYS_MS", (30, 30))
    env.player.play_tracks(env.tracks, 0)
    assert wait_until(env.qapp, lambda: len(env.mpv.loaded) == 1)
    env.mpv.entry += 1
    env.mpv.file_started.emit(env.mpv.entry)
    env.mpv.file_ended.emit(env.mpv.entry, "error", "loading failed")   # falla al abrir
    assert wait_until(env.qapp, lambda: len(env.mpv.loaded) == 2)
    assert env.mpv.loaded == ["url://v0/1", "url://v0/1"]              # misma URL
    assert env.ex.count() == 1                                         # sin otra resolución
    env.mpv.begin()
    assert env.player.state == PLAYING


def test_open_failure_falls_back_to_a_fresh_url_after_the_retries(env, monkeypatch):
    monkeypatch.setattr(config, "LOAD_RETRY_DELAYS_MS", (20, 20))
    env.player.play_tracks(env.tracks, 0)
    for expected in (1, 2, 3):
        assert wait_until(env.qapp, lambda: len(env.mpv.loaded) == expected)
        env.mpv.entry += 1
        env.mpv.file_started.emit(env.mpv.entry)
        env.mpv.file_ended.emit(env.mpv.entry, "error", "loading failed")
    assert wait_until(env.qapp, lambda: len(env.mpv.loaded) == 4)
    assert env.mpv.loaded[:3] == ["url://v0/1"] * 3
    assert env.mpv.loaded[3] == "url://v0/2"                           # agotados: URL fresca
    assert env.ex.count("v0") == 2


def test_pending_load_retry_is_dropped_when_the_user_changes_track(env, monkeypatch):
    monkeypatch.setattr(config, "LOAD_RETRY_DELAYS_MS", (150,))
    env.player.play_tracks(env.tracks, 0)
    assert wait_until(env.qapp, lambda: len(env.mpv.loaded) == 1)
    env.mpv.entry += 1
    env.mpv.file_started.emit(env.mpv.entry)
    env.mpv.file_ended.emit(env.mpv.entry, "error", "loading failed")
    env.player.play_tracks(env.tracks, 2)
    assert wait_until(env.qapp, lambda: len(env.mpv.loaded) == 2)
    settle(env.qapp, 0.3)
    assert env.mpv.loaded == ["url://v0/1", "url://v2/1"]


def test_events_from_a_previous_track_are_ignored(env):
    entry = start_playing(env)
    env.player.play_tracks(env.tracks, 3)
    assert wait_until(env.qapp, lambda: len(env.mpv.loaded) == 2)
    env.mpv.file_ended.emit(entry, "eof", "")                 # final tardío de la pista anterior
    settle(env.qapp)
    assert env.player.current_track().video_id == "v3"
    assert len(env.mpv.loaded) == 2


def test_unavailable_track_is_dated_and_skipped(env):
    env.ex.failures = {"v0": ErrorKind.UNAVAILABLE}
    env.player.play_tracks(env.tracks, 0)
    assert wait_until(env.qapp, lambda: len(env.mpv.loaded) == 1)
    assert env.mpv.loaded == ["url://v1/1"]
    assert env.db.get_track("v0").unavailable_since is not None
    assert any("No disponible" in n for n in env.notices)
    # En avance automático se salta la marcada; con un clic manual se reintenta.
    env.player.cycle_repeat()                                  # repetir toda la cola
    env.player._pos = len(env.tracks) - 1
    assert env.player._next_pos(auto=True) == 1
    assert env.player._next_pos(auto=False) == 0


def test_successful_play_clears_the_unavailable_mark(env):
    env.db.mark_unavailable("v0", "antes")
    start_playing(env)
    assert env.db.get_track("v0").unavailable_since is None


def test_several_failures_in_a_row_pause_the_queue_and_undo_marks(env):
    env.ex.failures = {f"v{i}": ErrorKind.UNAVAILABLE for i in range(6)}
    env.player.play_tracks(env.tracks, 0)
    assert wait_until(env.qapp, lambda: env.player.state == STOPPED)
    settle(env.qapp)
    assert env.ex.count() == config.MAX_CONSECUTIVE_FAILURES
    assert env.mpv.loaded == []
    # Huele a bloqueo: ninguna pista queda marcada como no disponible.
    assert all(env.db.get_track(f"v{i}").unavailable_since is None for i in range(6))
    assert any("bloqueo" in n for n in env.notices)


def test_bot_block_pauses_the_queue(env):
    env.ex.failures = {"v0": ErrorKind.BOT_BLOCK}
    env.player.play_tracks(env.tracks, 0)
    assert wait_until(env.qapp, lambda: env.blocked)
    assert env.player.state == STOPPED
    assert env.ex.count() == 1                                 # no avanza ni insiste
    assert env.blocked[0] > time.time()


def test_network_failure_pauses_and_can_be_resumed(env):
    env.ex.failures = {"v0": ErrorKind.NETWORK}
    env.player.play_tracks(env.tracks, 0)
    assert wait_until(env.qapp, lambda: env.player.state == STOPPED)
    assert any("Sin conexión" in n for n in env.notices)
    env.ex.failures = {}
    env.player.toggle_pause()
    assert wait_until(env.qapp, lambda: env.mpv.loaded == ["url://v0/2"])


def test_mpv_restart_restores_track_position_and_pause(env):
    start_playing(env)
    env.mpv.at(40)
    env.mpv.pause_changed.emit(True)
    assert env.player.state == PAUSED
    env.mpv.ready.emit()                                       # mpv murió y se relanzó
    assert wait_until(env.qapp, lambda: len(env.mpv.loaded) == 2)
    assert env.mpv.loaded[1] == "url://v0/1"                   # URL aún vigente: no re-resuelve
    assert env.mpv.pauses[-1] is True
    env.mpv.begin()
    assert env.mpv.seeks == [40]
    assert env.player.state == PAUSED
    assert env.player.current_track().video_id == "v0"


def test_repeat_one_reuses_the_current_url(env):
    entry = start_playing(env)
    env.player.cycle_repeat()
    env.player.cycle_repeat()                                  # off -> all -> one
    env.mpv.at(99.5)
    env.mpv.file_ended.emit(entry, "eof", "")
    assert wait_until(env.qapp, lambda: len(env.mpv.loaded) == 2)
    assert env.mpv.loaded[1] == "url://v0/1"
    assert env.ex.count() == 1


def test_queue_stops_at_the_end(env):
    env.player.play_tracks(env.tracks[:1], 0)
    assert wait_until(env.qapp, lambda: len(env.mpv.loaded) == 1)
    entry = env.mpv.begin()
    env.mpv.at(99.5)
    env.mpv.file_ended.emit(entry, "eof", "")
    settle(env.qapp)
    assert env.player.state == STOPPED
    assert env.player.state != LOADING


def test_first_load_waits_for_a_freshly_resolved_url(env, monkeypatch):
    """La URL recién resuelta da 403 un instante: el primer intento espera."""
    monkeypatch.setattr(config, "FIRST_LOAD_MIN_URL_AGE_S", 0.4)
    t0 = time.monotonic()
    env.player.play_tracks(env.tracks, 0)
    assert wait_until(env.qapp, lambda: env.ex.count() == 1)
    settle(env.qapp, 0.15)
    assert env.mpv.loaded == []                                   # aún esperando
    assert wait_until(env.qapp, lambda: len(env.mpv.loaded) == 1)
    assert time.monotonic() - t0 >= 0.35
    assert env.mpv.titles == ["Pista 0 — Artista"]                # título para los controles del sistema


def test_play_next_and_enqueue(env):
    start_playing(env)
    extra_a, extra_b = Track("xa", "Extra A"), Track("xb", "Extra B")
    env.player.enqueue(extra_b)
    env.player.play_next(extra_a)
    order, pos = env.player.queue_snapshot()
    assert pos == 0
    assert [t.video_id for t in order[:3]] == ["v0", "xa", "v1"]
    assert order[-1].video_id == "xb"
    env.player.next_track()
    assert env.player.current_track().video_id == "xa"


def test_play_next_prefetches_the_new_next_track(env, monkeypatch):
    monkeypatch.setattr(config, "PREFETCH_AHEAD", 1)
    start_playing(env)
    assert wait_until(env.qapp, lambda: cached(env, "v1"))
    env.player.enqueue(Track("xb", "Al final"))
    settle(env.qapp)
    assert env.ex.count("xb") == 0                                # la siguiente no cambió
    env.player.play_next(Track("xa", "Colada"))
    assert wait_until(env.qapp, lambda: cached(env, "xa"))        # ahora la siguiente es otra
    assert cached(env, "v1")                                      # y la anterior no se tira


def test_enqueue_on_an_empty_player_starts_playing(env):
    env.player.enqueue(env.tracks[2])
    assert wait_until(env.qapp, lambda: env.mpv.loaded == ["url://v2/1"])


def test_jump_to_queue_position(env):
    start_playing(env)
    env.player.jump_to(4)
    assert wait_until(env.qapp, lambda: len(env.mpv.loaded) == 2)
    assert env.player.current_track().video_id == "v4"


def test_autoplay_radio_extends_the_queue_when_it_ends(env):
    env.player.set_autoplay(True)
    assert env.db.get_bool("autoplay_radio")
    env.player.play_tracks(env.tracks[:1], 0)
    assert wait_until(env.qapp, lambda: len(env.mpv.loaded) == 1)
    entry = env.mpv.begin()
    env.mpv.at(99.5)
    env.mpv.file_ended.emit(entry, "eof", "")
    assert wait_until(env.qapp, lambda: len(env.mpv.loaded) == 2)
    assert env.player.radio_calls == ["v0"]
    assert env.player.current_track().video_id == "r0"
    order, _ = env.player.queue_snapshot()
    assert [t.video_id for t in order] == ["v0", "r0", "r1", "r2"]     # sin repetir la semilla


def test_queue_end_without_autoplay_does_not_call_radio(env):
    env.player.play_tracks(env.tracks[:1], 0)
    assert wait_until(env.qapp, lambda: len(env.mpv.loaded) == 1)
    entry = env.mpv.begin()
    env.mpv.at(99.5)
    env.mpv.file_ended.emit(entry, "eof", "")
    settle(env.qapp, 0.2)
    assert env.player.radio_calls == [] and env.player.state == STOPPED


def test_start_radio_plays_the_seed_and_fills_the_queue(env):
    env.player.start_radio(env.tracks[3])
    # semilla + r0, r1, r2 + v0 (la radio simulada también devuelve v0)
    assert wait_until(env.qapp, lambda: len(env.player.queue_snapshot()[0]) == 5)
    assert env.player.current_track().video_id == "v3"
    assert wait_until(env.qapp, lambda: env.mpv.loaded == ["url://v3/1"])


def test_stale_radio_result_is_ignored_after_a_new_queue(env):
    env.player.start_radio(env.tracks[3])
    env.player.play_tracks(env.tracks[:2], 0)                     # el usuario pone otra cosa
    settle(env.qapp, 0.3)
    assert [t.video_id for t in env.player.queue_snapshot()[0]] == ["v0", "v1"]


def test_media_keys_drive_the_queue(env):
    start_playing(env)
    env.mpv.media_key.emit("next")
    assert env.player.current_track().video_id == "v1"
    env.mpv.media_key.emit("prev")
    assert env.player.current_track().video_id == "v0"
