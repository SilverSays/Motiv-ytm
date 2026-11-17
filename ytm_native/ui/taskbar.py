"""Botones de la miniatura de la barra de tareas (Windows 7 en adelante).

Al pasar el ratón por el botón de la app en la barra de tareas, la vista en
miniatura lleva Anterior, Reproducir/Pausa y Siguiente. Es la interfaz COM
``ITaskbarList3`` llamada con ``ctypes``, sin dependencias.

* Los botones solo se pueden añadir cuando Explorer avisa con el mensaje
  ``TaskbarButtonCreated`` (también tras reiniciarse Explorer o al volver a
  mostrar una ventana que estaba en la bandeja).
* Un clic llega a la ventana como ``WM_COMMAND`` con ``THBN_CLICKED``.
* Cualquier fallo (otro sistema, Explorer sin barra, COM que no responde)
  apaga la función en silencio: la app sigue igual sin los botones.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from typing import Callable, Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QWidget

from . import theme
from .components import glyph_pixmap

log = logging.getLogger("ytmn.taskbar")

WM_COMMAND = 0x0111
WM_SETTINGCHANGE = 0x001A
THBN_CLICKED = 0x1800

THB_ICON, THB_TOOLTIP, THB_FLAGS = 0x2, 0x4, 0x8
THBF_ENABLED, THBF_DISABLED = 0x0, 0x1

PREV_ID, TOGGLE_ID, NEXT_ID = 0x4D01, 0x4D02, 0x4D03
ACTIONS = {PREV_ID: "previous", TOGGLE_ID: "toggle", NEXT_ID: "next"}

CLSID_TASKBARLIST = "{56FDF344-FD6D-11d0-958A-006097C9A090}"
IID_ITASKBARLIST3 = "{EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF}"
# Posiciones en la tabla de métodos (IUnknown, ITaskbarList, 2 y 3).
_RELEASE, _HRINIT, _ADD_BUTTONS, _UPDATE_BUTTONS = 2, 3, 15, 16


def _loword(value: int) -> int:
    return value & 0xFFFF


def _hiword(value: int) -> int:
    return (value >> 16) & 0xFFFF


# ---------------------------------------------------------------------------
# COM
# ---------------------------------------------------------------------------
if sys.platform == "win32":
    from ctypes import wintypes

    class _GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    class THUMBBUTTON(ctypes.Structure):
        _fields_ = [
            ("dwMask", wintypes.DWORD),
            ("iId", wintypes.UINT),
            ("iBitmap", wintypes.UINT),
            ("hIcon", wintypes.HICON),
            ("szTip", wintypes.WCHAR * 260),
            ("dwFlags", wintypes.DWORD),
        ]


class ComTaskbar:
    """``ITaskbarList3`` del sistema. ``create()`` da ``None`` si no hay."""

    def __init__(self, pointer: int):
        self._ptr = ctypes.c_void_p(pointer)

    @classmethod
    def create(cls) -> Optional["ComTaskbar"]:
        if sys.platform != "win32":
            return None
        try:
            ole32 = ctypes.windll.ole32
            # Qt ya inicia COM en el hilo de la ventana; si no, se inicia aquí
            # (S_FALSE o un modo distinto también sirven para crear el objeto).
            ole32.CoInitializeEx(None, 0x2)                   # COINIT_APARTMENTTHREADED
            clsid, iid = _GUID(), _GUID()
            ole32.CLSIDFromString(CLSID_TASKBARLIST, ctypes.byref(clsid))
            ole32.IIDFromString(IID_ITASKBARLIST3, ctypes.byref(iid))
            pointer = ctypes.c_void_p()
            hr = ole32.CoCreateInstance(ctypes.byref(clsid), None, 0x1,   # CLSCTX_INPROC_SERVER
                                        ctypes.byref(iid), ctypes.byref(pointer))
            if hr != 0 or not pointer.value:
                log.info("la barra de tareas no ofrece ITaskbarList3 (0x%08X)", hr & 0xFFFFFFFF)
                return None
            bar = cls(pointer.value)
            bar._call(_HRINIT)
            return bar
        except (OSError, AttributeError, ValueError):
            log.info("sin botones en la miniatura de la barra de tareas", exc_info=True)
            return None

    def _call(self, index: int, *args, argtypes: tuple = ()):
        if not self._ptr.value:
            raise OSError("ITaskbarList3 ya liberado")
        table = ctypes.cast(self._ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        method = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)(table[index])
        return method(self._ptr, *args)       # HRESULT de error: OSError

    def _buttons(self, buttons: list[tuple[int, int, str, int]]):
        array = (THUMBBUTTON * len(buttons))()
        for slot, (button_id, hicon, tip, flags) in zip(array, buttons):
            slot.dwMask = THB_ICON | THB_TOOLTIP | THB_FLAGS
            slot.iId = button_id
            slot.hIcon = hicon
            slot.szTip = tip[:259]
            slot.dwFlags = flags
        return array

    def add_buttons(self, hwnd: int, buttons: list[tuple[int, int, str, int]]) -> None:
        array = self._buttons(buttons)
        self._call(_ADD_BUTTONS, wintypes.HWND(hwnd), len(buttons), array,
                   argtypes=(wintypes.HWND, wintypes.UINT, ctypes.POINTER(THUMBBUTTON)))

    def update_buttons(self, hwnd: int, buttons: list[tuple[int, int, str, int]]) -> None:
        array = self._buttons(buttons)
        self._call(_UPDATE_BUTTONS, wintypes.HWND(hwnd), len(buttons), array,
                   argtypes=(wintypes.HWND, wintypes.UINT, ctypes.POINTER(THUMBBUTTON)))

    def release(self) -> None:
        if self._ptr.value:
            table = ctypes.cast(self._ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(table[_RELEASE])(self._ptr)
            self._ptr = ctypes.c_void_p()


def _register_message(name: str) -> int:
    if sys.platform != "win32":
        return 0
    try:
        return int(ctypes.windll.user32.RegisterWindowMessageW(name))
    except (OSError, AttributeError):
        return 0


def _destroy_icon(hicon: int) -> None:
    if sys.platform == "win32" and hicon:
        try:
            ctypes.windll.user32.DestroyIcon(wintypes.HICON(hicon))
        except (OSError, AttributeError):
            pass


def system_uses_light_theme() -> bool:
    """La barra de tareas sigue el tema de Windows, no el de la app."""
    if sys.platform != "win32":
        return False
    try:
        import winreg

        path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            value, _kind = winreg.QueryValueEx(key, "SystemUsesLightTheme")
        return bool(value)
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Los tres botones
# ---------------------------------------------------------------------------
class ThumbBar(QObject):
    """Anterior, Reproducir/Pausa y Siguiente en la miniatura de ``widget``.

    La ventana le pasa sus mensajes nativos con ``handle``; los clics salen
    por ``clicked`` ("previous", "toggle" o "next")."""

    clicked = Signal(str)

    def __init__(self, widget: QWidget, backend=None, icon_maker: Optional[Callable[[str, bool], int]] = None):
        super().__init__(widget)
        self._widget = widget
        if backend is None and QGuiApplication.platformName() == "windows":
            backend = ComTaskbar.create()
        self._backend = backend
        self._created_msg = _register_message("TaskbarButtonCreated") if backend is not None else 0
        self._icon_maker = icon_maker or self._make_icon
        self._icons: dict[str, int] = {}
        self._hwnd = 0
        self._added = False
        self._playing = False
        self._enabled = False
        self._shown: Optional[list] = None

    @property
    def active(self) -> bool:
        return self._backend is not None

    def handle(self, hwnd: int, message: int, wparam: int, lparam: int = 0) -> bool:
        """``True`` si el mensaje era un clic en un botón (ya atendido)."""
        if self._backend is None:
            return False
        if message == self._created_msg and message:
            self._hwnd = hwnd
            self._added = False
            self._install()
            return False
        if message == WM_COMMAND and _hiword(wparam) == THBN_CLICKED:
            action = ACTIONS.get(_loword(wparam))
            if action is not None:
                self.clicked.emit(action)
                return True
        elif message == WM_SETTINGCHANGE and lparam and self._added:
            try:
                changed = ctypes.wstring_at(lparam) if sys.platform == "win32" else ""
            except (OSError, ValueError):
                changed = ""
            if changed == "ImmersiveColorSet":          # Windows pasó a claro u oscuro
                self._drop_icons()
                self._shown = None
                self._refresh()
        return False

    def set_state(self, playing: bool, enabled: bool) -> None:
        if (playing, enabled) == (self._playing, self._enabled):
            return
        self._playing, self._enabled = playing, enabled
        self._refresh()

    def close(self) -> None:
        backend, self._backend = self._backend, None
        if backend is not None and hasattr(backend, "release"):
            try:
                backend.release()
            except OSError:
                pass
        self._drop_icons()

    # -- interno --------------------------------------------------------------
    def _spec(self) -> list[tuple[int, int, str, int]]:
        flags = THBF_ENABLED if self._enabled else THBF_DISABLED
        toggle = ("pause", "Pausar") if self._playing else ("play", "Reproducir")
        return [
            (PREV_ID, self._icon("previous"), "Anterior", flags),
            (TOGGLE_ID, self._icon(toggle[0]), toggle[1], flags),
            (NEXT_ID, self._icon("next"), "Siguiente", flags),
        ]

    def _icon(self, glyph: str) -> int:
        if glyph not in self._icons:
            self._icons[glyph] = self._icon_maker(glyph, system_uses_light_theme())
        return self._icons[glyph]

    def _make_icon(self, glyph: str, light: bool) -> int:
        dpr = self._widget.devicePixelRatioF() if self._widget is not None else 1.0
        color = theme.PAPEL if light else theme.SOBRE_SOLIDO
        pixmap = glyph_pixmap(glyph, 16, color, dpr)
        return int(pixmap.toImage().toHICON())

    def _drop_icons(self) -> None:
        for hicon in self._icons.values():
            _destroy_icon(hicon)
        self._icons.clear()

    def _install(self) -> None:
        spec = self._spec()
        try:
            self._backend.add_buttons(self._hwnd, spec)
        except OSError:
            self._give_up("no se pudieron añadir")
            return
        self._added = True
        self._shown = spec

    def _refresh(self) -> None:
        if self._backend is None or not self._added:
            return                  # se aplicará al crearse el botón de la barra
        spec = self._spec()
        if spec == self._shown:
            return
        try:
            self._backend.update_buttons(self._hwnd, spec)
        except OSError:
            self._give_up("no se pudieron actualizar")
            return
        self._shown = spec

    def _give_up(self, what: str) -> None:
        log.info("botones de la miniatura: %s; se desactivan", what, exc_info=True)
        self.close()
