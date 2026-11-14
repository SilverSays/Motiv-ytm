"""Pantalla de inicio: tus listas como fundas y, debajo, listas de la comunidad.

Sigue las reglas de "Funda y notas": el color no es de la app sino de la
música. Una tarjeta en reposo es solo su carátula y dos líneas de texto; al
acercar el ratón aparece debajo un papel del tono de esa carátula, con la
sombra dura de la funda, y el disco para reproducirla sin abrirla.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ytm_native.services.discover import PlaylistCard, Shelf
from . import theme
from .art import ArtLoader, neutral_tint, placeholder_pixmap, sized_url, square_pixmap
from .components import PillButton, Reveal, _alpha, _Pressable, note_launch
from .motion import CARD_STAGGER, EASE, FADE_MS, STAGGER_CAP, Tween, mix

CardSpec = tuple[str, str, str, object]   # título, nota, URL de la carátula, lo que representa


class SleeveCard(_Pressable):
    """Tarjeta de una lista: carátula, título y una nota.

    Un clic la abre; el disco que aparece sobre la carátula la reproduce. Con
    el teclado, Intro o Espacio la abren y las flechas pasan a la de al lado.
    ``round``: retrato redondo de un artista; no lleva disco (un artista se
    abre, no se reproduce).
    """

    opened = Signal(object)
    play_requested = Signal(object)

    PAD = 10
    TEXT_GAP = 10
    TITLE_H = 20
    NOTE_H = 18
    DISC = 38
    SHADOW = 5

    def __init__(self, loader: ArtLoader, parent: Optional[QWidget] = None, round_art: bool = False):
        super().__init__(parent)
        self._loader = loader
        self._round = round_art
        self._title = ""
        self._note = ""
        self._url = ""
        self._payload: object = None
        self._pix: Optional[QPixmap] = None
        self._pix_key: tuple = ()
        self._over_disc = False
        self._press_on_disc = False
        self._title_font = theme.ui_font(14, QFont.Weight.Medium)
        self._note_font = theme.ui_font(12)
        # La carátula que llega tarde se funde sobre el disco de relleno.
        self._art = Tween(self, 1.0, self._repaint, ms=FADE_MS, curve=EASE)
        self.reveal = Reveal(self)
        self.setMouseTracking(True)
        self.clicked.connect(self._activate)
        loader.loaded.connect(self._on_loaded)

    @classmethod
    def height_for(cls, width: int) -> int:
        return cls.PAD + (width - 2 * cls.PAD) + cls.TEXT_GAP + cls.TITLE_H + cls.NOTE_H + cls.PAD

    def set_card(self, title: str, note: str, url: str, payload: object) -> None:
        self._title, self._note, self._payload = title, note, payload
        if url != self._url:
            self._url = url
            self._pix = None
            self._art.set(1.0)
        self.setAccessibleName(f"{title}. {note}" if note else title)
        self.setToolTip(title)
        self.update()

    def payload(self) -> object:
        return self._payload

    # -- carátula -----------------------------------------------------------
    def _side(self) -> int:
        return max(1, self.width() - 2 * self.PAD)

    def _radius(self) -> float:
        return self._side() / 2 if self._round else theme.R_COVER

    def _cover_pixmap(self) -> Optional[QPixmap]:
        side, dpr = self._side(), self.devicePixelRatioF()
        key = (self._url, side, round(dpr, 2))
        if self._pix is None or self._pix_key != key:
            image = self._loader.image(self._url) if self._url else None
            if image is None:
                return None
            self._pix, self._pix_key = square_pixmap(image, side, self._radius(), dpr), key
        return self._pix

    def _on_loaded(self, url: str) -> None:
        if url and url == self._url:
            self._pix = None
            self._art.set(0.0)
            self._art.to(1.0)
            self.update()

    # -- ratón y teclado ----------------------------------------------------
    def _disc_center(self) -> QPointF:
        edge = self.PAD + self._side() - 8 - self.DISC / 2
        return QPointF(edge, edge)

    def _on_disc(self, pos: QPointF) -> bool:
        if self._round:
            return False
        center = self._disc_center()
        return (pos.x() - center.x()) ** 2 + (pos.y() - center.y()) ** 2 <= (self.DISC / 2 + 2) ** 2

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        super().mouseMoveEvent(event)
        over = self._on_disc(event.position())
        if over != self._over_disc:
            self._over_disc = over
            self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self._press_on_disc = (
            event.button() == Qt.MouseButton.LeftButton and self._on_disc(event.position())
        )
        super().mousePressEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._over_disc = False
        super().leaveEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        self._press_on_disc = False
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.click()
        elif key == Qt.Key.Key_Right:
            self.focusNextChild()
        elif key == Qt.Key.Key_Left:
            self.focusPreviousChild()
        else:
            super().keyPressEvent(event)

    def _activate(self) -> None:
        on_disc, self._press_on_disc = self._press_on_disc, False
        if not on_disc:
            # La carátula sale de aquí hacia la cabecera de lo que se abre.
            side = self._side()
            note_launch(self, QRectF(self.PAD, self.PAD, side, side),
                        self._loader.image(self._url) if self._url else None, self._radius())
        (self.play_requested if on_disc else self.opened).emit(self._payload)

    # -- pintura ------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802
        if self.reveal.value <= 0.001:
            return              # aún no le toca: ni se pinta ni se decodifica la carátula
        self._paint_card(event)
        if self.reveal.value < 1.0:
            painter = QPainter(self)
            self.reveal.cover(painter)
            painter.end()

    def _paint_card(self, _event) -> None:
        painter = QPainter(self)
        self._start(painter)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        hover = float(self._hover.value)
        side = self._side()
        cover = QRectF(self.PAD, self.PAD, side, side)
        pixmap = self._cover_pixmap()

        if hover > 0:
            tint = (self._loader.tint(self._url) if pixmap is not None else None) or neutral_tint()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_alpha(tint.band, hover))
            painter.drawRoundedRect(QRectF(self.rect()), theme.R_CARD, theme.R_CARD)
            painter.setBrush(_alpha(QColor(tint.band).darker(165), hover))
            painter.drawRoundedRect(cover.translated(self.SHADOW, self.SHADOW), self._radius(), self._radius())

        art = float(self._art.value) if pixmap is not None else 0.0
        if art < 1.0:
            if self._url and not self._loader.failed(self._url):
                # Carátula de camino: un hueco liso. Sobre el disco de relleno
                # el fundido enseñaría dos dibujos a la vez.
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(theme.RELIEVE))
                painter.drawRoundedRect(cover, self._radius(), self._radius())
            else:
                painter.drawPixmap(self.PAD, self.PAD,
                                   placeholder_pixmap(side, self._radius(), self.devicePixelRatioF()))
        if pixmap is not None:
            painter.setOpacity(art if self.isEnabled() else art * 0.38)
            painter.drawPixmap(self.PAD, self.PAD, pixmap)
            painter.setOpacity(1.0 if self.isEnabled() else 0.38)

        if hover > 0 and not self._round:
            # El disco nunca nace de la nada: aparece ya casi a su tamaño.
            center = self._disc_center()
            radius = self.DISC / 2 * (0.9 + 0.1 * hover)
            painter.setOpacity(hover)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(theme.SOLIDO_HOVER if self._over_disc else theme.SOLIDO))
            painter.drawEllipse(center, radius, radius)
            ink = QColor(theme.TINTA_OSCURA)
            pen = QPen(ink, 1.4)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(ink)
            path = QPainterPath()
            path.moveTo(center.x() - 3.5, center.y() - 6)
            path.lineTo(center.x() + 6.0, center.y())
            path.lineTo(center.x() - 3.5, center.y() + 6)
            path.closeSubpath()
            painter.drawPath(path)
            painter.setOpacity(1.0)

        # Bajo un retrato redondo el texto va centrado, como el retrato.
        flags = (Qt.AlignmentFlag.AlignHCenter if self._round else Qt.AlignmentFlag.AlignLeft)             | Qt.AlignmentFlag.AlignVCenter
        top = cover.bottom() + self.TEXT_GAP
        painter.setFont(self._title_font)
        painter.setPen(QColor(theme.PAPEL))
        painter.drawText(
            QRectF(self.PAD, top, side, self.TITLE_H), flags,
            QFontMetrics(self._title_font).elidedText(self._title, Qt.TextElideMode.ElideRight, side),
        )
        painter.setFont(self._note_font)
        painter.setPen(mix(QColor(theme.HUMO), QColor(theme.PAPEL_SUAVE), hover))
        painter.drawText(
            QRectF(self.PAD, top + self.TITLE_H, side, self.NOTE_H), flags,
            QFontMetrics(self._note_font).elidedText(self._note, Qt.TextElideMode.ElideRight, side),
        )

        if self._ring():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(theme.PAPEL), 2))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), theme.R_CARD, theme.R_CARD)


class CardGrid(QWidget):
    """Rejilla de tarjetas que reparte el ancho en columnas iguales.

    Enseña como mucho ``max_rows`` filas: lo que no cabe se queda fuera en vez
    de obligar a desplazarse en horizontal.
    """

    opened = Signal(object)
    play_requested = Signal(object)
    fitted = Signal(int, int)        # tarjetas a la vista, tarjetas en total

    MIN_CARD = 158
    MAX_CARD = 218
    GAP = 6

    def __init__(self, loader: ArtLoader, max_rows: int = 1, parent: Optional[QWidget] = None,
                 round_art: bool = False):
        super().__init__(parent)
        self._loader = loader
        self._max_rows = max_rows
        self._round = round_art
        self._cards: list[SleeveCard] = []
        self._count = 0
        self._shown = 0
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(0)

    def set_cards(self, specs: list[CardSpec], reveal_from: Optional[int] = None) -> None:
        """``reveal_from``: las tarjetas aparecen una tras otra, de izquierda a
        derecha, empezando pasados esos milisegundos."""
        while len(self._cards) < len(specs):
            card = SleeveCard(self._loader, self, round_art=self._round)
            card.opened.connect(self.opened)
            card.play_requested.connect(self.play_requested)
            card.hide()
            self._cards.append(card)
        self._count = len(specs)
        for card, (title, note, url, payload) in zip(self._cards, specs):
            card.set_card(title, note, url, payload)
        self._relayout()
        if reveal_from is not None:
            for i, card in enumerate(self._cards[: self._count]):
                card.reveal.start(reveal_from + min(i, STAGGER_CAP) * CARD_STAGGER)

    def shown(self) -> int:
        return self._shown

    def count(self) -> int:
        return self._count

    def set_max_rows(self, rows: int) -> None:
        if rows != self._max_rows:
            self._max_rows = rows
            self._relayout()

    def columns(self) -> int:
        return max(2, (self.width() + self.GAP) // (self.MIN_CARD + self.GAP))

    def _relayout(self) -> None:
        cols = self.columns()
        width = max(SleeveCard.PAD * 2 + 40,
                    min(self.MAX_CARD, (self.width() - self.GAP * (cols - 1)) // cols))
        height = SleeveCard.height_for(width)
        shown = min(self._count, cols * self._max_rows)
        for i, card in enumerate(self._cards):
            if i < shown:
                row, col = divmod(i, cols)
                card.setGeometry(col * (width + self.GAP), row * (height + self.GAP), width, height)
            card.setVisible(i < shown)
        rows = -(-shown // cols)
        self.setFixedHeight(rows * height + max(0, rows - 1) * self.GAP)
        self._shown = shown
        self.fitted.emit(shown, self._count)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width():
            self._relayout()


class ShelfSection(QWidget):
    """Una fila del inicio: título, el porqué en una nota y sus tarjetas."""

    def __init__(self, loader: ArtLoader, max_rows: int = 1, parent: Optional[QWidget] = None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        head = QVBoxLayout()
        # Las tarjetas llevan 10 px de aire alrededor de la carátula: el texto
        # se alinea con la carátula, no con el borde de la tarjeta.
        head.setContentsMargins(SleeveCard.PAD, 0, SleeveCard.PAD, 0)
        head.setSpacing(2)
        self.title = QLabel()
        self.title.setObjectName("shelfTitle")
        self.note = QLabel()
        self.note.setObjectName("shelfNote")
        for label in (self.title, self.note):
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            head.addWidget(label)
        lay.addLayout(head)
        lay.addSpacing(6)
        self.grid = CardGrid(loader, max_rows)
        lay.addWidget(self.grid)
        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setEnabled(False)
        self.setGraphicsEffect(self._effect)
        self._fade = Tween(self, 1.0, self._apply_fade, ms=FADE_MS + 40, curve=EASE)

    def set_head(self, title: str, note: str = "") -> None:
        self.title.setText(title)
        self.title.setToolTip(title)
        self.note.setText(note)
        self.note.setVisible(bool(note))

    def _apply_fade(self, value) -> None:
        opacity = float(value)
        self._effect.setOpacity(opacity)
        self._effect.setEnabled(opacity < 1.0)

    def appear(self) -> None:
        """Entrada de una fila que acaba de llegar de la red: solo se funde."""
        self._fade.set(0.0)
        self._fade.to(1.0)


class HomePage(QScrollArea):
    """Inicio: primero tus listas (las más reproducidas delante), luego lo
    escuchado hace poco y después las recomendaciones, que llegan de una en una."""

    play_recent = Signal(object)      # Track de «Escuchado hace poco»
    open_library = Signal(str)
    play_library = Signal(str)
    open_remote = Signal(object)      # PlaylistCard
    play_remote = Signal(object)
    refresh_requested = Signal()

    LIBRARY_ROWS = 2

    def __init__(self, loader: ArtLoader, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._loader = loader
        self._sections: list[ShelfSection] = []
        self._shelf_count = 0
        self.setObjectName("home")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.verticalScrollBar().setSingleStep(28)

        page = QWidget()
        page.setObjectName("homePage")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(22, 26, 16, 28)
        lay.setSpacing(0)

        head = QVBoxLayout()
        head.setContentsMargins(SleeveCard.PAD, 0, SleeveCard.PAD, 0)
        head.setSpacing(4)
        title = QLabel("Inicio")
        title.setObjectName("homeTitle")
        head.addWidget(title)
        self._facts_row = QHBoxLayout()
        self._facts_row.setSpacing(26)
        self._fact_labels: list[QLabel] = []
        self._facts_row.addStretch(1)
        head.addLayout(self._facts_row)
        lay.addLayout(head)
        lay.addSpacing(24)

        self.library = ShelfSection(loader, self.LIBRARY_ROWS)
        self.library.set_head("Tus listas")
        self.library.grid.opened.connect(lambda payload: self.open_library.emit(str(payload)))
        self.library.grid.play_requested.connect(lambda payload: self.play_library.emit(str(payload)))
        self.library.grid.fitted.connect(self._on_library_fitted)
        lay.addWidget(self.library)

        self.library_empty = QLabel()
        self.library_empty.setObjectName("homeStatus")
        self.library_empty.setWordWrap(True)
        self.library_empty.setMaximumWidth(560)
        self.library_empty.setContentsMargins(SleeveCard.PAD, 0, SleeveCard.PAD, 0)
        self.library_empty.hide()
        lay.addWidget(self.library_empty)

        # Lo último que sonó: un clic (o el disco) vuelve a ponerlo.
        self._recent_gap = QWidget()
        self._recent_gap.setFixedHeight(28)
        self._recent_gap.hide()
        lay.addWidget(self._recent_gap)
        self.recent = ShelfSection(loader)
        self.recent.set_head("Escuchado hace poco")
        self.recent.grid.opened.connect(self.play_recent)
        self.recent.grid.play_requested.connect(self.play_recent)
        self.recent.hide()
        lay.addWidget(self.recent)

        self._shelves = QVBoxLayout()
        self._shelves.setContentsMargins(0, 0, 0, 0)
        self._shelves.setSpacing(28)
        lay.addSpacing(28)
        lay.addLayout(self._shelves)

        status = QHBoxLayout()
        status.setContentsMargins(SleeveCard.PAD, 18, SleeveCard.PAD, 0)
        status.setSpacing(14)
        self.status = QLabel()
        self.status.setObjectName("homeStatus")
        self.action = PillButton("Otras recomendaciones", "quiet", glyph="sync")
        self.action.setToolTip("Busca otras listas de la comunidad a partir de tu música")
        self.action.clicked.connect(self.refresh_requested)
        status.addWidget(self.status)
        status.addWidget(self.action)
        status.addStretch(1)
        self.status.hide()
        self.action.hide()
        lay.addLayout(status)
        lay.addStretch(1)
        self.setWidget(page)

    # -- tus listas ---------------------------------------------------------
    def set_library(self, specs: list[CardSpec], facts: list[str], empty_text: str = "") -> None:
        while len(self._fact_labels) < len(facts):
            label = QLabel()
            label.setObjectName("homeFact")
            self._fact_labels.append(label)
            self._facts_row.insertWidget(len(self._fact_labels) - 1, label)
        for i, label in enumerate(self._fact_labels):
            label.setVisible(i < len(facts))
            if i < len(facts):
                label.setText(facts[i])
        self.library.setVisible(bool(specs))
        self.library.grid.set_cards(specs)
        self.library_empty.setText(empty_text)
        self.library_empty.setVisible(not specs and bool(empty_text))

    def _on_library_fitted(self, shown: int, total: int) -> None:
        # Si no caben todas, se dice dónde está el resto en vez de esconderlo.
        self.library.set_head(
            "Tus listas",
            "Las que más escuchas van primero. El resto sigue en la barra lateral."
            if shown < total else "",
        )

    # -- escuchado hace poco ------------------------------------------------
    def set_recent(self, specs: list[CardSpec]) -> None:
        """Tarjetas de canciones (su carga es la ``Track``); sin ninguna, la fila no existe."""
        self.recent.grid.set_cards(specs)
        self.recent.setVisible(bool(specs))
        self._recent_gap.setVisible(bool(specs))

    # -- recomendaciones ----------------------------------------------------
    @staticmethod
    def _specs(shelf: Shelf) -> list[CardSpec]:
        return [(c.title, c.note, sized_url(c.thumb_url, 400), c) for c in shelf.cards]

    def _section(self, index: int) -> ShelfSection:
        while len(self._sections) <= index:
            section = ShelfSection(self._loader)
            section.grid.opened.connect(self._emit_open)
            section.grid.play_requested.connect(self._emit_play)
            section.hide()
            self._sections.append(section)
            self._shelves.addWidget(section)
        return self._sections[index]

    def _emit_open(self, payload: object) -> None:
        if isinstance(payload, PlaylistCard):
            self.open_remote.emit(payload)

    def _emit_play(self, payload: object) -> None:
        if isinstance(payload, PlaylistCard):
            self.play_remote.emit(payload)

    def set_shelves(self, shelves: list[Shelf]) -> None:
        """Sustituye todas las filas de golpe (lo guardado de la última vez)."""
        self._shelf_count = 0
        for shelf in shelves:
            self._place(shelf)
        for section in self._sections[self._shelf_count:]:
            section.hide()

    def add_shelf(self, shelf: Shelf) -> None:
        """Añade una fila recién llegada: se funde al aparecer."""
        self._place(shelf).appear()

    def _place(self, shelf: Shelf) -> ShelfSection:
        section = self._section(self._shelf_count)
        self._shelf_count += 1
        section.set_head(shelf.title, shelf.note)
        section.grid.set_cards(self._specs(shelf))
        section.show()
        return section

    def shelf_count(self) -> int:
        return self._shelf_count

    def set_status(self, text: str = "", action: str = "") -> None:
        """``action``: "" (nada), "retry" o "refresh"."""
        self.status.setText(text)
        self.status.setVisible(bool(text))
        self.action.setVisible(bool(action))
        if action:
            self.action.setText("Reintentar" if action == "retry" else "Otras recomendaciones")
            self.action.updateGeometry()
