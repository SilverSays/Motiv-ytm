"""Avisos: la franja fija sobre la lista y los avisos que aparecen y se van.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QElapsedTimer, QEvent, QObject, QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QWidget

from . import theme
from .buttons import PillButton
from .motion import EASE, EASE_OUT, ENTER_MS, EXIT_MS, FADE_MS, Tween


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
