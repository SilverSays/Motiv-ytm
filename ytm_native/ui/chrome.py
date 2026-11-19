"""Lo que envuelve a todas las ventanas: estilo, paleta, barras de título y menús."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QApplication, QDialog, QLabel, QMainWindow, QMenu, QVBoxLayout, QWidget, QWidgetAction,
)

from . import focus, theme
from .motion import EASE_OUT, Tween

MENU_FADE_MS = 120


def rounded_menu(menu: QMenu) -> QMenu:
    """Menú con las esquinas redondeadas del QSS (sin el marco cuadrado del sistema)."""
    menu.setWindowFlags(
        menu.windowFlags() | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint
    )
    menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    return menu


def menu_section(menu: QMenu, title: str, note: str = "", first: bool = False) -> QWidgetAction:
    """Rótulo de un grupo de opciones dentro de un menú.

    Ordena, no se elige: va desactivado, así que ni el ratón lo resalta ni las
    flechas se paran en él. Sale hacia la izquierda, a la altura de las
    casillas, y el texto de sus opciones queda sangrado debajo. Entre grupos
    separa el aire de encima, no una raya. ``note`` es una línea de ayuda bajo
    el rótulo, para los submenús cuyas opciones no se explican solas.
    """
    box = QWidget()
    box.setObjectName("menuSectionBox")
    lay = QVBoxLayout(box)
    # El aire de arriba lo pone el QSS (``#menuSection``): así se aprieta
    # con el menú cuando la pantalla es baja (``compact``).
    lay.setContentsMargins(4, 0, 12, 0)
    lay.setSpacing(1)
    label = QLabel(title)
    label.setObjectName("menuSection")
    label.setProperty("first", first)
    lay.addWidget(label)
    if note:
        hint = QLabel(note)
        hint.setObjectName("menuNote")
        lay.addWidget(hint)
        lay.addSpacing(4)
    action = QWidgetAction(menu)
    action.setText(title)
    action.setDefaultWidget(box)
    action.setEnabled(False)
    menu.addAction(action)
    return action


_CHECK_ICONS: dict[tuple, QIcon] = {}
_CHECKS: list = []        # (acción, cómo repintar su marca): se repintan al cambiar de tema


def _box_icon(on: bool, dpr: float) -> QIcon:
    """Casilla de un interruptor: hueca si está apagado, llena de tinta con la
    marca en el color del panel si está encendido. Así un interruptor se
    distingue de una acción aunque esté apagado."""
    key = ("box", on, theme.scheme(), round(dpr, 2))
    icon = _CHECK_ICONS.get(key)
    if icon is None:
        side = max(1, round(16 * dpr))
        pix = QPixmap(side, side)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(dpr, dpr)
        frame = QRectF(1.5, 1.5, 13.0, 13.0)
        if on:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(theme.PAPEL))
            painter.drawRoundedRect(frame, 3.5, 3.5)
            pen = QPen(QColor(theme.PANEL), 1.8)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPolyline(QPolygonF([QPointF(4.6, 8.2), QPointF(7.0, 10.5), QPointF(11.5, 5.6)]))
        else:
            painter.setPen(QPen(QColor(theme.TENUE), 1.3))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(frame.adjusted(0.3, 0.3, -0.3, -0.3), 3.5, 3.5)
        painter.end()
        pix.setDevicePixelRatio(dpr)
        icon = _CHECK_ICONS[key] = QIcon(pix)
    return icon


def _track_icon(action: QAction, paint) -> QAction:
    action.toggled.connect(paint)
    paint(action.isChecked())
    _CHECKS.append((action, paint))
    return action


def show_check(action: QAction, dpr: float = 1.0) -> QAction:
    """Casilla propia para una acción que se enciende y se apaga.

    Se ve siempre, también apagada: lo que tiene casilla es un interruptor y
    lo que no la tiene, una acción. La columna del icono es la misma en los
    dos estados, así el texto no cambia de sitio de una apertura a otra.
    """
    return _track_icon(action, lambda on: action.setIcon(_box_icon(on, dpr)))


def refresh_checks() -> None:
    """Tras cambiar de tema: las marcas toman la tinta nueva."""
    alive = []
    for action, paint in _CHECKS:
        try:
            paint(action.isChecked())
        except RuntimeError:          # la acción ya no existe
            continue
        alive.append((action, paint))
    _CHECKS[:] = alive


def apply_theme(app: QApplication) -> None:
    """Paleta y hoja de estilos del esquema en uso (``theme.use_scheme``)."""
    app.setPalette(theme.palette())
    app.setStyleSheet(theme.QSS)


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
    apply_theme(app)
    chrome = _Chrome(app)
    app.installEventFilter(chrome)
    app._ytmn_chrome = chrome  # noqa: SLF001 - referencia viva mientras exista la aplicación
