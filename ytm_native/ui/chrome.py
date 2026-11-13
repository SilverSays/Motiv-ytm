"""Lo que envuelve a todas las ventanas: estilo, paleta, barras de título y menús."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QAction, QIcon, QPixmap
from PySide6.QtWidgets import QApplication, QDialog, QMainWindow, QMenu

from . import focus, theme
from .components import glyph_pixmap
from .motion import EASE_OUT, Tween

MENU_FADE_MS = 120


def rounded_menu(menu: QMenu) -> QMenu:
    """Menú con las esquinas redondeadas del QSS (sin el marco cuadrado del sistema)."""
    menu.setWindowFlags(
        menu.windowFlags() | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint
    )
    menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    return menu


def show_check(action: QAction, dpr: float = 1.0) -> QAction:
    """Marca de verificación propia para una acción con casilla.

    La columna del icono se reserva siempre (con una imagen vacía cuando está
    desmarcada), así el texto no cambia de sitio de una apertura a otra.
    """
    checked = QIcon(glyph_pixmap("check", 16, theme.PAPEL, dpr))
    blank_pixmap = QPixmap(max(1, round(16 * dpr)), max(1, round(16 * dpr)))
    blank_pixmap.fill(Qt.GlobalColor.transparent)
    blank_pixmap.setDevicePixelRatio(dpr)
    blank = QIcon(blank_pixmap)
    action.toggled.connect(lambda on: action.setIcon(checked if on else blank))
    action.setIcon(checked if action.isChecked() else blank)
    return action


class _Chrome(QObject):
    """Tiñe la barra de título de cada ventana o diálogo que se abre, funde la
    aparición de los menús y anota si se navega con teclado o con ratón.

    Un menú es ocasional, así que admite 120 ms; solo cambia la opacidad, de
    modo que también vale con las animaciones reducidas."""

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - API de Qt
        kind = event.type()
        if kind == QEvent.Type.KeyPress:
            focus.key_pressed(event.key())
        elif kind == QEvent.Type.MouseButtonPress:
            focus.mouse_pressed()
        elif kind == QEvent.Type.Show and obj.isWidgetType() and obj.isWindow():
            if isinstance(obj, QMenu):
                fade = obj.findChild(Tween, "menuFade")
                if fade is None:
                    fade = Tween(obj, 0.0, lambda value, menu=obj: menu.setWindowOpacity(float(value)),
                                 ms=MENU_FADE_MS, curve=EASE_OUT)
                    fade.setObjectName("menuFade")
                fade.set(0.0)
                fade.to(1.0)
            elif isinstance(obj, (QMainWindow, QDialog)):
                theme.tint_title_bar(obj)
        return False


def install(app: QApplication) -> None:
    app.setStyle("Fusion")
    app.setPalette(theme.palette())
    app.setStyleSheet(theme.QSS)
    chrome = _Chrome(app)
    app.installEventFilter(chrome)
    app._ytmn_chrome = chrome  # noqa: SLF001 - referencia viva mientras exista la aplicación
