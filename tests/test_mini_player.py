"""Mini reproductor siempre encima."""

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from ytm_native.core.db import Track
from ytm_native.playback.player import PAUSED, PLAYING
from ytm_native.ui import mini_player
from ytm_native.ui.mini_player import POS_KEY, MarqueeLabel, MiniPlayer
from ytm_native.ui.theme import ui_font

from test_home_search import animations_on, net, wait, wait_until, window  # noqa: F401

TRACK = Track("m1", "Una canción con un título larguísimo que no cabe en la ventanita", "Artista", duration_s=200)


@pytest.fixture
def playing(window, monkeypatch):
    """Una pista "sonando" sin mpv: el reproductor dice que tiene una."""
    monkeypatch.setattr(window.player, "current_track", lambda: TRACK)
    return window


def mouse(widget, kind, local, buttons=Qt.MouseButton.LeftButton):
    local = QPointF(local)
    global_pos = QPointF(widget.mapToGlobal(local.toPoint()))
    button = Qt.MouseButton.LeftButton if kind != QEvent.Type.MouseMove else Qt.MouseButton.NoButton
    event = QMouseEvent(kind, local, global_pos, button, buttons, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, event)


# -- texto que se desliza ---------------------------------------------------------


def test_texto_largo_se_desliza_y_el_corto_no(qapp, monkeypatch):
    monkeypatch.setattr(mini_player, "MARQUEE_REST_MS", 0)
    label = MarqueeLabel(ui_font(14), "#000000")
    label.resize(120, label.height())
    label.setText("corto")
    label.show()
    assert not label.overflows() and not label.scrolling()
    label.setText("un título bastante más largo que el ancho que tiene")
    assert label.overflows() and label.scrolling()
    assert wait_until(lambda: label.offset() > 3, 1500)
    label.hide()
    assert not label.scrolling()                              # oculto, nada se mueve
    label.show()
    assert label.scrolling() and label.offset() == 0          # vuelve a empezar desde el principio
    label.setText("corto")
    assert not label.scrolling()
    label.deleteLater()


def test_con_animaciones_reducidas_se_recorta_quieto(qapp, monkeypatch):
    monkeypatch.setattr(mini_player, "reduced_motion", lambda: True)
    label = MarqueeLabel(ui_font(14), "#000000")
    label.resize(120, label.height())
    label.setText("un título bastante más largo que el ancho que tiene")
    label.show()
    assert label.overflows() and not label.scrolling()
    label.grab()                                              # se pinta recortado sin fallar
    label.deleteLater()


def test_el_desplazamiento_descansa_al_principio_de_cada_vuelta(qapp, monkeypatch):
    monkeypatch.setattr(mini_player, "MARQUEE_REST_MS", 400)
    label = MarqueeLabel(ui_font(14), "#000000")
    label.resize(120, label.height())
    label.setText("un título bastante más largo que el ancho que tiene")
    label.show()
    wait(200)
    assert label.offset() == 0                                # aún quieto, legible
    assert wait_until(lambda: label.offset() > 0, 1500)
    label.deleteLater()


# -- la ventana --------------------------------------------------------------


def test_abrir_el_mini_oculta_la_principal_y_volver_la_devuelve(playing):
    window = playing
    window.toggle_mini()
    mini = window.mini
    assert mini.isVisible() and not window.isVisible()
    flags = mini.windowFlags()
    assert flags & Qt.WindowType.WindowStaysOnTopHint and flags & Qt.WindowType.FramelessWindowHint
    assert (mini.width(), mini.height()) == (MiniPlayer.WIDTH, MiniPlayer.HEIGHT)
    mini.window_btn.click()                                   # volver a la ventana principal
    assert window.isVisible() and not mini.isVisible()
    window.toggle_mini()                                      # Ctrl+M otra vez: el mismo mini
    assert window.mini is mini and mini.isVisible()
    window.toggle_mini()
    assert window.isVisible() and not mini.isVisible()


def test_cerrar_el_mini_con_alt_f4_no_deja_la_app_sin_ventanas(playing):
    window = playing
    window.show_mini()
    window.mini.close()
    assert wait_until(window.isVisible)
    assert not window.mini.isVisible()


def test_los_controles_operan_sobre_la_misma_reproduccion(playing, monkeypatch):
    window = playing
    calls = []
    for name in ("toggle_pause", "next_track", "previous_track", "seek"):
        monkeypatch.setattr(window.player, name, lambda *a, n=name: calls.append((n, *a)))
    window.show_mini()
    mini = window.mini
    window.player.state_changed.emit(PLAYING)
    for button in (mini.play_btn, mini.next_btn, mini.prev_btn):
        assert button.isEnabled()
        button.click()
    assert calls == [("toggle_pause",), ("next_track",), ("previous_track",)]
    window.player.position_changed.emit(20.0, 200.0)
    mini.seek.sliderPressed.emit()
    mini.seek.setValue(500)
    mini.seek.sliderReleased.emit()
    assert calls[-1] == ("seek", 100.0)



def test_espacio_pausa_salvo_que_un_boton_lo_use(playing, monkeypatch):
    from PySide6.QtTest import QTest

    window = playing
    calls = []
    for name in ("toggle_pause", "next_track"):
        monkeypatch.setattr(window.player, name, lambda *_a, n=name: calls.append(n))
    window.show_mini()
    mini = window.mini
    window.player.state_changed.emit(PLAYING)
    QTest.keyClick(mini, Qt.Key.Key_Space)
    assert calls == ["toggle_pause"]
    QTest.keyClick(mini.next_btn, Qt.Key.Key_Space)          # con el foco en "Siguiente"
    assert calls == ["toggle_pause", "next_track"]
    QTest.keyClick(mini, Qt.Key.Key_Escape)                  # Esc vuelve a la principal
    assert window.isVisible() and not mini.isVisible()

def test_lo_que_pasa_en_la_principal_se_ve_en_el_mini(playing):
    window = playing
    window.show_mini()
    mini = window.mini
    window.player.current_changed.emit(TRACK)
    assert mini.title.text() == TRACK.title and mini.artist.text() == "Artista"
    window.player.state_changed.emit(PLAYING)
    assert mini.play_btn._glyph == "pause"
    window.player.state_changed.emit(PAUSED)
    assert mini.play_btn._glyph == "play"
    window.player.position_changed.emit(30.0, 120.0)
    assert mini.seek.value() == 250
    assert mini.time_label._text == "0:30 / 2:00"
    assert mini.title.scrolling()                             # el título no cabe: se desliza


def test_sin_pista_los_controles_estan_desactivados(window):
    window.show_mini()
    mini = window.mini
    assert mini.title.text() == "Nada en reproducción"
    assert not mini.play_btn.isEnabled() and not mini.seek.isEnabled()


# -- posición ----------------------------------------------------------------


def test_recuerda_donde_se_dejo(window, db):
    window.show_mini()
    mini = window.mini
    mini.move(100, 120)
    assert wait_until(lambda: db.get_setting(POS_KEY) == "100,120", 2000)
    window.bring_to_front()
    again = MiniPlayer(window.player, window.art, db)
    again.place()
    assert (again.x(), again.y()) == (100, 120)
    again.deleteLater()


def test_una_posicion_fuera_de_las_pantallas_no_la_pierde(window, db):
    db.set_setting(POS_KEY, "99999,99999")
    mini = MiniPlayer(window.player, window.art, db)
    mini.place()
    screen = QApplication.primaryScreen().availableGeometry()
    assert screen.contains(mini.geometry())
    db.set_setting(POS_KEY, "basura")
    mini.place()
    assert screen.contains(mini.geometry())
    mini.deleteLater()


def test_se_arrastra_desde_las_zonas_sin_controles(window, db):
    window.show_mini()
    mini = window.mini
    mini.move(200, 200)
    start = mini.pos()
    mouse(mini, QEvent.Type.MouseButtonPress, QPoint(5, 50))       # el margen, no un control
    mouse(mini, QEvent.Type.MouseMove, QPoint(45, 80))
    assert mini.pos() == start + QPoint(40, 30)
    mouse(mini, QEvent.Type.MouseButtonRelease, QPoint(45, 80), Qt.MouseButton.NoButton)
    assert db.get_setting(POS_KEY) == f"{mini.x()},{mini.y()}"
