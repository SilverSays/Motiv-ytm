"""Primitivas de pintado y movimiento que comparten las demás piezas:
glifos, revelado, fundidos de visibilidad y texto recortado.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QObject, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import QGraphicsOpacityEffect, QSizePolicy, QWidget

from . import theme
from .motion import EASE, EASE_OUT, REVEAL_MS, FADE_MS, Tween


def glyph_pixmap(name: str, size: int, color: str, dpr: float = 1.0) -> QPixmap:
    """Icono de la fuente de símbolos como imagen (para la barra lateral)."""
    px = max(1, round(size * dpr))
    out = QPixmap(px, px)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    font = theme.icon_font(round(size * 0.56 * dpr)) or QFont()
    painter.setFont(font)
    painter.setPen(QColor(color))
    painter.drawText(QRect(0, 0, px, px), Qt.AlignmentFlag.AlignCenter, theme.glyph(name))
    painter.end()
    out.setDevicePixelRatio(dpr)
    return out


def paint_reveal(painter: QPainter, rect: QRectF, progress: float, left: float, width: float,
                 base: Optional[str] = None) -> None:
    """Tapa con el color del panel lo que aún no ha aparecido: un borde suave
    que barre de izquierda a derecha entre ``left`` y ``left + width``. Es un
    fundido, no un desplazamiento: con las animaciones reducidas también vale."""
    if progress >= 1.0:
        return
    base = base or theme.PANEL           # el del tema en uso (claro u oscuro)
    soft = max(24.0, width * 0.45)
    edge = left - soft + progress * (width + soft)
    gradient = QLinearGradient(edge, 0, edge + soft, 0)
    clear = QColor(base)
    clear.setAlpha(0)
    gradient.setColorAt(0.0, clear)
    gradient.setColorAt(1.0, QColor(base))
    painter.save()
    painter.setOpacity(1.0)
    painter.fillRect(rect, QBrush(gradient))
    painter.restore()


class Reveal(QObject):
    """Aparición de un widget que se pinta a mano: ``start(delay)`` lo deja
    oculto y, pasado el retraso, lo descubre de izquierda a derecha. Mientras
    está oculto no se pinta nada, así que tampoco se decodifica su carátula:
    con muchas tarjetas a la vez, ese trabajo se reparte entre fotogramas."""

    def __init__(self, widget: QWidget):
        super().__init__(widget)
        self._widget = widget
        self._tween = Tween(widget, 1.0, lambda _v: widget.update(), ms=REVEAL_MS, curve=EASE_OUT)
        self._serial = 0

    @property
    def value(self) -> float:
        return float(self._tween.value)

    def start(self, delay_ms: int = 0) -> None:
        self._serial += 1
        serial = self._serial
        self._tween.set(0.0)
        # El destino se decide al vencer el retraso: si para entonces el widget
        # no se ve, aparece sin animación.
        QTimer.singleShot(max(0, delay_ms), self, lambda: serial == self._serial and self._tween.to(1.0))

    def cover(self, painter: QPainter) -> None:
        paint_reveal(painter, QRectF(self._widget.rect()), self.value, 0, self._widget.width())


def fade_visible(widget: QWidget, visible: bool, animate: bool = True) -> None:
    """Muestra u oculta ``widget`` fundiendo su opacidad en lugar de hacerlo
    de golpe. Ocultarse es lo último que pasa: mientras se funde conserva su
    sitio, así nada de alrededor salta antes de tiempo."""
    tween = getattr(widget, "_ytmn_fade", None)
    if tween is None:
        effect = QGraphicsOpacityEffect(widget)
        effect.setEnabled(False)
        widget.setGraphicsEffect(effect)

        def step(value, w=widget, e=effect) -> None:
            opacity = float(value)
            e.setOpacity(opacity)
            e.setEnabled(opacity < 0.999)       # opaco del todo, se pinta directo y nítido
            if opacity <= 0.001 and float(w._ytmn_fade.target) <= 0.0:
                w.hide()

        tween = Tween(widget, 0.0 if widget.isHidden() else 1.0, step, ms=FADE_MS, curve=EASE)
        widget._ytmn_fade = tween
    if visible:
        if not widget.isHidden() and float(tween.target) >= 1.0:
            return
        if widget.isHidden():
            tween.set(0.0)
            widget.show()
        tween.to(1.0) if animate else tween.set(1.0)
    else:
        if widget.isHidden() or float(tween.target) <= 0.0:
            return
        tween.to(0.0) if animate else tween.set(0.0)


def window_settling(widget: QWidget) -> bool:
    """¿La ventana está deslizando un panel o cambiando de vista?

    Mientras tanto el ancho del contenido cambia en cada fotograma: lo que se
    recoloca a mano (rejillas de tarjetas, cabecera, columnas) no debe
    animarse ni recalcularse en cada paso, sino una vez al final."""
    probe = getattr(widget.window(), "layout_settling", None)
    return bool(probe()) if callable(probe) else False


def window_sliding(widget: QWidget) -> bool:
    """¿La ventana está deslizando un panel ahora mismo?"""
    probe = getattr(widget.window(), "is_sliding", None)
    return bool(probe()) if callable(probe) else False


def _alpha(color: str | QColor, alpha: float) -> QColor:
    out = QColor(color)
    out.setAlphaF(max(0.0, min(1.0, alpha)))
    return out


def _draw_glyph(painter: QPainter, rect: QRectF, name: str, px: int, color: QColor) -> None:
    font = theme.icon_font(px)
    if font is None:
        font = theme.ui_font(px)
    painter.setFont(font)
    painter.setPen(color)
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, theme.glyph(name))


# ---------------------------------------------------------------------------
# Texto y carátula
# ---------------------------------------------------------------------------
class ElidedLabel(QWidget):
    """Texto de una línea que se recorta con puntos suspensivos al ancho que
    tenga. Con ``fade`` funde el texto anterior con el nuevo."""

    def __init__(self, font: QFont, color: str, align=Qt.AlignmentFlag.AlignLeft, fade: bool = False,
                 width: Optional[int] = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._font = font
        self._color = QColor(color)
        self._align = align
        self._text = ""
        self._previous = ""
        self._fade = Tween(self, 1.0, lambda _v: self.update(), ms=FADE_MS, curve=EASE) if fade else None
        metrics = QFontMetrics(font)
        self.setFixedHeight(metrics.height() + 2)
        if width is None:
            # Ocupa el ancho que le den y recorta: nunca empuja a sus vecinos.
            self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        else:
            self.setFixedWidth(width)
            self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def text(self) -> str:
        return self._text

    def setText(self, text: str) -> None:  # noqa: N802 - mismo nombre que QLabel
        if text == self._text:
            return
        self._previous = self._text
        self._text = text
        self.setAccessibleName(text)
        if self._fade is not None and self._previous:
            self._fade.set(0.0)
            self._fade.to(1.0)
        self._sync_tooltip()
        self.update()

    def set_color(self, color: str | QColor) -> None:
        self._color = QColor(color)
        self.update()

    def retheme(self, previous: str) -> None:
        self.set_color(theme.translate(self._color, previous))

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(QFontMetrics(self._font).horizontalAdvance(self._text) + 2, self.height())

    def _sync_tooltip(self) -> None:
        cut = QFontMetrics(self._font).horizontalAdvance(self._text) > self.width()
        self.setToolTip(self._text if cut else "")

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._sync_tooltip()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setFont(self._font)
        painter.setPen(self._color)
        metrics = QFontMetrics(self._font)
        flags = self._align | Qt.AlignmentFlag.AlignVCenter
        progress = float(self._fade.value) if self._fade is not None else 1.0
        if progress < 1.0 and self._previous:
            painter.setOpacity(1.0 - progress)
            painter.drawText(self.rect(), flags,
                             metrics.elidedText(self._previous, Qt.TextElideMode.ElideRight, self.width()))
        painter.setOpacity(progress)
        painter.drawText(self.rect(), flags,
                         metrics.elidedText(self._text, Qt.TextElideMode.ElideRight, self.width()))
