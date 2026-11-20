"""El panel de la letra, con la línea que suena resaltada.
"""

from __future__ import annotations

import time
from typing import Optional

from PySide6.QtCore import QEvent, QModelIndex, QRect, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .buttons import IconButton
from .motion import EASE, EASE_IN_OUT, FADE_MS, SCROLL_MS, Tween, mix
from .primitives import ElidedLabel
from ytm_native.core.i18n import tr


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
        title = QLabel(tr("Letra"))
        title.setObjectName("panelTitle")
        self.source = ElidedLabel(theme.ui_font(12), theme.TENUE, align=Qt.AlignmentFlag.AlignRight)
        close = IconButton("close", tr("Ocultar la letra (Ctrl+L)"), size=32, glyph_px=11, quiet=True)
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

    def retheme(self, previous: str) -> None:
        self._delegate.ink = theme.translate(self._delegate.ink, previous)
        self._delegate.rest = QColor(theme.TENUE)
        for row in range(self.list.count()):
            item = self.list.item(row)
            brush = item.data(Qt.ItemDataRole.ForegroundRole)
            if isinstance(brush, QBrush):
                item.setForeground(theme.translate(brush.color(), previous))
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
