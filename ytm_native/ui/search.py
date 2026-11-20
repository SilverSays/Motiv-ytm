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

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from ytm_native.services.discover import ArtistCard, PlaylistCard
from .art import ArtLoader, sized_url
# ``ArtistStrip`` vive en ``cards`` desde la Tarea 10.6; se sigue pudiendo
# importar de aquí.
from .cards import ArtistStrip, CardGrid  # noqa: F401 - ArtistStrip se reexporta
from .components import PillButton
from ytm_native.core.i18n import tr

__all__ = ["ARTISTS", "PLAYLISTS", "SONGS", "ArtistStrip", "CardsPage", "ScopeBar"]

SONGS, PLAYLISTS, ARTISTS = "canciones", "listas", "artistas"


class ScopeBar(QWidget):
    """Pestañas de una vista: una sola marcada. Por defecto, las de la búsqueda."""

    changed = Signal(str)

    LABELS = ((SONGS, tr("Canciones")), (PLAYLISTS, tr("Listas de la comunidad")), (ARTISTS, tr("Artistas")))

    def __init__(self, parent: Optional[QWidget] = None,
                 labels: Optional[tuple[tuple[str, str], ...]] = None):
        super().__init__(parent)
        if labels is not None:
            self.LABELS = labels
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
        self.buttons[self.LABELS[0][0]].setChecked(True)

    def set_scope(self, key: str) -> None:
        self.buttons[key].setChecked(True)


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
