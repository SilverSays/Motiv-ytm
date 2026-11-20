"""La tabla de canciones: vista, pintado de filas, filtro y el aviso de vacía.
"""

from __future__ import annotations

import math
from typing import Callable, Optional

from PySide6.QtCore import (
    QElapsedTimer,
    QItemSelectionModel,
    QModelIndex,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QLabel,
    QLineEdit,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .art import ArtLoader, placeholder_pixmap
from .motion import (
    EASE_IN_OUT,
    EASE_OUT,
    REFLOW_MS,
    REVEAL_MS,
    ROW_STAGGER,
    STAGGER_CAP,
    Tween,
    reduced_motion,
)
from .primitives import _alpha, _draw_glyph, paint_reveal, window_settling
from ytm_native.core.i18n import tr


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
    row_moved = Signal(int, int)        # fila arrastrada, posición en la que queda
    delete_requested = Signal(list)     # Supr sobre estas filas (ordenadas)
    DURATION_WIDTH = 76

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.hover_row = -1
        self.playing = False
        self.phase = 0.0
        self._press_row = -1
        self._drag_row = -1                 # fila donde se pulsó: la que se arrastra
        # Filas enteras; varias con Mayús (seguidas) o Ctrl (sueltas).
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
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

    def retheme(self, previous: str) -> None:
        delegate = self.itemDelegate()
        if hasattr(delegate, "retheme"):
            delegate.retheme(previous)
        self.viewport().update()

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
        self._drag_row = row
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        pos = event.position().toPoint()
        row = self.indexAt(pos).row()
        pressed, self._press_row = self._press_row, -1
        super().mouseReleaseEvent(event)
        if pressed >= 0 and row == pressed and self.thumb_rect(row).contains(pos):
            self.thumb_clicked.emit(row)

    # -- selección y teclado -------------------------------------------------
    def selected_rows(self) -> list[int]:
        model = self.selectionModel()
        return sorted({index.row() for index in model.selectedIndexes()}) if model is not None else []

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Delete and event.modifiers() == Qt.KeyboardModifier.NoModifier:
            rows = self.selected_rows()
            if rows:
                # La tabla no sabe qué es quitar en cada vista: lo decide quien la llena.
                self.delete_requested.emit(rows)
                event.accept()
                return
        super().keyPressEvent(event)

    # -- reordenar arrastrando ----------------------------------------------
    def set_reorderable(self, enabled: bool) -> None:
        """Deja arrastrar una fila entre otras (la cola, una lista local). La
        tabla no mueve nada: emite ``row_moved`` y quien la llena decide."""
        model = self.model()
        if model is not None and hasattr(model, "set_reorderable"):
            model.set_reorderable(enabled)
        self.setDragEnabled(enabled)
        self.setAcceptDrops(enabled)
        self.setDropIndicatorShown(enabled)
        self.setDragDropMode(
            QAbstractItemView.DragDropMode.InternalMove if enabled else QAbstractItemView.DragDropMode.NoDragDrop
        )
        self.setDefaultDropAction(Qt.DropAction.MoveAction)

    def drop_target(self, from_row: int, point) -> int:
        """Posición final de ``from_row`` si se suelta en ``point`` (del
        viewport): delante de la fila bajo el puntero si se suelta en su
        mitad superior, detrás si en la inferior; bajo la última, al final."""
        rows = self.model().rowCount()
        index = self.indexAt(point)
        if not index.isValid():
            before = rows
        else:
            rect = self.visualRect(index)
            before = index.row() + (1 if point.y() >= rect.center().y() else 0)
        # Al quitar la fila de su sitio, las de detrás suben una.
        to = before - 1 if before > from_row else before
        return max(0, min(to, rows - 1))

    def startDrag(self, actions) -> None:  # noqa: N802 - API de Qt
        self._press_row = -1        # el clic en la carátula ya no cuenta: es un arrastre
        self.keep_only_grabbed_row()
        super().startDrag(actions)

    def keep_only_grabbed_row(self) -> None:
        """Se mueve una fila cada vez: la que se agarró, aunque hubiera más
        seleccionadas. La selección se reduce al empezar a arrastrar, así se
        ve qué se mueve antes de soltar."""
        model = self.model()
        if len(self.selected_rows()) > 1 and model is not None and 0 <= self._drag_row < model.rowCount():
            flags = QItemSelectionModel.SelectionFlag
            self.selectionModel().setCurrentIndex(model.index(self._drag_row, 0), flags.ClearAndSelect | flags.Rows)

    def dropEvent(self, event) -> None:  # noqa: N802
        model = self.model()
        rows = model.dragged_rows(event.mimeData()) if hasattr(model, "dragged_rows") else []
        own = event.source() is self and self.dragDropMode() == QAbstractItemView.DragDropMode.InternalMove
        target = self.drop_target(rows[0], event.position().toPoint()) if own and len(rows) == 1 else None
        # Qt recoge el arrastre (desplazamiento automático, marca de soltar);
        # el modelo no cambia nada por su cuenta.
        super().dropEvent(event)
        if target is None:
            return
        # Atendido, pero no como «mover»: con esa respuesta Qt intentaría
        # quitar las filas de origen del modelo al acabar el arrastre.
        event.setDropAction(Qt.DropAction.IgnoreAction)
        event.accept()
        if target != rows[0]:
            self.row_moved.emit(rows[0], target)


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

    def retheme(self, previous: str) -> None:
        self.ink = theme.translate(self.ink, previous)
        self._papel = QColor(theme.PAPEL)
        self._humo = QColor(theme.HUMO)
        self._dim = QColor(theme.TENUE)
        self._on_veil = QColor(theme.SOBRE_SOLIDO)
        self._play_pen.setColor(self._on_veil)

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
class FilterBox(QLineEdit):
    """Campo discreto para filtrar la lista a la vista: una lupa, el texto y
    el botón de limpiar. Esc lo vacía; flecha abajo pasa a las filas."""

    down_pressed = Signal()
    GLYPH_X = 9
    GLYPH = 14

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("listFilter")
        self.setPlaceholderText(tr("Filtrar en esta lista…"))
        self.setAccessibleName(tr("Filtrar en esta lista"))
        self.setClearButtonEnabled(True)
        self.setTextMargins(self.GLYPH + 8, 0, 0, 0)
        self.setMinimumWidth(170)
        self.setMaximumWidth(280)

    def paintEvent(self, event) -> None:  # noqa: N802 - API de Qt
        super().paintEvent(event)
        # El color se lee al pintar: así sigue al tema sin tener que avisarle.
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setFont(theme.icon_font(self.GLYPH - 3) or self.font())
        painter.setPen(QColor(theme.PAPEL if self.hasFocus() else theme.TENUE))
        painter.drawText(QRect(self.GLYPH_X, 0, self.GLYPH, self.height()), Qt.AlignmentFlag.AlignCenter,
                         theme.glyph("search"))
        painter.end()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape and self.text():
            self.clear()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Down:
            self.down_pressed.emit()
            event.accept()
            return
        super().keyPressEvent(event)


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
