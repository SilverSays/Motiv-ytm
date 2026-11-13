"""¿Se está navegando con el teclado?

El anillo de foco solo se enseña cuando la respuesta es sí (lo que en la web
hace ``:focus-visible``): quien usa el ratón no ve anillos tras cada clic y
quien usa Tab siempre sabe dónde está. No basta con mirar por qué llegó el
foco a un control: Qt también lo mueve "como si fuera Tab" cuando se oculta el
control que lo tenía.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

_NAVIGATION_KEYS = (
    Qt.Key.Key_Tab,
    Qt.Key.Key_Backtab,
    Qt.Key.Key_Up,
    Qt.Key.Key_Down,
    Qt.Key.Key_Left,
    Qt.Key.Key_Right,
)

_keyboard = False


def keyboard() -> bool:
    return _keyboard


def _set(value: bool) -> None:
    global _keyboard
    if value == _keyboard:
        return
    _keyboard = value
    widget = QApplication.focusWidget()
    if widget is not None:
        widget.update()      # el anillo aparece o desaparece en el control con foco


def key_pressed(key: int) -> None:
    if key in _NAVIGATION_KEYS:
        _set(True)


def mouse_pressed() -> None:
    _set(False)
