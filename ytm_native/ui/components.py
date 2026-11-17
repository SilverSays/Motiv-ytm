"""Piezas visuales: botones, carátula, banda de cabecera, filas, letra y avisos.

Todo lo que responde al ratón se pinta a mano para poder darle lo que el QSS
no sabe hacer: hundirse al pulsar, fundir el hover y enseñar el anillo de foco
solo cuando se navega con el teclado.
"""

from __future__ import annotations

import math
import time
from typing import Callable, Optional

from PySide6.QtCore import (
    QElapsedTimer,
    QEvent,
    QModelIndex,
    QObject,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QBrush, QColor, QCursor, QFont, QFontMetrics, QImage, QLinearGradient, QPainter, QPainterPath, QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QSlider,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from . import focus, theme
from .art import ArtLoader, Tint, neutral_tint, placeholder_pixmap
from .motion import (
    EASE,
    EASE_IN_OUT,
    EASE_OUT,
    ENTER_MS,
    FRAME_MS,
    REFLOW_MS,
    SPIN_MS,
    REVEAL_MS,
    ROW_STAGGER,
    STAGGER_CAP,
    EXIT_MS,
    FADE_MS,
    HOVER_MS,
    PRESS_MS,
    RELEASE_MS,
    SCROLL_MS,
    Tween,
    mix,
    reduced_motion,
)

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
                 base: str = theme.PANEL) -> None:
    """Tapa con el color del panel lo que aún no ha aparecido: un borde suave
    que barre de izquierda a derecha entre ``left`` y ``left + width``. Es un
    fundido, no un desplazamiento: con las animaciones reducidas también vale."""
    if progress >= 1.0:
        return
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
# Botones
# ---------------------------------------------------------------------------
class _Pressable(QAbstractButton):
    """Base de los botones: se hunde al pulsar y funde el hover.

    La escala solo responde al ratón. Activar un botón con el teclado no anima
    nada: lo que se repite muchas veces al día tiene que ser inmediato.
    """

    PRESS_SCALE = 0.97

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._scale = Tween(self, 1.0, self._repaint, ms=PRESS_MS, movement=True)
        self._hover = Tween(self, 0.0, self._repaint, ms=HOVER_MS, curve=EASE)

    def _repaint(self, _value=None) -> None:
        self.update()

    def _start(self, painter: QPainter) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        scale = float(self._scale.value)
        if scale != 1.0:
            center = QPointF(self.width() / 2, self.height() / 2)
            painter.translate(center)
            painter.scale(scale, scale)
            painter.translate(-center)
        if not self.isEnabled():
            painter.setOpacity(0.38)

    def _ring(self) -> bool:
        return self.hasFocus() and focus.keyboard()

    def enterEvent(self, event) -> None:  # noqa: N802 - API de Qt
        if self.isEnabled():
            self._hover.to(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover.to(0.0)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        super().mousePressEvent(event)
        if self.isDown():
            self._scale.to(self.PRESS_SCALE, PRESS_MS)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        super().mouseMoveEvent(event)
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._scale.to(self.PRESS_SCALE if self.isDown() else 1.0, PRESS_MS)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        super().mouseReleaseEvent(event)
        self._scale.to(1.0, RELEASE_MS)

    def changeEvent(self, event) -> None:  # noqa: N802
        if event.type() == QEvent.Type.EnabledChange and not self.isEnabled():
            self._hover.set(0.0)
            self._scale.set(1.0)
        super().changeEvent(event)


class PillButton(_Pressable):
    """Botón de texto. ``primary`` es claro y sólido; ``ghost`` va sobre la banda
    de color; ``quiet`` va sobre un panel y se vuelve sólido al marcarse."""

    HEIGHT = 36
    PAD = 18

    def __init__(self, text: str, kind: str = "ghost", glyph: Optional[str] = None,
                 parent: Optional[QWidget] = None, tabular: bool = False):
        super().__init__(parent)
        self._kind = kind
        self._glyph = glyph
        weight = QFont.Weight.DemiBold if kind == "primary" else QFont.Weight.Medium
        # Cifras de ancho fijo para lo que cuenta (una cuenta atrás no baila).
        self._font = theme.ui_font(13, weight, tabular=tabular)
        self._icon_only = False
        # Cuánto texto se ve (1 = entero, 0 = solo el icono).
        self._label = Tween(self, 1.0, self._on_label, ms=REFLOW_MS, curve=EASE_IN_OUT, movement=True)
        self.setText(text)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def full_width(self) -> int:
        """Ancho con el texto puesto, aunque ahora se vea solo el icono."""
        return self._text_width() + 2 * self.PAD

    def _on_label(self, _value) -> None:
        self.updateGeometry()
        self.update()

    def _share(self) -> float:
        return float(self._label.value) if self._glyph else 1.0

    def set_icon_only(self, icon_only: bool, animate: bool = True) -> None:
        """En paneles estrechos el botón se queda en un círculo con su icono;
        el texto pasa a la descripción emergente y al lector de pantalla. Al
        cambiar el ancho de la ventana el texto se recoge poco a poco."""
        icon_only = icon_only and bool(self._glyph)
        if icon_only == self._icon_only:
            return
        if self._glyph:
            target = 0.0 if icon_only else 1.0
            self._label.to(target) if animate else self._label.set(target)
        self._icon_only = icon_only
        self.setAccessibleName(self.text() if icon_only else "")
        if icon_only and not self.toolTip():
            self.setToolTip(self.text())
            self._auto_tip = True
        elif not icon_only and getattr(self, "_auto_tip", False):
            self.setToolTip("")
            self._auto_tip = False
        self.updateGeometry()
        self.update()

    def set_label(self, text: str, glyph: Optional[str] = None) -> None:
        """Cambia texto e icono (el botón cambia de tamaño con ellos)."""
        self._glyph = glyph
        self.setText(text)
        self.updateGeometry()
        self.update()

    def _text_width(self) -> int:
        width = QFontMetrics(self._font).horizontalAdvance(self.text())
        return width + (22 if self._glyph else 0)

    def _visible_share(self) -> float:
        """Cuánto texto cabe de verdad: si el layout lo aprieta por debajo de su
        tamaño (una ventana que se estrecha de golpe), se pinta como un botón a
        medio recoger, nunca con las letras cortadas por los dos lados."""
        share = self._share()
        if not self._glyph:
            return share
        room = (self.width() - self.HEIGHT) / max(1, self.full_width() - self.HEIGHT)
        return max(0.0, min(share, room))

    def _content_width(self, share: Optional[float] = None) -> float:
        if not self._glyph:
            return self._text_width()
        share = self._share() if share is None else share
        return 14 + (self._text_width() - 14) * share

    def sizeHint(self) -> QSize:  # noqa: N802
        if not self._glyph:
            return QSize(self.full_width(), self.HEIGHT)
        width = self.HEIGHT + (self.full_width() - self.HEIGHT) * self._share()
        return QSize(round(width), self.HEIGHT)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        self._start(painter)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = rect.height() / 2
        hover = float(self._hover.value)
        solid = self._kind == "primary" or self.isChecked()
        if solid:
            fill = mix(QColor(theme.SOLIDO), QColor(theme.SOLIDO_HOVER), hover)
            border = None
            ink = QColor(theme.TINTA_OSCURA)
        elif self._kind == "ghost":
            fill = _alpha(theme.PAPEL, 0.10 * hover)
            border = _alpha(theme.PAPEL, 0.34 + 0.22 * hover)
            ink = QColor(theme.PAPEL)
        else:
            fill = _alpha(theme.RELIEVE_2, hover)
            border = QColor(theme.LINEA)
            ink = mix(QColor(theme.HUMO), QColor(theme.PAPEL), hover)
        painter.setPen(QPen(border, 1) if border is not None else Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, radius, radius)
        if self._ring():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            if solid:
                inner = rect.adjusted(3, 3, -3, -3)
                painter.setPen(QPen(QColor(theme.TINTA_OSCURA), 2))
                painter.drawRoundedRect(inner, inner.height() / 2, inner.height() / 2)
            else:
                ring = rect.adjusted(0.5, 0.5, -0.5, -0.5)
                painter.setPen(QPen(QColor(theme.PAPEL), 2))
                painter.drawRoundedRect(ring, ring.height() / 2, ring.height() / 2)

        share = self._visible_share()
        x = (self.width() - self._content_width(share)) / 2
        if self._glyph:
            _draw_glyph(painter, QRectF(x, 0, 14, self.height()), self._glyph, 13, ink)
            x += 22
        if share <= 0.02:
            return
        painter.setOpacity(painter.opacity() * share)
        painter.setClipRect(rect.adjusted(radius * 0.5, 0, -radius * 0.5, 0))
        painter.setFont(self._font)
        painter.setPen(ink)
        painter.drawText(
            QRectF(x, 0, max(0.0, self.width() - x), self.height() - 1),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.text(),
        )


class IconButton(_Pressable):
    """Botón redondo de un solo icono. Marcado, el icono toma la tinta de la
    pista y gana un punto debajo: el color nunca es la única señal."""

    PRESS_SCALE = 0.95

    def __init__(self, glyph: str, tooltip: str, size: int = 40, glyph_px: int = 16,
                 checkable: bool = False, quiet: bool = False, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._glyph = glyph
        self._glyph_px = glyph_px
        self._quiet = quiet
        self._ink = QColor(theme.CREMA)
        self.setCheckable(checkable)
        self.setFixedSize(size, size)
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)

    def set_glyph(self, name: str) -> None:
        if name != self._glyph:
            self._glyph = name
            self.update()

    def set_ink(self, color: QColor) -> None:
        self._ink = QColor(color)
        self.update()

    def setToolTip(self, text: str) -> None:  # noqa: N802
        super().setToolTip(text)
        self.setAccessibleName(text)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        self._start(painter)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        hover = float(self._hover.value)
        if hover > 0:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_alpha(theme.RELIEVE_2, hover))
            painter.drawEllipse(rect)
        if self._ring():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(theme.PAPEL), 2))
            painter.drawEllipse(rect)
        if self.isChecked():
            color = self._ink
        else:
            rest = QColor(theme.HUMO if self._quiet else theme.PAPEL)
            color = mix(rest, QColor(theme.PAPEL), hover)
        _draw_glyph(painter, QRectF(self.rect()), self._glyph, self._glyph_px, color)
        if self.isChecked():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._ink)
            painter.drawEllipse(QPointF(self.width() / 2, self.height() - 5.5), 1.75, 1.75)


class PaneButton(IconButton):
    """Contraer o desplegar la barra lateral.

    El icono se dibuja a mano (un panel con su columna izquierda): el glifo de
    la fuente de iconos sale con flecos de color a este tamaño. Desplegada, la
    columna va rellena; contraída, solo perfilada.
    """

    def __init__(self, tooltip: str, size: int = 36, parent: Optional[QWidget] = None):
        super().__init__("more", tooltip, size=size, quiet=True, parent=parent)
        self._collapsed = False

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = collapsed
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        self._start(painter)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        hover = float(self._hover.value)
        if hover > 0:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_alpha(theme.RELIEVE_2, hover))
            painter.drawEllipse(rect)
        if self._ring():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(theme.PAPEL), 2))
            painter.drawEllipse(rect)
        color = mix(QColor(theme.HUMO), QColor(theme.PAPEL), hover)
        cx, cy = self.width() / 2, self.height() / 2
        frame = QRectF(cx - 8.5, cy - 6.5, 17, 13)
        divider = frame.left() + 6
        if not self._collapsed:
            column = QPainterPath()
            column.addRoundedRect(frame, 3, 3)
            painter.save()
            painter.setClipRect(QRectF(frame.left(), frame.top(), 6, frame.height()))
            painter.fillPath(column, _alpha(color, 0.45))
            painter.restore()
        pen = QPen(color, 1.3)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(frame, 3, 3)
        painter.drawLine(QPointF(divider, frame.top()), QPointF(divider, frame.bottom()))


class PlayButton(_Pressable):
    """Reproducir / pausa. Mientras se resuelve el audio gira un arco alrededor:
    la espera de unos segundos tiene respuesta visible desde el primer clic."""

    PRESS_SCALE = 0.95
    SIZE = 48

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._playing = False
        self._loading = False
        self._ink = QColor(theme.CREMA)
        # Mientras suena, un anillo de la tinta de la pista rodea el disco: el
        # estado se lee sin mirar el icono. Solo se funde (nada se mueve).
        self._halo = Tween(self, 0.0, self._repaint, ms=FADE_MS, curve=EASE)
        self._clock = QElapsedTimer()
        self._spin = QTimer(self)
        self._spin.setInterval(16)
        self._spin.timeout.connect(self.update)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setToolTip("Reproducir o pausar (Espacio)")
        self.setAccessibleName("Reproducir o pausar")

    def set_playing(self, playing: bool) -> None:
        if playing != self._playing:
            self._playing = playing
            self._halo.to(1.0 if playing else 0.0)
            self.update()

    def set_loading(self, loading: bool) -> None:
        if loading == self._loading:
            return
        self._loading = loading
        if loading:
            self._clock.start()
            self._spin.start()
        else:
            self._spin.stop()
        self.update()

    def set_ink(self, color: QColor) -> None:
        self._ink = QColor(color)
        self.update()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._spin.stop()
        super().hideEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        if self._loading:
            self._spin.start()
        super().showEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._loading:
            # Giro constante: lineal, y rápido para que la espera parezca corta.
            angle = -(self._clock.elapsed() % 700) / 700 * 360
            pen = QPen(self._ink, 2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawArc(QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5), int(angle * 16), 100 * 16)
        elif self._ring():
            painter.setPen(QPen(QColor(theme.PAPEL), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QRectF(self.rect()).adjusted(1, 1, -1, -1))
        elif float(self._halo.value) > 0:
            painter.setPen(QPen(_alpha(self._ink, 0.7 * float(self._halo.value)), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QRectF(self.rect()).adjusted(1, 1, -1, -1))

        scale = float(self._scale.value)
        center = QPointF(self.width() / 2, self.height() / 2)
        if scale != 1.0:
            painter.translate(center)
            painter.scale(scale, scale)
            painter.translate(-center)
        enabled = self.isEnabled()
        disc = QRectF(self.rect()).adjusted(4.5, 4.5, -4.5, -4.5)
        painter.setPen(Qt.PenStyle.NoPen)
        if enabled:
            painter.setBrush(mix(QColor(theme.SOLIDO), QColor(theme.SOLIDO_HOVER), float(self._hover.value)))
        else:
            painter.setBrush(QColor(theme.RELIEVE_2))
        painter.drawEllipse(disc)

        ink = QColor(theme.TINTA_OSCURA if enabled else theme.TENUE)
        painter.setBrush(ink)
        cx, cy = center.x(), center.y()
        if self._playing:
            painter.drawRoundedRect(QRectF(cx - 6.5, cy - 7, 4.5, 14), 1.2, 1.2)
            painter.drawRoundedRect(QRectF(cx + 2.0, cy - 7, 4.5, 14), 1.2, 1.2)
        else:
            # El triángulo va 1,5 px a la derecha: centrado de verdad parece caído a la izquierda.
            pen = QPen(ink, 1.6)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            path = QPainterPath()
            path.moveTo(cx - 4.0, cy - 7.0)
            path.lineTo(cx + 7.5, cy)
            path.lineTo(cx - 4.0, cy + 7.0)
            path.closeSubpath()
            painter.drawPath(path)


# ---------------------------------------------------------------------------
# Barra de progreso y volumen
# ---------------------------------------------------------------------------
class SeekSlider(QSlider):
    """Barra fina que engorda y enseña el tirador al acercar el ratón.

    Un clic salta a ese punto. Con el foco del teclado, las flechas emiten
    ``nudged`` (-1 / +1) para que la ventana decida cuánto avanzar.
    """

    nudged = Signal(int)
    PAD = 8

    def __init__(self, parent: Optional[QWidget] = None, nudge_keys: bool = False):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._nudge_keys = nudge_keys
        self._ink = QColor(theme.CREMA)
        self._grow = Tween(self, 0.0, lambda _v: self.update(), ms=HOVER_MS, curve=EASE_OUT)
        self.hover_text: Optional[Callable[[float], str]] = None   # fracción 0..1 → texto
        self.setMouseTracking(True)
        self.setFixedHeight(20)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)

    def set_ink(self, color: QColor) -> None:
        self._ink = QColor(color)
        self.update()

    def _fraction(self) -> float:
        span = self.maximum() - self.minimum()
        return (self.value() - self.minimum()) / span if span > 0 else 0.0

    def _fraction_at(self, x: float) -> float:
        span = max(1, self.width() - 2 * self.PAD)
        return max(0.0, min(1.0, (x - self.PAD) / span))

    def _value_at(self, x: float) -> int:
        return self.minimum() + round(self._fraction_at(x) * (self.maximum() - self.minimum()))

    def _show_hover_text(self, event) -> None:
        if self.hover_text is not None and self.isEnabled():
            text = self.hover_text(self._fraction_at(event.position().x()))
            if text:
                QToolTip.showText(event.globalPosition().toPoint(), text, self)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self.setSliderDown(True)
            self.setValue(self._value_at(event.position().x()))
            self._grow.to(1.0)
            event.accept()
        else:
            event.ignore()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self.isSliderDown():
            self.setValue(self._value_at(event.position().x()))
        self._show_hover_text(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self.isSliderDown():
            self.setSliderDown(False)
            if not self.underMouse():
                self._grow.to(0.0)

    def enterEvent(self, event) -> None:  # noqa: N802
        if self.isEnabled():
            self._grow.to(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if not self.isSliderDown():
            self._grow.to(0.0)
        super().leaveEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if self._nudge_keys and event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            self.nudged.emit(-1 if event.key() == Qt.Key.Key_Left else 1)
            event.accept()
            return
        super().keyPressEvent(event)

    def wheelEvent(self, event) -> None:  # noqa: N802
        if self._nudge_keys:
            event.ignore()      # la rueda sobre el progreso no debe mover la pista
        else:
            super().wheelEvent(event)

    def changeEvent(self, event) -> None:  # noqa: N802
        if event.type() == QEvent.Type.EnabledChange and not self.isEnabled():
            self._grow.set(0.0)
        super().changeEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        enabled = self.isEnabled()
        ring = self.hasFocus() and focus.keyboard()
        grow = 1.0 if ring else float(self._grow.value)
        thickness = 4.0 + 2.0 * grow
        left, right = self.PAD, self.width() - self.PAD
        y = self.height() / 2
        track = QRectF(left, y - thickness / 2, right - left, thickness)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.LINEA if enabled else theme.RELIEVE))
        painter.drawRoundedRect(track, thickness / 2, thickness / 2)
        x = left + (right - left) * self._fraction()
        if x > left:
            painter.setBrush(self._ink if enabled else QColor(theme.LINEA))
            painter.drawRoundedRect(QRectF(left, track.top(), x - left, thickness), thickness / 2, thickness / 2)
        if enabled and grow > 0:
            # El tirador nunca nace de la nada: aparece ya con dos tercios de su tamaño.
            painter.setOpacity(grow)
            painter.setBrush(QColor(theme.PAPEL))
            painter.drawEllipse(QPointF(x, y), 6.5 * (0.66 + 0.34 * grow), 6.5 * (0.66 + 0.34 * grow))
            painter.setOpacity(1.0)
        if ring:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(theme.PAPEL), 1.5))
            painter.drawEllipse(QPointF(x, y), 9.0, 9.0)


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


class CoverLabel(QWidget):
    """Carátula cuadrada. Con ``shadow`` lleva una sombra dura y desplazada,
    como una funda apoyada sobre papel de color (sin desenfoque).

    Al cambiar de imagen funde la anterior con la nueva: la nueva sube de
    opacidad sobre la anterior, que sigue opaca debajo, así el conjunto nunca
    se oscurece a mitad del cambio.
    """

    def __init__(self, loader: ArtLoader, size: int, radius: float = theme.R_COVER, shadow: int = 0,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._loader = loader
        self._size = size
        self._radius = radius
        self._round = False
        self._shadow = shadow
        self._url = ""
        self._shadow_color = QColor(theme.LINEA_CLARA)
        self._shown: Optional[QPixmap] = None
        self._previous: Optional[QPixmap] = None
        self._fade = Tween(self, 1.0, lambda _v: self.update(), ms=FADE_MS, curve=EASE)
        # Cuánto se ha separado el eco de la funda (0 = debajo, 1 = en su sitio).
        self._echo = Tween(self, 1.0, lambda _v: self.update(), ms=ENTER_MS, curve=EASE_OUT, movement=True)
        self._display: Optional[float] = None    # tamaño de paso mientras se anima
        self._presence = 1.0
        self._held = False      # la imagen está de viaje: aquí no se pinta
        self._waiting = False   # se sigue enseñando la anterior hasta que llegue la nueva
        self._hold_for = ""     # la dirección por la que se esperó (una sola vez)
        self._serial = 0
        self.setFixedSize(size + shadow, size + shadow)
        loader.loaded.connect(self._on_loaded)

    def set_url(self, url: str) -> None:
        if url != self._url:
            self._url = url
            self._swap()

    def art_rect(self) -> QRectF:
        """Dónde se pinta la imagen, en coordenadas de este widget."""
        side = self._display if self._display is not None else self._size
        return QRectF(0, 0, side, side)

    def corner(self) -> float:
        return self._corner()

    def set_held(self, held: bool) -> None:
        """Retiene la imagen mientras una copia vuela hacia aquí. Al soltarla
        el eco se separa de la funda, como si la carátula acabara de posarse."""
        if held == self._held:
            return
        self._held = held
        if not held and self._shadow:
            self._echo.set(0.0)
            self._echo.to(1.0)
        self.update()

    def set_display_size(self, px: float, presence: float = 1.0) -> None:
        """Tamaño de paso durante una animación: se pinta la imagen que ya hay,
        escalada, sin pedir otra a cada fotograma. ``set_size`` fija el final."""
        self._display = px
        self._presence = max(0.0, min(1.0, presence))
        scale = px / self._size
        self.setFixedSize(round((self._size + self._shadow) * scale), round((self._size + self._shadow) * scale))
        self.update()

    def set_size(self, size: int, shadow: int) -> None:
        if self._display is not None:
            self._display = None
            self._presence = 1.0
            self.setFixedSize(self._size + self._shadow, self._size + self._shadow)
            self.update()
        if (size, shadow) != (self._size, self._shadow):
            self._size, self._shadow = size, shadow
            self.setFixedSize(size + shadow, size + shadow)
            self._shown = self._resolve()
            self._previous = None
            self._fade.set(1.0)
            self.update()

    def set_shadow_color(self, color: QColor) -> None:
        self._shadow_color = color
        self.update()

    def set_round(self, round_art: bool) -> None:
        """Retrato redondo (un artista) en vez de funda cuadrada."""
        if round_art != self._round:
            self._round = round_art
            self._shown = self._resolve()
            self._previous = None
            self._fade.set(1.0)
            self.update()

    def _corner(self) -> float:
        return self._size / 2 if self._round else self._radius

    def _resolve(self) -> QPixmap:
        dpr = self.devicePixelRatioF()
        pixmap = self._loader.pixmap(self._url, self._size, self._corner(), dpr) if self._url else None
        return pixmap or placeholder_pixmap(self._size, self._corner(), dpr)

    # Lo que se espera a la carátula nueva antes de dar paso a la funda vacía.
    HOLD_MS = 1200

    def _swap(self) -> None:
        url = self._url
        new_art = (self._loader.pixmap(url, self._size, self._corner(), self.devicePixelRatioF())
                   if url else None)
        pending = new_art is None and bool(url) and not self._loader.failed(url)
        if pending and self._shown is not None and self._hold_for != url:
            # La nueva aún viene de camino: la de antes sigue entera en vez de
            # fundirse con la funda vacía y luego saltar a la buena (dos
            # cambios seguidos se ven como un parpadeo). Si tarda, la funda.
            self._hold_for = url
            self._waiting = True
            self._serial += 1
            serial = self._serial
            QTimer.singleShot(self.HOLD_MS, self, lambda: self._give_up(serial))
            return
        if pending and self._waiting:
            return
        self._waiting = False
        self._serial += 1
        new = new_art or placeholder_pixmap(self._size, self._corner(), self.devicePixelRatioF())
        if self._shown is not None and new.cacheKey() == self._shown.cacheKey():
            return
        self._previous = self._shown
        self._shown = new
        if self._previous is None:
            self._fade.set(1.0)
        else:
            self._fade.set(0.0)
            self._fade.to(1.0)
            if self._shadow:
                # Ocasional (cambiar de lista o de canción): el eco nace bajo la
                # funda y se desliza a su sitio, como una estela de la imagen nueva.
                self._echo.set(0.0)
                self._echo.to(1.0)
        self.update()

    def _give_up(self, serial: int) -> None:
        if serial == self._serial and self._waiting:
            self._waiting = False
            self._swap()

    def _on_loaded(self, url: str) -> None:
        if url == self._url:
            self._swap()

    def _refresh_resolution(self) -> None:
        """La ventana pasó a una pantalla con otra escala: la misma imagen,
        rehecha a la densidad nueva y sin fundido (no ha cambiado nada)."""
        self._shown = self._resolve()
        self._previous = None
        self._fade.set(1.0)
        self.update()

    def event(self, event) -> bool:  # noqa: D401 - API de Qt
        if event.type() == QEvent.Type.DevicePixelRatioChange:
            self._refresh_resolution()
        return super().event(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._held:
            return
        if self._shown is None:
            self._shown = self._resolve()
        if self._display is not None:
            scale = self._display / self._size
            painter.scale(scale, scale)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.setOpacity(self._presence)
        if self._shadow:
            offset = self._shadow * float(self._echo.value)
            if offset > 0.5:
                echo = QRectF(offset, offset, self._size, self._size).adjusted(0.75, 0.75, -0.75, -0.75)
                painter.setPen(QPen(self._shadow_color, 1.5))
                painter.setBrush(_alpha(self._shadow_color, 0.38))
                painter.drawRoundedRect(echo, self._corner(), self._corner())
        progress = float(self._fade.value)
        if progress < 1.0 and self._previous is not None:
            painter.drawPixmap(0, 0, self._previous)
            painter.setOpacity(progress)
        painter.drawPixmap(0, 0, self._shown)


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
        painter.setBrush(QColor(theme.ORO))
        painter.drawRoundedRect(QRectF(self.rect()), self.W / 2, self.W / 2)


# ---------------------------------------------------------------------------
# Banda de cabecera
# ---------------------------------------------------------------------------
class PlaylistBand(QWidget):
    """Cabecera de la vista: banda de color plano con carátula, título y notas.

    El color no salta de una lista a otra: se funde. En ventanas bajas la banda
    se encoge (``set_compact``) para dejarle sitio a la lista.
    """

    COVER = 172
    COVER_COMPACT = 112
    FACT_GAP = 26
    HEIGHT = 28 + 172 + 8 + 26
    HEIGHT_COMPACT = 20 + 112 + 6 + 18
    # La búsqueda añade la fila de sugerencias bajo el campo.
    SEARCH_HEIGHT = 262
    SEARCH_HEIGHT_COMPACT = 186

    def __init__(self, loader: ArtLoader, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._compact = False
        self._show_cover = True
        self._cover_target = self.COVER     # 0: sin carátula
        self._cover_shadow = 8
        self._has_description = False
        self._searching = False
        self._color = Tween(self, QColor(neutral_tint().band), self._on_color, ms=FADE_MS + 60, curve=EASE)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._init_rings()

        outer = QHBoxLayout(self)
        outer.setContentsMargins(32, 28, 32, 26)
        outer.setSpacing(28)

        self.cover = CoverLabel(loader, self.COVER, radius=theme.R_COVER, shadow=8)
        # Al estrechar la ventana la carátula se encoge o se va poco a poco, y
        # el texto se desliza a ocupar su sitio.
        self._cover_px = Tween(self, float(self.COVER), self._apply_cover, ms=REFLOW_MS,
                               curve=EASE_IN_OUT, movement=True)
        outer.addWidget(self.cover, 0, Qt.AlignmentFlag.AlignTop)

        text = QVBoxLayout()
        text.setSpacing(0)
        self.title = QLabel()
        self.title.setObjectName("bandTitle")
        self.title.setWordWrap(False)
        self.title.setMinimumWidth(60)
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        text.addWidget(self.title)

        self.description = QLabel()
        self.description.setObjectName("bandText")
        self.description.setWordWrap(True)
        self.description.setMaximumWidth(620)
        self.description.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.description.setMaximumHeight(56)
        self.description.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        text.addSpacing(6)
        text.addWidget(self.description)

        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("bandSearch")
        self.search_edit.setPlaceholderText("Canción, artista o álbum")
        self.search_edit.setAccessibleName("Buscar música")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setMaximumWidth(520)
        self.search_edit.setMinimumHeight(42)
        # Las sugerencias van pegadas al campo: es donde está la vista al escribir.
        self.suggestions = SuggestionStrip(on_band=True)
        self._search_box = QWidget()
        search_lay = QVBoxLayout(self._search_box)
        search_lay.setContentsMargins(0, 0, 0, 0)
        search_lay.setSpacing(10)
        search_lay.addWidget(self.search_edit)
        search_lay.addWidget(self.suggestions)
        text.addSpacing(10)
        text.addWidget(self._search_box)

        text.addStretch(1)
        self._facts_row = QHBoxLayout()
        self._facts_row.setSpacing(self.FACT_GAP)
        self._fact_labels: list[QLabel] = []
        self._facts: list[str] = []
        text.addLayout(self._facts_row)
        text.addSpacing(14)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.play_btn = PillButton("Reproducir", "primary", glyph="play")
        self.shuffle_btn = PillButton("Aleatorio", "ghost", glyph="shuffle")
        self.sync_btn = PillButton("Sincronizar lista", "ghost", glyph="sync")
        self.sync_btn.setToolTip("Descarga esta lista completa aunque el conteo no haya cambiado")
        # Botón de la vista: "Opciones" en una lista local, "Guardar como lista
        # local" en una ajena. La ventana decide texto y qué hace.
        self.extra_btn = PillButton("", "ghost")
        self.extra_btn.hide()
        self._actions = (self.play_btn, self.shuffle_btn, self.sync_btn, self.extra_btn)
        for button in self._actions:
            actions.addWidget(button)
        # En la búsqueda la nota ("12 resultados para…") comparte fila con los
        # botones: su sitio de siempre lo ocupan las sugerencias.
        self.search_note = QLabel()
        self.search_note.setObjectName("bandFact")
        self.search_note.setMinimumHeight(PillButton.HEIGHT)
        self.search_note.hide()
        actions.addWidget(self.search_note)
        actions.addStretch(1)
        # Solo en las listas ajenas a la biblioteca: no tienen fila en la barra
        # lateral, así que el camino de vuelta tiene que estar a la vista.
        self.back_btn = PillButton("Volver", "ghost", glyph="back")
        self.back_btn.setToolTip("Volver a la vista anterior (Alt+Izquierda)")
        self.back_btn.hide()
        actions.addWidget(self.back_btn)
        text.addLayout(actions)
        outer.addLayout(text, 1)
        self.setFixedHeight(self.HEIGHT)
        self._full_title = ""

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - API de Qt
        # El ancho no lo impone el contenido: la banda se adapta a lo que le den
        # (carátula más pequeña o fuera, botones en solo icono, notas que no
        # caben ocultas). Si dependiera de lo que muestra, el mínimo cambiaría
        # al arrastrar los paneles y el divisor se trabaría o daría saltos.
        return QSize(0, super().minimumSizeHint().height())

    # -- contenido ----------------------------------------------------------
    def set_content(self, title: str, description: str, facts: list[str], cover_url: str,
                    show_search: bool = False, show_sync: bool = True, show_cover: bool = True,
                    show_back: bool = False, extra: Optional[tuple[str, str]] = None,
                    round_cover: bool = False) -> None:
        self._show_cover = show_cover
        self.cover.set_round(round_cover)
        self.back_btn.setVisible(show_back)
        if extra is not None:
            self.extra_btn.set_label(*extra)
        self.extra_btn.setVisible(extra is not None)
        self._full_title = title
        self._elide_title()
        self.title.setToolTip(title)
        self.description.setText(description)
        # Solo líneas enteras: con la columna estrecha no asoma media línea sobre las notas.
        self.description.ensurePolished()
        self.description.setMaximumHeight(QFontMetrics(self.description.font()).lineSpacing() * 3)
        self._has_description = bool(description)
        self._searching = show_search
        self.description.setVisible(self._has_description and not show_search and not self._compact)
        self._search_box.setVisible(show_search)
        self.search_note.setVisible(show_search)
        self.sync_btn.setVisible(show_sync)
        self.cover.set_url(cover_url)
        self._apply_height()
        self.set_facts(facts)
        self._fit_width(animate=False)      # otra vista: llega ya colocada

    def set_compact(self, compact: bool) -> None:
        if compact == self._compact:
            return
        self._compact = compact
        margins = (24, 20, 24, 18) if compact else (32, 28, 32, 26)
        self.layout().setContentsMargins(*margins)
        self.layout().setSpacing(22 if compact else 28)
        self.title.setStyleSheet("font-size: 28px;" if compact else "")
        self.search_edit.setMinimumHeight(38 if compact else 42)
        self.description.setVisible(self._has_description and not self._searching and not compact)
        self._apply_height()
        self._fit_width()

    # Ancho que necesita la columna de texto junto a cada tamaño de carátula.
    TEXT_MIN_FULL = 340
    TEXT_MIN_SMALL = 230
    ACTION_GAP = 10
    # Margen con el que se recoge cada cosa: al estrechar la ventana arrastrando,
    # la animación termina antes de que el sitio falte de verdad.
    SLACK = 40

    def _text_width(self) -> int:
        """Ancho de la columna de texto cuando la carátula llegue a su tamaño."""
        margins = self.layout().contentsMargins()
        width = self.width() - margins.left() - margins.right()
        if self._cover_target:
            width -= self._cover_target + self._cover_shadow + self.layout().spacing()
        return width

    def _apply_cover(self, value) -> None:
        px, target = float(value), self._cover_target
        if target and abs(px - target) < 0.5:
            self.cover.set_size(target, self._cover_shadow)
            self.cover.setVisible(True)
        elif px < 4:
            self.cover.hide()
        else:
            self.cover.setVisible(True)
            # Al irse, además de encogerse se funde; entre dos tamaños, no.
            self.cover.set_display_size(px, presence=min(1.0, px / self.COVER_COMPACT))

    def _fit_width(self, animate: bool = True) -> None:
        """Reparte el ancho: primero encoge la carátula, luego la quita; los
        botones que no caben con su texto se quedan en solo icono. Con
        ``animate`` (cambios de ancho) todo eso se ve pasar; al cambiar de
        vista llega ya colocado."""
        margins = self.layout().contentsMargins()
        inner = self.width() - margins.left() - margins.right()
        spacing = self.layout().spacing()
        big = self.COVER_COMPACT if self._compact else self.COVER
        if inner - big - 8 - spacing >= self.TEXT_MIN_FULL + self.SLACK:
            size, fits = big, True
        elif inner - self.COVER_COMPACT - 6 - spacing >= self.TEXT_MIN_SMALL + self.SLACK:
            size, fits = self.COVER_COMPACT, True
        else:
            size, fits = big, False
        target = size if (self._show_cover and fits) else 0
        if target:
            self._cover_shadow = 6 if size == self.COVER_COMPACT else 8
        self._cover_target = target
        if animate and self.cover.isVisible() != bool(target) and not self.cover.isVisible():
            self._cover_px.set(0.0)          # aparece desde nada, no desde su último tamaño
        if animate:
            self._cover_px.to(float(target))
        else:
            self._cover_px.set(float(target))
            self._apply_cover(float(target))
        self._fit_actions(self._text_width(), animate)
        self._elide_title()
        self._fit_facts(animate)

    def _fit_actions(self, available: int, animate: bool = True) -> None:
        shown = [b for b in self._actions if b.isVisibleTo(self)]
        if self.back_btn.isVisibleTo(self):
            shown.append(self.back_btn)
        if not shown:
            return
        gap = self.ACTION_GAP
        # La nota de la búsqueda comparte fila con los botones: se le guarda sitio.
        if self.search_note.isVisibleTo(self):
            available -= min(160, self.search_note.sizeHint().width()) + gap
        gaps = gap * (len(shown) - 1)
        full = sum(b.full_width() for b in shown) + gaps
        available -= self.SLACK
        secondary_icons = full > available
        primary_icon = shown[0].full_width() + PillButton.HEIGHT * (len(shown) - 1) + gaps > available
        for i, button in enumerate(shown):
            button.set_icon_only(primary_icon if i == 0 else secondary_icons, animate)

    def _apply_height(self) -> None:
        if self._searching:
            # En ventanas bajas el título cede su sitio: la barra lateral ya dice dónde se está.
            self.title.setVisible(not self._compact)
            self.setFixedHeight(self.SEARCH_HEIGHT_COMPACT if self._compact else self.SEARCH_HEIGHT)
        else:
            self.title.setVisible(True)
            self.setFixedHeight(self.HEIGHT_COMPACT if self._compact else self.HEIGHT)

    def set_facts(self, facts: list[str]) -> None:
        """Datos de la lista como notas separadas (sin puntos ni viñetas)."""
        if self._searching:
            self._facts = []
            self.search_note.setText(facts[0] if facts else "")
            self._fit_facts()
            return
        self._facts = list(facts)
        while len(self._fact_labels) < len(facts):
            label = QLabel()
            label.setObjectName("bandFact")
            label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            self._fact_labels.append(label)
            self._facts_row.insertWidget(len(self._fact_labels) - 1, label)
        if self._facts_row.count() == len(self._fact_labels):
            self._facts_row.addStretch(1)
        for i, label in enumerate(self._fact_labels):
            if i < len(facts):
                label.setText(facts[i])
        self._fit_facts()

    def _fit_facts(self, animate: bool = False) -> None:
        """Muestra solo las notas que caben enteras; ninguna se corta a media
        palabra. Al cambiar el ancho, las que sobran se funden."""
        available = self._text_width()
        used = 0
        for i, label in enumerate(self._fact_labels):
            if i >= len(self._facts):
                fade_visible(label, False, animate=False)
                continue
            needed = label.sizeHint().width() + (self.FACT_GAP if i else 0)
            fits = i == 0 or used + needed <= available
            fade_visible(label, fits, animate)
            if fits:
                used += needed

    def set_tint(self, tint: Tint) -> None:
        self._color.to(QColor(tint.band))

    def _on_color(self, color) -> None:
        self.cover.set_shadow_color(QColor(color).darker(128))
        self.update()

    def _elide_title(self) -> None:
        self.title.ensurePolished()
        metrics = QFontMetrics(self.title.font())
        # Se mide con el ancho que tendrá la columna, no con el de ahora: durante
        # un arrastre el layout aún no ha recolocado el título.
        width = max(40, min(self._text_width(), self.title.maximumWidth()))
        self.title.setText(metrics.elidedText(self._full_title, Qt.TextElideMode.ElideRight, width))

    def resizeEvent(self, event) -> None:  # noqa: N802 - API de Qt
        super().resizeEvent(event)
        self._layout_rings()
        # Con un panel deslizándose o al cambiar de vista, el ancho cambia en
        # cada fotograma: animar cada paso haría competir decenas de tweens.
        self._fit_width(animate=not window_settling(self))

    # -- aros de luz --------------------------------------------------------
    # Dos anillos que salen por la esquina, con tramos de oro: la única
    # decoración de la interfaz. Mientras suena música los tramos giran (el de
    # fuera a SPIN_OUTER grados por segundo, el de dentro a SPIN_INNER) y los
    # anillos respiran; la cadencia sigue el reloj de la pista (se para con
    # ella y salta con ella al buscar), no un pulso inventado. En pausa
    # frenan con EASE_OUT hasta quedarse quietos, y entonces no queda ningún
    # temporizador en marcha.
    RING_INNER, RING_OUTER = 0.55, 0.78     # radios, en altos de banda
    RING_CENTER = (0.08, -0.05)              # el centro, fuera de la esquina
    BREATH = 0.012                           # cuánto crece y mengua el radio
    BREATH_S = 7.0                           # periodo de la respiración
    SPIN_OUTER, SPIN_INNER = 14.0, 23.0      # grados por segundo (X e Y)
    CADENCE = 0.22                           # cuánto acelera en lo alto de cada ciclo
    CADENCE_S = 4.0                          # ciclo de la cadencia, en segundos de pista
    ARC_OUTER, ARC_INNER = 24, 16            # largo de cada tramo de oro, en grados
    REST_OUTER, REST_INNER = 214.0, 236.0    # dónde quedan en reposo
    ARCS = 4                                 # tramos por anillo: siempre asoma alguno
    RING_PEN = 1.5
    GOLD_PEN = 3.0

    def _init_rings(self) -> None:
        self._playing = False
        self._window_shown = True
        self._angles = [self.REST_OUTER, self.REST_INNER]
        self._breath = (0.0, 0.0)
        self._breath_t = 0.0
        self._level = 0.0                    # 0 quietos, 1 a toda velocidad
        self._level_from = self._level_to = 0.0
        self._ramp_at = 0
        self._song_pos = 0.0
        self._song_pos_at = 0
        self._ring_clock = QElapsedTimer()
        self._ring_clock.start()
        self._last_tick = 0
        self._ring_timer = QTimer(self)
        self._ring_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._ring_timer.setInterval(FRAME_MS)
        self._ring_timer.timeout.connect(self._ring_tick)
        # Lo que no cambia de un fotograma a otro se prepara una vez.
        self._pen_inner = QPen(QColor(255, 255, 255, 120), self.RING_PEN)
        self._pen_outer = QPen(QColor(255, 255, 255, 170), self.RING_PEN)
        self._pen_gold = QPen(QColor(theme.ORO), self.GOLD_PEN)
        self._pen_gold.setCapStyle(Qt.PenCapStyle.RoundCap)
        self._band_path = QPainterPath()
        self._ring_center = QPointF()
        self._radii = (0.0, 0.0)
        self._rings_rect = QRect()
        self._layout_rings()

    def _layout_rings(self) -> None:
        """Geometría de la banda y de los aros: solo cambia con el tamaño."""
        rect = QRectF(self.rect())
        radius = float(theme.R_PANEL)
        path = QPainterPath()
        path.moveTo(rect.left(), rect.bottom())
        path.lineTo(rect.left(), rect.top() + radius)
        path.quadTo(rect.left(), rect.top(), rect.left() + radius, rect.top())
        path.lineTo(rect.right() - radius, rect.top())
        path.quadTo(rect.right(), rect.top(), rect.right(), rect.top() + radius)
        path.lineTo(rect.right(), rect.bottom())
        path.closeSubpath()
        self._band_path = path
        h = rect.height()
        dx, dy = self.RING_CENTER
        self._ring_center = QPointF(rect.right() + h * dx, rect.top() + h * dy)
        self._radii = (h * self.RING_OUTER, h * self.RING_INNER)
        # Lo único que se repinta en cada fotograma: el cuadro que encierra el
        # anillo de fuera en su punto más grande, con el grosor del oro.
        reach = self._radii[0] * (1 + self.BREATH) + self.GOLD_PEN + 2
        left = math.floor(self._ring_center.x() - reach)
        bottom = math.ceil(self._ring_center.y() + reach)
        self._rings_rect = QRect(left, 0, self.width() - left, bottom).intersected(self.rect())

    def rings_rect(self) -> QRect:
        return QRect(self._rings_rect)

    def rings_running(self) -> bool:
        return self._ring_timer.isActive()

    def set_playing(self, playing: bool) -> None:
        """Con música, los tramos cogen velocidad; sin ella, frenan."""
        if playing == self._playing:
            return
        self._playing = playing
        if reduced_motion():
            return                       # quietos siempre: ni temporizador ni repintados
        now = self._ring_clock.elapsed()
        self._level_from = self._level_at(now)
        self._level_to = 1.0 if playing else 0.0
        self._ramp_at = now
        if playing:
            self._song_pos_at = now
        self._sync_rings()

    def set_position(self, seconds: float) -> None:
        """Segundo de la pista: marca la cadencia del giro."""
        self._song_pos = max(0.0, float(seconds))
        self._song_pos_at = self._ring_clock.elapsed()

    def set_window_shown(self, shown: bool) -> None:
        """La ventana se minimizó o volvió: minimizada no se gasta nada."""
        self._window_shown = shown
        self._sync_rings()

    def showEvent(self, event) -> None:  # noqa: N802 - API de Qt
        super().showEvent(event)
        self._sync_rings()

    def hideEvent(self, event) -> None:  # noqa: N802 - API de Qt
        super().hideEvent(event)
        self._sync_rings()

    def _level_at(self, now: int) -> float:
        progress = (now - self._ramp_at) / SPIN_MS
        if progress >= 1.0:
            return self._level_to
        eased = EASE_OUT.valueForProgress(max(0.0, progress))
        return self._level_from + (self._level_to - self._level_from) * eased

    def _sync_rings(self) -> None:
        moving = self._playing or self._level > 0.0 or self._level_to > 0.0
        run = (moving and self._window_shown and self.isVisible() and not reduced_motion())
        if run and not self._ring_timer.isActive():
            self._last_tick = self._ring_clock.elapsed()
            self._ring_timer.start()
        elif not run and self._ring_timer.isActive():
            self._ring_timer.stop()

    def _ring_tick(self) -> None:
        now = self._ring_clock.elapsed()
        # Tras un tropiezo (la ventana ocupada) el giro sigue donde iba: no salta.
        dt = min(0.05, max(0.0, (now - self._last_tick) / 1000.0))
        self._last_tick = now
        level = self._level = self._level_at(now)
        song = self._song_pos + ((now - self._song_pos_at) / 1000.0 if self._playing else 0.0)
        cadence = 0.5 - 0.5 * math.cos(math.tau * song / self.CADENCE_S)
        speed = level * (1.0 + self.CADENCE * cadence) * dt
        self._angles[0] = (self._angles[0] - self.SPIN_OUTER * speed) % 360.0
        self._angles[1] = (self._angles[1] - self.SPIN_INNER * speed) % 360.0
        self._breath_t += dt
        phase = math.tau * self._breath_t / self.BREATH_S
        amount = self.BREATH * level
        self._breath = (amount * math.sin(phase), amount * math.sin(phase + math.pi / 2))
        self.update(self._rings_rect)
        if level <= 0.0 and self._level_to <= 0.0 and not self._playing:
            self._level = 0.0
            self._ring_timer.stop()

    def paintEvent(self, _event) -> None:  # noqa: N802 - API de Qt
        # Color plano, sin degradados; solo las esquinas de arriba siguen al panel.
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillPath(self._band_path, QColor(self._color.value))
        # Los aros no llegan a las esquinas redondeadas: basta el recorte del widget.
        painter.setBrush(Qt.BrushStyle.NoBrush)
        center = self._ring_center
        outer = self._radii[0] * (1.0 + self._breath[0])
        inner = self._radii[1] * (1.0 + self._breath[1])
        painter.setPen(self._pen_inner)
        painter.drawEllipse(center, inner, inner)
        painter.setPen(self._pen_outer)
        painter.drawEllipse(center, outer, outer)
        painter.setPen(self._pen_gold)
        step = 360.0 / self.ARCS
        for radius, angle, span in ((outer, self._angles[0], self.ARC_OUTER),
                                    (inner, self._angles[1], self.ARC_INNER)):
            box = QRectF(center.x() - radius, center.y() - radius, 2 * radius, 2 * radius)
            for k in range(self.ARCS):
                painter.drawArc(box, round((angle + k * step) * 16), span * 16)


# ---------------------------------------------------------------------------
# Sugerencias de búsqueda
# ---------------------------------------------------------------------------
class Chip(_Pressable):
    """Una sugerencia como nota suelta. Lo ya escrito va apagado y lo que la
    sugerencia añade, claro y en negrita: se lee de un vistazo qué cambia."""

    HEIGHT = 32
    PAD = 13
    MAX_TEXT = 250

    def __init__(self, parent: Optional[QWidget] = None, on_band: bool = False):
        super().__init__(parent)
        self._on_band = on_band
        self._typed = ""
        self._rest = ""
        self._glyph: Optional[str] = None
        self._strong = True
        self._font = theme.ui_font(13)
        self._bold = theme.ui_font(13, QFont.Weight.DemiBold)
        self.setFixedHeight(self.HEIGHT)

    def suggestion(self) -> str:
        return self._typed + self._rest

    def set_suggestion(self, text: str, typed: str = "", glyph: Optional[str] = None,
                       strong: bool = True) -> None:
        typed = typed.strip()
        if typed and text.casefold().startswith(typed.casefold()) and len(text) > len(typed):
            self._typed, self._rest = text[: len(typed)], text[len(typed):]
        else:
            self._typed, self._rest = "", text
        self._glyph = glyph
        self._strong = strong
        self.setAccessibleName(text)
        self.setFixedWidth(self._lead() + min(self.MAX_TEXT, self._text_width()) + self.PAD)
        self.setToolTip(text if self._text_width() > self.MAX_TEXT else "")
        self.update()

    def _lead(self) -> int:
        return self.PAD + (20 if self._glyph else 0)

    def _rest_font(self) -> QFont:
        return self._bold if self._strong else self._font

    def _text_width(self) -> int:
        return (QFontMetrics(self._font).horizontalAdvance(self._typed)
                + QFontMetrics(self._rest_font()).horizontalAdvance(self._rest) + 1)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.click()
            return
        super().keyPressEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        self._start(painter)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = rect.height() / 2
        hover = float(self._hover.value)
        if self._on_band:
            # Sobre la banda de color: mismos trazos translúcidos que el campo de búsqueda.
            painter.setPen(QPen(_alpha(theme.PAPEL, 0.22 + 0.20 * hover), 1))
            painter.setBrush(_alpha(theme.PAPEL, 0.10 * hover))
        else:
            painter.setPen(QPen(mix(QColor(theme.LINEA), QColor(theme.LINEA_CLARA), hover), 1))
            painter.setBrush(_alpha(theme.RELIEVE, hover))
        painter.drawRoundedRect(rect, radius, radius)
        if self._ring():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(theme.PAPEL), 2))
            ring = rect.adjusted(0.5, 0.5, -0.5, -0.5)
            painter.drawRoundedRect(ring, ring.height() / 2, ring.height() / 2)

        x = float(self.PAD)
        quiet = _alpha(theme.PAPEL, 0.66) if self._on_band else QColor(theme.HUMO)
        if self._glyph:
            _draw_glyph(painter, QRectF(x, 0, 14, self.height()), self._glyph, 12, quiet)
            x += 20
        flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        room = self.width() - x - self.PAD + 1
        if self._typed:
            width = QFontMetrics(self._font).horizontalAdvance(self._typed)
            painter.setFont(self._font)
            painter.setPen(quiet)
            painter.drawText(QRectF(x, 0, room, self.height() - 1), flags, self._typed)
            x += width
            room -= width
        font = self._rest_font()
        painter.setFont(font)
        rest_color = QColor(theme.PAPEL) if self._strong else mix(quiet, QColor(theme.PAPEL), hover)
        painter.setPen(rest_color)
        painter.drawText(
            QRectF(x, 0, room, self.height() - 1), flags,
            QFontMetrics(font).elidedText(self._rest, Qt.TextElideMode.ElideRight, int(room)),
        )


class SuggestionStrip(QWidget):
    """Fila de sugerencias bajo el buscador.

    Ocupa siempre el mismo alto, haya o no sugerencias, para que los
    resultados no den saltos mientras se escribe. Las que no caben enteras se
    ocultan. Al escribir, los textos cambian en el sitio y sin animación (se
    repite a cada tecla); solo la primera aparición se funde.
    """

    picked = Signal(str)
    GAP = 8

    def __init__(self, on_band: bool = False, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._on_band = on_band
        self._inset = 0
        self._chips: list[Chip] = []
        self._count = 0
        self._lead = ""
        self._lead_font = theme.ui_font(12)
        self.setFixedHeight(Chip.HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setEnabled(False)
        self.setGraphicsEffect(self._effect)
        self._fade = Tween(self, 1.0, self._apply_fade, ms=FADE_MS, curve=EASE)

    def _apply_fade(self, value) -> None:
        opacity = float(value)
        self._effect.setOpacity(opacity)
        self._effect.setEnabled(opacity < 1.0)   # en reposo se pinta sin capa intermedia

    def set_items(self, texts: list[str], typed: str = "", lead: str = "",
                  glyph: Optional[str] = None, strong: bool = True) -> None:
        was_empty = self._count == 0
        while len(self._chips) < len(texts):
            chip = Chip(self, self._on_band)
            chip.clicked.connect(lambda _checked=False, c=chip: self.picked.emit(c.suggestion()))
            self._chips.append(chip)
        self._count = len(texts)
        self._lead = lead if texts else ""
        self.setAccessibleName(lead or "Sugerencias de búsqueda")
        for chip, text in zip(self._chips, texts):
            chip.set_suggestion(text, typed, glyph, strong)
        self._fit()
        self.update()
        if was_empty and texts:
            self._fade.set(0.0)
            self._fade.to(1.0)

    def shown(self) -> list[str]:
        """Las sugerencias que caben y se ven (en orden)."""
        return [c.suggestion() for c in self._chips[: self._count] if not c.isHidden()]

    def _lead_width(self) -> int:
        if not self._lead:
            return 0
        return QFontMetrics(self._lead_font).horizontalAdvance(self._lead) + 12

    def _fit(self) -> None:
        x = self._inset + self._lead_width()
        fits = True
        for i, chip in enumerate(self._chips):
            fits = fits and i < self._count and x + chip.width() <= self.width()
            chip.setVisible(fits)
            if fits:
                chip.move(x, 0)
                x += chip.width() + self.GAP

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._fit()

    def paintEvent(self, _event) -> None:  # noqa: N802
        if not self._lead:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setFont(self._lead_font)
        painter.setPen(_alpha(theme.PAPEL, 0.66) if self._on_band else QColor(theme.HUMO))
        painter.drawText(
            QRect(self._inset, 0, self._lead_width(), self.height() - 1),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._lead,
        )


# ---------------------------------------------------------------------------
# Lista de pistas
# ---------------------------------------------------------------------------
class TrackTable(QTableView):
    """Tabla de pistas con columnas proporcionales: el título nunca desaparece
    aunque el panel de letras estreche la vista.

    Lleva la cuenta de la fila bajo el ratón (toda la fila se resalta, no solo
    una celda), deja reproducir con un clic sobre la carátula y mueve el
    ecualizador de la pista que suena.
    """

    thumb_clicked = Signal(int)
    DURATION_WIDTH = 76

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.hover_row = -1
        self.playing = False
        self.phase = 0.0
        self._press_row = -1
        self._clock = QElapsedTimer()
        self._clock.start()
        self._equalizer = QTimer(self)
        self._equalizer.setInterval(40)
        self._equalizer.timeout.connect(self._tick)
        self.setMouseTracking(True)
        # Parte visible de la columna del álbum (0 = oculta). Al estrechar la
        # ventana se recoge y su texto se funde; el título gana el sitio.
        self.album_share = 1.0
        self._album = Tween(self, 1.0, self._on_album, ms=REFLOW_MS, curve=EASE_IN_OUT, movement=True)
        # Aparición de una lista nueva: cada fila barre de izquierda a derecha
        # un poco después que la de encima.
        self._reveal_clock = QElapsedTimer()
        self._revealing = False
        self._reveal_timer = QTimer(self)
        self._reveal_timer.setInterval(16)
        self._reveal_timer.timeout.connect(self._reveal_tick)
        self._signature: tuple = ()

    def setModel(self, model) -> None:  # noqa: N802 - API de Qt
        super().setModel(model)
        model.modelReset.connect(self._on_reset)

    def _on_reset(self) -> None:
        model = self.model()
        rows = model.rowCount()
        signature = tuple(model.track_at(i).video_id for i in range(min(rows, 40)))
        # Solo lo que es nuevo: refrescar la misma lista (la cola al avanzar,
        # volver a la misma vista) no la hace aparecer otra vez.
        if signature and signature != self._signature:
            self._reveal_clock.start()
            self._revealing = True
            self._reveal_timer.start()
        self._signature = signature

    def _reveal_tick(self) -> None:
        self.viewport().update()
        if self._reveal_clock.elapsed() > ROW_STAGGER * STAGGER_CAP + REVEAL_MS:
            self._revealing = False
            self._reveal_timer.stop()
            self.viewport().update()

    def reveal_progress(self, row: int) -> float:
        if not self._revealing:
            return 1.0
        order = max(0, min(STAGGER_CAP, row - max(0, self.rowAt(0))))
        t = (self._reveal_clock.elapsed() - order * ROW_STAGGER) / REVEAL_MS
        if t >= 1.0:
            return 1.0
        return EASE_OUT.valueForProgress(max(0.0, t))

    # -- columnas -----------------------------------------------------------
    def resizeEvent(self, event) -> None:  # noqa: N802 - API de Qt
        super().resizeEvent(event)
        self.fit_columns()

    def _on_album(self, value) -> None:
        self.album_share = float(value)
        self._apply_columns()

    def fit_columns(self) -> None:
        available = max(120, self.viewport().width() - self.DURATION_WIDTH)
        share = 1.0 if available > 460 else 0.0
        if window_settling(self):
            self._album.set(share)          # sin animación mientras la ventana se reacomoda
        else:
            self._album.to(share)
        self._apply_columns()

    def _apply_columns(self) -> None:
        available = max(120, self.viewport().width() - self.DURATION_WIDTH)
        album = int(available * 0.32 * self.album_share)
        self.setColumnHidden(1, album <= 0)
        self.setColumnWidth(0, available - album)
        self.setColumnWidth(1, album)
        self.setColumnWidth(2, self.DURATION_WIDTH)

    # -- ecualizador --------------------------------------------------------
    def set_playing(self, playing: bool) -> None:
        self.playing = playing
        if playing and not reduced_motion() and self.isVisible():
            self._equalizer.start()
        else:
            self._equalizer.stop()
        self.viewport().update()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self.playing and not reduced_motion():
            self._equalizer.start()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._equalizer.stop()
        super().hideEvent(event)

    def _tick(self) -> None:
        model = self.model()
        if model is None or not model.rowCount():
            return
        self.phase = self._clock.elapsed() / 1000.0
        first = max(0, self.rowAt(0))
        last = self.rowAt(self.viewport().height() - 1)
        last = model.rowCount() - 1 if last < 0 else last
        for row in range(first, last + 1):
            if model.is_current(row):
                self.viewport().update(self.thumb_rect(row))

    # -- ratón --------------------------------------------------------------
    def thumb_rect(self, row: int) -> QRect:
        top = self.rowViewportPosition(row)
        size = TrackDelegate.THUMB
        return QRect(TrackDelegate.PAD, top + (self.rowHeight(row) - size) // 2, size, size)

    def _row_rect(self, row: int) -> QRect:
        return QRect(0, self.rowViewportPosition(row), self.viewport().width(), self.rowHeight(row))

    def _set_hover(self, pos) -> None:
        row = self.indexAt(pos).row() if pos is not None else -1
        over_thumb = row >= 0 and self.thumb_rect(row).contains(pos)
        self.viewport().setCursor(
            Qt.CursorShape.PointingHandCursor if over_thumb else Qt.CursorShape.ArrowCursor
        )
        if row == self.hover_row:
            return
        previous, self.hover_row = self.hover_row, row
        for changed in (previous, row):
            if changed >= 0:
                self.viewport().update(self._row_rect(changed))

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        super().mouseMoveEvent(event)
        self._set_hover(event.position().toPoint())

    def leaveEvent(self, event) -> None:  # noqa: N802
        super().leaveEvent(event)
        self._set_hover(None)

    def scrollContentsBy(self, dx: int, dy: int) -> None:  # noqa: N802
        super().scrollContentsBy(dx, dy)
        # La rueda mueve las filas bajo un ratón quieto: el resalte las sigue.
        if self.viewport().underMouse():
            self._set_hover(self.viewport().mapFromGlobal(QCursor.pos()))

    def mousePressEvent(self, event) -> None:  # noqa: N802
        pos = event.position().toPoint()
        row = self.indexAt(pos).row()
        on_thumb = event.button() == Qt.MouseButton.LeftButton and row >= 0 and self.thumb_rect(row).contains(pos)
        self._press_row = row if on_thumb else -1
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        pos = event.position().toPoint()
        row = self.indexAt(pos).row()
        pressed, self._press_row = self._press_row, -1
        super().mouseReleaseEvent(event)
        if pressed >= 0 and row == pressed and self.thumb_rect(row).contains(pos):
            self.thumb_clicked.emit(row)


class TrackDelegate(QStyledItemDelegate):
    """Fila de pista: carátula pequeña, título y artista en la primera columna.

    El resalte de la fila no se anima: se recorre cientos de veces al día y
    tiene que responder en el acto.
    """

    THUMB = 40
    ROW_HEIGHT = 56
    PAD = 8

    def __init__(self, loader: ArtLoader, art_url: Callable[[object], str], view: TrackTable):
        super().__init__(view)
        self._loader = loader
        self._art_url = art_url
        self._view = view
        self.ink = QColor(theme.CREMA)
        self._papel = QColor(theme.PAPEL)
        self._humo = QColor(theme.HUMO)
        self._dim = QColor(theme.TENUE)
        # Fuentes y métricas se crean una vez: el pintado de cada fila no
        # reserva objetos nuevos (al desplazar deprisa son cientos por segundo).
        self._title_font = theme.ui_font(14)
        self._title_font_current = theme.ui_font(14, QFont.Weight.DemiBold)
        self._artist_font = theme.ui_font(12)
        self._cell_font = theme.ui_font(13)
        self._time_font = theme.ui_font(13, tabular=True)
        self._metrics = {id(f): QFontMetrics(f) for f in (
            self._title_font, self._title_font_current, self._artist_font, self._cell_font, self._time_font)}
        self._on_veil = QColor(theme.SOBRE_SOLIDO)
        self._play_pen = QPen(self._on_veil, 1.4)
        self._play_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)

    def _elide(self, font: QFont, text: str, width: int) -> str:
        return self._metrics[id(font)].elidedText(text, Qt.TextElideMode.ElideRight, width)

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # noqa: N802
        return QSize(option.rect.width(), self.ROW_HEIGHT)

    def _row_background(self, painter: QPainter, rect: QRect, current: bool, selected: bool, hover: bool) -> None:
        if current:
            color = _alpha(self.ink, 0.17 if selected else 0.11)
        elif selected:
            color = QColor(theme.RELIEVE_2)
        elif hover:
            color = QColor(theme.RELIEVE)
        else:
            return
        # Un solo rectángulo redondeado para toda la fila, recortado a cada celda.
        full = QRectF(0, rect.top() + 1, self._view.viewport().width(), rect.height() - 2)
        path = QPainterPath()
        path.addRoundedRect(full, theme.R_CONTROL, theme.R_CONTROL)
        painter.save()
        painter.setClipRect(rect)
        painter.fillPath(path, color)
        painter.restore()

    def _equalizer(self, painter: QPainter, thumb: QRectF) -> None:
        speeds, offsets, still = (7.3, 9.1, 6.2), (0.0, 1.7, 3.1), (0.45, 0.85, 0.6)
        moving = self._view.playing and not reduced_motion()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.SOBRE_SOLIDO))     # va sobre el velo de la carátula
        base = thumb.center().y() + 8
        x = thumb.center().x() - 7
        for i in range(3):
            level = (0.5 + 0.5 * math.sin(self._view.phase * speeds[i] + offsets[i])) if moving else still[i]
            height = 4 + 12 * level
            painter.drawRoundedRect(QRectF(x + i * 5.5, base - height, 3, height), 1.5, 1.5)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        model = index.model()
        row = index.row()
        track = model.track_at(row)
        current = model.is_current(row)
        unavailable = track.unavailable_since is not None
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = row == self._view.hover_row
        rect = option.rect
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self._row_background(painter, rect, current, selected, hover)

        if index.column() == 0:
            dpr = painter.device().devicePixelRatioF()
            x = rect.left() + self.PAD
            y = rect.top() + (rect.height() - self.THUMB) // 2
            pixmap = self._loader.pixmap(self._art_url(track), self.THUMB, theme.R_THUMB, dpr, lazy=True)
            if pixmap is None:
                pixmap = placeholder_pixmap(self.THUMB, theme.R_THUMB, dpr)
            if unavailable:
                painter.setOpacity(0.35)
            painter.drawPixmap(x, y, pixmap)
            painter.setOpacity(1.0)
            if current or hover:
                thumb = QRectF(x, y, self.THUMB, self.THUMB)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(theme.VELO)
                painter.drawRoundedRect(thumb, theme.R_THUMB, theme.R_THUMB)
                if current:
                    self._equalizer(painter, thumb)
                else:
                    cx, cy = thumb.center().x(), thumb.center().y()
                    path = QPainterPath()
                    path.moveTo(cx - 3.5, cy - 6)
                    path.lineTo(cx + 6.5, cy)
                    path.lineTo(cx - 3.5, cy + 6)
                    path.closeSubpath()
                    painter.setPen(self._play_pen)
                    painter.setBrush(self._on_veil)
                    painter.drawPath(path)

            text_left = x + self.THUMB + 14
            text_width = max(10, rect.right() - text_left - 10)
            title_font = self._title_font_current if current else self._title_font
            painter.setFont(title_font)
            painter.setPen(self._dim if unavailable else (self.ink if current else self._papel))
            painter.drawText(
                QRect(text_left, rect.top() + 9, text_width, 20),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                self._elide(title_font, track.title, text_width),
            )
            painter.setFont(self._artist_font)
            painter.setPen(self._dim if unavailable else self._humo)
            painter.drawText(
                QRect(text_left, rect.top() + 29, text_width, 18),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                self._elide(self._artist_font, track.artists, text_width),
            )
        else:
            # Cifras tabulares: las duraciones se alinean sin recurrir a una monoespaciada.
            font = self._time_font if index.column() == 2 else self._cell_font
            if index.column() == 1:
                painter.setOpacity(self._view.album_share)
            painter.setFont(font)
            painter.setPen(self._dim if unavailable else self._humo)
            text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
            align = Qt.AlignmentFlag.AlignRight if index.column() == 2 else Qt.AlignmentFlag.AlignLeft
            inner = rect.adjusted(10, 0, -14, 0)
            painter.drawText(
                inner, align | Qt.AlignmentFlag.AlignVCenter,
                self._elide(font, text, inner.width()),
            )
        progress = self._view.reveal_progress(row)
        if progress < 1.0:
            # El barrido abarca la fila entera, no cada celda por separado.
            paint_reveal(painter, QRectF(rect), progress, 0, self._view.viewport().width())
        painter.restore()


# ---------------------------------------------------------------------------
# Estados vacíos y avisos fijos
# ---------------------------------------------------------------------------
class EmptyState(QWidget):
    """Lo que se ve cuando no hay pistas: un icono, qué pasa y qué hacer."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._glyph = "music"
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(6)
        lay.addStretch(2)
        lay.addSpacing(84)
        self._title = QLabel()
        self._title.setObjectName("emptyTitle")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint = QLabel()
        self._hint.setObjectName("emptyHint")
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint.setWordWrap(True)
        lay.addWidget(self._title)
        lay.addWidget(self._hint)
        lay.addStretch(3)

    def set_content(self, glyph: str, title: str, hint: str = "") -> None:
        self._glyph = glyph
        self._title.setText(title)
        self._hint.setText(hint)
        self._hint.setVisible(bool(hint))
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = QPointF(self.width() / 2, self._title.y() - 16 - 32)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.RELIEVE))
        painter.drawEllipse(center, 32, 32)
        _draw_glyph(painter, QRectF(center.x() - 32, center.y() - 32, 64, 64), self._glyph, 24,
                    QColor(theme.HUMO))


class Banner(QFrame):
    """Aviso fijo sobre la lista (faltan binarios, sesión caducada) con su acción."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("banner")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 10, 10, 10)
        lay.setSpacing(16)
        self.label = QLabel()
        self.label.setObjectName("bannerText")
        self.label.setWordWrap(True)
        self.button = PillButton("", "primary")
        lay.addWidget(self.label, 1)
        lay.addWidget(self.button, 0, Qt.AlignmentFlag.AlignVCenter)


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
        self.menu_btn = IconButton("more", "Menú de la aplicación", size=36, glyph_px=16, quiet=True)
        lay.addWidget(self.status, 1)
        lay.addWidget(self.menu_btn)
        self._detail = ""

    def set_status(self, text: str, alert: bool = False, detail: str = "") -> None:
        self.status.setText(text)
        self.status.set_color(theme.AVISO if alert else theme.HUMO)
        self._detail = detail
        self.setToolTip(detail or text)

    def set_collapsed(self, collapsed: bool) -> None:
        """Barra lateral contraída: solo cabe el botón del menú, centrado. El
        estado sigue disponible al pasar el ratón por encima."""
        self.status.setVisible(not collapsed)
        self.layout().setContentsMargins(*((14, 6, 14, 6) if collapsed else (16, 6, 6, 6)))


_launch: Optional[tuple] = None


def note_launch(widget: QWidget, rect: QRectF, image: Optional[QImage], radius: float) -> None:
    """Apunta de dónde sale la carátula de lo que se va a abrir (una tarjeta,
    una fila de la barra lateral). La ventana la recoge al enseñar la vista
    y la hace volar hasta la cabecera."""
    global _launch
    if image is None or image.isNull() or focus.keyboard():
        _launch = None
        return
    top_left = widget.mapToGlobal(rect.topLeft().toPoint())
    _launch = (QRectF(QPointF(top_left), rect.size()), image, radius, time.monotonic())


def take_launch(max_age: float = 0.6) -> Optional[tuple]:
    """El origen apuntado, si es reciente (y lo olvida)."""
    global _launch
    launch, _launch = _launch, None
    if launch is None or time.monotonic() - launch[3] > max_age:
        return None
    return launch


def fit_aspect(rect: QRectF, aspect: float) -> QRectF:
    """El rectángulo más grande de proporción ``aspect`` (ancho / alto) que
    cabe en ``rect``, centrado en él."""
    if rect.isEmpty() or aspect <= 0:
        return QRectF(rect)
    width, height = rect.width(), rect.height()
    if width / height > aspect:
        width = height * aspect
    else:
        height = width / aspect
    out = QRectF(0, 0, width, height)
    out.moveCenter(rect.center())
    return out


class CoverFlight(QWidget):
    """La carátula viajando del reproductor al panel «Ahora suena» (o de vuelta).

    Es una capa transparente sobre la ventana que pinta solo la imagen en
    camino. Origen y destino pueden ser rectángulos fijos (el vuelo es una
    recta, siempre igual) o funciones que se vuelven a medir en cada
    fotograma (cuando el destino aún se está colocando).

    Con ``aspect`` la imagen conserva su proporción durante todo el vuelo (una
    miniatura de vídeo apaisada no se estira ni se recorta a medias): el marco
    de los extremos se ajusta a esa proporción. El dibujo no sale del pasillo
    que une origen y destino, así que nunca asoma sobre la cabecera.
    """

    def __init__(self, host: QWidget, image: QImage, start, end, radii: tuple[float, float], ms: int,
                 on_done: Callable[[], None], aspect: Optional[float] = None):
        super().__init__(host)
        self._image = image
        self._aspect = aspect
        self._start = self._fixed(start)
        self._end = self._fixed(end)
        self._radii = radii
        self._on_done = on_done
        self._done = False
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setGeometry(host.rect())
        self._t = Tween(self, 0.0, self._step, ms=ms, curve=EASE_IN_OUT, movement=True)
        self.show()
        self.raise_()

    def _fixed(self, where):
        """Un extremo fijo se ajusta una vez a la proporción; uno medido en
        vivo, en cada fotograma."""
        if callable(where):
            if self._aspect is None:
                return where
            return lambda: fit_aspect(where(), self._aspect)
        rect = QRectF(where) if self._aspect is None else fit_aspect(QRectF(where), self._aspect)
        return lambda: rect

    def start_rect(self) -> QRectF:
        return self._start()

    def end_rect(self) -> QRectF:
        return self._end()

    def fly(self) -> None:
        self._t.to(1.0)

    def land(self) -> None:
        """Termina ya (otra acción la interrumpe)."""
        self._t.set(1.0)

    def _step(self, value) -> None:
        if float(value) >= 1.0 and not self._done:
            self._done = True
            # Primero aparece la carátula de destino y después se va la copia:
            # en ningún fotograma falta la imagen.
            self._on_done()
            self.hide()
            self.deleteLater()
            return
        self.update()

    def rect_at(self, t: float) -> QRectF:
        a, b = self._start(), self._end()
        return QRectF(
            a.x() + (b.x() - a.x()) * t, a.y() + (b.y() - a.y()) * t,
            a.width() + (b.width() - a.width()) * t, a.height() + (b.height() - a.height()) * t,
        )

    def paintEvent(self, _event) -> None:  # noqa: N802
        t = float(self._t.value)
        rect = self.rect_at(t)
        if rect.width() < 2 or rect.height() < 2:
            return
        radius = self._radii[0] + (self._radii[1] - self._radii[0]) * t
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        # El pasillo entre origen y destino: fuera de él no se pinta nada.
        painter.setClipRect(self._start().united(self._end()).adjusted(-1, -1, 1, 1))
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        painter.setClipPath(path, Qt.ClipOperation.IntersectClip)
        # Rellena el marco recortando lo que sobra, como las carátulas.
        iw, ih = self._image.width(), self._image.height()
        scale = max(rect.width() / iw, rect.height() / ih)
        sw, sh = rect.width() / scale, rect.height() / scale
        painter.drawImage(rect, self._image, QRectF((iw - sw) / 2, (ih - sh) / 2, sw, sh))


class ClickableCover(CoverLabel):
    """Carátula del reproductor: un clic abre el panel de la canción.

    Al acercar el ratón se oscurece y enseña el icono de ampliar, para que se
    vea que responde antes de pulsarla.
    """

    clicked = Signal()

    def __init__(self, loader: ArtLoader, size: int, radius: float = theme.R_COVER,
                 parent: Optional[QWidget] = None):
        super().__init__(loader, size, radius=radius, parent=parent)
        self._veil = Tween(self, 0.0, lambda _v: self.update(), ms=HOVER_MS, curve=EASE)
        self._pressed = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAccessibleName("Ver la canción que suena")

    def enterEvent(self, event) -> None:  # noqa: N802
        self._veil.to(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._veil.to(0.0)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self._pressed = event.button() == Qt.MouseButton.LeftButton

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        pressed, self._pressed = self._pressed, False
        if pressed and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        veil = float(self._veil.value)
        ring = self.hasFocus() and focus.keyboard()
        if veil <= 0 and not ring:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0, 0, self._size, self._size)
        if veil > 0:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_alpha(theme.VELO, theme.VELO.alphaF() * veil))
            painter.drawRoundedRect(rect, self._radius, self._radius)
            painter.setOpacity(veil)
            _draw_glyph(painter, rect, "expand", 16, QColor(theme.SOBRE_SOLIDO))
            painter.setOpacity(1.0)
        if ring:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(theme.PAPEL), 2))
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), self._radius, self._radius)


# ---------------------------------------------------------------------------
# Letra
# ---------------------------------------------------------------------------
class _LyricsDelegate(QStyledItemDelegate):
    """Pinta cada línea de la letra con su ajuste de línea exacto.

    El color de la línea actual y de la anterior sale de aquí (y no de los
    datos de cada elemento) para que el fundido solo repinte dos filas, sin
    recalcular la lista entera en cada fotograma.
    """

    PAD_X = 10
    PAD_Y = 7

    def __init__(self, view: QListWidget):
        super().__init__(view)
        self._view = view
        self._font = theme.ui_font(16, QFont.Weight.DemiBold)
        self.ink = QColor(theme.CREMA)
        self.rest = QColor(theme.TENUE)
        self.current = -1
        self.previous = -1
        self.progress = 1.0

    def _font_for(self, index: QModelIndex) -> QFont:
        font = index.data(Qt.ItemDataRole.FontRole)
        return font if isinstance(font, QFont) else self._font

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # noqa: N802
        full = self._view.viewport().width()
        width = max(60, full - 2 * self.PAD_X)
        box = QFontMetrics(self._font_for(index)).boundingRect(
            QRect(0, 0, width, 5000), int(Qt.TextFlag.TextWordWrap), str(index.data() or "")
        )
        return QSize(full, box.height() + 2 * self.PAD_Y)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        own = index.data(Qt.ItemDataRole.ForegroundRole)
        if isinstance(own, QBrush):
            color = own.color()
        elif index.row() == self.current:
            color = mix(self.rest, self.ink, self.progress)
        elif index.row() == self.previous:
            color = mix(self.ink, self.rest, self.progress)
        else:
            color = self.rest
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setFont(self._font_for(index))
        painter.setPen(color)
        painter.drawText(
            option.rect.adjusted(self.PAD_X, self.PAD_Y, -self.PAD_X, -self.PAD_Y),
            int(Qt.TextFlag.TextWordWrap) | int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop),
            str(index.data() or ""),
        )
        painter.restore()


class LyricsPanel(QFrame):
    """Panel de la letra. La línea que suena toma la tinta de la carátula y la
    lista se desliza para dejarla centrada; si el usuario mueve la rueda, el
    desplazamiento automático se aparta unos segundos."""

    line_clicked = Signal(int)
    close_requested = Signal()
    HOLD_S = 4.0

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("panel")
        self._index = -1
        self._lines = 0
        self._hold_until = 0.0

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(18, 10, 8, 4)
        head.setSpacing(8)
        title = QLabel("Letra")
        title.setObjectName("panelTitle")
        self.source = ElidedLabel(theme.ui_font(12), theme.TENUE, align=Qt.AlignmentFlag.AlignRight)
        close = IconButton("close", "Ocultar la letra (Ctrl+L)", size=32, glyph_px=11, quiet=True)
        close.clicked.connect(self.close_requested)
        head.addWidget(title)
        head.addWidget(self.source, 1)
        head.addWidget(close)
        lay.addLayout(head)

        self.list = QListWidget()
        self.list.setObjectName("lyricsList")
        self.list.setWordWrap(True)
        self.list.setResizeMode(QListView.ResizeMode.Adjust)
        self._delegate = _LyricsDelegate(self.list)
        self.list.setItemDelegate(self._delegate)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list.itemClicked.connect(self._on_clicked)
        self.list.viewport().installEventFilter(self)
        lay.addWidget(self.list, 1)

        self._glow = Tween(self, 1.0, self._apply_glow, ms=FADE_MS + 40, curve=EASE)
        self._scroll = Tween(self, 0.0, self._apply_scroll, ms=SCROLL_MS, curve=EASE_IN_OUT, movement=True)

    # -- contenido ----------------------------------------------------------
    def count(self) -> int:
        return self._lines

    def show_message(self, text: str) -> None:
        self._reset()
        self.source.setText("")
        item = QListWidgetItem(text)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        item.setForeground(QColor(theme.HUMO))
        item.setFont(theme.ui_font(14))
        self.list.addItem(item)
        self.list.viewport().setCursor(Qt.CursorShape.ArrowCursor)

    def show_lines(self, lines: list[str], synced: bool, source: str) -> None:
        self._reset()
        self._lines = len(lines)
        for text in lines:
            item = QListWidgetItem(text)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            if not synced:
                # Sin tiempos no hay línea actual: toda la letra se lee igual de clara.
                item.setForeground(QColor(theme.PAPEL_SUAVE))
            self.list.addItem(item)
        self.source.setText(source)
        # Solo la letra sincronizada responde al clic (salta a esa línea).
        self.list.viewport().setCursor(
            Qt.CursorShape.PointingHandCursor if synced else Qt.CursorShape.ArrowCursor
        )
        self.list.scrollToTop()

    def _reset(self) -> None:
        self._glow.set(1.0)
        self._scroll.set(0.0)
        self._index = -1
        self._delegate.current = self._delegate.previous = -1
        self._lines = 0
        self._hold_until = 0.0
        self.list.clear()

    def set_ink(self, color: QColor) -> None:
        self._delegate.ink = QColor(color)
        self.list.viewport().update()

    # -- línea actual -------------------------------------------------------
    def highlight(self, index: int) -> None:
        if index == self._index:
            return
        stale = self._delegate.previous
        self._delegate.previous, self._delegate.current = self._index, index
        self._index = index
        self._repaint_row(stale)        # la que se estaba apagando queda en reposo
        self._glow.set(0.0)
        self._glow.to(1.0)
        if index >= 0 and time.monotonic() >= self._hold_until:
            self._scroll_to(index)

    def _repaint_row(self, row: int) -> None:
        item = self.list.item(row) if 0 <= row < self._lines else None
        if item is not None:
            self.list.viewport().update(self.list.visualItemRect(item))

    def _apply_glow(self, progress) -> None:
        self._delegate.progress = float(progress)
        self._repaint_row(self._delegate.current)
        self._repaint_row(self._delegate.previous)

    def _scroll_to(self, index: int) -> None:
        item = self.list.item(index)
        if item is None:
            return
        bar = self.list.verticalScrollBar()
        rect = self.list.visualItemRect(item)
        target = bar.value() + rect.center().y() - self.list.viewport().height() / 2
        target = float(max(bar.minimum(), min(bar.maximum(), round(target))))
        self._scroll.set(float(bar.value()))
        self._scroll.to(target)

    def _apply_scroll(self, value) -> None:
        self.list.verticalScrollBar().setValue(round(float(value)))

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Wheel:
            # El usuario está leyendo otra parte: no se le arrebata la lista.
            self._hold_until = time.monotonic() + self.HOLD_S
            self._scroll.set(float(self.list.verticalScrollBar().value()))
        return super().eventFilter(obj, event)

    def _on_clicked(self, item: QListWidgetItem) -> None:
        row = self.list.row(item)
        if 0 <= row < self._lines:
            self._hold_until = 0.0
            self.line_clicked.emit(row)


# ---------------------------------------------------------------------------
# Avisos flotantes
# ---------------------------------------------------------------------------
class Toast(QWidget):
    MAX_TEXT_WIDTH = 340

    def __init__(self, host: "ToastHost", surface: QWidget, text: str, kind: str):
        super().__init__(surface)
        self._host = host
        self.text = text
        self.kind = kind
        self.leaving = False
        self._font = theme.ui_font(13)
        left = 30 if kind == "error" else 14
        box = QFontMetrics(self._font).boundingRect(
            QRect(0, 0, self.MAX_TEXT_WIDTH, 2000), int(Qt.TextFlag.TextWordWrap), text
        )
        self._text_rect = QRect(left, 11, box.width() + 4, box.height())
        self.setFixedSize(left + box.width() + 4 + 14, box.height() + 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(text)

        self._x = 0
        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(0.0)
        self.setGraphicsEffect(self._effect)
        self.opacity = Tween(self, 0.0, self._effect.setOpacity, ms=FADE_MS, curve=EASE)
        self.y_pos = Tween(self, 0.0, self._place, ms=ENTER_MS, curve=EASE_OUT, movement=True)

        self._remaining = max(4000, min(10000, 3200 + 45 * len(text))) + (2000 if kind == "error" else 0)
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(lambda: self._host.dismiss(self))

    def set_x(self, x: int) -> None:
        self._x = x
        self.move(x, round(float(self.y_pos.value)))

    def _place(self, y) -> None:
        self.move(self._x, round(float(y)))

    def start(self) -> None:
        self._clock.start()
        self._timer.start(self._remaining)

    def restart(self) -> None:
        self._remaining = max(self._remaining, 4000)
        self.start()

    def pause(self) -> None:
        if self._timer.isActive():
            self._timer.stop()
            self._remaining = max(600, self._remaining - self._clock.elapsed())

    def resume(self) -> None:
        if not self.leaving and not self._timer.isActive():
            self.start()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._host.set_hovered(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._host.set_hovered(False)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self._host.dismiss(self)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(QColor(theme.LINEA_CLARA), 1))
        painter.setBrush(QColor(theme.RELIEVE_2))
        painter.drawRoundedRect(rect, 10, 10)
        if self.kind == "error":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(theme.AVISO))
            line = QFontMetrics(self._font).height()
            painter.drawEllipse(QPointF(17, self._text_rect.top() + line / 2 + 0.5), 3.5, 3.5)
        painter.setFont(self._font)
        painter.setPen(QColor(theme.PAPEL))
        painter.drawText(self._text_rect, int(Qt.TextFlag.TextWordWrap), self.text)


class ToastHost(QObject):
    """Avisos apilados en la esquina inferior derecha, sobre el reproductor.

    Entran desde abajo, que es también por donde salen. Los que ya estaban se
    apartan con una transición que se puede interrumpir, así varios avisos
    seguidos no dan tirones. El reloj de cada aviso se detiene mientras el
    ratón está encima o la ventana no está activa: nadie pierde un mensaje por
    estar mirando otra cosa.
    """

    MAX_VISIBLE = 3
    MARGIN = 16
    GAP = 8

    def __init__(self, surface: QWidget, bottom: Callable[[], int]):
        super().__init__(surface)
        self._surface = surface
        self._bottom = bottom
        self._toasts: list[Toast] = []
        self._hovered = False
        self._inactive = False
        surface.installEventFilter(self)

    def show(self, text: str, kind: str = "info") -> None:
        text = (text or "").strip()
        if not text:
            return
        for toast in self._toasts:
            if toast.text == text and not toast.leaving:
                toast.restart()      # el mismo aviso otra vez: se renueva, no se duplica
                return
        toast = Toast(self, self._surface, text, kind)
        self._toasts.append(toast)
        active = [t for t in self._toasts if not t.leaving]
        for old in active[:-self.MAX_VISIBLE]:
            self.dismiss(old)
        toast.show()
        toast.raise_()
        self._layout(entering=toast)
        if not self._paused():
            toast.start()

    def dismiss(self, toast: Toast) -> None:
        if toast.leaving or toast not in self._toasts:
            return
        toast.leaving = True
        toast.pause()
        # Sale más rápido de lo que entró.
        toast.opacity.to(0.0, EXIT_MS)
        toast.y_pos.to(float(toast.y_pos.value) + 8, EXIT_MS)
        QTimer.singleShot(EXIT_MS + 40, lambda: self._remove(toast))
        self._layout()

    def _remove(self, toast: Toast) -> None:
        if toast in self._toasts:
            self._toasts.remove(toast)
        if toast.underMouse():
            self.set_hovered(False)     # al ocultarse ya no llegará su leaveEvent
        toast.hide()
        toast.deleteLater()

    def _layout(self, entering: Optional[Toast] = None, animate: bool = True) -> None:
        y = float(self._bottom() - 12)
        right = self._surface.width() - self.MARGIN
        for toast in reversed([t for t in self._toasts if not t.leaving]):
            y -= toast.height()
            toast.set_x(right - toast.width())
            if toast is entering:
                toast.y_pos.set(y + 14)
                toast.y_pos.to(y, ENTER_MS, EASE_OUT)
                toast.opacity.to(1.0)
            elif animate:
                toast.y_pos.to(y, 240, EASE)
            else:
                toast.y_pos.set(y)
            y -= self.GAP

    def _paused(self) -> bool:
        return self._hovered or self._inactive

    def _sync_timers(self) -> None:
        for toast in self._toasts:
            if self._paused():
                toast.pause()
            else:
                toast.resume()

    def set_hovered(self, hovered: bool) -> None:
        self._hovered = hovered
        self._sync_timers()

    def set_inactive(self, inactive: bool) -> None:
        self._inactive = inactive
        self._sync_timers()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self._surface and event.type() == QEvent.Type.Resize:
            self._layout(animate=False)
        return super().eventFilter(obj, event)
