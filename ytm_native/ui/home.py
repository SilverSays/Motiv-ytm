"""Pantalla de inicio: tus listas como fundas y, debajo, listas de la comunidad.

Sigue las reglas de "Funda y notas": el color no es de la app sino de la
música. Una tarjeta en reposo es solo su carátula y dos líneas de texto; al
acercar el ratón aparece debajo un papel del tono de esa carátula, con la
sombra dura de la funda, y el disco para reproducirla sin abrirla.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from ytm_native.services.discover import PlaylistCard, Shelf
from .art import ArtLoader, sized_url
from .band import ShelfSection
# Las tarjetas y su rejilla viven en ``cards`` desde la Tarea 10.6; se siguen
# pudiendo importar de aquí.
from .cards import CardGrid, CardSpec, SleeveCard  # noqa: F401 - CardGrid y SleeveCard se reexportan
from .components import PillButton
from ytm_native.core.i18n import tr

__all__ = ["CardGrid", "CardSpec", "HomePage", "ShelfSection", "SleeveCard"]




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
        title = QLabel(tr("Inicio"))
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
        self.library.set_head(tr("Tus listas"))
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
        self.recent.set_head(tr("Escuchado hace poco"))
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
        self.action = PillButton(tr("Otras recomendaciones"), "quiet", glyph="sync")
        self.action.setToolTip(tr("Busca otras listas de la comunidad a partir de tu música"))
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
            tr("Tus listas"),
            tr("Las que más escuchas van primero. El resto sigue en la barra lateral.")
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
            self.action.setText(tr("Reintentar") if action == "retry" else tr("Otras recomendaciones"))
            self.action.updateGeometry()
