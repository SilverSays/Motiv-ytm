"""Botones y deslizadores pintados a mano.

Todo lo que responde al ratón se pinta a mano para poder darle lo que el QSS
no sabe hacer: hundirse al pulsar, fundir el hover y enseñar el anillo de foco
solo cuando se navega con el teclado.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QElapsedTimer, QEvent, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QAbstractButton, QSizePolicy, QSlider, QToolTip, QWidget

from . import focus, theme
from .motion import (
    EASE,
    EASE_IN_OUT,
    EASE_OUT,
    REFLOW_MS,
    FADE_MS,
    HOVER_MS,
    PRESS_MS,
    RELEASE_MS,
    Tween,
    mix,
)
from .primitives import _alpha, _draw_glyph
from ytm_native.core.i18n import tr


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

    def retheme(self, previous: str) -> None:
        self._ink = theme.translate(self._ink, previous)
        self.update()

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
        self.setToolTip(tr("Reproducir o pausar (Espacio)"))
        self.setAccessibleName(tr("Reproducir o pausar"))

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
