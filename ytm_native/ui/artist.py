"""Página del artista: ocupa todo el panel de contenido, bajo la banda de cabecera.

La banda (la misma de las listas, con el retrato redondo y el color de la foto)
lleva el nombre, sus cifras y los botones de reproducir. Debajo, en una sola
página que se desplaza: canciones populares, álbumes, sencillos, listas,
música parecida, artistas parecidos y su historia completa.

Mismas reglas que el inicio: las carátulas son fundas, los artistas retratos
redondos; una fila de tarjetas enseña lo que cabe y «Ver todo» despliega el
resto (sin animar el alto). Lo que llega de la red se funde; lo guardado
aparece sin animación.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ytm_native.core.db import Track
from ytm_native.services.discover import ArtistCard, ArtistPage, PlaylistCard
from .art import ArtLoader, sized_url
from .components import PillButton
from .cards import CardGrid, SleeveCard
from .motion import EASE, FADE_MS, ROW_STAGGER, STAGGER_CAP, Tween
from .now import SimilarRow
from ytm_native.core.i18n import tr


class TrackColumns(QWidget):
    """Pistas en una o dos columnas según el ancho (de arriba abajo y luego a
    la derecha, como se leen). Un clic reproduce esa y siguen las demás."""

    play = Signal(int)
    menu = Signal(object, object)      # pista, posición global

    GAP = 18
    TWO_COLUMNS_FROM = 640

    def __init__(self, loader: ArtLoader, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._loader = loader
        self._rows: list[SimilarRow] = []
        self._count = 0
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(0)

    def set_tracks(self, tracks: list[Track]) -> None:
        while len(self._rows) < len(tracks):
            row = SimilarRow(self._loader, self)
            index = len(self._rows)
            row.clicked.connect(lambda _checked=False, i=index: self.play.emit(i))
            row.customContextMenuRequested.connect(
                lambda point, r=row: self.menu.emit(r.track, r.mapToGlobal(point))
            )
            self._rows.append(row)
        self._count = len(tracks)
        for i, row in enumerate(self._rows):
            if i < len(tracks):
                row.set_track(tracks[i])
        self._relayout()

    def rows(self) -> list[SimilarRow]:
        return self._rows[: self._count]

    def reveal(self, start: int) -> None:
        """Las filas aparecen escalonadas en el orden en que se leen."""
        for i, row in enumerate(self.rows()):
            row.reveal.start(start + min(i, STAGGER_CAP) * ROW_STAGGER)

    def _relayout(self) -> None:
        cols = 2 if self.width() >= self.TWO_COLUMNS_FROM and self._count > 3 else 1
        per_column = -(-self._count // cols) if self._count else 0
        width = (self.width() - self.GAP * (cols - 1)) // cols
        for i, row in enumerate(self._rows):
            if i < self._count:
                col, line = divmod(i, per_column)
                row.setGeometry(col * (width + self.GAP), line * SimilarRow.HEIGHT, width, SimilarRow.HEIGHT)
            row.setVisible(i < self._count)
        self.setFixedHeight(per_column * SimilarRow.HEIGHT)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width():
            self._relayout()


class Section(QWidget):
    """Un apartado de la página: título, una nota y su contenido. A la derecha
    del título, un botón opcional («Ver todo», «Todas las canciones»)."""

    def __init__(self, title: str, content: QWidget, parent: Optional[QWidget] = None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        head = QHBoxLayout()
        # El texto se alinea con las carátulas, no con el borde de la tarjeta.
        head.setContentsMargins(SleeveCard.PAD, 0, SleeveCard.PAD, 0)
        head.setSpacing(12)
        words = QVBoxLayout()
        words.setSpacing(2)
        self.title = QLabel(title)
        self.title.setObjectName("shelfTitle")
        self.note = QLabel()
        self.note.setObjectName("shelfNote")
        self.note.hide()
        for label in (self.title, self.note):
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            words.addWidget(label)
        head.addLayout(words, 1)
        self.action = PillButton("", "quiet")
        self.action.setFixedHeight(32)
        self.action.hide()
        head.addWidget(self.action, 0, Qt.AlignmentFlag.AlignBottom)
        lay.addLayout(head)
        lay.addSpacing(8)
        self.content = content
        lay.addWidget(content)
        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setEnabled(False)
        self.setGraphicsEffect(self._effect)
        self._fade = Tween(self, 1.0, self._apply_fade, ms=FADE_MS + 40, curve=EASE)

    def set_note(self, text: str) -> None:
        self.note.setText(text)
        self.note.setVisible(bool(text))

    def _apply_fade(self, value) -> None:
        opacity = float(value)
        self._effect.setOpacity(opacity)
        self._effect.setEnabled(opacity < 1.0)

    def appear(self) -> None:
        self._fade.set(0.0)
        self._fade.to(1.0)


class CardSection(Section):
    """Apartado de tarjetas: una fila con lo que cabe y «Ver todo» para el resto."""

    opened = Signal(object)
    play_requested = Signal(object)

    def __init__(self, title: str, loader: ArtLoader, round_art: bool = False,
                 parent: Optional[QWidget] = None):
        self.grid = CardGrid(loader, 1, round_art=round_art)
        super().__init__(title, self.grid, parent)
        self._open = False
        self.grid.opened.connect(self.opened)
        self.grid.play_requested.connect(self.play_requested)
        self.grid.fitted.connect(self._on_fitted)
        self.action.clicked.connect(self._toggle)

    def set_cards(self, specs: list, reveal_from: Optional[int] = None) -> None:
        self._open = False
        self.grid.set_max_rows(1)
        self.grid.set_cards(specs, reveal_from)

    def _on_fitted(self, shown: int, total: int) -> None:
        # Solo hay botón si algo se queda fuera (o si ya se desplegó).
        self.action.setVisible(self._open or shown < total)
        self.action.set_label(tr("Ver menos") if self._open else tr("Ver todo ({total})", total=total))

    def _toggle(self) -> None:
        self._open = not self._open
        self.grid.set_max_rows(99 if self._open else 1)


class ArtistView(QScrollArea):
    """El cuerpo de la página del artista (la banda la pone la ventana)."""

    play_tracks = Signal(object, int)        # lista de pistas, posición
    track_menu = Signal(object, object)      # pista, posición global
    open_remote = Signal(object)             # PlaylistCard
    play_remote = Signal(object)
    open_artist = Signal(object)             # ArtistCard
    open_all_songs = Signal()
    retry = Signal()

    def __init__(self, loader: ArtLoader, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._loader = loader
        self._page: Optional[ArtistPage] = None
        self.setObjectName("home")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.verticalScrollBar().setSingleStep(28)

        page = QWidget()
        page.setObjectName("homePage")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(22, 22, 16, 28)
        lay.setSpacing(30)

        # -- espera y error --------------------------------------------------
        status = QHBoxLayout()
        status.setContentsMargins(SleeveCard.PAD, 0, SleeveCard.PAD, 0)
        status.setSpacing(14)
        self.status = QLabel()
        self.status.setObjectName("homeStatus")
        self.status.setWordWrap(True)
        self.retry_btn = PillButton(tr("Reintentar"), "quiet", glyph="sync")
        self.retry_btn.clicked.connect(self.retry)
        status.addWidget(self.status, 1)
        status.addWidget(self.retry_btn)
        self.status_box = QWidget()
        self.status_box.setLayout(status)
        lay.addWidget(self.status_box)

        # -- canciones -------------------------------------------------------
        self.songs = TrackColumns(loader)
        self.songs.play.connect(lambda i: self._play("top_songs", i))
        self.songs.menu.connect(self.track_menu)
        self.songs_section = Section(tr("Canciones populares"), self._indented(self.songs))
        self.songs_section.action.set_label(tr("Todas las canciones"), "queue")
        self.songs_section.action.setToolTip(tr("Abrir la lista con todas sus canciones"))
        self.songs_section.action.clicked.connect(self.open_all_songs)
        lay.addWidget(self.songs_section)

        # -- discografía y listas -------------------------------------------
        self.albums = CardSection(tr("Álbumes"), loader)
        self.singles = CardSection(tr("Sencillos y EP"), loader)
        self.playlists = CardSection(tr("Listas"), loader)
        for section in (self.albums, self.singles, self.playlists):
            section.opened.connect(self._emit_open)
            section.play_requested.connect(self._emit_play)
            lay.addWidget(section)

        # -- lo parecido -----------------------------------------------------
        self.similar = TrackColumns(loader)
        self.similar.play.connect(lambda i: self._play("similar", i))
        self.similar.menu.connect(self.track_menu)
        self.similar_section = Section(tr("Música parecida"), self._indented(self.similar))
        lay.addWidget(self.similar_section)
        self.related = CardSection(tr("Artistas parecidos"), loader, round_art=True)
        self.related.opened.connect(self._emit_artist)
        lay.addWidget(self.related)

        # -- sobre el artista ------------------------------------------------
        about = QWidget()
        about_lay = QVBoxLayout(about)
        about_lay.setContentsMargins(SleeveCard.PAD, 0, SleeveCard.PAD, 0)
        about_lay.setSpacing(16)
        self.bio = QLabel()
        self.bio.setObjectName("nowBio")
        self.bio.setWordWrap(True)
        self.bio.setMaximumWidth(680)
        self.bio.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.bio.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        about_lay.addWidget(self.bio)
        self.stats = QGridLayout()
        self.stats.setContentsMargins(0, 0, 0, 0)
        self.stats.setHorizontalSpacing(18)
        self.stats.setVerticalSpacing(6)
        self.stats.setColumnStretch(1, 1)
        self._stat_labels: list[tuple[QLabel, QLabel]] = []
        about_lay.addLayout(self.stats)
        self.about_section = Section(tr("Sobre el artista"), about)
        lay.addWidget(self.about_section)
        lay.addStretch(1)
        self.setWidget(page)

        self._sections = (self.songs_section, self.albums, self.singles, self.playlists,
                          self.similar_section, self.related, self.about_section)
        self.set_loading()

    @staticmethod
    def _indented(widget: QWidget) -> QWidget:
        """Las filas de pista llevan la carátula a 6 px de su borde: se meten 4
        px más para que la carátula quede alineada con el título del apartado."""
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(4, 0, 0, 0)
        lay.addWidget(widget)
        return box

    # -- estados ------------------------------------------------------------
    def _only_status(self, text: str, retry: bool) -> None:
        self._page = None
        for section in self._sections:
            section.hide()
        self.status.setText(text)
        self.retry_btn.setVisible(retry)
        self.status_box.show()
        self.verticalScrollBar().setValue(0)

    def set_loading(self) -> None:
        self._only_status(tr("Cargando el artista…"), retry=False)

    def set_error(self, text: str = "") -> None:
        self._only_status(text or tr("No se pudo cargar el artista. Comprueba la conexión."), retry=True)

    def page(self) -> Optional[ArtistPage]:
        return self._page

    def set_page(self, page: ArtistPage, animate: bool = False) -> None:
        """Rellena la página. ``animate``: acaba de llegar de la red (se funde)."""
        same = self._page is not None and self._page.channel_id == page.channel_id
        self._page = page
        self.status_box.hide()

        self.songs.set_tracks(page.top_songs)
        self.songs_section.setVisible(bool(page.top_songs))
        self.songs_section.action.setVisible(bool(page.songs_playlist))

        def cards(items: list[PlaylistCard]) -> list:
            return [(c.title, c.note, sized_url(c.thumb_url, 400), c) for c in items]

        # Todo aparece escalonado de arriba abajo y de izquierda a derecha. Con
        # la página ya guardada es lo que evita el tirón: las carátulas se leen
        # del disco a medida que les toca aparecer, no todas en el primer fotograma.
        self.songs.reveal(0)
        self.albums.set_cards(cards(page.albums), reveal_from=90)
        self.albums.setVisible(bool(page.albums))
        self.singles.set_cards(cards(page.singles), reveal_from=180)
        self.singles.setVisible(bool(page.singles))
        self.playlists.set_cards(cards(page.playlists), reveal_from=270)
        self.playlists.set_note(tr("Del artista y de la comunidad, con «{name}» en el título", name=page.name))
        self.playlists.setVisible(bool(page.playlists))

        self.similar.set_tracks(page.similar)
        self.similar.reveal(330)
        self.similar_section.set_note(tr("Lo que suena en la radio de {name}, sin sus canciones", name=page.name))
        self.similar_section.setVisible(bool(page.similar))
        self.related.set_cards([(a.name, a.note, sized_url(a.thumb_url, 400), a) for a in page.related],
                               reveal_from=390)
        self.related.setVisible(bool(page.related))

        self.about_section.title.setText(tr("Sobre {name}", name=page.name))
        self.bio.setText(page.bio)
        self.bio.setVisible(bool(page.bio))
        self._fill_stats(page.stats)
        self.about_section.setVisible(bool(page.bio or page.stats))

        if not any(s.isVisibleTo(self.widget()) for s in self._sections):
            self.status.setText(tr("YouTube Music no tiene más información sobre este artista."))
            self.retry_btn.hide()
            self.status_box.show()
        if not same:
            self.verticalScrollBar().setValue(0)
        if animate:
            for section in self._sections:
                if section.isVisibleTo(self.widget()):
                    section.appear()

    def _fill_stats(self, stats: list[list[str]]) -> None:
        while len(self._stat_labels) < len(stats):
            label, value = QLabel(), QLabel()
            label.setObjectName("nowFactLabel")
            value.setObjectName("nowFactValue")
            row = len(self._stat_labels)
            self.stats.addWidget(label, row, 0, Qt.AlignmentFlag.AlignTop)
            self.stats.addWidget(value, row, 1)
            self._stat_labels.append((label, value))
        for i, (label, value) in enumerate(self._stat_labels):
            shown = i < len(stats)
            label.setVisible(shown)
            value.setVisible(shown)
            if shown:
                label.setText(tr(stats[i][0]))     # guardadas en español
                value.setText(stats[i][1])

    # -- señales ------------------------------------------------------------
    def _play(self, which: str, index: int) -> None:
        if self._page is not None:
            self.play_tracks.emit(list(getattr(self._page, which)), index)

    def _emit_open(self, payload: object) -> None:
        if isinstance(payload, PlaylistCard):
            self.open_remote.emit(payload)

    def _emit_play(self, payload: object) -> None:
        if isinstance(payload, PlaylistCard):
            self.play_remote.emit(payload)

    def _emit_artist(self, payload: object) -> None:
        if isinstance(payload, ArtistCard):
            self.open_artist.emit(payload)
