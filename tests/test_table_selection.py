"""Selección de varias filas: menú en bloque, Supr, y arrastrar solo donde el orden es de la app (sin red)."""

import pytest
from PySide6.QtCore import QItemSelectionModel, QPoint, QPointF, Qt
from PySide6.QtGui import QDropEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractItemView

from ytm_native.core.db import Track
from ytm_native.playback.player import PLAYING
from ytm_native.ui import main_window as mw

from test_home_search import animations_on, net, wait_until, window  # noqa: F401
from test_player import env, start_playing  # noqa: F401
from test_player import wait_until as player_wait_until

MODE = QAbstractItemView.DragDropMode


def songs(n: int, prefix: str = "s") -> list[Track]:
    return [Track(f"{prefix}{i}", f"Canción {i}", "Artista", "", 180) for i in range(n)]


@pytest.fixture
def local(window, db, monkeypatch):
    """Una lista local con seis canciones, a la vista."""
    monkeypatch.setattr(mw.bootstrap, "missing_binaries", lambda: [])
    pid = db.create_local_playlist("Mía", songs(6))
    window._reload_sidebar()
    window._navigate(pid)
    assert window.model.rowCount() == 6
    return pid


def select(table, rows) -> None:
    model, sel = table.model(), table.selectionModel()
    sel.clearSelection()
    flags = QItemSelectionModel.SelectionFlag
    for row in rows:
        sel.select(model.index(row, 0), flags.Select | flags.Rows)


def ids(tracks) -> list[str]:
    return [t.video_id for t in tracks]


def db_order(db, pid) -> list[str]:
    return ids(db.playlist_tracks(pid))


def texts(menu) -> list[str]:
    return [a.text() for a in menu.actions() if not a.isSeparator() and a.text()]


def action(menu, text):
    return next(a for a in menu.actions() if a.text() == text)


def row_point(table, row, lower=False) -> QPoint:
    rect = table.visualRect(table.model().index(row, 0))
    return QPoint(rect.center().x(), rect.top() + (rect.height() * 3 // 4 if lower else rect.height() // 4))


def with_queue(window, n=3):
    """Una cola detenida (sin resolver nada) para ver adónde van las pistas."""
    tracks = [Track(f"q{i}", f"Cola {i}", "Artista", "", 200) for i in range(n)]
    assert window.player.restore(tracks, pos=0)
    return tracks


# ---------------------------------------------------------------------------
# Selección
# ---------------------------------------------------------------------------
def test_la_tabla_selecciona_varias_filas_enteras(window):
    assert window.table.selectionMode() == QAbstractItemView.SelectionMode.ExtendedSelection
    assert window.table.selectionBehavior() == QAbstractItemView.SelectionBehavior.SelectRows


def test_mayus_y_ctrl_amplian_la_seleccion(window, local):
    table = window.table
    QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     row_point(table, 1))
    QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier,
                     row_point(table, 3))
    assert table.selected_rows() == [1, 2, 3]
    QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier,
                     row_point(table, 5))
    assert table.selected_rows() == [1, 2, 3, 5]


# ---------------------------------------------------------------------------
# Arrastrar: solo en las listas locales y en la cola
# ---------------------------------------------------------------------------
def test_arrastrar_solo_en_listas_locales_y_en_la_cola(window, local):
    table = window.table
    assert table.dragDropMode() == MODE.InternalMove and table.dragEnabled() and table.acceptDrops()
    for key in ("PLuno", mw.SEARCH_KEY, mw.HISTORY_KEY, mw.LOCAL_KEY,
                mw.REMOTE_PREFIX + "PLajena", mw.ARTIST_PREFIX + "UCartista"):
        window._show_view(key)
        assert table.dragDropMode() == MODE.NoDragDrop, key
        assert not table.dragEnabled() and not table.acceptDrops(), key
        assert not window.model.flags(window.model.index(0, 0)) & Qt.ItemFlag.ItemIsDragEnabled, key
    window._show_view(mw.QUEUE_KEY)
    assert table.dragDropMode() == MODE.InternalMove
    window._show_view(local)
    assert table.dragDropMode() == MODE.InternalMove


def drop(table, from_row, point) -> QDropEvent:
    mime = table.model().mimeData([table.model().index(from_row, 0)])
    event = QDropEvent(QPointF(point), Qt.DropAction.MoveAction, mime,
                       Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    event.source = lambda: table            # un arrastre de la propia tabla
    table.dropEvent(event)
    return event


def test_soltar_una_fila_reordena_la_lista_local(window, db, local):
    table = window.table
    event = drop(table, 0, row_point(table, 3, lower=True))
    assert event.isAccepted()
    expected = ["s1", "s2", "s3", "s0", "s4", "s5"]
    assert db_order(db, local) == expected
    assert ids(window.model.tracks()) == expected
    assert table.selected_rows() == [3]                  # la selección sigue a la fila movida
    assert window._view_key == local


def test_soltar_una_fila_al_principio_cambia_la_caratula_de_la_lista(window, db, local):
    drop(window.table, 5, row_point(window.table, 0))
    assert db_order(db, local)[0] == "s5"
    assert "s5" in window._band_art


def test_al_agarrar_una_de_varias_seleccionadas_se_mueve_solo_esa(window, local):
    table = window.table
    select(table, [1, 3, 4])
    QTest.mousePress(table.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     row_point(table, 3))
    assert table.selected_rows() == [1, 3, 4]            # Qt la conserva por si se arrastra
    table.keep_only_grabbed_row()                        # lo que hace al empezar a arrastrar
    assert table.selected_rows() == [3]
    QTest.mouseRelease(table.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                       row_point(table, 3))


def test_con_una_sola_seleccionada_agarrar_no_toca_la_seleccion(window, local):
    table = window.table
    select(table, [2])
    table._drag_row = 4
    table.keep_only_grabbed_row()
    assert table.selected_rows() == [2]


# ---------------------------------------------------------------------------
# Supr
# ---------------------------------------------------------------------------
def test_supr_quita_tres_filas_de_la_lista_local_tras_confirmar(window, db, local, monkeypatch):
    asked = []
    monkeypatch.setattr(window, "_confirm", lambda *args: asked.append(args) or True)
    window.table.setFocus()
    select(window.table, [0, 2, 5])
    QTest.keyClick(window.table, Qt.Key.Key_Delete)
    assert len(asked) == 1 and "3 canciones" in asked[0][1] and "Mía" in asked[0][1]
    assert db_order(db, local) == ["s1", "s3", "s4"]
    assert ids(window.model.tracks()) == ["s1", "s3", "s4"]
    assert window.band._facts[0] == "3 canciones"


def test_supr_sin_confirmar_no_quita_nada(window, db, local, monkeypatch):
    monkeypatch.setattr(window, "_confirm", lambda *args: False)
    select(window.table, [1, 2])
    QTest.keyClick(window.table, Qt.Key.Key_Delete)
    assert db_order(db, local) == [f"s{i}" for i in range(6)]


def test_supr_sobre_una_sola_no_pregunta(window, db, local, monkeypatch):
    asked = []
    monkeypatch.setattr(window, "_confirm", lambda *args: asked.append(args) or True)
    select(window.table, [4])
    QTest.keyClick(window.table, Qt.Key.Key_Delete)
    assert asked == [] and db_order(db, local) == ["s0", "s1", "s2", "s3", "s5"]


def test_supr_con_modificadores_no_hace_nada(window, db, local, monkeypatch):
    monkeypatch.setattr(window, "_confirm", lambda *args: True)
    select(window.table, [0, 1])
    QTest.keyClick(window.table, Qt.Key.Key_Delete, Qt.KeyboardModifier.ShiftModifier)
    assert len(db_order(db, local)) == 6


@pytest.mark.parametrize("key", ["PLuno", mw.QUEUE_KEY])
def test_supr_fuera_de_una_lista_local_no_quita_nada(window, db, key, monkeypatch):
    asked = []
    monkeypatch.setattr(window, "_confirm", lambda *args: asked.append(args) or True)
    with_queue(window)
    window._navigate(key)
    select(window.table, [0, 1])
    QTest.keyClick(window.table, Qt.Key.Key_Delete)
    assert asked == []
    assert ids(db.playlist_tracks("PLuno")) == ["PLuno-0", "PLuno-1", "PLuno-2"]
    assert ids(window.player.queue_snapshot()[0]) == ["q0", "q1", "q2"]


# ---------------------------------------------------------------------------
# Menú de una selección
# ---------------------------------------------------------------------------
class FakeMenu:
    def exec(self, _pos):
        pass


def test_clic_derecho_sobre_la_seleccion_abre_el_menu_de_todas(window, local, monkeypatch):
    opened = []
    monkeypatch.setattr(window, "_tracks_menu", lambda tracks: opened.append(("varias", ids(tracks))) or FakeMenu())
    monkeypatch.setattr(window, "_track_menu", lambda track, row=None: opened.append(("una", row)) or FakeMenu())
    select(window.table, [1, 3, 4])
    window._on_table_menu(row_point(window.table, 3))
    window._on_table_menu(row_point(window.table, 0))        # fuera de la selección: solo esa
    assert opened == [("varias", ["s1", "s3", "s4"]), ("una", 0)]


def test_el_menu_de_varias_lleva_lo_que_vale_para_todas(window, local):
    tracks = window.model.tracks()[:3]
    menu = window._tracks_menu(tracks)
    assert texts(menu) == ["3 canciones", "Reproducir a continuación", "Añadir a la cola",
                           "Añadir a una lista", "Quitar de esta lista"]
    assert not menu.actions()[0].isEnabled()            # el rótulo con la cuenta no se elige
    window._navigate("PLuno")
    assert "Quitar de esta lista" not in texts(window._tracks_menu(window.model.tracks()[:2]))


def test_anadir_cuatro_a_la_cola_las_encola_en_orden(window, local):
    with_queue(window)
    select(window.table, [5, 0, 3, 2])
    tracks = [window.model.tracks()[r] for r in window.table.selected_rows()]
    action(window._tracks_menu(tracks), "Añadir a la cola").trigger()
    assert ids(window.player.queue_snapshot()[0]) == ["q0", "q1", "q2", "s0", "s2", "s3", "s5"]
    assert window.player.current_track().video_id == "q0"


def test_reproducir_a_continuacion_inserta_el_lote_tras_la_actual(window, local):
    with_queue(window)
    tracks = [window.model.tracks()[r] for r in (1, 4)]
    action(window._tracks_menu(tracks), "Reproducir a continuación").trigger()
    assert ids(window.player.queue_snapshot()[0]) == ["q0", "s1", "s4", "q1", "q2"]


def test_anadir_varias_a_otra_lista_local(window, db, local):
    other = db.create_local_playlist("Otra", songs(1))           # s0 ya está en la otra
    window._reload_sidebar()
    tracks = window.model.tracks()[:3]
    menu = window._tracks_menu(tracks)
    add = action(menu, "Añadir a una lista").menu()
    action(add, "Otra").trigger()
    assert db_order(db, other) == ["s0", "s1", "s2"]
    action(add, "Otra").trigger()                                 # otra vez: ya estaban
    assert db_order(db, other) == ["s0", "s1", "s2"]


def test_nueva_lista_con_la_seleccion(window, db, local, monkeypatch):
    monkeypatch.setattr(window, "_ask_text", lambda *a, **k: "Selección")
    tracks = [window.model.tracks()[r] for r in (4, 1)]
    action(action(window._tracks_menu(tracks), "Añadir a una lista").menu(), "Nueva lista…").trigger()
    created = next(p for p in db.playlists() if p.title == "Selección")
    assert db_order(db, created.playlist_id) == ["s4", "s1"]


def test_quitar_de_esta_lista_en_bloque(window, db, local, monkeypatch):
    monkeypatch.setattr(window, "_confirm", lambda *args: True)
    tracks = [window.model.tracks()[r] for r in (0, 1, 2, 3)]
    action(window._tracks_menu(tracks), "Quitar de esta lista").trigger()
    assert db_order(db, local) == ["s4", "s5"] and ids(window.model.tracks()) == ["s4", "s5"]


def test_los_avisos_cuentan_las_canciones(window, db, local, monkeypatch):
    shown = []
    monkeypatch.setattr(window, "_notice", lambda text, kind="info": shown.append(text))
    monkeypatch.setattr(window, "_confirm", lambda *args: True)
    with_queue(window)
    window._queue_tracks(window.model.tracks()[:2], next_up=False)
    window._remove_tracks_from_local_playlist(local, window.model.tracks()[:2])
    other = db.create_local_playlist("Otra")
    window._add_tracks_to_local_playlist(other, window.model.tracks()[:1])
    window._add_tracks_to_local_playlist(other, window.model.tracks()[:1])
    assert shown == ["2 canciones añadidas a la cola.", "2 canciones quitadas de la lista.",
                     "1 canción añadida a «Otra».", "Ya estaban todas en «Otra»."]


# ---------------------------------------------------------------------------
# Varias a la vez en la cola del reproductor
# ---------------------------------------------------------------------------
def test_varias_al_final_avisan_una_sola_vez(env):
    start_playing(env, 0)
    changes = []
    env.player.queue_changed.connect(lambda: changes.append(1))
    extra = songs(3, "x")
    env.player.enqueue_many(extra)
    assert ids(env.player.queue_snapshot()[0])[-3:] == ["x0", "x1", "x2"] and changes == [1]


def test_varias_a_continuacion_respetan_su_orden_tambien_al_quitar_el_aleatorio(env):
    env.player.set_shuffle(True)
    start_playing(env, 2)
    env.player.play_next_many(songs(2, "x"))
    assert ids(env.player.queue_snapshot()[0])[:3] == ["v2", "x0", "x1"]
    env.player.set_shuffle(False)
    # En el orden de la lista van tras la que sonaba.
    assert ids(env.player.queue_snapshot()[0]) == ["v0", "v1", "v2", "x0", "x1", "v3", "v4", "v5"]


def test_varias_con_la_cola_vacia_empiezan_a_sonar_la_primera(env):
    env.player.play_next_many(songs(3, "x"))
    assert player_wait_until(env.qapp, lambda: len(env.mpv.loaded) == 1)
    env.mpv.begin()
    assert env.player.state == PLAYING and env.player.current_track().video_id == "x0"
    assert ids(env.player.queue_snapshot()[0]) == ["x0", "x1", "x2"]


def test_una_lista_vacia_no_cambia_la_cola(env):
    changes = []
    env.player.queue_changed.connect(lambda: changes.append(1))
    env.player.enqueue_many([])
    env.player.play_next_many([])
    assert changes == [] and env.player.current_track() is None
