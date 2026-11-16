"""Seguir donde se dejó: guardar la cola y el punto, y recuperarlos al abrir (sin red)."""

import json
import sqlite3

import pytest

from ytm_native.core import config
from ytm_native.core.db import Track
from ytm_native.playback import session
from ytm_native.playback.player import PAUSED, PLAYING, REPEAT_ALL, STOPPED, PlaybackController
from ytm_native.playback.session import SessionKeeper, decode_queue, encode_queue
from ytm_native.ui import main_window as mw

# El reproductor simulado es el de las pruebas de la cola; la ventana, la del inicio.
from test_home_search import animations_on, net, window  # noqa: F401
from test_player import FakeExtractor, FakeMpv, env, settle, start_playing, wait_until  # noqa: F401


@pytest.fixture
def keeper(env, monkeypatch):
    monkeypatch.setattr(config, "SESSION_QUEUE_SAVE_DELAY_MS", 10)
    keeper = SessionKeeper(env.db, env.player)
    keeper._queue_timer.setInterval(10)
    yield keeper
    keeper._tick.stop()
    keeper._queue_timer.stop()


def reopen(env):
    """Otro reproductor sobre la misma base: lo que pasa al volver a abrir la app."""
    mpv = FakeMpv()
    player = PlaybackController(env.db, env.ex, mpv)
    return player, mpv, SessionKeeper(env.db, player)


def saved_state(db) -> dict:
    return json.loads(db.get_setting(session.STATE_KEY))


def test_la_cola_viaja_de_ida_y_vuelta():
    tracks = [Track("a", "Uno", "Ana", "Disco", 200, thumb_url="http://x/a.jpg"), Track("b", "Dos")]
    assert decode_queue(encode_queue(tracks, [1, 0])) == (tracks, [1, 0])
    # El orden natural no se escribe, y vuelve igual.
    plain = encode_queue(tracks, [0, 1])
    assert json.loads(plain)["order"] is None
    assert decode_queue(plain) == (tracks, [0, 1])


@pytest.mark.parametrize("raw", [None, "", "no es json", "[]", '{"v":99,"tracks":[]}',
                                 '{"v":1,"tracks":[]}', '{"v":1,"tracks":[["", "x", "", "", 1, ""]]}',
                                 '{"v":1,"tracks":[["a"]]}'])
def test_una_cola_ilegible_no_se_recupera(raw):
    assert decode_queue(raw) is None


def test_un_orden_roto_vuelve_al_de_la_lista():
    raw = json.dumps({"v": 1, "tracks": [["a", "A", "", "", 10, ""], ["b", "B", "", "", 10, ""]],
                      "order": [0, 0]})
    assert decode_queue(raw)[1] == [0, 1]
    raw = raw.replace("[0, 0]", '["1", 0]')
    assert decode_queue(raw)[1] == [0, 1]


def test_se_guarda_al_reproducir_y_el_segundo_al_pausar(env, keeper):
    start_playing(env, 2)
    assert wait_until(env.qapp, lambda: env.db.get_setting(session.QUEUE_KEY) is not None)
    tracks, order = decode_queue(env.db.get_setting(session.QUEUE_KEY))
    assert [t.video_id for t in tracks] == [t.video_id for t in env.tracks] and order == list(range(6))
    assert saved_state(env.db)["video_id"] == "v2"
    env.mpv.at(41.3)
    env.mpv.pause_changed.emit(True)
    assert env.player.state == PAUSED
    assert saved_state(env.db) == {"pos": 2, "video_id": "v2", "position": 41.3,
                                   "shuffle": False, "repeat": "off"}


def test_mientras_suena_el_segundo_se_guarda_cada_poco(env, keeper):
    keeper._tick.setInterval(20)
    start_playing(env)
    env.mpv.at(12)
    assert wait_until(env.qapp, lambda: saved_state(env.db)["position"] == 12)
    env.mpv.at(17.5)
    assert wait_until(env.qapp, lambda: saved_state(env.db)["position"] == 17.5)


def test_al_abrir_vuelve_la_cola_detenida_y_reproducir_sigue_en_su_punto(env, keeper):
    start_playing(env, 3)
    env.player.cycle_repeat()
    env.mpv.at(63)
    keeper.save()                                   # lo que hace la ventana al cerrarse
    env.player.stop()

    player, mpv, again = reopen(env)
    seen = []
    player.current_changed.connect(seen.append)
    assert again.restore() is True
    assert player.state == STOPPED and mpv.loaded == []      # no suena sola
    assert player.current_track().video_id == "v3" and seen[-1].video_id == "v3"
    assert player.repeat == REPEAT_ALL
    assert [t.video_id for t in player.upcoming()] == ["v4", "v5"]
    assert player.session_state()["position"] == 63

    player.toggle_pause()
    assert wait_until(env.qapp, lambda: len(mpv.loaded) == 1)
    mpv.begin()
    assert mpv.seeks == [63] and player.state == PLAYING
    player.stop()
    settle(env.qapp, 0.05)


def test_el_orden_aleatorio_vuelve_tal_cual(env, keeper):
    env.player.set_shuffle(True)
    start_playing(env, 4)
    order = [t.video_id for t in env.player.queue_snapshot()[0]]
    assert order[0] == "v4"
    env.player.next_track()
    assert wait_until(env.qapp, lambda: len(env.mpv.loaded) == 2)
    keeper.save()
    env.player.stop()

    player, _mpv, again = reopen(env)
    assert again.restore()
    restored, pos = player.queue_snapshot()
    assert [t.video_id for t in restored] == order and pos == 1
    assert player.shuffle is True
    # Quitar el aleatorio deja la lista en su orden original, con la misma pista.
    current = player.current_track().video_id
    player.set_shuffle(False)
    assert [t.video_id for t in player.queue_snapshot()[0]] == [f"v{i}" for i in range(6)]
    assert player.current_track().video_id == current


def test_si_la_cola_y_el_estado_no_casan_manda_la_pista(env, keeper):
    env.db.set_setting(session.QUEUE_KEY, encode_queue(env.tracks, list(range(6))))
    env.db.set_setting(session.STATE_KEY, json.dumps({"pos": 0, "video_id": "v5", "position": 30}))
    assert keeper.restore()
    assert env.player.current_track().video_id == "v5"
    assert env.player.session_state()["position"] == 30


def test_si_la_pista_ya_no_esta_en_la_cola_se_empieza_desde_el_principio(env, keeper):
    env.db.set_setting(session.QUEUE_KEY, encode_queue(env.tracks, list(range(6))))
    env.db.set_setting(session.STATE_KEY, json.dumps({"pos": 1, "video_id": "otra", "position": 30}))
    assert keeper.restore()
    assert env.player.current_track().video_id == "v1"
    assert env.player.session_state()["position"] == 0


def test_un_punto_mas_alla_del_final_empieza_la_pista(env, keeper):
    env.db.set_setting(session.QUEUE_KEY, encode_queue(env.tracks, list(range(6))))
    env.db.set_setting(session.STATE_KEY, json.dumps({"pos": 0, "video_id": "v0", "position": 500}))
    assert keeper.restore()
    assert env.player.session_state()["position"] == 0


def test_no_pisa_una_cola_que_ya_existe(env, keeper):
    env.db.set_setting(session.QUEUE_KEY, encode_queue(env.tracks[:2], [0, 1]))
    start_playing(env, 5)
    assert keeper.restore() is False
    assert env.player.current_track().video_id == "v5"


def test_recuperar_no_reescribe_lo_que_acaba_de_leer(env, keeper):
    env.db.set_setting(session.QUEUE_KEY, encode_queue(env.tracks, list(range(6))))
    env.db.set_setting(session.STATE_KEY, json.dumps({"pos": 2, "video_id": "v2", "position": 9}))
    writes = []
    original = env.db.set_setting
    env.db.set_setting = lambda key, value: (writes.append(key), original(key, value))[1]
    assert keeper.restore()
    settle(env.qapp, 0.06)
    assert writes == []


def test_detenida_se_puede_mover_el_punto_desde_el_que_sigue(env, keeper):
    env.db.set_setting(session.QUEUE_KEY, encode_queue(env.tracks, list(range(6))))
    assert keeper.restore()
    moves = []
    env.player.position_changed.connect(lambda pos, dur: moves.append((pos, dur)))
    env.player.seek(48)
    assert moves == [(48, 100)] and env.mpv.seeks == []
    env.player.seek(400)                            # no pasa del final
    assert moves[-1] == (99, 100)
    env.player.toggle_pause()
    assert wait_until(env.qapp, lambda: len(env.mpv.loaded) == 1)
    env.mpv.begin()
    assert env.mpv.seeks == [99]


def test_desactivado_no_guarda_ni_recupera_y_borra_lo_guardado(env, keeper):
    start_playing(env)
    keeper.save()
    assert env.db.get_setting(session.QUEUE_KEY)
    keeper.set_enabled(False)
    assert env.db.get_setting(session.QUEUE_KEY) is None
    assert env.db.get_setting(session.STATE_KEY) is None
    env.mpv.at(20)
    env.mpv.pause_changed.emit(True)
    keeper.save()
    assert env.db.get_setting(session.STATE_KEY) is None
    env.player.stop()
    _player, _mpv, again = reopen(env)
    assert again.enabled is False and again.restore() is False
    # Al volver a activarlo guarda lo que haya en ese momento.
    keeper.set_enabled(True)
    assert env.db.get_setting(session.QUEUE_KEY)


# ---------------------------------------------------------------------------
# En la ventana
# ---------------------------------------------------------------------------
def test_la_ventana_recupera_la_sesion_y_la_ensena_detenida(window, db, qapp):
    tracks = [Track(f"s{i}", f"Sesión {i}", "Artista", "", 200) for i in range(3)]
    db.set_setting(session.QUEUE_KEY, encode_queue(tracks, [0, 1, 2]))
    db.set_setting(session.STATE_KEY, json.dumps({"pos": 1, "video_id": "s1", "position": 75}))
    assert window.session.restore()
    window._on_player_state(window.player.state)
    assert window.now_title.text() == "Sesión 1"
    assert window.pos_label.text() == "1:15" and window.dur_label.text() == "3:20"
    assert window.play_btn.isEnabled() and window.next_btn.isEnabled()
    assert window.seek_slider.isEnabled() and window.seek_slider.value() == 375
    assert window.player.state == STOPPED
    window._navigate(mw.QUEUE_KEY)
    assert [t.video_id for t in window.model.tracks()] == ["s0", "s1", "s2"]


def test_la_opcion_del_menu_apaga_y_enciende_el_recuerdo(window, db):
    assert window.resume_action.isChecked()
    window.resume_action.setChecked(False)
    assert db.get_setting(session.ENABLED_KEY) == "0" and window.session.enabled is False
    window.resume_action.setChecked(True)
    assert db.get_setting(session.ENABLED_KEY) == "1"


def test_al_cerrar_la_ventana_queda_guardado_el_punto(window, db, qapp):
    tracks = [Track(f"s{i}", f"Sesión {i}", "Artista", "", 200) for i in range(3)]
    assert window.player.restore(tracks, pos=2, position=10)
    window.player.seek(33)
    window.close()
    assert json.loads(db.get_setting(session.STATE_KEY))["position"] == 33
    assert [t.video_id for t in decode_queue(db.get_setting(session.QUEUE_KEY))[0]] == ["s0", "s1", "s2"]


def test_las_filas_danadas_se_descartan_sin_perder_el_resto():
    good = ["a", "Uno", "Ana", "Disco", 200, "http://x/a.jpg"]
    raw = json.dumps({"v": 1, "order": [3, 0, 2, 1, 4], "tracks": [
        good,
        [None, "Fantasma", "", "", 10, ""],        # sin videoId: daría una pista "None"
        ["c", None, "", "", 10, ""],               # sin título
        ["d", "Cuatro", None, None, None, None],    # vacíos admitidos
        ["e", 5, "", "", 10, ""],                  # título que no es texto
    ]})
    tracks, order = decode_queue(raw)
    assert [t.video_id for t in tracks] == ["a", "d"]
    assert tracks[1] == Track("d", "Cuatro", "", "", None, thumb_url="")
    assert order == [1, 0]                     # el aleatorio se conserva para las que quedan


@pytest.mark.parametrize("row", [
    [" ", "Título", "", "", 1, ""],
    ["a", "", "", "", 1, ""],
    ["a", "T", 3, "", 1, ""],
    ["a", "T", "", ["x"], 1, ""],
    ["a", "T", "", "", 1, {"url": "x"}],
    {"video_id": "a"},
    "a",
    ["a", "T", "", "", 1],
])
def test_una_fila_con_tipos_inesperados_no_crea_pista(row):
    assert decode_queue(json.dumps({"v": 1, "tracks": [row]})) is None


def test_una_duracion_rara_no_tira_la_pista():
    for duration in (True, -5, 3.5, "200"):
        raw = json.dumps({"v": 1, "tracks": [["a", "Uno", "", "", duration, ""]]})
        assert decode_queue(raw)[0][0].duration_s is None


def test_tracks_que_no_es_lista_no_se_recupera():
    assert decode_queue('{"v":1,"tracks":{"a":1}}') is None


def test_un_fallo_al_guardar_el_punto_se_reintenta(env, keeper, monkeypatch):
    start_playing(env, 2)
    env.mpv.at(20)
    real_set = env.db.set_setting
    failures = []

    def flaky(key, value):
        if key == session.STATE_KEY and not failures:
            failures.append(value)
            raise sqlite3.OperationalError("database is locked")
        real_set(key, value)

    monkeypatch.setattr(env.db, "set_setting", flaky)
    keeper._save_state()                       # falla: no queda como guardado
    assert failures and keeper._last_state != failures[0]
    keeper._save_state()                       # el mismo estado se vuelve a escribir
    assert env.db.get_setting(session.STATE_KEY) == failures[0]
    assert keeper._last_state == failures[0]
