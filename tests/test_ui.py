"""Piezas de la interfaz: movimiento interrumpible, botones, barra de progreso y avisos."""

import pytest
from PySide6.QtCore import QEventLoop, QPoint, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QHBoxLayout, QWidget

from ytm_native.ui import focus, motion, theme
from ytm_native.ui.components import ElidedLabel, PillButton, SeekSlider, ToastHost
from ytm_native.ui.motion import Tween


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
