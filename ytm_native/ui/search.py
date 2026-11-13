"""Lo que la búsqueda enseña además de las canciones.

* ``ScopeBar``: Canciones, Listas de la comunidad y Artistas. Cambiar de una a
  otra no anima nada (se hace muchas veces y con el teclado también).
* ``ArtistStrip``: si lo buscado es un artista, una fila con su retrato sobre
  las canciones; un clic abre su página.
* ``CardsPage``: listas de la comunidad (fundas) o artistas (retratos) en una
  rejilla que se desplaza.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from ytm_native.services.discover import ArtistCard, PlaylistCard
from . import theme
from .art import ArtLoader, placeholder_pixmap, sized_url
from .components import PillButton, _alpha, _draw_glyph, _Pressable
from .home import CardGrid
from .motion import mix

SONGS, PLAYLISTS, ARTISTS = "canciones", "listas", "artistas"


class ScopeBar(QWidget):
    changed = Signal(str)

    LABELS = ((SONGS, "Canciones"), (PLAYLISTS, "Listas de la comunidad"), (ARTISTS, "Artistas"))

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self.buttons: dict[str, PillButton] = {}
        for key, label in self.LABELS:
            button = PillButton(label, "quiet")
            button.setCheckable(True)
            button.setFixedHeight(32)
            button.clicked.connect(lambda _checked=False, k=key: self.changed.emit(k))
            self._group.addButton(button)
            self.buttons[key] = button
            lay.addWidget(button)
        lay.addStretch(1)
        self.buttons[SONGS].setChecked(True)

    def set_scope(self, key: str) -> None:
        self.buttons[key].setChecked(True)


class ArtistStrip(_Pressable):
    """El artista que coincide con lo buscado, sobre las canciones."""

    PRESS_SCALE = 0.985
    HEIGHT = 64
    AVATAR = 48

    opened = Signal(object)

    def __init__(self, loader: ArtLoader, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._loader = loader
        self.card: Optional[ArtistCard] = None
        self._url = ""
        self._name_font = theme.ui_font(15, QFont.Weight.DemiBold)
        self._note_font = theme.ui_font(12)
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.clicked.connect(lambda: self.card is not None and self.opened.emit(self.card))
        loader.loaded.connect(lambda url: url == self._url and self.update())

    def set_card(self, card: Optional[ArtistCard]) -> None:
        self.card = card
        self._url = sized_url(card.thumb_url, 120) if card else ""
        if card is not None:
            self.setAccessibleName(f"{card.name}. Abrir la página del artista")
            self.setToolTip("Abrir la página del artista")
        self.update()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.click()
            return
        super().keyPressEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        if self.card is None:
            return
        painter = QPainter(self)
        self._start(painter)
        hover = float(self._hover.value)
        rect = QRectF(self.rect()).adjusted(0, 2, 0, -2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(mix(QColor(theme.RELIEVE), QColor(theme.RELIEVE_2), hover))
        painter.drawRoundedRect(rect, theme.R_CONTROL, theme.R_CONTROL)
        if self._ring():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(theme.PAPEL), 2))
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), theme.R_CONTROL, theme.R_CONTROL)
        dpr = self.devicePixelRatioF()
        x, y = 10, (self.height() - self.AVATAR) // 2
        radius = self.AVATAR / 2
        pixmap = self._loader.pixmap(self._url, self.AVATAR, radius, dpr) if self._url else None
        painter.drawPixmap(x, y, pixmap or placeholder_pixmap(self.AVATAR, radius, dpr))
        left = x + self.AVATAR + 14
        right = 36
        width = max(10, self.width() - left - right)
        flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        painter.setFont(self._name_font)
        painter.setPen(QColor(theme.PAPEL))
        painter.drawText(QRectF(left, 12, width, 22), flags,
                         QFontMetrics(self._name_font).elidedText(self.card.name, Qt.TextElideMode.ElideRight, width))
        painter.setFont(self._note_font)
        painter.setPen(QColor(theme.HUMO))
        note = "Artista" if self.card.note in ("", "Artista") else f"Artista con {self.card.note}"
        painter.drawText(QRectF(left, 33, width, 18), flags, note)
        # Una flecha discreta dice que esto lleva a otra parte.
        arrow = _alpha(theme.PAPEL, 0.55 + 0.45 * hover)
        painter.save()
        painter.translate(self.width() - 22, self.height() / 2)
        painter.rotate(180)
        _draw_glyph(painter, QRectF(-7, -self.height() / 2, 14, self.height()), "back", 13, arrow)
        painter.restore()


class CardsPage(QScrollArea):
    """Rejilla de tarjetas a toda la vista: listas de la comunidad o artistas."""

    open_playlist = Signal(object)    # PlaylistCard
    play_playlist = Signal(object)
    open_artist = Signal(object)      # ArtistCard

    def __init__(self, loader: ArtLoader, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("home")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.verticalScrollBar().setSingleStep(28)
        page = QWidget()
        page.setObjectName("homePage")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 12)
        lay.setSpacing(0)
        self.playlists = CardGrid(loader, 99)
        self.artists = CardGrid(loader, 99, round_art=True)
        self.playlists.opened.connect(lambda p: isinstance(p, PlaylistCard) and self.open_playlist.emit(p))
        self.playlists.play_requested.connect(lambda p: isinstance(p, PlaylistCard) and self.play_playlist.emit(p))
        self.artists.opened.connect(lambda p: isinstance(p, ArtistCard) and self.open_artist.emit(p))
        lay.addWidget(self.playlists)
        lay.addWidget(self.artists)
        lay.addStretch(1)
        self.setWidget(page)

    def show_playlists(self, cards: list[PlaylistCard]) -> None:
        self.artists.hide()
        self.playlists.show()
        self.playlists.set_cards([(c.title, c.note, sized_url(c.thumb_url, 400), c) for c in cards], reveal_from=0)
        self.verticalScrollBar().setValue(0)

    def show_artists(self, cards: list[ArtistCard]) -> None:
        self.playlists.hide()
        self.artists.show()
        self.artists.set_cards([(c.name, c.note, sized_url(c.thumb_url, 400), c) for c in cards], reveal_from=0)
        self.verticalScrollBar().setValue(0)

    def first_card(self) -> Optional[QWidget]:
        grid = self.artists if self.artists.isVisibleTo(self) else self.playlists
        return grid._cards[0] if grid.shown() else None
