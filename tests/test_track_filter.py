"""Filtrar y ordenar dentro de una lista: el modelo intermedio, el campo y la traducción de filas (sin red)."""

import pytest
from PySide6.QtCore import QItemSelectionModel, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractItemView

from ytm_native.core.db import Track
from ytm_native.ui import main_window as mw
from ytm_native.ui.widgets import TrackFilterProxyModel, TrackModel, fold

from test_home_search import animations_on, net, window  # noqa: F401

ASC, DESC = Qt.SortOrder.AscendingOrder, Qt.SortOrder.DescendingOrder

SONGS = [
    Track("a", "Canción del alba", "Rocío Dúrcal", "Amor eterno", 612),
    Track("b", "bésame mucho", "Consuelo Velázquez", "Boleros", 185),
    Track("c", "Cielito lindo", "Pedro Infante", "Clásicos", 95),
    Track("d", "La bamba", "Ritchie Valens", "Ritchie", 124),
    Track("e", "Canción mixteca", "Lila Downs", "La Sandunga", 263),
    Track("f", "Zapateado", "Anónimo", "", None),
]


def ids(tracks) -> list[str]:
    return [t.video_id for t in tracks]


@pytest.fixture
def proxy(qapp):
    model = TrackModel()
    model.set_tracks(SONGS)
    proxy = TrackFilterProxyModel()
    proxy.setSourceModel(model)
    proxy.model = model
    return proxy


# ---------------------------------------------------------------------------
# Comparar sin tildes ni mayúsculas
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text, folded", [
    ("Canción", "cancion"), ("ÁÉÍÓÚ Ü Ñ", "aeiou u n"), ("Straße", "strasse"), ("", ""), ("ﬁn", "fin"),
])
def test_fold_quita_tildes_y_mayusculas(text, folded):
    assert fold(text) == folded


# ---------------------------------------------------------------------------
# Filtro
# ---------------------------------------------------------------------------
def test_sin_filtro_se_ven_todas_en_su_orden(proxy):
    assert ids(proxy.tracks()) == list("abcdef") and proxy.is_natural()


@pytest.mark.parametrize("text, expected", [
    ("cancion", "ae"),          # título, sin tilde
    ("CANCIÓN", "ae"),          # con tilde y en mayúsculas
    ("durcal", "a"),            # artista
    ("velazquez", "b"),
    ("sandunga", "e"),          # álbum
    ("ritchie", "d"),           # artista y álbum a la vez: una sola fila
    ("cancion mixteca", "e"),   # varias palabras: todas tienen que estar
    ("mixteca cancion", "e"),   # en cualquier orden
    ("lila sandunga", "e"),     # repartidas entre artista y álbum
    ("cancion bamba", ""),
    ("anonimo", "f"),
    ("   ", "abcdef"),          # solo espacios: no filtra
    ("zzz", ""),
])
def test_el_filtro_mira_titulo_artista_y_album(proxy, text, expected):
    proxy.set_filter_text(text)
    assert ids(proxy.tracks()) == list(expected)
    assert proxy.filter_text_active() == bool(text.strip())


def test_filtrar_no_toca_el_modelo_de_debajo(proxy):
    proxy.set_filter_text("cancion")
    assert proxy.rowCount() == 2
    assert ids(proxy.model.tracks()) == list("abcdef") and proxy.model.rowCount() == 6
    proxy.set_filter_text("")
    assert ids(proxy.tracks()) == list("abcdef")


def test_lo_que_llega_a_la_lista_pasa_por_el_filtro(proxy):
    proxy.set_filter_text("cancion")
    proxy.model.set_tracks(SONGS + [Track("g", "Otra canción", "X"), Track("h", "Nada", "Y")])
    assert ids(proxy.tracks()) == ["a", "e", "g"]


# ---------------------------------------------------------------------------
# Orden
# ---------------------------------------------------------------------------
def test_la_duracion_se_ordena_por_segundos_no_como_texto(proxy):
    # Como texto, "10:12" (612 s) iría antes que "2:04" y "3:05".
    proxy.sort(2, ASC)
    assert ids(proxy.tracks()) == ["f", "c", "d", "b", "e", "a"]   # sin duración, como 0
    proxy.sort(2, DESC)
    assert ids(proxy.tracks()) == ["a", "e", "b", "d", "c", "f"]
    as_text = sorted(SONGS, key=lambda t: proxy.model.data(proxy.model.index(SONGS.index(t), 2)))
    assert ids(as_text) != ids(proxy.tracks())[::-1]                 # el orden de texto sería otro


def test_el_titulo_se_ordena_sin_mayusculas_ni_tildes(proxy):
    proxy.sort(0, ASC)
    # «bésame» va antes que «Canción» aunque empiece en minúscula y lleve tilde.
    assert ids(proxy.tracks()) == ["b", "a", "e", "c", "d", "f"]


def test_el_album_se_ordena_y_sin_album_va_primero(proxy):
    proxy.sort(1, ASC)
    assert ids(proxy.tracks()) == ["f", "a", "b", "c", "e", "d"]


def test_la_columna_de_notas_se_ordena_por_la_nota(proxy):
    notes = {"a": "c", "b": "a", "c": "b", "d": "e", "e": "d", "f": "f"}
    proxy.model.set_tracks(SONGS, notes, "Escuchada")
    proxy.sort(1, ASC)
    assert ids(proxy.tracks()) == ["b", "c", "a", "e", "d", "f"]


def test_los_empates_conservan_el_orden_de_la_lista(proxy):
    same = [Track(f"t{i}", "Igual", "X", "", 100) for i in range(5)]
    proxy.model.set_tracks(same)
    proxy.sort(0, ASC)
    assert ids(proxy.tracks()) == [f"t{i}" for i in range(5)]


def test_quitar_el_orden_devuelve_la_lista_original(proxy):
    proxy.sort(2, DESC)
    assert not proxy.is_natural()
    proxy.sort(-1)
    assert ids(proxy.tracks()) == list("abcdef") and proxy.is_natural()


def test_filtro_y_orden_juntos(proxy):
    proxy.set_filter_text("cancion")
    proxy.sort(2, ASC)
    assert ids(proxy.tracks()) == ["e", "a"]


def test_la_cabecera_lleva_la_flecha_del_orden(proxy):
    head = lambda col: proxy.headerData(col, Qt.Orientation.Horizontal)  # noqa: E731
    assert [head(c) for c in range(3)] == ["Título", "Álbum", "Duración"]
    proxy.sort(2, ASC)
    assert head(2) == "Duración ↑" and head(0) == "Título"
    proxy.sort(2, DESC)
    assert head(2) == "Duración ↓"
    proxy.sort(-1)
    assert head(2) == "Duración"


# ---------------------------------------------------------------------------
# Traducción de filas
# ---------------------------------------------------------------------------
def test_las_filas_se_traducen_en_los_dos_sentidos(proxy):
    proxy.set_filter_text("cancion")
    proxy.sort(2, ASC)                                   # e (263 s), a (612 s)
    assert [proxy.source_row(r) for r in range(2)] == [4, 0]
    assert proxy.view_row(0) == 1 and proxy.view_row(4) == 0
    assert proxy.view_row(2) == -1                       # filtrada: no se ve
    assert proxy.track_at(0).video_id == "e"


def test_lo_que_suena_se_marca_en_su_fila_de_la_vista(proxy):
    proxy.model.set_current("a")
    proxy.sort(2, DESC)
    assert [proxy.is_current(r) for r in range(proxy.rowCount())] == [True] + [False] * 5
    proxy.sort(2, ASC)
    assert proxy.is_current(5) and not proxy.is_current(0)


def test_el_arrastre_se_pide_al_modelo_de_debajo(proxy):
    proxy.set_reorderable(True)
    assert proxy.flags(proxy.index(0, 0)) & Qt.ItemFlag.ItemIsDragEnabled
    mime = proxy.mimeData([proxy.index(3, 0)])
    assert proxy.dragged_rows(mime) == [3]


# ---------------------------------------------------------------------------
# En la ventana
# ---------------------------------------------------------------------------
@pytest.fixture
def local(window, db, monkeypatch):
    monkeypatch.setattr(mw.bootstrap, "missing_binaries", lambda: [])
    pid = db.create_local_playlist("Para la tarde", SONGS)
    window._reload_sidebar()
    window._navigate(pid)
    return pid


@pytest.fixture
def plays(window, monkeypatch):
    calls = []
    monkeypatch.setattr(window.player, "play_tracks", lambda tracks, start=0: calls.append((ids(tracks), start)))
    return calls


def row_point(table, row) -> QPoint:
    return table.visualRect(table.model().index(row, 0)).center()


def double_click(table, row) -> None:
    """Doble clic en una fila. La ventana atiende ``doubleClicked`` y también
    ``activated``, que Qt emite con el mismo doble clic: las pruebas miran a
    qué pista llega cada llamada, no cuántas hay."""
    # Como el ratón de verdad: la pulsación primero (sin ella Qt no da por
    # bueno el doble clic) y luego el doble clic.
    point = row_point(table, row)
    QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    QTest.mouseDClick(table.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)


def view_ids(window) -> list[str]:
    return ids(window.proxy.tracks())


def test_la_tabla_ve_la_lista_a_traves_del_filtro(window):
    assert window.table.model() is window.proxy and window.proxy.sourceModel() is window.model
    head = window.table.horizontalHeader()
    assert head.sectionsClickable() and window.table.isSortingEnabled()
    assert head.isSortIndicatorClearable()
    assert window.proxy.sortColumn() == -1                # se abre en el orden de la lista


def test_el_campo_de_filtro_esta_en_las_listas_y_no_en_la_busqueda(window, local):
    box = window.list_filter
    assert box.isVisibleTo(window) and box.placeholderText() == "Filtrar en esta lista…"
    assert box.isClearButtonEnabled()
    for key in ("PLuno", mw.HISTORY_KEY):
        window._navigate(key)
        assert box.isVisibleTo(window) or window.model.rowCount() == 0, key
    window._navigate(mw.SEARCH_KEY)
    assert not box.isVisibleTo(window)


def test_escribir_filtra_al_momento(window, local):
    QTest.keyClicks(window.list_filter, "cancion")
    assert view_ids(window) == ["a", "e"]
    assert ids(window.model.tracks()) == list("abcdef")              # la lista no cambia
    assert window.filter_note.isHidden()


def test_sin_coincidencias_se_dice_y_no_se_puede_reproducir(window, local):
    window.list_filter.setText("zzz")
    assert window.proxy.rowCount() == 0
    assert window.filter_note.isVisibleTo(window)
    assert window.filter_note.text() == "Ninguna canción coincide con «zzz»."
    assert not window.play_all_btn.isEnabled() and not window.shuffle_all_btn.isEnabled()
    window.list_filter.clear()
    assert window.filter_note.isHidden() and window.play_all_btn.isEnabled()


def test_esc_vacia_el_filtro_y_flecha_abajo_baja_a_las_filas(window, local):
    window.activateWindow()
    QTest.qWaitForWindowActive(window)
    box = window.list_filter
    box.setFocus()
    QTest.keyClicks(box, "cancion")
    QTest.keyClick(box, Qt.Key.Key_Down)
    assert window.table.hasFocus() and window.table.selected_rows() == [0]
    box.setFocus()
    QTest.keyClick(box, Qt.Key.Key_Escape)
    assert box.text() == "" and window.proxy.rowCount() == 6


def test_otra_lista_llega_sin_filtro_ni_orden(window, local):
    window.list_filter.setText("cancion")
    window.table.horizontalHeader().setSortIndicator(2, DESC)
    window._navigate("PLuno")
    assert window.list_filter.text() == "" and window.proxy.sortColumn() == -1
    assert view_ids(window) == ["PLuno-0", "PLuno-1", "PLuno-2"]


def test_refrescar_la_misma_lista_conserva_filtro_y_orden(window, local):
    window.list_filter.setText("cancion")
    window.table.horizontalHeader().setSortIndicator(2, ASC)
    window._after_local_change(local)                      # lo que pasa tras añadir o quitar
    assert window.list_filter.text() == "cancion" and window.proxy.sortColumn() == 2
    assert view_ids(window) == ["e", "a"]


def test_clic_en_la_cabecera_ordena_y_al_tercero_vuelve_al_orden_de_la_lista(window, local):
    head = window.table.horizontalHeader()
    point = QPoint(head.sectionViewportPosition(2) + 10, head.height() // 2)
    seen = []
    for _ in range(3):
        QTest.mouseClick(head.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
        seen.append(view_ids(window))
    assert seen == [["f", "c", "d", "b", "e", "a"], ["a", "e", "b", "d", "c", "f"], list("abcdef")]


# -- cada clic llega a la pista que se ve -----------------------------------
def test_doble_clic_en_una_fila_filtrada_reproduce_esa_pista(window, local, plays):
    window.list_filter.setText("mixteca")
    double_click(window.table, 0)
    assert plays and set(map(str, plays)) == {str((["e"], 0))}


def test_doble_clic_con_la_lista_ordenada_sigue_el_orden_que_se_ve(window, local, plays):
    window.table.horizontalHeader().setSortIndicator(2, DESC)
    double_click(window.table, 1)
    # Suena «e» y siguen las que vienen detrás en la vista: b, d…
    assert plays and set(map(str, plays)) == {str((["a", "e", "b", "d", "c", "f"], 1))}
    assert window.proxy.track_at(1).video_id == "e"


def test_intro_sobre_una_fila_filtrada(window, local, plays):
    window.list_filter.setText("cancion")
    window.table.setFocus()
    window.table.setCurrentIndex(window.proxy.index(1, 0))
    QTest.keyClick(window.table, Qt.Key.Key_Return)
    assert plays == [(["a", "e"], 1)]


def test_clic_en_la_caratula_de_una_fila_filtrada(window, local, plays):
    window.list_filter.setText("bamba")
    window.table.thumb_clicked.emit(0)
    assert plays == [(["d"], 0)]


def test_reproducir_y_aleatorio_usan_lo_que_se_ve(window, local, plays):
    window.list_filter.setText("cancion")
    window.play_all_btn.click()
    window.player.shuffle = True                            # sin barajar de verdad la cola
    window.shuffle_all_btn.click()
    assert plays[0] == (["a", "e"], 0)
    assert plays[1][0] == ["a", "e"] and plays[1][1] in (0, 1)


def test_el_menu_de_una_fila_filtrada_es_de_esa_pista(window, local, monkeypatch):
    opened = []

    class Menu:
        def exec(self, _pos):
            pass

    monkeypatch.setattr(window, "_track_menu", lambda track, row=None: opened.append((track.video_id, row)) or Menu())
    window.list_filter.setText("cielito")
    window._on_table_menu(row_point(window.table, 0))
    assert opened == [("c", 0)]


def test_el_menu_de_varias_filtradas_lleva_las_que_se_ven(window, local, monkeypatch):
    opened = []

    class Menu:
        def exec(self, _pos):
            pass

    monkeypatch.setattr(window, "_tracks_menu", lambda tracks: opened.append(ids(tracks)) or Menu())
    window.table.horizontalHeader().setSortIndicator(2, DESC)    # a, e, b, d, c, f
    sel = window.table.selectionModel()
    flags = QItemSelectionModel.SelectionFlag
    for row in (1, 3):
        sel.select(window.proxy.index(row, 0), flags.Select | flags.Rows)
    window._on_table_menu(row_point(window.table, 3))
    assert opened == [["e", "d"]]


def test_supr_en_una_lista_filtrada_quita_las_que_se_ven(window, db, local, monkeypatch):
    monkeypatch.setattr(window, "_confirm", lambda *args: True)
    window.list_filter.setText("cancion")                    # a, e: filas 0 y 4 de la lista
    window.table.selectAll()
    QTest.keyClick(window.table, Qt.Key.Key_Delete)
    assert ids(db.playlist_tracks(local)) == ["b", "c", "d", "f"]
    assert window.proxy.rowCount() == 0 and window.list_filter.text() == "cancion"


def test_en_la_cola_filtrada_se_salta_a_su_posicion_real(window, monkeypatch):
    monkeypatch.setattr(mw.bootstrap, "missing_binaries", lambda: [])
    assert window.player.restore(list(SONGS), pos=0)
    window._navigate(mw.QUEUE_KEY)
    jumps = []
    monkeypatch.setattr(window.player, "jump_to", jumps.append)
    window.list_filter.setText("mixteca")
    double_click(window.table, 0)
    assert jumps and set(jumps) == {4}


def test_en_la_cola_filtrada_quitar_quita_la_que_se_ve(window):
    assert window.player.restore(list(SONGS), pos=0)
    window._navigate(mw.QUEUE_KEY)
    window.list_filter.setText("bamba")
    menu = window._track_menu(window.proxy.track_at(0), 0)
    next(a for a in menu.actions() if a.text() == "Quitar de la cola").trigger()
    assert ids(window.player.queue_snapshot()[0]) == ["a", "b", "c", "e", "f"]


def test_en_la_cola_ordenada_la_que_suena_tampoco_se_quita(window):
    assert window.player.restore(list(SONGS), pos=0)          # suena «a», la más larga
    window._navigate(mw.QUEUE_KEY)
    window.table.horizontalHeader().setSortIndicator(2, DESC)
    assert window.proxy.track_at(0).video_id == "a"
    texts = [a.text() for a in window._track_menu(window.proxy.track_at(0), 0).actions()]
    assert "Quitar de la cola" not in texts
    texts = [a.text() for a in window._track_menu(window.proxy.track_at(5), 5).actions()]
    assert "Quitar de la cola" in texts


# -- arrastre ---------------------------------------------------------------
MODE = QAbstractItemView.DragDropMode


@pytest.mark.parametrize("where", ["local", "queue"])
def test_con_filtro_u_orden_no_se_arrastra(window, local, where):
    if where == "queue":
        assert window.player.restore(list(SONGS), pos=0)
        window._navigate(mw.QUEUE_KEY)
    table, head = window.table, window.table.horizontalHeader()
    assert table.dragDropMode() == MODE.InternalMove and table.dragEnabled()
    window.list_filter.setText("cancion")
    assert table.dragDropMode() == MODE.NoDragDrop and not table.dragEnabled()
    assert not window.proxy.flags(window.proxy.index(0, 0)) & Qt.ItemFlag.ItemIsDragEnabled
    window.list_filter.clear()
    assert table.dragDropMode() == MODE.InternalMove
    head.setSortIndicator(0, ASC)
    assert table.dragDropMode() == MODE.NoDragDrop
    head.setSortIndicator(-1, ASC)
    assert table.dragDropMode() == MODE.InternalMove and table.dragEnabled()


def test_un_movimiento_que_llegue_con_la_lista_ordenada_se_ignora(window, db, local):
    window.table.horizontalHeader().setSortIndicator(2, DESC)
    window.table.row_moved.emit(0, 3)
    assert ids(db.playlist_tracks(local)) == list("abcdef")


def test_sin_filtro_ni_orden_arrastrar_sigue_moviendo_la_fila(window, db, local):
    window.table.row_moved.emit(0, 3)
    assert ids(db.playlist_tracks(local)) == ["b", "c", "d", "a", "e", "f"]
    assert window.table.selected_rows() == [3]
