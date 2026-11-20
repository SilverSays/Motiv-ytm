"""Editar la cola: quitar, mover y vaciar, en el reproductor, la sesión y la ventana (sin red)."""

import itertools

import pytest
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QAbstractItemView

from ytm_native.core import config
from ytm_native.core.db import Track
from ytm_native.playback import session
from ytm_native.playback.player import PLAYING, STOPPED, PlaybackController
from ytm_native.playback.session import SessionKeeper, decode_queue
from ytm_native.ui import main_window as mw
from ytm_native.ui.widgets import TrackModel

from test_home_search import animations_on, net, window  # noqa: F401
from test_player import FakeMpv, cached, env, settle, start_playing, wait_until  # noqa: F401
from test_session import keeper, reopen, saved_state  # noqa: F401


def ids(player) -> list[str]:
    return [t.video_id for t in player.queue_snapshot()[0]]


def check_invariants(player) -> None:
    """Lo que ninguna edición puede romper: cada índice de la cola aparece
    una sola vez en los dos órdenes, y sin aleatorio son el mismo."""
    n = len(player._queue)
    assert sorted(player._order) == list(range(n))
    assert sorted(player._base) == list(range(n))
    if not player.shuffle:
        assert player._base == player._order
    if n:
        assert 0 <= player._pos < n
    else:
        assert player._pos == -1 and player.current_track() is None


def fresh(env, tracks=None, **restore):
    """Otro reproductor con la cola puesta y detenido (sin resolver nada)."""
    player = PlaybackController(env.db, env.ex, FakeMpv())
    assert player.restore(list(tracks or env.tracks), **restore)
    return player


def count_changes(player) -> list:
    changes = []
    player.queue_changed.connect(lambda: changes.append(1))
    return changes


# ---------------------------------------------------------------------------
# Quitar
# ---------------------------------------------------------------------------
def test_quitar_una_que_viene_despues_no_toca_lo_que_suena(env):
    start_playing(env, 0)
    changes = count_changes(env.player)
    stops = env.mpv.stops
    assert env.player.remove_from_queue(3) is True
    assert ids(env.player) == ["v0", "v1", "v2", "v4", "v5"]
    assert env.player.current_track().video_id == "v0" and env.player.state == PLAYING
    assert env.mpv.loaded == ["url://v0/1"] and env.mpv.stops == stops       # nada se recarga
    assert changes == [1]
    check_invariants(env.player)


def test_quitar_una_anterior_mantiene_alineada_la_que_suena(env):
    start_playing(env, 3)
    assert env.player.remove_from_queue(1)
    assert ids(env.player) == ["v0", "v2", "v3", "v4", "v5"]
    assert env.player.queue_snapshot()[1] == 2
    assert env.player.current_track().video_id == "v3"
    assert [t.video_id for t in env.player.upcoming()] == ["v4", "v5"]
    check_invariants(env.player)


def test_la_que_suena_no_se_quita(env):
    start_playing(env, 2)
    changes = count_changes(env.player)
    assert env.player.remove_from_queue(2) is False
    assert ids(env.player) == [f"v{i}" for i in range(6)] and changes == []
    check_invariants(env.player)


@pytest.mark.parametrize("pos", [-1, 6, 99])
def test_quitar_fuera_de_la_cola_no_hace_nada(env, pos):
    start_playing(env)
    changes = count_changes(env.player)
    assert env.player.remove_from_queue(pos) is False
    assert len(ids(env.player)) == 6 and changes == []


def test_quitar_en_una_cola_vacia_no_hace_nada(env):
    assert env.player.remove_from_queue(0) is False
    check_invariants(env.player)


def test_quitar_cualquier_fila_desde_cualquier_punto(env):
    names = [t.video_id for t in env.tracks]
    for pos, row in itertools.product(range(6), range(6)):
        if row == pos:
            continue
        player = fresh(env, pos=pos)
        assert player.remove_from_queue(row)
        expected = names[:row] + names[row + 1:]
        assert ids(player) == expected, (pos, row)
        assert player.current_track().video_id == names[pos], (pos, row)
        assert player.queue_snapshot()[1] == expected.index(names[pos])
        check_invariants(player)


def test_quitar_hasta_dejar_solo_la_que_suena(env):
    player = fresh(env, pos=2)
    while len(ids(player)) > 1:
        row = 0 if player.queue_snapshot()[1] != 0 else 1
        assert player.remove_from_queue(row)
        check_invariants(player)
    assert ids(player) == ["v2"] and player.queue_snapshot()[1] == 0
    assert player.remove_from_queue(0) is False


def test_con_la_misma_cancion_dos_veces_se_quita_la_fila_pedida(env):
    t = env.tracks
    player = fresh(env, [t[0], t[1], t[0], t[2]], pos=2)       # suena la segunda copia
    assert player.remove_from_queue(0)
    assert ids(player) == ["v1", "v0", "v2"] and player.queue_snapshot()[1] == 1
    check_invariants(player)


def test_quitar_con_aleatorio_y_volver_al_orden_de_la_lista(env):
    order = [3, 0, 5, 1, 4, 2]
    player = fresh(env, order=order, pos=0, shuffle=True)    # suena v3
    assert ids(player) == ["v3", "v0", "v5", "v1", "v4", "v2"]
    assert player.remove_from_queue(2)                        # v5
    assert ids(player) == ["v3", "v0", "v1", "v4", "v2"]
    check_invariants(player)
    player.set_shuffle(False)
    # La lista vuelve a su orden, sin la quitada y con la misma pista sonando.
    assert ids(player) == ["v0", "v1", "v2", "v3", "v4"]
    assert player.current_track().video_id == "v3"
    check_invariants(player)


def test_tras_quitar_se_resuelve_la_nueva_siguiente(env, monkeypatch):
    monkeypatch.setattr(config, "PREFETCH_AHEAD", 1)
    start_playing(env)
    assert wait_until(env.qapp, lambda: cached(env, "v1"))
    assert env.player.remove_from_queue(1)
    assert wait_until(env.qapp, lambda: cached(env, "v2"))
    assert env.ex.count("v2", "prefetch") == 1


# ---------------------------------------------------------------------------
# Mover
# ---------------------------------------------------------------------------
def test_mover_una_pista_para_que_suene_despues(env):
    start_playing(env, 0)
    changes = count_changes(env.player)
    assert env.player.move_in_queue(4, 1) is True
    assert ids(env.player) == ["v0", "v4", "v1", "v2", "v3", "v5"]
    assert env.player.current_track().video_id == "v0" and env.player.queue_snapshot()[1] == 0
    assert changes == [1] and env.mpv.loaded == ["url://v0/1"]
    check_invariants(env.player)
    env.player.next_track()
    assert env.player.current_track().video_id == "v4"


def test_mover_la_que_suena_no_la_corta(env):
    start_playing(env, 2)
    stops = env.mpv.stops
    assert env.player.move_in_queue(2, 5)
    assert ids(env.player) == ["v0", "v1", "v3", "v4", "v5", "v2"]
    assert env.player.queue_snapshot()[1] == 5 and env.player.current_track().video_id == "v2"
    assert env.player.state == PLAYING and env.mpv.stops == stops
    assert env.player.upcoming() == []
    check_invariants(env.player)


def test_mover_cualquier_fila_a_cualquier_sitio(env):
    names = [t.video_id for t in env.tracks]
    for pos, src, dst in itertools.product(range(6), range(6), range(6)):
        player = fresh(env, pos=pos)
        assert player.move_in_queue(src, dst)
        expected = list(names)
        expected.insert(dst, expected.pop(src))
        assert ids(player) == expected, (pos, src, dst)
        assert player.current_track().video_id == names[pos], (pos, src, dst)
        assert player.queue_snapshot()[1] == expected.index(names[pos]), (pos, src, dst)
        check_invariants(player)


def test_mover_a_su_mismo_sitio_no_avisa(env):
    player = fresh(env, pos=1)
    changes = count_changes(player)
    assert player.move_in_queue(3, 3) is True
    assert changes == [] and ids(player) == [f"v{i}" for i in range(6)]


@pytest.mark.parametrize("src, dst", [(-1, 2), (2, -1), (6, 0), (0, 6), (10, 10)])
def test_mover_fuera_de_la_cola_no_hace_nada(env, src, dst):
    player = fresh(env, pos=1)
    changes = count_changes(player)
    assert player.move_in_queue(src, dst) is False
    assert changes == [] and ids(player) == [f"v{i}" for i in range(6)]


def test_con_aleatorio_mover_no_cambia_el_orden_de_la_lista(env):
    order = [3, 0, 5, 1, 4, 2]
    player = fresh(env, order=order, pos=1, shuffle=True)     # suena v0
    assert player.move_in_queue(5, 2)                          # v2, justo después de la que suena
    assert ids(player) == ["v3", "v0", "v2", "v5", "v1", "v4"]
    assert player._base == list(range(6))
    check_invariants(player)
    player.set_shuffle(False)
    assert ids(player) == [f"v{i}" for i in range(6)]
    assert player.current_track().video_id == "v0"


def test_tras_mover_se_resuelve_la_nueva_siguiente(env, monkeypatch):
    monkeypatch.setattr(config, "PREFETCH_AHEAD", 1)
    start_playing(env)
    assert wait_until(env.qapp, lambda: cached(env, "v1"))
    assert env.player.move_in_queue(5, 1)
    assert wait_until(env.qapp, lambda: cached(env, "v5"))
    assert env.ex.count("v5", "prefetch") == 1


# ---------------------------------------------------------------------------
# Vaciar
# ---------------------------------------------------------------------------
def test_vaciar_deja_solo_la_que_suena_y_sigue_sonando(env):
    start_playing(env, 3)
    changes = count_changes(env.player)
    stops = env.mpv.stops
    env.player.clear_queue()
    assert ids(env.player) == ["v3"] and env.player.queue_snapshot()[1] == 0
    assert env.player._queue == [env.tracks[3]] and env.player._order == [0] and env.player._base == [0]
    assert env.player.state == PLAYING and env.mpv.stops == stops and len(env.mpv.loaded) == 1
    assert env.player.upcoming() == [] and changes == [1]
    check_invariants(env.player)
    # Lo que se añada después va detrás de ella.
    env.player.enqueue(env.tracks[0])
    env.player.play_next(env.tracks[5])
    assert ids(env.player) == ["v3", "v5", "v0"]
    check_invariants(env.player)


def test_vaciar_con_aleatorio(env):
    player = fresh(env, order=[3, 0, 5, 1, 4, 2], pos=2, shuffle=True)    # suena v5
    player.clear_queue()
    assert ids(player) == ["v5"]
    check_invariants(player)
    player.set_shuffle(False)
    assert ids(player) == ["v5"] and player.current_track().video_id == "v5"


def test_vaciar_detenida_conserva_la_pista_y_su_punto(env):
    player = fresh(env, pos=4, position=30.0)
    player.clear_queue()
    assert ids(player) == ["v4"] and player.state == STOPPED
    assert player.session_state()["position"] == 30.0


def test_vaciar_una_cola_vacia(env):
    changes = count_changes(env.player)
    env.player.clear_queue()
    assert env.player.current_track() is None and ids(env.player) == []
    assert changes == [1]
    check_invariants(env.player)


def test_vaciar_descarta_la_radio_que_estaba_en_camino(env):
    env.player.start_radio(env.tracks[2])
    env.player.clear_queue()                 # antes de que llegue la respuesta de la radio
    assert wait_until(env.qapp, lambda: env.player.radio_calls == ["v2"])
    settle(env.qapp, 0.2)
    assert ids(env.player) == ["v2"]


# ---------------------------------------------------------------------------
# La sesión guardada sigue a la cola editada
# ---------------------------------------------------------------------------
def saved_ids(db) -> list[str]:
    decoded = decode_queue(db.get_setting(session.QUEUE_KEY))
    if decoded is None:
        return []                       # aún sin escribir
    tracks, order = decoded
    return [tracks[i].video_id for i in order]


def test_la_sesion_guarda_la_cola_tras_quitar(env, keeper):
    start_playing(env, 3)
    assert wait_until(env.qapp, lambda: env.db.get_setting(session.QUEUE_KEY) is not None)
    assert env.player.remove_from_queue(1)
    assert wait_until(env.qapp, lambda: len(saved_ids(env.db)) == 5)
    assert saved_ids(env.db) == ["v0", "v2", "v3", "v4", "v5"]
    # El punto se escribe a la vez: la pista no cambió, pero sí su posición.
    assert saved_state(env.db)["pos"] == 2 and saved_state(env.db)["video_id"] == "v3"
    keeper.save()
    env.player.stop()
    player, _mpv, again = reopen(env)
    assert again.restore()
    assert ids(player) == ["v0", "v2", "v3", "v4", "v5"]
    assert player.current_track().video_id == "v3"


def test_la_sesion_guarda_el_orden_tras_mover(env, keeper):
    start_playing(env, 0)
    assert env.player.move_in_queue(5, 1)
    assert wait_until(env.qapp, lambda: saved_ids(env.db)[1:2] == ["v5"])
    keeper.save()
    env.player.stop()
    player, _mpv, again = reopen(env)
    assert again.restore()
    assert ids(player) == ["v0", "v5", "v1", "v2", "v3", "v4"]
    assert player.current_track().video_id == "v0"


def test_la_sesion_recuerda_la_copia_que_sonaba_tras_mover(env, keeper):
    t = env.tracks
    env.player.play_tracks([t[0], t[1], t[0]], 2)              # suena la segunda copia de v0
    assert wait_until(env.qapp, lambda: len(env.mpv.loaded) == 1)
    env.mpv.begin()
    assert env.player.move_in_queue(1, 0)                      # v1, v0, v0: sigue la de la pos. 2
    assert wait_until(env.qapp, lambda: saved_ids(env.db) == ["v1", "v0", "v0"])
    assert saved_state(env.db)["pos"] == 2
    env.player.stop()
    player, _mpv, again = reopen(env)
    assert again.restore()
    assert player.queue_snapshot()[1] == 2


def test_la_sesion_guarda_la_cola_vaciada(env, keeper):
    start_playing(env, 4)
    env.player.clear_queue()
    assert wait_until(env.qapp, lambda: saved_ids(env.db) == ["v4"])
    assert saved_state(env.db)["pos"] == 0
    env.player.stop()
    player, _mpv, again = reopen(env)
    assert again.restore()
    assert ids(player) == ["v4"] and player.current_track().video_id == "v4"


def test_con_la_sesion_desactivada_editar_no_escribe(env, keeper):
    keeper.set_enabled(False)
    start_playing(env, 0)
    env.player.remove_from_queue(2)
    env.player.move_in_queue(1, 3)
    env.player.clear_queue()
    settle(env.qapp, 0.1)
    assert env.db.get_setting(session.QUEUE_KEY) is None
    assert env.db.get_setting(session.STATE_KEY) is None


# ---------------------------------------------------------------------------
# El modelo de la tabla
# ---------------------------------------------------------------------------
def test_el_modelo_solo_deja_arrastrar_si_se_puede_reordenar(qapp):
    model = TrackModel()
    model.set_tracks([Track(f"m{i}", f"T{i}", "A") for i in range(3)])
    index = model.index(1, 0)
    assert not model.flags(index) & Qt.ItemFlag.ItemIsDragEnabled
    model.set_reorderable(True)
    assert model.flags(index) & Qt.ItemFlag.ItemIsDragEnabled
    assert not model.flags(index) & Qt.ItemFlag.ItemIsDropEnabled      # se suelta entre filas
    assert model.flags(model.index(-1, -1)) & Qt.ItemFlag.ItemIsDropEnabled
    assert model.supportedDropActions() & Qt.DropAction.MoveAction
    model.set_reorderable(False)
    assert not model.flags(index) & Qt.ItemFlag.ItemIsDragEnabled


def test_lo_arrastrado_lleva_la_fila_y_el_modelo_no_se_toca_al_soltar(qapp):
    model = TrackModel()
    model.set_tracks([Track(f"m{i}", f"T{i}", "A") for i in range(3)])
    mime = model.mimeData([model.index(2, c) for c in range(3)])
    assert model.dragged_rows(mime) == [2]
    assert model.dragged_rows(QMimeData()) == [] and model.dragged_rows(None) == []
    assert model.dropMimeData(mime, Qt.DropAction.MoveAction, 0, 0, model.index(-1, -1)) is False
    assert [t.video_id for t in model.tracks()] == ["m0", "m1", "m2"]


# ---------------------------------------------------------------------------
# En la ventana
# ---------------------------------------------------------------------------
def queue_window(window, pos=1):
    tracks = [Track(f"q{i}", f"Cola {i}", "Artista", "", 200) for i in range(5)]
    assert window.player.restore(tracks, pos=pos)
    window._navigate(mw.QUEUE_KEY)
    return tracks


def menu_texts(menu) -> list[str]:
    return [a.text() for a in menu.actions() if not a.isSeparator()]


def window_ids(window) -> list[str]:
    return [t.video_id for t in window.model.tracks()]


def test_el_menu_de_la_cola_permite_quitar_salvo_la_que_suena(window):
    tracks = queue_window(window, pos=1)
    current_menu = window._track_menu(tracks[1], 1)
    assert "Quitar de la cola" not in menu_texts(current_menu)
    menu = window._track_menu(tracks[3], 3)
    action = next(a for a in menu.actions() if a.text() == "Quitar de la cola")
    action.trigger()
    assert window_ids(window) == ["q0", "q1", "q2", "q4"]
    assert window.band._facts == ["4 pistas", "2 por delante"]


def test_quitar_de_la_cola_no_borra_otra_si_la_cola_cambio(window):
    tracks = queue_window(window, pos=0)
    menu = window._track_menu(tracks[3], 3)
    window.player.remove_from_queue(1)                  # la fila 3 ya es otra pista
    next(a for a in menu.actions() if a.text() == "Quitar de la cola").trigger()
    assert window_ids(window) == ["q0", "q2", "q3", "q4"]


def test_fuera_de_la_cola_el_menu_no_ofrece_quitar_de_la_cola(window):
    tracks = queue_window(window)
    window._navigate("PLuno")
    assert "Quitar de la cola" not in menu_texts(window._track_menu(tracks[3], 0))


def test_vaciar_la_cola_desde_la_cabecera_pide_confirmacion(window, monkeypatch):
    queue_window(window, pos=2)
    button = window.band.extra_btn
    assert button.isVisibleTo(window) and button.text() == "Vaciar la cola" and button.isEnabled()
    asked = []
    monkeypatch.setattr(window, "_confirm", lambda *args: asked.append(args) or False)
    button.click()
    assert len(asked) == 1 and "4 pistas" in asked[0][1] and len(window_ids(window)) == 5
    monkeypatch.setattr(window, "_confirm", lambda *args: True)
    button.click()
    assert window_ids(window) == ["q2"]
    assert window.player.current_track().video_id == "q2"
    assert not button.isEnabled()                       # ya no queda nada que vaciar
    # El botón de otra vista no hereda que estuviera apagado.
    window._navigate(mw.HISTORY_KEY)
    assert button.isEnabled() and button.toolTip() == ""


def test_con_la_cola_vacia_no_hay_boton_de_vaciar(window):
    window._navigate(mw.QUEUE_KEY)
    assert window.band.extra_btn.isHidden()


def test_solo_la_cola_se_reordena_arrastrando(window):
    queue_window(window)
    mode = QAbstractItemView.DragDropMode
    assert window.table.dragDropMode() == mode.InternalMove and window.table.dragEnabled()
    window._navigate("PLuno")
    assert window.table.dragDropMode() == mode.NoDragDrop and not window.table.dragEnabled()
    assert not window.model.flags(window.model.index(0, 0)) & Qt.ItemFlag.ItemIsDragEnabled
    window._navigate(mw.QUEUE_KEY)
    assert window.table.dragDropMode() == mode.InternalMove


def row_point(table, row, lower=False) -> QPoint:
    rect = table.visualRect(table.model().index(row, 0))
    return QPoint(rect.center().x(), rect.top() + (rect.height() * 3 // 4 if lower else rect.height() // 4))


def test_donde_queda_la_fila_al_soltarla(window):
    queue_window(window)
    table = window.table
    assert table.drop_target(0, row_point(table, 3)) == 2           # delante de la 3: queda en la 2
    assert table.drop_target(0, row_point(table, 3, lower=True)) == 3
    assert table.drop_target(4, row_point(table, 1)) == 1
    assert table.drop_target(4, row_point(table, 1, lower=True)) == 2
    assert table.drop_target(2, row_point(table, 2)) == 2           # sobre sí misma: no se mueve
    below = QPoint(10, table.visualRect(table.model().index(4, 0)).bottom() + 30)
    assert table.drop_target(0, below) == 4                          # bajo la última: al final
    assert table.drop_target(4, row_point(table, 0)) == 0


def test_soltar_una_fila_mueve_la_pista_en_la_cola(window):
    queue_window(window, pos=1)
    table = window.table
    mime = window.model.mimeData([window.model.index(4, 0)])
    event = QDropEvent(QPointF(row_point(table, 1)), Qt.DropAction.MoveAction, mime,
                       Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    event.source = lambda: table            # un arrastre de la propia tabla
    table.dropEvent(event)
    assert event.isAccepted() and event.dropAction() == Qt.DropAction.IgnoreAction
    assert window_ids(window) == ["q0", "q4", "q1", "q2", "q3"]
    assert window.player.current_track().video_id == "q1"
    assert table.currentIndex().row() == 1                # la selección sigue a la fila movida


def test_soltar_algo_de_fuera_no_mueve_nada(window):
    queue_window(window)
    table = window.table
    mime = window.model.mimeData([window.model.index(4, 0)])
    event = QDropEvent(QPointF(row_point(table, 1)), Qt.DropAction.MoveAction, mime,
                       Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    table.dropEvent(event)                   # sin origen: no viene de esta tabla
    assert window_ids(window) == [f"q{i}" for i in range(5)]


def test_la_senal_de_mover_fuera_de_la_cola_se_ignora(window):
    tracks = queue_window(window)
    window._navigate("PLuno")
    window.table.row_moved.emit(0, 2)
    assert [t.video_id for t in window.player.queue_snapshot()[0]] == [t.video_id for t in tracks]
