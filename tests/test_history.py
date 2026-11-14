"""Historial de escucha: cuándo cuenta una canción, las consultas y la vista (sin red)."""

import random
import time

import pytest
from PySide6.QtCore import Qt

from ytm_native.core import config
from ytm_native.core.db import Track
from ytm_native.playback import history
from ytm_native.playback.history import PlayTracker, needed_seconds
from ytm_native.playback.player import PAUSED
from ytm_native.services import discover
from ytm_native.ui import main_window as mw
from ytm_native.ui.widgets import TrackModel

# El reproductor simulado es el de las pruebas de la cola; la ventana, la del inicio.
from test_home_search import animations_on, net, wait, window  # noqa: F401
from test_player import env, start_playing, wait_until  # noqa: F401


@pytest.fixture
def tracker(env):
    tracker = PlayTracker(env.db, env.player)
    tracker.seen = []
    tracker.logged.connect(tracker.seen.append)
    return tracker


def listen(env, start: float, seconds: int) -> float:
    """Hace sonar ``seconds`` segundos a partir de ``start``, aviso a aviso."""
    pos = start
    for _ in range(seconds * 2):
        pos += 0.5
        env.mpv.at(pos)
    return pos


def plays(db) -> int:
    return db.play_stats()[0]


# ---------------------------------------------------------------------------
# Cuándo cuenta
# ---------------------------------------------------------------------------
def test_cuanto_tiene_que_sonar():
    assert needed_seconds(240) == config.HISTORY_MIN_LISTEN_S
    assert needed_seconds(40) == 20            # pista corta: la mitad
    assert needed_seconds(0) == config.HISTORY_MIN_LISTEN_S


def test_cuenta_al_medio_minuto_y_una_sola_vez(env, tracker):
    start_playing(env)
    pos = listen(env, 0, 29)
    assert plays(env.db) == 0
    listen(env, pos, 5)
    assert plays(env.db) == 1 and tracker.seen == ["v0"]
    listen(env, 34, 40)                        # seguir oyéndola no suma otra
    assert plays(env.db) == 1


def test_saltar_a_los_pocos_segundos_no_deja_rastro(env, tracker):
    start_playing(env)
    listen(env, 0, 10)
    env.player.next_track()
    assert wait_until(env.qapp, lambda: len(env.mpv.loaded) == 2)
    env.mpv.begin()
    listen(env, 0, 25)                         # tampoco se suman los 10 s de la anterior
    assert plays(env.db) == 0


def test_mover_la_barra_no_cuenta_como_oir(env, tracker):
    start_playing(env)
    listen(env, 0, 5)
    env.mpv.at(80)                             # salto hacia delante
    listen(env, 80, 5)
    assert plays(env.db) == 0
    listen(env, 85, 21)
    assert plays(env.db) == 1


def test_en_pausa_no_cuenta(env, tracker):
    start_playing(env)
    listen(env, 0, 20)
    env.mpv.pause_changed.emit(True)
    assert env.player.state == PAUSED
    listen(env, 20, 20)                        # avisos que llegaran en pausa no suman
    assert plays(env.db) == 0


def test_volver_al_principio_y_oirla_otra_vez_es_otra_escucha(env, tracker):
    start_playing(env)
    listen(env, 0, 31)
    assert plays(env.db) == 1
    env.mpv.at(0.3)                            # «Anterior» o repetir una: vuelve a empezar
    listen(env, 0.3, 31)
    assert plays(env.db) == 2
    assert env.db.recent_plays()[0][2] == 2    # la misma canción, dos veces


def test_apagado_no_anota_y_al_encender_empieza_de_cero(env, tracker):
    tracker.set_enabled(False)
    assert env.db.get_setting(history.ENABLED_KEY) == "0"
    start_playing(env)
    pos = listen(env, 0, 40)
    assert plays(env.db) == 0
    tracker.set_enabled(True)
    pos = listen(env, pos, 20)
    assert plays(env.db) == 0                  # lo oído apagado no cuenta después
    listen(env, pos, 11)
    assert plays(env.db) == 1
    assert PlayTracker(env.db, env.player).enabled is True


def test_una_pista_de_la_busqueda_queda_guardada_para_ensenarla(env, tracker):
    suelta = Track("fuera", "No está en la base", "Alguien", "", 100)
    env.player.play_tracks([suelta], 0)
    assert wait_until(env.qapp, lambda: len(env.mpv.loaded) == 1)
    env.mpv.begin()
    listen(env, 0, 31)
    assert [t.video_id for t, _at, _n in env.db.recent_plays()] == ["fuera"]


# ---------------------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------------------
def fill(db, now):
    for vid, title, artists in (("a", "A", "Air"), ("b", "B", "Daft Punk, Pharrell Williams"),
                                ("c", "C", "Air")):
        db.upsert_track(Track(vid, title, artists, "", 200))
    day = 86400
    for vid, ago in (("a", 50 * day), ("a", 3 * day), ("b", 2 * day), ("b", 2 * day - 60),
                     ("b", 40 * day), ("c", 60)):
        db.log_play(vid, now - ago)


def test_recientes_sin_repetir_y_lo_mas_escuchado(db):
    now = time.time()
    fill(db, now)
    recent = db.recent_plays()
    assert [(t.video_id, n) for t, _at, n in recent] == [("c", 1), ("b", 3), ("a", 2)]
    assert recent[0][1] == pytest.approx(now - 60)
    assert [(t.video_id, n) for t, _at, n in db.top_plays()] == [("b", 3), ("a", 2), ("c", 1)]
    month = now - 30 * 86400
    assert [(t.video_id, n) for t, _at, n in db.top_plays(month)] == [("b", 2), ("c", 1), ("a", 1)]
    assert db.play_stats() == (6, 3) and db.play_stats(month) == (4, 3)
    assert db.recent_plays(limit=1)[0][0].video_id == "c"
    assert sorted(db.played_artist_rows(month)) == [("Air", 1), ("Air", 1),
                                                    ("Daft Punk, Pharrell Williams", 2)]
    assert [t.video_id for t in db.played_tracks_mentioning("Air")] == ["a", "c"] or \
        sorted(t.video_id for t in db.played_tracks_mentioning("Air")) == ["a", "c"]
    assert db.clear_history() == 6 and db.play_stats() == (0, 0)


def test_una_base_antigua_gana_la_tabla_al_abrirse(isolated_home):
    import sqlite3

    from ytm_native.core.db import Database

    path = isolated_home / "vieja.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    old.commit()
    old.close()
    db = Database(path)
    db.upsert_track(Track("x", "X"))
    db.log_play("x")
    assert db.play_stats() == (1, 1)


# ---------------------------------------------------------------------------
# Gustos
# ---------------------------------------------------------------------------
def test_lo_escuchado_pesa_en_los_gustos_aunque_no_este_en_ninguna_lista(db):
    now = time.time()
    db.upsert_playlist_meta("PL", "Lista", 0)
    db.replace_playlist_tracks(
        "PL", [(Track(f"l{i}", f"Canción {i}", "Air"), None) for i in range(3)], 3
    )
    assert discover.taste(db, now=now).artists == (("Air", 3),)
    db.upsert_track(Track("m", "Midnight City", "M83, Invitada", "", 240))
    for i in range(10):
        db.log_play("m", now - i * 3600)
    artists = discover.taste(db, now=now).artists
    assert artists[0] == ("M83", 0) and ("Air", 3) in artists and ("Invitada", 0) in artists
    # Lo de hace más de tres meses ya no pesa.
    later = now + (config.HISTORY_TASTE_DAYS + 1) * 86400
    assert discover.taste(db, now=later).artists == (("Air", 3),)
    # Y sirve de semilla aunque la biblioteca no tenga nada suyo.
    assert discover._artist_seed(db, "M83", random.Random(1)).video_id == "m"
    assert discover._artist_seed(db, "Air", random.Random(1)).video_id.startswith("l")


def test_la_portada_general_se_renueva_cuando_ya_hay_historial(db):
    now = time.time()
    cached = (now - 2 * 3600, "general", [])
    assert discover.shelves_stale(db, cached, now) is False
    db.upsert_track(Track("m", "M", "M83"))
    for _ in range(5):
        db.log_play("m", now)
    assert discover.shelves_stale(db, cached, now) is True
    assert discover.shelves_stale(db, (now - 600, "general", []), now) is False   # como mucho cada hora
    assert discover.shelves_stale(db, (now - 2 * 3600, "gustos", []), now) is False


# ---------------------------------------------------------------------------
# Tabla
# ---------------------------------------------------------------------------
def test_la_columna_del_album_puede_llevar_una_nota_propia(qapp):
    model = TrackModel()
    tracks = [Track("a", "A", "Air", "Moon Safari"), Track("b", "B", "Air", "10 000 Hz Legend")]
    headers = []
    model.headerDataChanged.connect(lambda *_a: headers.append(model.headerData(1, Qt.Orientation.Horizontal)))
    model.set_tracks(tracks, {"a": "3 veces"}, "Escuchas")
    assert model.headerData(1, Qt.Orientation.Horizontal) == "Escuchas"
    assert model.data(model.index(0, 1)) == "3 veces" and model.data(model.index(1, 1)) == ""
    model.set_tracks(tracks)
    assert model.headerData(1, Qt.Orientation.Horizontal) == "Álbum"
    assert model.data(model.index(0, 1)) == "Moon Safari"
    assert headers == ["Escuchas", "Álbum"]


# ---------------------------------------------------------------------------
# En la ventana
# ---------------------------------------------------------------------------
def heard(db, now=None):
    now = time.time() if now is None else now
    for i in range(4):
        db.upsert_track(Track(f"h{i}", f"Oída {i}", f"Artista {i}", f"Disco {i}", 200))
    for vid, ago in (("h0", 40 * 86400), ("h1", 7200), ("h1", 7000), ("h2", 300), ("h3", 20)):
        db.log_play(vid, now - ago)


def test_el_historial_esta_en_la_barra_lateral_y_vacio_lo_dice(window):
    assert window._sidebar_row(mw.HISTORY_KEY) == 3
    window._navigate(mw.HISTORY_KEY)
    assert not window.empty.isHidden() and window.table.isHidden()
    assert "Aún no hay nada" in window.empty._title.text()
    assert window.heard_bar.isHidden() and window.band.extra_btn.isHidden()
    window.history_action.setChecked(False)
    assert "desactivado" in window.empty._title.text()
    window.history_action.setChecked(True)
    window._navigate(mw.HOME_KEY)
    assert window.heard_bar.isHidden()


def test_la_vista_ensena_lo_ultimo_con_cuando_y_lo_mas_escuchado_con_cuantas(window, db):
    heard(db)
    window._navigate(mw.HISTORY_KEY)
    assert [t.video_id for t in window.model.tracks()] == ["h3", "h2", "h1", "h0"]
    notes = [window.model.data(window.model.index(row, 1)) for row in range(4)]
    assert notes == ["Hace un momento", "Hace 5 min", "Hace 1 h", "Hace 40 d"]
    assert window.model.headerData(1, Qt.Orientation.Horizontal) == "Última vez"
    assert window.band.title.toolTip() == "Historial"
    assert not window.heard_bar.isHidden() and not window.band.extra_btn.isHidden()
    assert [label.text() for label in window.band._fact_labels if not label.isHidden()] == \
        ["5 escuchas", "4 canciones distintas"]

    window.heard_bar.buttons[mw.HEARD_ALL].click()
    assert [t.video_id for t in window.model.tracks()][0] == "h1"
    assert window.model.data(window.model.index(0, 1)) == "2 veces"
    assert window.model.data(window.model.index(1, 1)) == "1 vez"
    assert window.model.headerData(1, Qt.Orientation.Horizontal) == "Escuchas"

    window.heard_bar.buttons[mw.HEARD_MONTH].click()
    assert [t.video_id for t in window.model.tracks()] == ["h1", "h3", "h2"]
    assert [label.text() for label in window.band._fact_labels if not label.isHidden()] == \
        ["4 escuchas en 30 días", "3 canciones distintas"]

    # Al salir, la tabla vuelve a enseñar el álbum.
    window._navigate("PLuno")
    assert window.model.headerData(1, Qt.Orientation.Horizontal) == "Álbum"


def test_reproducir_desde_el_historial_pone_esa_cola(window, db, monkeypatch):
    heard(db)
    monkeypatch.setattr(mw.bootstrap, "missing_binaries", lambda: [])
    window._navigate(mw.HISTORY_KEY)
    window._play_from(1)
    assert window.player.current_track().video_id == "h2"
    assert [t.video_id for t in window.player.upcoming()] == ["h1", "h0"]
    window.player.stop()


def test_una_escucha_nueva_aparece_sin_salir_de_la_vista(window, db):
    heard(db)
    window._navigate(mw.HISTORY_KEY)
    db.upsert_track(Track("nueva", "Recién oída", "Alguien"))
    db.log_play("nueva")
    window.tracker.logged.emit("nueva")
    assert window.model.tracks()[0].video_id == "nueva"


def test_borrar_el_historial_pide_confirmacion(window, db, monkeypatch):
    heard(db)
    window._navigate(mw.HISTORY_KEY)
    asked = []
    monkeypatch.setattr(window, "_confirm", lambda *args: asked.append(args) or False)
    window.band.extra_btn.click()
    assert len(asked) == 1 and "5 escuchas" in asked[0][1] and db.play_stats()[0] == 5
    monkeypatch.setattr(window, "_confirm", lambda *args: True)
    window.band.extra_btn.click()
    assert db.play_stats() == (0, 0)
    assert window.model.rowCount() == 0 and not window.empty.isHidden()


def test_el_inicio_ensena_lo_escuchado_hace_poco_y_un_clic_lo_pone(window, db, monkeypatch):
    assert window.home.recent.isHidden()                 # sin historial, la fila no existe
    heard(db)
    monkeypatch.setattr(mw.bootstrap, "missing_binaries", lambda: [])
    window._show_view(mw.HOME_KEY, force=True)
    assert not window.home.recent.isHidden()
    cards = window.home.recent.grid._cards[: window.home.recent.grid.count()]
    assert [c.payload().video_id for c in cards] == ["h3", "h2", "h1", "h0"]
    assert cards[0]._title == "Oída 3" and cards[0]._note == "Artista 3"
    window.home.recent.grid.opened.emit(cards[1].payload())
    assert window.player.current_track().video_id == "h2"
    assert [t.video_id for t in window.player.upcoming()] == ["h1", "h0"]
    assert window._view_key == mw.HOME_KEY               # reproduce sin cambiar de vista
    window.player.stop()
    # Una escucha nueva entra en la fila sin recargar el inicio.
    db.upsert_track(Track("nueva", "Recién oída", "Alguien"))
    db.log_play("nueva")
    window.tracker.logged.emit("nueva")
    assert window.home.recent.grid._cards[0].payload().video_id == "nueva"
