"""Piezas de las listas de navegación (barra lateral y Opciones): la marca de
la fila activa, el anillo de foco y el pie de la barra.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QEvent, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QListWidget, QWidget

from . import focus, theme
from .buttons import IconButton, PillButton
from .motion import EASE_IN_OUT, Tween
from .primitives import ElidedLabel
from ytm_native.core.i18n import tr


class ActiveMark(QWidget):
    """Tramo de oro junto a la vista activa de la barra lateral.

    Cambiar de vista con el ratón la desliza de una fila a otra (de dónde se
    viene y adónde se va); con el teclado, o al desplazar la lista, salta sin
    animación: navegar con flechas se repite demasiado para moverse.
    """

    W, H = 3, 18
    SLIDE_MS = 220

    def __init__(self, view: QListWidget):
        super().__init__(view.viewport())
        self._view = view
        self._x = 0
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.resize(self.W, self.H)
        self._y = Tween(self, 0.0, self._place, ms=self.SLIDE_MS, curve=EASE_IN_OUT, movement=True)
        view.currentRowChanged.connect(lambda _row: self.follow(animate=not focus.keyboard()))
        view.verticalScrollBar().valueChanged.connect(lambda _value: self.follow(animate=False))
        self.hide()

    def follow(self, animate: bool = False) -> None:
        item = self._view.currentItem()
        if item is None or not (item.flags() & Qt.ItemFlag.ItemIsSelectable):
            self.hide()
            return
        rect = self._view.visualItemRect(item)
        self._x = rect.left() + 4
        target = rect.center().y() - self.H / 2
        if animate and self.isVisible():
            self._y.to(target)
        else:
            self._y.set(target)
            self.show()

    def _place(self, y) -> None:
        self.move(self._x, round(float(y)))

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.MARCA))
        painter.drawRoundedRect(QRectF(self.rect()), self.W / 2, self.W / 2)


class ListFocusRing(QWidget):
    """Anillo de foco sobre la fila actual de una lista lateral, solo cuando
    se llega con el teclado (como el resto de anillos de la app, ver
    ``focus``). Sin él, al tabular hasta la barra lateral no se veía dónde
    estaba el foco: la fila elegida pinta igual con foco que sin él.

    Va encima del área de la lista, sin recibir el ratón."""

    PEN = 2.0

    def __init__(self, view: QListWidget):
        super().__init__(view.viewport())
        self._view = view
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.resize(view.viewport().size())
        view.viewport().installEventFilter(self)
        view.installEventFilter(self)
        view.currentRowChanged.connect(lambda _row: self.update())
        view.verticalScrollBar().valueChanged.connect(lambda _value: self._pin())
        self.show()

    def _pin(self) -> None:
        # Al desplazarse, la lista mueve con su contenido a los hijos de su
        # área: el anillo se queda cubriéndola entera.
        self.move(0, 0)
        self.update()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        kind = event.type()
        if watched is self._view.viewport() and kind == QEvent.Type.Resize:
            self.resize(event.size())
        elif kind in (QEvent.Type.FocusIn, QEvent.Type.FocusOut, QEvent.Type.KeyPress):
            self.update()
        return False

    def shown(self) -> bool:
        view = self._view
        item = view.currentItem()
        return (view.hasFocus() and focus.keyboard() and item is not None
                and bool(item.flags() & Qt.ItemFlag.ItemIsSelectable))

    def paintEvent(self, _event) -> None:  # noqa: N802
        if not self.shown():
            return
        rect = QRectF(self._view.visualItemRect(self._view.currentItem()))
        inset = self.PEN / 2 + 1
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(theme.PAPEL), self.PEN))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect.adjusted(inset, inset, -inset, -inset), theme.R_CONTROL, theme.R_CONTROL)
        painter.end()


class SidebarFooter(QWidget):
    """Pie de la barra lateral: estado de la biblioteca y el menú de la app."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("sidebarFooter")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 6, 6, 6)
        lay.setSpacing(4)
        self.status = ElidedLabel(theme.ui_font(12), theme.HUMO)
        self.menu_btn = IconButton("more", tr("Menú de la aplicación"), size=36, glyph_px=16, quiet=True)
        # Versión nueva de la app: un enlace discreto que abre su página en el
        # navegador (no descarga ni instala nada). Oculto mientras no haya.
        self.update_btn = PillButton("", "quiet", glyph="window")
        self.update_btn.setFixedHeight(30)
        self.update_btn.hide()
        lay.addWidget(self.status, 1)
        lay.addWidget(self.update_btn)
        lay.addWidget(self.menu_btn)
        self._detail = ""
        self._collapsed = False

    def set_status(self, text: str, alert: bool = False, detail: str = "") -> None:
        self.status.setText(text)
        self.status.set_color(theme.AVISO if alert else theme.HUMO)
        self._detail = detail
        self.setToolTip(detail or text)

    def set_collapsed(self, collapsed: bool) -> None:
        """Barra lateral contraída: solo cabe el botón del menú, centrado. El
        estado sigue disponible al pasar el ratón por encima."""
        self._collapsed = collapsed
        self.status.setVisible(not collapsed)
        self.update_btn.setVisible(not collapsed and bool(self.update_btn.text()))
        self.layout().setContentsMargins(*((14, 6, 14, 6) if collapsed else (16, 6, 6, 6)))

    def set_update(self, version: str) -> None:
        """Enseña (o quita, con ``""``) el enlace a la versión nueva."""
        if version:
            self.update_btn.set_label(version, "window")
            self.update_btn.setToolTip(tr("Versión {version} disponible: abre su página en el navegador", version=version))
            self.update_btn.setAccessibleName(tr("Versión {version} disponible", version=version))
        else:
            self.update_btn.set_label("", "window")
        self.update_btn.setVisible(bool(version) and not self._collapsed)
