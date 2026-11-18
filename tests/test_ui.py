"""Piezas de la interfaz: movimiento interrumpible, botones, barra de progreso y avisos."""

import os
import time

import pytest
from PySide6.QtCore import QEventLoop, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QHBoxLayout, QWidget

from ytm_native.core.db import Track
from ytm_native.ui import art, focus, motion, theme
from ytm_native.ui import main_window as mw
from ytm_native.ui.art import ArtLoader, prune_cache, tint_from_image
from ytm_native.ui.components import CoverLabel, ElidedLabel, PillButton, PlaylistBand, SeekSlider, ToastHost
from ytm_native.ui.home import CardGrid, SleeveCard
from ytm_native.ui.motion import Tween
from ytm_native.ui.widgets import AccountDialog, TrackModel

# La ventana entera, sin red: los mismos dobles que las pruebas del inicio.
from test_home_search import FakeNet, net, wait_until, window  # noqa: F401 - fixtures de pytest


def wait(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


@pytest.fixture
def host(qapp):
    widget = QWidget()
    widget.resize(600, 400)
    widget.show()
    yield widget
    widget.close()


@pytest.fixture(autouse=True)
def animations_on(monkeypatch):
    monkeypatch.setattr(motion, "_reduced", False)


def test_tween_retoma_desde_donde_esta(host):
    tween = Tween(host, 0.0, lambda _v: None, ms=200)
    tween.to(1.0)
    wait(80)
    middle = float(tween.value)
    assert 0.0 < middle < 1.0
    # Cambiar de destino a mitad de camino no reinicia: sigue desde el valor actual.
    tween.to(0.0)
    wait(20)
    assert float(tween.value) <= middle
    wait(320)
    assert float(tween.value) == 0.0


def test_animaciones_reducidas_quitan_el_movimiento_pero_no_los_fundidos(host, monkeypatch):
    monkeypatch.setattr(motion, "_reduced", True)
    move = Tween(host, 0.0, lambda _v: None, ms=200, movement=True)
    fade = Tween(host, 0.0, lambda _v: None, ms=200)
    move.to(1.0)
    fade.to(1.0)
    assert float(move.value) == 1.0
    assert float(fade.value) < 1.0 and fade.running()


def test_tween_oculto_no_anima(qapp):
    hidden = QWidget()
    tween = Tween(hidden, 0.0, lambda _v: None, ms=200)
    tween.to(1.0)
    assert float(tween.value) == 1.0 and not tween.running()


def test_boton_se_hunde_con_el_raton_y_no_con_el_teclado(host):
    button = PillButton("Reproducir", "primary", parent=host)
    button.show()
    clicks = []
    button.clicked.connect(lambda: clicks.append(1))
    QTest.mousePress(button, Qt.MouseButton.LeftButton)
    wait(motion.PRESS_MS + 60)
    assert float(button._scale.value) == pytest.approx(PillButton.PRESS_SCALE, abs=0.002)
    QTest.mouseRelease(button, Qt.MouseButton.LeftButton)
    wait(motion.RELEASE_MS + 60)
    assert float(button._scale.value) == pytest.approx(1.0, abs=0.002)
    button.setFocus()
    QTest.keyClick(button, Qt.Key.Key_Space)
    assert float(button._scale.value) == 1.0 and not button._scale.running()
    assert len(clicks) == 2


def test_anillo_de_foco_solo_con_teclado(host):
    button = PillButton("Aleatorio", parent=host)
    button.show()
    host.activateWindow()
    button.setFocus()
    wait(30)
    if not button.hasFocus():
        pytest.skip("esta plataforma de pruebas no entrega el foco a la ventana")
    focus.mouse_pressed()
    assert not button._ring()
    focus.key_pressed(Qt.Key.Key_Tab)
    assert button._ring()
    focus.mouse_pressed()
    assert not button._ring()


def test_barra_salta_al_clic_y_avisa(host):
    slider = SeekSlider(host, nudge_keys=True)
    slider.setRange(0, 1000)
    slider.resize(416, 20)
    slider.show()
    pressed, released, nudges = [], [], []
    slider.sliderPressed.connect(lambda: pressed.append(1))
    slider.sliderReleased.connect(lambda: released.append(1))
    slider.nudged.connect(nudges.append)
    x = slider.PAD + (slider.width() - 2 * slider.PAD) // 4
    QTest.mouseClick(slider, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x, 10))
    assert slider.value() == pytest.approx(250, abs=3)
    assert pressed and released
    QTest.keyClick(slider, Qt.Key.Key_Left)
    QTest.keyClick(slider, Qt.Key.Key_Right)
    assert nudges == [-1, 1]
    assert slider.value() == pytest.approx(250, abs=3)   # las flechas no mueven el valor por su cuenta


def test_etiqueta_de_ancho_fijo_no_se_solapa(host):
    row = QHBoxLayout(host)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(4)
    left = ElidedLabel(theme.ui_font(12), theme.HUMO, width=44)
    slider = SeekSlider()
    right = ElidedLabel(theme.ui_font(12), theme.HUMO, width=44)
    for widget, stretch in ((left, 0), (slider, 1), (right, 0)):
        row.addWidget(widget, stretch)
    wait(30)
    assert left.width() == 44 and right.width() == 44
    assert left.geometry().right() < slider.geometry().left()
    assert slider.geometry().right() < right.geometry().left()


def test_avisos_no_se_duplican_y_se_limitan_a_tres(host):
    toasts = ToastHost(host, lambda: 380)
    toasts.show("Añadida a la cola")
    toasts.show("Añadida a la cola")
    assert len(toasts._toasts) == 1
    for text in ("Dos", "Tres", "Cuatro"):
        toasts.show(text)
    active = [t for t in toasts._toasts if not t.leaving]
    assert [t.text for t in active] == ["Dos", "Tres", "Cuatro"]
    wait(motion.EXIT_MS + 120)
    assert len(toasts._toasts) == 3
    newest = toasts._toasts[-1]
    assert newest.y() + newest.height() == 380 - 12


def test_avisos_pausan_su_reloj_con_la_ventana_inactiva(host):
    toasts = ToastHost(host, lambda: 380)
    toasts.show("Sincronización terminada")
    toast = toasts._toasts[0]
    assert toast._timer.isActive()
    toasts.set_inactive(True)
    assert not toast._timer.isActive()
    toasts.set_inactive(False)
    assert toast._timer.isActive()
    toasts.dismiss(toast)
    wait(motion.EXIT_MS + 120)
    assert toasts._toasts == []


def test_boton_recoge_su_texto_poco_a_poco(host):
    button = PillButton("Sincronizar lista", "ghost", glyph="sync", parent=host)
    button.show()
    full = button.sizeHint().width()
    button.set_icon_only(True)
    wait(60)
    middle = button.sizeHint().width()
    assert PillButton.HEIGHT < middle < full          # a mitad de camino, ni entero ni círculo
    wait(motion.REFLOW_MS + 80)
    assert button.sizeHint().width() == PillButton.HEIGHT
    assert button.accessibleName() == "Sincronizar lista" and button.toolTip() == "Sincronizar lista"
    button.set_icon_only(False, animate=False)
    assert button.sizeHint().width() == full


# ---------------------------------------------------------------------------
# Contraste (WCAG AA)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("fg", [theme.PAPEL, theme.HUMO, theme.TENUE])
@pytest.mark.parametrize("bg", [theme.PANEL, theme.RELIEVE, theme.RELIEVE_2, theme.BANDA_NEUTRA])
def test_texto_del_tema_cumple_contraste_aa(qapp, fg, bg):
    assert theme.contrast_ratio(fg, bg) >= theme.MIN_TEXT_CONTRAST


def test_contraste_de_referencia():
    assert theme.contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0)
    assert theme.contrast_ratio("#777777", "#ffffff") == pytest.approx(4.48, abs=0.01)
    # Un texto translúcido se mide con lo que de verdad se ve sobre el fondo.
    assert theme.contrast_ratio(QColor(0, 0, 0, 0), "#ffffff") == pytest.approx(1.0)


def _plain(color: str) -> QImage:
    image = QImage(48, 48, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    return image


@pytest.mark.parametrize("color", ["#f5d90a", "#3ddc4a", "#29e3ea", "#ff9f1a", "#d0e83a", "#2a3bd8"])
def test_tinta_de_caratulas_claras_se_lee_sobre_el_panel_y_la_fila(qapp, color):
    tint = tint_from_image(_plain(color))
    assert tint.from_art
    panel = QColor(theme.PANEL)
    row = QColor(tint.ink)
    row.setAlphaF(art.INK_ROW_ALPHA)
    assert theme.contrast_ratio(tint.ink, panel) >= theme.MIN_TEXT_CONTRAST
    assert theme.contrast_ratio(tint.ink, theme.composite(row, panel)) >= theme.MIN_TEXT_CONTRAST
    # Y el texto más tenue de la cabecera se lee sobre la banda.
    assert theme.contrast_ratio(art.BAND_TEXT, tint.band) >= theme.MIN_TEXT_CONTRAST
    assert theme.contrast_ratio(theme.PAPEL, tint.band) >= theme.MIN_TEXT_CONTRAST


# ---------------------------------------------------------------------------
# Caché de carátulas
# ---------------------------------------------------------------------------
def test_cache_de_caratulas_se_escribe_entera_y_se_poda(qapp, tmp_path):
    loader = ArtLoader(cache_dir=tmp_path / "caratulas")
    loader._store("https://x/a.jpg", b"a" * 100)
    path = loader._path("https://x/a.jpg")
    assert path.read_bytes() == b"a" * 100
    assert not list(path.parent.glob("*.tmp"))               # sin temporales a la vista
    folder = tmp_path / "caratulas" / "zz"
    folder.mkdir()
    now = time.time()
    for i, age in enumerate((300, 200, 100)):
        f = folder / f"{i}.img"
        f.write_bytes(b"x" * 1000)
        os.utime(f, (now - age, now - age))
    orphan = folder / "3.img.99.tmp"
    orphan.write_bytes(b"x")
    os.utime(orphan, (now - 7200, now - 7200))
    removed = prune_cache(tmp_path / "caratulas", limit_bytes=2200)
    assert removed == 2                                       # el temporal huérfano y la más vieja
    assert sorted(p.name for p in folder.iterdir()) == ["1.img", "2.img"]


def test_filas_leen_pocas_caratulas_por_fotograma(qapp, tmp_path):
    loader = ArtLoader(cache_dir=tmp_path / "caratulas")
    urls = [f"https://x/{i}.jpg" for i in range(ArtLoader.DECODES_PER_FRAME + 3)]
    image = _plain("#336699")
    for url in urls:
        path = loader._path(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(str(path), "PNG")
    ready = []
    loader.loaded.connect(ready.append)
    got = [loader.pixmap(url, 40, 5, 1.0, lazy=True) for url in urls]
    assert sum(p is not None for p in got) == ArtLoader.DECODES_PER_FRAME
    # Las que no cupieron llegan en el fotograma siguiente y lo avisan.
    wait(30)
    assert sorted(ready) == sorted(urls[ArtLoader.DECODES_PER_FRAME:])
    assert all(loader.pixmap(url, 40, 5, 1.0, lazy=True) is not None for url in urls)


def test_caratula_espera_a_la_nueva_sin_pasar_por_la_funda_vacia(qapp, tmp_path):
    loader = ArtLoader(cache_dir=tmp_path / "caratulas")
    old_url, new_url = "https://x/vieja.jpg", "https://x/nueva.jpg"
    path = loader._path(old_url)
    path.parent.mkdir(parents=True, exist_ok=True)
    _plain("#aa3366").save(str(path), "PNG")
    loader._enqueue = lambda url: None                        # sin red: la nueva «viene de camino»
    cover = CoverLabel(loader, 56)
    cover.set_url(old_url)
    cover.show()
    first = cover._shown
    cover.set_url(new_url)
    assert cover._shown is first and cover._waiting           # la de antes sigue entera
    loader._remember(new_url, _plain("#3366aa"))
    loader.loaded.emit(new_url)
    assert not cover._waiting and cover._shown is not first and cover._previous is first
    cover.close()


# ---------------------------------------------------------------------------
# Modelo de la tabla: cambios por partes
# ---------------------------------------------------------------------------
def test_marca_de_lo_que_suena_repinta_solo_dos_filas(qapp):
    model = TrackModel()
    model.set_tracks([Track(f"t{i}", f"T{i}", "A") for i in range(500)])
    changed = []
    model.dataChanged.connect(lambda a, b, _roles=None: changed.append((a.row(), b.row())))
    model.set_current("t10")
    model.set_current("t11")
    assert changed == [(10, 10), (10, 10), (11, 11)]


def test_cola_retocada_inserta_filas_sin_reiniciar_el_modelo(qapp):
    model = TrackModel()
    tracks = [Track(f"t{i}", f"T{i}", "A") for i in range(500)]
    model.set_tracks(tracks)
    resets, inserted = [], []
    model.modelReset.connect(lambda: resets.append(1))
    model.rowsInserted.connect(lambda _p, first, last: inserted.append((first, last)))
    model.set_tracks(tracks[:3] + [Track("nueva", "Nueva", "B")] + tracks[3:])
    assert not resets and inserted == [(3, 3)]
    assert model.track_at(3).video_id == "nueva" and model.rowCount() == 501
    model.set_tracks([Track(f"otra{i}", "O", "C") for i in range(500)])
    assert resets == [1]                                      # otra lista: se empieza de cero


# ---------------------------------------------------------------------------
# Aros de la cabecera
# ---------------------------------------------------------------------------
@pytest.fixture
def band(qapp, tmp_path):
    widget = PlaylistBand(ArtLoader(cache_dir=tmp_path / "caratulas"))
    widget.resize(900, PlaylistBand.HEIGHT)
    widget.show()
    yield widget
    widget.close()


def test_aros_giran_solo_con_musica_y_frenan_al_pausar(band):
    assert not band.rings_running()                           # en reposo, ningún temporizador
    start = list(band._angles)
    band.set_playing(True)
    assert band.rings_running() and band._ring_timer.interval() == motion.FRAME_MS
    wait(motion.SPIN_MS + 120)
    assert band._level == pytest.approx(1.0)
    assert band._angles[0] != start[0] and band._angles[1] != start[1]
    band.set_playing(False)
    assert band.rings_running()                               # frena: no se corta en seco
    wait(motion.SPIN_MS // 3)
    assert 0.0 < band._level < 1.0
    wait(motion.SPIN_MS + 120)
    assert not band.rings_running() and band._level == 0.0


def test_aros_repintan_solo_su_recuadro(band, monkeypatch):
    rects = []
    original = band.update
    monkeypatch.setattr(band, "update", lambda *args: (rects.append(args), original(*args)))
    band.set_playing(True)
    wait(120)
    assert rects and all(len(args) == 1 and isinstance(args[0], QRect) for args in rects)
    area = band.rings_rect()
    assert all(args[0] == area for args in rects)
    assert area.width() * area.height() < band.width() * band.height() / 3
    assert area.right() == band.rect().right() and area.top() == 0


def test_aros_se_detienen_con_la_ventana_minimizada_u_oculta(band):
    band.set_playing(True)
    assert band.rings_running()
    band.set_window_shown(False)
    assert not band.rings_running()
    band.set_window_shown(True)
    assert band.rings_running()
    band.hide()
    assert not band.rings_running()
    band.show()
    assert band.rings_running()


def test_aros_quietos_con_animaciones_reducidas(band, monkeypatch):
    monkeypatch.setattr(motion, "_reduced", True)
    band.set_playing(True)
    assert not band.rings_running()
    wait(60)
    assert band._angles == [band.REST_OUTER, band.REST_INNER] and band._breath == (0.0, 0.0)


# ---------------------------------------------------------------------------
# En la ventana
# ---------------------------------------------------------------------------
def test_espacio_acciona_el_control_con_foco(window, monkeypatch):
    toggles = []
    monkeypatch.setattr(window.player, "toggle_pause", lambda: toggles.append(1))
    window._on_focus_changed(None, window.shuffle_btn)
    assert not window.space_shortcut.isEnabled()              # el botón recibe el espacio
    window._on_focus_changed(None, window.search_edit)
    assert not window.space_shortcut.isEnabled()
    window._on_focus_changed(None, window.volume_slider)
    assert not window.space_shortcut.isEnabled()
    window._on_focus_changed(None, window.table)
    assert window.space_shortcut.isEnabled()                  # en la lista, pausa o reanuda
    clicks = []
    window.shuffle_btn.clicked.connect(lambda: clicks.append(1))
    window.shuffle_btn.setFocus()
    window._on_focus_changed(None, window.shuffle_btn)
    QTest.keyClick(window.shuffle_btn, Qt.Key.Key_Space)
    assert clicks == [1] and toggles == []


def test_flechas_de_la_barra_lateral_esperan_a_que_se_pare(window):
    window.sidebar.setFocus()
    window._select_in_sidebar(mw.HOME_KEY)
    QTest.keyClick(window.sidebar, Qt.Key.Key_Down)
    QTest.keyClick(window.sidebar, Qt.Key.Key_Down)
    assert window.sidebar.currentItem().data(mw.ROLE_ID) == mw.QUEUE_KEY
    assert window._view_key == mw.HOME_KEY                    # aún no se cargó nada
    assert wait_until(lambda: window._view_key == mw.QUEUE_KEY, timeout_ms=window.NAV_DEBOUNCE_MS + 400)


def test_nueva_lista_con_flechas_espera_a_intro(window, monkeypatch):
    opened = []
    monkeypatch.setattr(window, "_create_local_playlist", lambda *a, **k: opened.append(1))
    window.sidebar.setFocus()
    window._select_in_sidebar(mw.HISTORY_KEY)
    QTest.keyClick(window.sidebar, Qt.Key.Key_Down)
    assert window.sidebar.currentItem().data(mw.ROLE_ID) == mw.NEW_LIST_KEY
    wait(window.NAV_DEBOUNCE_MS + 120)
    assert opened == [] and window._view_key == mw.HOME_KEY  # pasar por la fila no abre nada
    QTest.keyClick(window.sidebar, Qt.Key.Key_Return)
    wait(30)
    assert opened == [1]


def test_sincronizar_sin_cambios_no_reconstruye_nada(window, db):
    window._navigate("PLuno")
    items = [window.sidebar.item(r) for r in range(window.sidebar.count())]
    resets = []
    window.model.modelReset.connect(lambda: resets.append(1))
    window.table.setFocus()
    window._on_sync_done(None, None)
    assert [window.sidebar.item(r) for r in range(window.sidebar.count())] == items
    assert window._view_key == "PLuno" and not resets


def test_sincronizar_con_cambios_retoca_la_barra_y_la_lista(window, db):
    window._navigate("PLdos")
    keep = window.sidebar.item(window._sidebar_row("PLuno"))
    resets = []
    window.model.modelReset.connect(lambda: resets.append(1))
    db.upsert_playlist_meta("PLtres", "Lista nueva", 2)
    db.replace_playlist_tracks(
        "PLdos", [(Track(f"PLdos-{i}", f"Canción {i}", "Artista"), None) for i in range(4)], 4
    )
    window._on_sync_done(None, None)
    assert window._sidebar_row("PLtres") > 0
    assert window.sidebar.item(window._sidebar_row("PLuno")) is keep  # la fila no se rehízo
    assert window.sidebar.currentItem().data(mw.ROLE_ID) == "PLdos"
    assert window._view_key == "PLdos" and window.model.rowCount() == 4 and not resets
    assert window.band._facts[0] == "4 canciones"


def test_cola_avanza_sin_reconstruirse_ni_volver_arriba(window, monkeypatch):
    tracks = [Track(f"c{i}", f"Cola {i}", "A") for i in range(300)]
    state = {"pos": 0}
    monkeypatch.setattr(window.player, "queue_snapshot", lambda: (tracks, state["pos"]))
    monkeypatch.setattr(window.player, "current_track", lambda: tracks[state["pos"]])
    window._navigate(mw.QUEUE_KEY)
    assert window.model.rowCount() == 300
    bar = window.table.verticalScrollBar()
    bar.setValue(bar.maximum() // 2)
    scroll = bar.value()
    resets = []
    window.model.modelReset.connect(lambda: resets.append(1))
    state["pos"] = 1
    window._on_current_changed(tracks[1])
    assert not resets and bar.value() == scroll
    assert window.model.is_current(1) and not window.model.is_current(0)
    assert window.band._facts == ["300 pistas", "298 por delante"]


def test_silenciar_no_guarda_el_cero(window, db):
    from ytm_native.playback.player import saved_volume

    window.volume_slider.setValue(75)
    window._toggle_mute()
    assert window.volume_slider.value() == 0
    assert saved_volume(db) == 75                             # al reabrir vuelve a 75
    window._toggle_mute()
    assert window.volume_slider.value() == 75
    window.volume_slider.setValue(0)                          # bajar la barra a mano tampoco lo guarda
    window._toggle_mute()
    assert window.volume_slider.value() == 75


def test_aros_de_la_ventana_siguen_al_reproductor(window):
    window._navigate("PLuno")
    window._on_player_state(mw.PLAYING)
    assert window.band.rings_running()
    window._on_player_state(mw.PAUSED)
    assert wait_until(lambda: not window.band.rings_running(), timeout_ms=motion.SPIN_MS + 400)


def test_tab_sale_del_campo_de_cabeceras(qapp, db):
    dialog = AccountDialog(db)
    assert dialog.headers_edit.tabChangesFocus()
    dialog.close()


# ---------------------------------------------------------------------------
# Paneles que se deslizan y vuelo de la carátula (Tarea 5.7)
# ---------------------------------------------------------------------------
def _expected_plan(grid) -> tuple:
    cols = grid.columns()
    width = max(SleeveCard.PAD * 2 + 40, min(grid.MAX_CARD, (grid.width() - grid.GAP * (cols - 1)) // cols))
    return cols, width, min(grid.count(), cols * grid._max_rows), grid.count()


def test_rejilla_no_se_recoloca_si_nada_cambia(qapp, tmp_path):
    grid = CardGrid(ArtLoader(cache_dir=tmp_path / "caratulas"))
    grid.resize(2000, 10)
    grid.show()
    grid.set_cards([(f"T{i}", "", "", i) for i in range(5)])
    fitted = []
    grid.fitted.connect(lambda shown, total: fitted.append(shown))
    done = grid.relayouts
    # Mismas columnas y el mismo ancho de tarjeta (12 columnas: cambia cada 12 px):
    # no se mueve ninguna tarjeta ni se avisa.
    grid.resize(2003, 10)
    grid.resize(2005, 10)
    assert grid.relayouts == done and fitted == []
    grid.resize(500, 10)                                     # menos columnas: ahora sí
    assert grid.relayouts == done + 1 and len(fitted) == 1
    assert grid._plan == _expected_plan(grid)
    grid.close()


def _slide_ready(window, monkeypatch):
    """La ventana desliza los paneles como con el ratón."""
    focus.mouse_pressed()
    monkeypatch.setattr(motion, "_reduced", False)
    assert window._animate_panes()


def test_deslizar_un_panel_no_recoloca_las_tarjetas_en_cada_paso(window, net, monkeypatch):
    _slide_ready(window, monkeypatch)
    assert window._view_key == mw.HOME_KEY
    assert wait_until(lambda: window.home.shelf_count() == 2)
    wait(60)
    grids = [g for g in window.findChildren(CardGrid) if g.isVisible()]
    assert grids
    before = {id(g): g.relayouts for g in grids}
    widths = []
    probe = window.home.library.grid
    original = probe.resizeEvent

    def spy(event, grid=probe):
        widths.append(event.size().width())
        original(event)

    monkeypatch.setattr(probe, "resizeEvent", spy)
    window.now_action.setChecked(True)                        # abre «Ahora suena» deslizándose
    assert window.is_sliding()
    assert wait_until(lambda: not window.is_sliding(), timeout_ms=motion.PANE_OPEN_MS + 600)
    assert len(set(widths)) > 3                               # el ancho cambió en muchos fotogramas
    for grid in grids:
        # Como mucho un reajuste por rejilla: el del final, no uno por fotograma.
        assert grid.relayouts - before[id(grid)] <= 1
        assert grid._plan == _expected_plan(grid)              # y queda al ancho definitivo
        assert not grid._deferred


def test_cerrar_el_panel_tambien_reajusta_al_final(window, net, monkeypatch):
    _slide_ready(window, monkeypatch)
    window.now_action.setChecked(True)
    assert wait_until(lambda: not window.is_sliding(), timeout_ms=motion.PANE_OPEN_MS + 600)
    grid = window.home.library.grid
    before = grid.relayouts
    window.now_action.setChecked(False)
    assert window.is_sliding() and window.layout_settling()
    assert wait_until(lambda: not window.now_panel.isVisible(), timeout_ms=motion.PANE_CLOSE_MS + 600)
    assert grid.relayouts - before <= 1 and grid._plan == _expected_plan(grid)


def _art(window, url: str, color: str, size=(160, 160)) -> None:
    image = QImage(*size, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    window.art._remember(url, image)


def _host_rect(window, cover) -> "QRectF":
    from PySide6.QtCore import QPoint, QPointF

    return cover.art_rect().translated(QPointF(cover.mapTo(window.centralWidget(), QPoint(0, 0))))


@pytest.mark.parametrize("thumb, size", [
    ("https://lh3.googleusercontent.com/caratula=w120-h120", (544, 544)),
    ("https://i.ytimg.com/vi/sonic/hq720.jpg", (1280, 720)),           # miniatura de vídeo apaisada
])
def test_vuelo_de_la_caratula_va_a_un_destino_fijo_y_proyectado(window, net, monkeypatch, thumb, size):
    from ytm_native.ui.art import BAR_ART_PX, track_art_url

    _slide_ready(window, monkeypatch)
    track = Track("sonic", "His World", "Crush 40", thumb_url=thumb)
    for px in (BAR_ART_PX, 544):
        _art(window, track_art_url(track.video_id, thumb, px), "#2a5bd8", size)
    monkeypatch.setattr(window.player, "current_track", lambda: track)
    window._on_current_changed(track)
    window.now_cover.clicked.emit()
    flight = window._flight
    assert flight is not None and window.now_panel.width() <= 1   # el panel apenas empieza a abrirse
    end = flight.end_rect()
    # c) El destino es la carátula con el panel ya abierto, no la de ancho 0.
    assert end == window._projected_now_cover()
    assert end.width() > 200 and end.x() > window.centralWidget().width() / 2
    aspect = size[0] / size[1]
    assert end.width() / end.height() == pytest.approx(aspect, rel=0.02)
    assert flight.start_rect().width() / flight.start_rect().height() == pytest.approx(aspect, rel=0.02)
    wait(motion.FLIGHT_MS // 2)
    assert flight.end_rect() == end                           # no se mueve durante el vuelo
    middle = flight.rect_at(0.5)
    assert middle.width() / middle.height() == pytest.approx(aspect, rel=0.02)   # sin estirarse
    assert wait_until(lambda: window._flight is None and not window.is_sliding(),
                      timeout_ms=motion.FLIGHT_MS + 600)
    cover = window.now_panel.cover
    assert not cover._held and cover._current() is not None   # se ve en el acto, sin funda vacía
    # Aterriza exactamente donde queda la carátula de verdad.
    real = _host_rect(window, cover)
    assert abs(real.x() - end.x()) <= 1 and abs(real.y() - end.y()) <= 1
    assert abs(real.width() - end.width()) <= 1 and abs(real.height() - end.height()) <= 1


def test_vuelo_con_el_panel_desplazado_no_salta(window, net, monkeypatch):
    """Abrir el panel con el desplazamiento de la vez anterior hacía aterrizar
    la copia entera sobre el título y saltar luego a la carátula medio tapada;
    al cerrarlo, la copia salía de encima de la cabecera."""
    from ytm_native.ui.art import BAR_ART_PX, track_art_url

    _slide_ready(window, monkeypatch)
    window.resize(1180, 520)                   # baja: el panel tiene que desplazarse
    thumb = "https://lh3.googleusercontent.com/caratula=w120-h120"
    track = Track("sonic", "His World", "Crush 40", thumb_url=thumb)
    for px in (BAR_ART_PX, 544):
        _art(window, track_art_url(track.video_id, thumb, px), "#2a5bd8")
    monkeypatch.setattr(window.player, "current_track", lambda: track)
    window._on_current_changed(track)
    window.now_cover.clicked.emit()
    assert wait_until(lambda: window._flight is None and not window.is_sliding()
                      and window.now_panel.info() is not None, timeout_ms=motion.FLIGHT_MS + 1500)
    panel = window.now_panel
    bar = panel.scroll.verticalScrollBar()
    assert bar.maximum() > panel.cover.height() // 2
    bar.setValue(bar.maximum())                # la carátula queda fuera de la vista
    assert not panel.cover_in_view()

    # Cerrar: sin vuelo, porque la carátula no se ve.
    window.now_cover.clicked.emit()
    assert window._flight is None
    assert wait_until(lambda: not panel.isVisible() and not window.is_sliding(),
                      timeout_ms=motion.FLIGHT_MS + 600)

    # Abrir otra vez: el panel empieza arriba y la copia aterriza donde está la carátula.
    window.now_cover.clicked.emit()
    flight = window._flight
    assert flight is not None and bar.value() == 0
    end = flight.end_rect()
    assert wait_until(lambda: window._flight is None and not window.is_sliding(),
                      timeout_ms=motion.FLIGHT_MS + 600)
    assert bar.value() == 0 and panel.cover_in_view()
    real = _host_rect(window, panel.cover)
    assert abs(real.x() - end.x()) <= 1 and abs(real.y() - end.y()) <= 1
    assert abs(real.width() - end.width()) <= 1 and abs(real.height() - end.height()) <= 1

    # Con la carátula a la vista, cerrar sí vuela.
    window.now_cover.clicked.emit()
    assert window._flight is not None and not window._flight.opening


def test_caratula_grande_usa_la_pequena_mientras_llega(window, net, monkeypatch):
    from ytm_native.ui.art import BAR_ART_PX, track_art_url

    _slide_ready(window, monkeypatch)
    thumb = "https://lh3.googleusercontent.com/otra=w120-h120"
    track = Track("t2", "Otra", "Alguien", thumb_url=thumb)
    _art(window, track_art_url(track.video_id, thumb, BAR_ART_PX), "#aa3366")
    monkeypatch.setattr(window.art, "_enqueue", lambda url: None)  # la grande no llega
    monkeypatch.setattr(window.player, "current_track", lambda: track)
    window._on_current_changed(track)
    window.now_cover.clicked.emit()
    assert window._flight is not None                         # vuela con la que ya había
    assert wait_until(lambda: window._flight is None, timeout_ms=motion.FLIGHT_MS + 600)
    cover = window.now_panel.cover
    assert cover._current() is not None and cover._pix_source == cover._fallback
    big = track_art_url(track.video_id, thumb, 544)
    _art(window, big, "#aa3366", (544, 544))
    window.art.loaded.emit(big)
    assert cover._previous is not None and cover._fade.running()   # se funde encima de la pequeña
    cover._current()
    assert cover._pix_source == big


def test_cambiar_de_vista_no_anima_anchos(window, monkeypatch):
    calls = []
    original = window.band._fit_width
    monkeypatch.setattr(window.band, "_fit_width", lambda animate=True: (calls.append(animate), original(animate)))
    window._navigate("PLuno")
    assert window.layout_settling()
    window.band.resize(window.band.width() - 40, window.band.height())
    assert calls and not any(calls)                           # ni al entrar ni al recolocarse
    wait(30)
    assert not window.layout_settling()
