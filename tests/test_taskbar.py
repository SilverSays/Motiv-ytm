"""Botones de la miniatura de la barra de tareas (ITaskbarList3)."""

import ctypes
import sys

import pytest
from PySide6.QtWidgets import QWidget

from ytm_native.playback.player import PAUSED, PLAYING
from ytm_native.ui import taskbar
from ytm_native.ui.taskbar import (
    NEXT_ID, PREV_ID, THBF_DISABLED, THBF_ENABLED, THBN_CLICKED, TOGGLE_ID, WM_COMMAND, ComTaskbar, ThumbBar,
)

from test_home_search import animations_on, net, window  # noqa: F401

CREATED = 0xC123            # el número que Windows daría a "TaskbarButtonCreated"


class FakeBackend:
    def __init__(self, fail_on=()):
        self.added, self.updated = [], []
        self.fail_on = set(fail_on)
        self.released = False

    def add_buttons(self, hwnd, buttons):
        if "add" in self.fail_on:
            raise OSError("E_FAIL")
        self.added.append((hwnd, list(buttons)))

    def update_buttons(self, hwnd, buttons):
        if "update" in self.fail_on:
            raise OSError("E_FAIL")
        self.updated.append((hwnd, list(buttons)))

    def release(self):
        self.released = True


def make_bar(widget, backend):
    bar = ThumbBar(widget, backend=backend, icon_maker=lambda glyph, light: hash(glyph) & 0xFFFF)
    bar._created_msg = CREATED
    return bar


def tips(buttons):
    return [tip for _id, _icon, tip, _flags in buttons]


def flags(buttons):
    return {flag for _id, _icon, _tip, flag in buttons}


def click(button_id):
    return (THBN_CLICKED << 16) | button_id


@pytest.fixture
def host(qapp):
    widget = QWidget()
    yield widget
    widget.deleteLater()


def test_los_botones_se_anaden_cuando_explorer_crea_el_boton(host):
    backend = FakeBackend()
    bar = make_bar(host, backend)
    bar.set_state(False, False)
    assert backend.added == []                              # aún no hay botón en la barra
    assert not bar.handle(0x1234, CREATED, 0)                # el mensaje sigue su curso
    (hwnd, buttons), = backend.added
    assert hwnd == 0x1234
    assert [b[0] for b in buttons] == [PREV_ID, TOGGLE_ID, NEXT_ID]
    assert tips(buttons) == ["Anterior", "Reproducir", "Siguiente"]
    assert flags(buttons) == {THBF_DISABLED}                 # sin cola, desactivados


def test_el_boton_central_cambia_con_el_estado_y_no_repite_ordenes(host):
    backend = FakeBackend()
    bar = make_bar(host, backend)
    bar.handle(1, CREATED, 0)
    bar.set_state(True, True)
    (_hwnd, buttons), = backend.updated
    assert tips(buttons) == ["Anterior", "Pausar", "Siguiente"]
    assert flags(buttons) == {THBF_ENABLED}
    play_icon, pause_icon = backend.added[0][1][1][1], buttons[1][1]
    assert play_icon != pause_icon                           # el icono también cambia
    bar.set_state(True, True)
    assert len(backend.updated) == 1                         # nada nuevo que enviar
    bar.set_state(False, True)
    assert tips(backend.updated[-1][1])[1] == "Reproducir"


def test_explorer_reiniciado_vuelve_a_recibir_los_botones(host):
    backend = FakeBackend()
    bar = make_bar(host, backend)
    bar.handle(1, CREATED, 0)
    bar.set_state(True, True)
    bar.handle(2, CREATED, 0)                                # otra vez (Explorer o volver de la bandeja)
    assert len(backend.added) == 2
    assert tips(backend.added[-1][1])[1] == "Pausar"         # con el estado de ahora


def test_un_clic_llega_como_wm_command(host):
    bar = make_bar(host, FakeBackend())
    got = []
    bar.clicked.connect(got.append)
    assert bar.handle(1, WM_COMMAND, click(PREV_ID))
    assert bar.handle(1, WM_COMMAND, click(TOGGLE_ID))
    assert bar.handle(1, WM_COMMAND, click(NEXT_ID))
    assert got == ["previous", "toggle", "next"]
    assert not bar.handle(1, WM_COMMAND, PREV_ID)            # un WM_COMMAND de otra cosa
    assert not bar.handle(1, WM_COMMAND, click(0x77))
    assert got == ["previous", "toggle", "next"]


def test_si_windows_falla_se_apaga_en_silencio(host):
    backend = FakeBackend(fail_on={"add"})
    bar = make_bar(host, backend)
    bar.handle(1, CREATED, 0)                                # no lanza
    assert not bar.active and backend.released
    bar.set_state(True, True)                                # ni lanza ni intenta nada
    assert not bar.handle(1, WM_COMMAND, click(NEXT_ID))
    backend = FakeBackend(fail_on={"update"})
    bar = make_bar(host, backend)
    bar.handle(1, CREATED, 0)
    bar.set_state(True, True)
    assert not bar.active


def test_sin_barra_de_tareas_de_windows_no_hay_botones(host):
    bar = ThumbBar(host)                                     # plataforma de pruebas: "offscreen"
    assert not bar.active
    assert not bar.handle(1, WM_COMMAND, click(NEXT_ID))
    bar.set_state(True, True)


@pytest.mark.skipif(sys.platform != "win32", reason="COM de Windows")
def test_el_com_real_se_crea_y_un_fallo_de_verdad_se_aguanta(host):
    backend = ComTaskbar.create()
    if backend is None:
        pytest.skip("este Windows no ofrece ITaskbarList3")
    # Un HRESULT de error de verdad (Windows admite como mucho 7 botones):
    # llega como OSError.
    with pytest.raises(OSError):
        backend.add_buttons(0x1234, [(0x4D00 + i, 0, "x", 0) for i in range(8)])
    bar = ThumbBar(host, backend=backend)
    bar._created_msg = CREATED
    real_spec = bar._spec
    bar._spec = lambda: real_spec() * 3                      # 9 botones: Windows los rechaza
    bar.handle(0x1234, CREATED, 0)                           # y la app sigue sin ellos
    bar.set_state(True, True)
    assert not bar.active
    bar.close()


@pytest.mark.skipif(sys.platform != "win32", reason="mensajes de Windows")
def test_la_ventana_reparte_los_clics_al_reproductor(window, monkeypatch):
    calls = []
    for name in ("toggle_pause", "next_track", "previous_track"):
        monkeypatch.setattr(window.player, name, lambda n=name: calls.append(n))
    backend = FakeBackend()
    bar = window.thumbbar = make_bar(window, backend)
    bar.clicked.connect(window._on_thumb_button)
    from ctypes import wintypes

    from PySide6.QtCore import QByteArray
    from shiboken6.Shiboken import VoidPtr

    def send(message, wparam):
        msg = wintypes.MSG()
        msg.hWnd, msg.message, msg.wParam = 0x99, message, wparam
        # Como lo entrega Qt: un QByteArray y un VoidPtr de tamaño 0 (falso en un ``if``).
        pointer = VoidPtr(ctypes.addressof(msg), 0, False)
        return window.nativeEvent(QByteArray(b"windows_generic_MSG"), pointer)

    assert send(CREATED, 0) == (False, 0)
    assert backend.added and backend.added[0][0] == 0x99
    assert send(WM_COMMAND, click(NEXT_ID)) == (True, 0)
    assert send(WM_COMMAND, click(TOGGLE_ID)) == (True, 0)
    assert send(WM_COMMAND, click(PREV_ID)) == (True, 0)
    assert calls == ["next_track", "toggle_pause", "previous_track"]
    assert send(0x0200, 0)[0] is False                       # WM_MOUSEMOVE: no es asunto suyo


def test_el_estado_de_la_ventana_llega_a_los_botones(window, monkeypatch):
    backend = FakeBackend()
    window.thumbbar = make_bar(window, backend)
    window.thumbbar.handle(1, CREATED, 0)
    monkeypatch.setattr(window.player, "current_track", lambda: object())
    window._on_player_state(PLAYING)
    assert tips(backend.updated[-1][1])[1] == "Pausar"
    assert flags(backend.updated[-1][1]) == {THBF_ENABLED}
    monkeypatch.setattr(window.player, "current_track", lambda: None)
    window._on_player_state(PAUSED)
    assert flags(backend.updated[-1][1]) == {THBF_DISABLED}


def test_los_iconos_reales_se_crean_y_se_liberan(host):
    if sys.platform != "win32":
        pytest.skip("HICON es de Windows")
    bar = ThumbBar(host, backend=FakeBackend())
    bar._created_msg = CREATED
    bar.handle(1, CREATED, 0)
    assert all(icon for icon in bar._icons.values())
    destroyed = []
    original = taskbar._destroy_icon
    taskbar._destroy_icon = lambda h: (destroyed.append(h), original(h))
    try:
        bar.close()
    finally:
        taskbar._destroy_icon = original
    assert len(destroyed) == 3                               # anterior, reproducir y siguiente
