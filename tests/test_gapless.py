"""Reproducción sin pausas: la siguiente pista encadenada en mpv con ``loadfile … append``.

Tres niveles: el reproductor con un mpv simulado en el propio proceso (cada
evento a mano), el IPC con el mpv simulado por tubería y, si está instalado,
el mpv de verdad en silencio con tonos generados.
"""

import gc
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from ytm_native.core import config
from ytm_native.core.db import Track
from ytm_native.playback.extractor import ResolveResult
from ytm_native.playback.mpv_ipc import MpvPlayer
from ytm_native.playback.player import (
    LOADING, PAUSED, PLAYING, REPEAT_ALL, REPEAT_ONE, STOPPED, TRANSITION_KEY, TRANSITION_OFF,
    PlaybackController,
)

from test_player import FakeExtractor, FakeMpv, settle, wait_until

FAKE = str(Path(__file__).with_name("fake_mpv.py"))
REAL_MPV = Path(os.environ.get("LOCALAPPDATA", "")) / "YTMusicNativo" / "bin" / "mpv.exe"


class GaplessMpv(FakeMpv):
    """mpv simulado que entiende la lista: lo cargado con ``load`` sustituye
    todo; lo de ``append`` espera a que acabe lo que suena. ``ops`` guarda el
    orden de las órdenes que le llegan."""

    def __init__(self):
        super().__init__()
        self.ops = []
        self.appended = []                # (url, token, entrada) tras lo que suena
        self.playing = None               # entrada que suena
        self.to_start = None              # entrada cargada con ``load`` que aún no empezó
        self.reject_appends = False
        self.fail_chained = False         # lo encadenado no abre al llegarle el turno

    def load(self, url, user_agent="", token=0):
        self.ops.append(("load", url))
        self.loaded.append(url)
        self.duration = 0.0
        self.entry += 1
        self.to_start = self.entry
        self.appended = []
        self.pending_acks.append((token, self.entry, True))
        if not self.defer_acks:
            self.ack()
        return len(self.loaded)

    def append(self, url, user_agent="", token=0):
        self.ops.append(("append", url))
        self.entry += 1
        if not self.reject_appends:
            self.appended.append((url, token, self.entry))
        self.pending_acks.append((token, self.entry, not self.reject_appends))
        if not self.defer_acks:
            self.ack()
        return self.entry

    def playlist_clear(self):
        self.ops.append(("playlist-clear",))
        self.appended = []

    def stop(self):
        self.ops.append(("stop",))
        super().stop()
        self.appended = []
        self.playing = self.to_start = None

    def begin(self, duration=100.0):
        """mpv empieza lo cargado con ``load``."""
        entry, self.to_start = self.to_start, None
        self.playing = entry
        self.file_started.emit(entry)
        self.file_loaded.emit()
        self.duration = duration
        self.duration_changed.emit(duration)
        return entry

    def finish(self):
        """Lo que suena llega al final y, como mpv, sigue con lo encadenado:
        end-file(eof) de la actual, luego start-file de la siguiente."""
        ended, self.playing = self.playing, None
        self.last_time_pos = self.duration - 0.2
        self.position_changed.emit(self.last_time_pos)
        self.file_ended.emit(ended, "eof", "")
        if not self.appended:
            return None
        url, _token, entry = self.appended.pop(0)
        self.playing = entry
        self.last_time_pos = 0.0
        self.duration = 0.0
        self.file_started.emit(entry)
        if self.fail_chained:
            self.playing = None
            self.file_ended.emit(entry, "error", "HTTP error 403 Forbidden")
            return entry
        self.file_loaded.emit()
        self.duration = 100.0
        self.duration_changed.emit(100.0)
        return entry

    def chained_urls(self):
        return [url for url, _token, _entry in self.appended]

    def ops_since(self, mark):
        return self.ops[mark:]


def make_env(qapp, db, monkeypatch, tracks=None, local_resolver=None):
    monkeypatch.setattr(config, "FIRST_LOAD_MIN_URL_AGE_S", 0.0)
    monkeypatch.setattr(config, "PREFETCH_AHEAD", 1)
    monkeypatch.setattr(config, "PREFETCH_START_DELAY_MS", 0)
    tracks = tracks or [Track(f"v{i}", f"Pista {i}", "Artista", "Álbum", 100) for i in range(6)]
    for t in tracks:
        db.upsert_track(t)
    mpv, extractor = GaplessMpv(), FakeExtractor()
    player = PlaybackController(db, extractor, mpv, radio_fetcher=lambda _vid: [],
                                local_resolver=local_resolver)
    states, currents, notices = [], [], []
    player.state_changed.connect(states.append)
    player.current_changed.connect(lambda t: currents.append(t.video_id if t else None))
    player.notice.connect(notices.append)
    return type("Env", (), dict(qapp=qapp, db=db, tracks=tracks, mpv=mpv, ex=extractor, player=player,
                                states=states, currents=currents, notices=notices))


@pytest.fixture
def env(qapp, db, monkeypatch):
    env = make_env(qapp, db, monkeypatch)
    yield env
    teardown(env)


def teardown(env):
    env.player.stop()
    for thread in threading.enumerate():
        if thread.name.startswith(("resolve-", "radio")):
            thread.join(5)
    env.qapp.processEvents()
    env.player = env.mpv = None
    gc.collect()


def start(env, index=0):
    env.player.play_tracks(env.tracks, index)
    assert wait_until(env.qapp, lambda: env.mpv.to_start is not None)
    env.mpv.begin()
    assert env.player.state == PLAYING


def chained(env, url):
    return wait_until(env.qapp, lambda: env.mpv.chained_urls() == [url])


def current(env):
    return env.player.current_track().video_id


# ---------------------------------------------------------------------------
# Encadenar
# ---------------------------------------------------------------------------
def test_la_siguiente_precargada_se_encadena_con_append(env):
    start(env)
    assert chained(env, "url://v1/1")
    assert ("append", "url://v1/1") in env.mpv.ops
    assert env.mpv.loaded == ["url://v0/1"]                  # lo que suena no se recarga
    assert env.ex.calls == [("v0", "play"), ("v1", "prefetch")]


def test_nada_se_encadena_hasta_que_la_actual_suena(env):
    env.player.play_tracks(env.tracks, 0)
    assert wait_until(env.qapp, lambda: env.mpv.to_start is not None)
    settle(env.qapp, 0.2)
    assert not [op for op in env.mpv.ops if op[0] == "append"]


def test_al_acabar_sigue_la_encadenada_sin_detenerse_ni_recargar(env):
    start(env)
    assert chained(env, "url://v1/1")
    mark = len(env.states)
    env.mpv.finish()
    assert current(env) == "v1" and env.player.queue_snapshot()[1] == 1
    assert env.currents[-1] == "v1"
    assert env.states[mark:] == []                           # ni STOPPED ni LOADING: sigue PLAYING
    assert env.player.state == PLAYING
    assert env.mpv.loaded == ["url://v0/1"]                  # ningún replace
    assert env.mpv.titles[-1] == "Pista 1 — Artista"         # los controles de Windows ya la nombran
    assert env.ex.count("v1") == 1                           # ni otra resolución


def test_tras_encadenar_los_eventos_de_la_nueva_cuentan(env):
    start(env)
    assert chained(env, "url://v1/1")
    env.mpv.finish()
    positions = []
    env.player.position_changed.connect(lambda pos, dur: positions.append((pos, dur)))
    env.mpv.at(12.5)
    assert positions[-1] == (12.5, 100.0)
    assert env.player.session_state()["position"] == 12.5


def test_una_detras_de_otra_toda_la_cola(env):
    start(env)
    for i in range(1, 6):
        assert chained(env, f"url://v{i}/1"), (i, env.mpv.ops)
        env.mpv.finish()
        assert current(env) == f"v{i}"
    assert env.mpv.loaded == ["url://v0/1"]
    assert STOPPED not in env.states and env.currents == [f"v{i}" for i in range(6)]
    # Al final de la cola no queda nada que encadenar: se detiene como siempre.
    settle(env.qapp, 0.1)
    assert env.mpv.appended == []
    env.mpv.finish()
    assert env.player.state == STOPPED and current(env) == "v5"


def test_repetir_toda_la_cola_encadena_la_primera_tras_la_ultima(env):
    env.player.cycle_repeat()
    assert env.player.repeat == REPEAT_ALL
    start(env, 5)
    assert chained(env, "url://v0/1")
    env.mpv.finish()
    assert current(env) == "v0" and env.mpv.loaded == ["url://v5/1"]


def test_en_pausa_lo_encadenado_sigue_ahi(env):
    start(env)
    assert chained(env, "url://v1/1")
    env.mpv.pause_changed.emit(True)
    assert env.player.state == PAUSED
    settle(env.qapp, 0.05)
    assert env.mpv.chained_urls() == ["url://v1/1"]


# ---------------------------------------------------------------------------
# Repetir una
# ---------------------------------------------------------------------------
def test_repetir_una_encadena_la_misma_pista(env):
    env.player.cycle_repeat()
    env.player.cycle_repeat()
    assert env.player.repeat == REPEAT_ONE
    start(env, 2)
    assert chained(env, "url://v2/1")
    env.mpv.finish()
    assert current(env) == "v2" and env.player.queue_snapshot()[1] == 2
    assert env.mpv.loaded == ["url://v2/1"] and env.ex.count("v2") == 1
    assert chained(env, "url://v2/1")                         # y otra vez, mientras siga repitiendo


def test_al_cambiar_de_repetir_una_se_encadena_la_siguiente(env):
    env.player.cycle_repeat()
    env.player.cycle_repeat()
    start(env, 0)
    assert chained(env, "url://v0/1")
    env.player.cycle_repeat()                                 # repetir: desactivado
    assert ("playlist-clear",) in env.mpv.ops
    assert chained(env, "url://v1/1")


# ---------------------------------------------------------------------------
# Lo que descarta lo encadenado
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("jump", ["next", "prev", "jump_to", "play_tracks"])
def test_un_salto_del_usuario_vacia_mpv_antes_de_cargar_con_replace(env, jump):
    start(env, 2)
    assert chained(env, "url://v3/1")
    mark = len(env.mpv.ops)
    {
        "next": env.player.next_track,
        "prev": env.player.previous_track,
        "jump_to": lambda: env.player.jump_to(5),
        "play_tracks": lambda: env.player.play_tracks(env.tracks[::-1], 0),
    }[jump]()
    assert wait_until(env.qapp, lambda: any(op[0] == "load" for op in env.mpv.ops_since(mark)))
    after = env.mpv.ops_since(mark)
    first_load = next(i for i, op in enumerate(after) if op[0] == "load")
    assert ("stop",) in after[:first_load]                   # la lista de mpv se vació antes
    assert env.player._chain is None


def test_quitar_la_siguiente_de_la_cola_encadena_la_nueva_siguiente(env):
    start(env)
    assert chained(env, "url://v1/1")
    env.player.remove_from_queue(1)
    assert ("playlist-clear",) in env.mpv.ops
    assert chained(env, "url://v2/1")
    env.mpv.finish()
    assert current(env) == "v2"


def test_mover_otra_a_continuacion_cambia_lo_encadenado(env):
    start(env)
    assert chained(env, "url://v1/1")
    env.player.move_in_queue(4, 1)
    assert chained(env, "url://v4/1")
    env.mpv.finish()
    assert current(env) == "v4"


def test_editar_lejos_de_la_siguiente_no_toca_lo_encadenado(env):
    start(env)
    assert chained(env, "url://v1/1")
    mark = len(env.mpv.ops)
    env.player.remove_from_queue(4)
    env.player.enqueue(Track("x", "Otra", "Y"))
    settle(env.qapp, 0.05)
    assert ("playlist-clear",) not in env.mpv.ops_since(mark)
    assert env.mpv.chained_urls() == ["url://v1/1"]


def test_vaciar_la_cola_quita_lo_encadenado(env):
    start(env)
    assert chained(env, "url://v1/1")
    env.player.clear_queue()
    assert env.mpv.appended == [] and env.player._chain is None
    env.mpv.finish()
    assert env.player.state == STOPPED and current(env) == "v0"


def test_parar_al_acabar_esta_pista_no_deja_que_suene_la_siguiente(env):
    halted = []
    env.player.halted_after_track.connect(lambda: halted.append(1))
    start(env)
    assert chained(env, "url://v1/1")
    env.player.stop_after_current = True                     # lo que hace el temporizador
    assert ("playlist-clear",) in env.mpv.ops and env.mpv.appended == []
    env.mpv.finish()
    assert env.player.state == STOPPED and current(env) == "v0" and halted == [1]
    assert env.mpv.loaded == ["url://v0/1"]


def test_la_transicion_normal_quita_lo_encadenado_y_se_recuerda(env, db):
    start(env)
    assert chained(env, "url://v1/1")
    env.player.set_transition(TRANSITION_OFF)
    assert env.mpv.appended == [] and db.get_setting(TRANSITION_KEY) == "off"
    env.mpv.finish()                                          # sin nada en mpv: como siempre
    assert wait_until(env.qapp, lambda: env.mpv.to_start is not None)
    assert env.mpv.loaded == ["url://v0/1", "url://v1/1"] and current(env) == "v1"
    env.mpv.begin()
    settle(env.qapp, 0.1)
    assert not [op for op in env.mpv.ops if op == ("append", "url://v2/1")]
    assert PlaybackController(db, env.ex, GaplessMpv()).transition == TRANSITION_OFF


def test_un_mpv_relanzado_no_tiene_nada_encadenado(env):
    start(env)
    assert chained(env, "url://v1/1")
    env.mpv.ready.emit()                                      # mpv murió y volvió
    assert env.player._chain is None
    assert wait_until(env.qapp, lambda: env.mpv.to_start is not None)
    assert env.mpv.loaded[-1] == "url://v0/1"                 # restaura la actual


# ---------------------------------------------------------------------------
# Contingencias
# ---------------------------------------------------------------------------
def test_si_lo_encadenado_no_abre_se_resuelve_de_nuevo_y_se_carga_con_replace(env):
    start(env)
    assert chained(env, "url://v1/1")
    env.mpv.fail_chained = True
    env.mpv.finish()
    # Ya es la actual; su URL falló: otra resolución (no la del caché) y replace.
    assert current(env) == "v1" and env.player.state == LOADING
    assert wait_until(env.qapp, lambda: env.mpv.loaded[-1:] == ["url://v1/2"])
    assert env.ex.calls.count(("v1", "play")) == 1
    env.mpv.begin()
    assert env.player.state == PLAYING and current(env) == "v1"
    assert STOPPED not in env.states


def test_si_la_actual_falla_a_mitad_lo_encadenado_no_suena(env):
    start(env)
    assert chained(env, "url://v1/1")
    env.mpv.at(30)
    mark = len(env.mpv.ops)
    env.mpv.file_ended.emit(env.mpv.playing, "error", "conexión cortada")
    after = env.mpv.ops_since(mark)
    assert after and after[0] == ("stop",)                    # antes de nada, mpv no sigue solo
    assert wait_until(env.qapp, lambda: env.mpv.loaded[-1:] == ["url://v0/2"])
    assert current(env) == "v0"
    env.mpv.begin()
    assert env.mpv.seeks[-1] == 30                            # sigue donde iba


def test_un_final_prematuro_recupera_la_actual_en_vez_de_saltar(env):
    start(env)
    assert chained(env, "url://v1/1")
    env.mpv.at(40)
    entry = env.mpv.playing
    env.mpv.last_time_pos = 40
    env.mpv.file_ended.emit(entry, "eof", "")                 # a los 40 s de 100: no acabó de verdad
    assert ("stop",) in env.mpv.ops and current(env) == "v0"
    assert wait_until(env.qapp, lambda: env.mpv.loaded[-1:] == ["url://v0/2"])


def test_si_mpv_no_acepta_el_append_se_sigue_como_siempre(env):
    env.mpv.reject_appends = True
    start(env)
    assert wait_until(env.qapp, lambda: ("append", "url://v1/1") in env.mpv.ops)
    assert env.player._chain is None
    env.mpv.finish()
    assert wait_until(env.qapp, lambda: env.mpv.loaded[-1:] == ["url://v1/1"])
    assert current(env) == "v1"


def test_sin_respuesta_de_mpv_al_append_no_se_adopta(env):
    start(env)
    env.mpv.defer_acks = True
    assert wait_until(env.qapp, lambda: ("append", "url://v1/1") in env.mpv.ops)
    env.mpv.appended = []                                     # mpv aún no lo tenía
    env.mpv.finish()
    assert ("stop",) in env.mpv.ops                           # por si acaso, que no siga solo
    assert wait_until(env.qapp, lambda: env.mpv.loaded[-1:] == ["url://v1/1"])
    env.mpv.ack()                                             # la respuesta tardía no confunde nada
    assert current(env) == "v1"


def test_una_url_que_caduco_mientras_esperaba_no_se_adopta(env):
    start(env)
    assert chained(env, "url://v1/1")
    env.player._chain.res.expire_ts = time.time()             # caducó durante la pista actual
    env.mpv.finish()
    assert ("stop",) in env.mpv.ops
    assert wait_until(env.qapp, lambda: env.mpv.loaded[-1:] == ["url://v1/2"])


def test_una_pista_marcada_no_disponible_no_se_encadena(env, db):
    db.mark_unavailable("v1", "borrada")
    start(env)
    assert chained(env, "url://v2/1")


# ---------------------------------------------------------------------------
# Archivos del disco
# ---------------------------------------------------------------------------
@pytest.fixture
def local_env(qapp, db, monkeypatch, tmp_path):
    tracks = [Track(f"local:{i}", f"Archivo {i}", "Yo", "", 100) for i in range(3)]
    paths = {t.video_id: str(tmp_path / f"{i}.flac") for i, t in enumerate(tracks)}
    env = make_env(qapp, db, monkeypatch, tracks=tracks, local_resolver=paths.get)
    env.paths = paths
    yield env
    teardown(env)


def test_los_archivos_del_disco_se_encadenan_sin_resolver_nada(local_env):
    env = local_env
    start(env)
    assert wait_until(env.qapp, lambda: len(env.mpv.appended) == 1)
    assert env.mpv.appended[0][0].startswith("file:") and env.mpv.appended[0][0].endswith("1.flac")
    env.mpv.finish()
    assert current(env) == "local:1" and env.ex.calls == []
    assert wait_until(env.qapp, lambda: env.mpv.chained_urls()[:1] and env.mpv.chained_urls()[0].endswith("2.flac"))


def test_un_archivo_que_ya_no_esta_no_se_encadena(local_env):
    env = local_env
    env.paths["local:1"] = None
    start(env)
    settle(env.qapp, 0.1)
    assert env.mpv.appended == []


# ---------------------------------------------------------------------------
# IPC con el mpv simulado por tubería
# ---------------------------------------------------------------------------
@pytest.fixture
def piped(qapp):
    player = MpvPlayer(mpv_path=Path(sys.executable), pre_args=[FAKE])
    events = []
    player.file_started.connect(lambda e: events.append(("started", e)))
    player.file_loaded.connect(lambda: events.append(("loaded",)))
    player.file_ended.connect(lambda e, r, err: events.append(("ended", e, r)))
    player.load_acked.connect(lambda token, entry, ok: events.append(("acked", token, entry, ok)))
    player.events = events
    player.start()
    assert wait_until(qapp, player.is_ready, timeout=10)
    yield player
    player.shutdown()


def test_mpv_se_lanza_sin_pausas_entre_pistas(piped):
    args = piped._proc.arguments()
    assert "--gapless-audio=yes" in args and "--prefetch-playlist=yes" in args


def test_append_responde_con_su_entrada_y_no_corta_lo_que_suena(qapp, piped):
    piped.load("https://example.invalid/normal", token=1)
    assert wait_until(qapp, lambda: ("loaded",) in piped.events)
    assert piped.append("https://example.invalid/siguiente", "UA/1", token=2) > 0
    assert wait_until(qapp, lambda: ("acked", 2, 2, True) in piped.events)
    settle(qapp, 0.1)
    assert ("started", 2) not in piped.events                 # aún suena la primera
    piped.command("ytmn-test-eof")
    assert wait_until(qapp, lambda: ("started", 2) in piped.events)
    ended = piped.events.index(("ended", 1, "eof"))
    assert ended < piped.events.index(("started", 2))          # primero acaba, luego empieza la otra


def test_playlist_clear_quita_lo_encadenado(qapp, piped):
    piped.load("https://example.invalid/normal", token=1)
    assert wait_until(qapp, lambda: ("loaded",) in piped.events)
    piped.append("https://example.invalid/siguiente", token=2)
    piped.playlist_clear()
    piped.command("ytmn-test-eof")
    assert wait_until(qapp, lambda: ("ended", 1, "eof") in piped.events)
    settle(qapp, 0.2)
    assert ("started", 2) not in piped.events


def test_la_duracion_de_la_anterior_no_pasa_a_la_encadenada(qapp, piped):
    piped.load("https://example.invalid/normal", token=1)
    assert wait_until(qapp, lambda: piped.duration == 100.0)
    piped._on_event({"event": "start-file", "playlist_entry_id": 9})
    assert piped.duration == 0.0 and piped.last_time_pos == 0.0


# ---------------------------------------------------------------------------
# El mpv de verdad, en silencio
# ---------------------------------------------------------------------------
class ToneExtractor:
    """Resuelve cada pista a un tono generado por mpv (sin red)."""

    def __init__(self):
        self.calls = []

    def resolve(self, video_id, cancel=None, purpose="play"):
        self.calls.append((video_id, purpose))
        now = time.time()
        freq = 300 + 100 * int(video_id[1:])
        return ResolveResult(video_id=video_id, url=f"av://lavfi:sine=frequency={freq}:duration=1.5",
                             resolved_at=now, expire_ts=now + 3600, duration_s=1.5, ready=True)


@pytest.mark.skipif(not REAL_MPV.is_file(), reason="sin mpv instalado")
def test_mpv_real_encadena_tres_pistas_con_una_sola_carga(qapp, db, monkeypatch):
    monkeypatch.setattr(config, "PREFETCH_AHEAD", 1)
    monkeypatch.setattr(config, "PREFETCH_START_DELAY_MS", 0)
    tracks = [Track(f"t{i}", f"Tono {i}", "Generador", "", 2) for i in range(3)]
    for t in tracks:
        db.upsert_track(t)
    mpv = MpvPlayer(mpv_path=REAL_MPV, pre_args=["--ao=null"])
    sent = []
    real_command = mpv.command
    mpv.command = lambda *args: (sent.append(args[:3]), real_command(*args))[1]
    extractor = ToneExtractor()
    player = PlaybackController(db, extractor, mpv, radio_fetcher=lambda _vid: [])
    states, currents = [], []
    player.state_changed.connect(states.append)
    player.current_changed.connect(lambda t: currents.append(t.video_id if t else None))
    mpv.start()
    try:
        assert wait_until(qapp, mpv.is_ready, timeout=15)
        player.play_tracks(tracks, 0)
        assert wait_until(qapp, lambda: currents[-1:] == ["t2"], timeout=20), (currents, sent)
        assert wait_until(qapp, lambda: player.state == STOPPED, timeout=10)
        loads = [args for args in sent if args[0] == "loadfile"]
        assert [a[2] for a in loads] == ["replace", "append", "append"], loads
        assert currents == ["t0", "t1", "t2"]
        # De la primera a la última sin pararse ni volver a cargar.
        assert states == [LOADING, PLAYING, STOPPED], states
        assert extractor.calls == [("t0", "play"), ("t1", "prefetch"), ("t2", "prefetch")]
    finally:
        player.stop()
        mpv.shutdown()
        for thread in threading.enumerate():
            if thread.name.startswith("resolve-"):
                thread.join(5)
