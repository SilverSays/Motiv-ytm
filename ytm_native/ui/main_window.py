"""Ventana principal: biblioteca, búsqueda, barra de reproducción y estado."""

from __future__ import annotations

import logging
import os
import random
import re
import time
from typing import Callable, Optional

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ytm_native.playback import bootstrap
from ytm_native.core import config, paths
from ytm_native.services import discover
from ytm_native.services.discover import ArtistCard, ArtistPage, PlaylistCard, RemotePlaylist, SearchExtras
from ytm_native.services.lyrics import Lyrics, current_line_index, fetch_lyrics
from ytm_native.core.db import Database, Track, is_local_playlist
from ytm_native.playback.extractor import ExtractorManager, ExtractorState, UpdateResult, UpdateStatus
from ytm_native.playback.mpv_ipc import MpvPlayer
from ytm_native.playback.player import LOADING, PAUSED, PLAYING, REPEAT_ALL, REPEAT_OFF, REPEAT_ONE, PlaybackController
from ytm_native.services.sync import LibrarySync, SyncReport, SyncState, looks_device_bound, search_tracks
from . import focus, theme
from .art import ArtLoader, neutral_tint, placeholder_pixmap, sized_url, track_art_url
from .artist import ArtistView
from .chrome import rounded_menu, show_check
from .components import (
    ActiveMark,
    CoverFlight,
    note_launch,
    take_launch,
    Banner,
    ClickableCover,
    ElidedLabel,
    EmptyState,
    IconButton,
    LyricsPanel,
    PaneButton,
    PillButton,
    PlayButton,
    PlaylistBand,
    SeekSlider,
    SidebarFooter,
    ToastHost,
    TrackDelegate,
    TrackTable,
    glyph_pixmap,
)
from .home import HomePage
from .motion import EASE, EASE_DRAWER, FADE_MS, FLIGHT_MS, PANE_CLOSE_MS, PANE_OPEN_MS, Tween, reduced_motion
from .now import NowPanel
from .search import ARTISTS, PLAYLISTS, SONGS, ArtistStrip, CardsPage, ScopeBar
from .widgets import AccountDialog, TrackModel, format_ago, format_time, run_async

log = logging.getLogger("ytmn.ui")

HOME_KEY = "__inicio__"
SEARCH_KEY = "__buscar__"
QUEUE_KEY = "__cola__"
FIXED_KEYS = (HOME_KEY, SEARCH_KEY, QUEUE_KEY)
# Fila de la barra lateral que no es una vista: abre el diálogo de lista nueva.
NEW_LIST_KEY = "__nueva_lista__"
# Vista de una lista ajena a la biblioteca (una recomendación abierta).
REMOTE_PREFIX = "remota:"
# Página de un artista (por su canal).
ARTIST_PREFIX = "artista:"
ROLE_ID = Qt.ItemDataRole.UserRole
ROLE_ART = Qt.ItemDataRole.UserRole + 1

SEARCH_IDLE_NOTE = "Busca sin iniciar sesión"


def plural(n: int, one: str, many: str) -> str:
    # Los millares, con espacio fino: "12 480 canciones".
    count = f"{n:,}".replace(",", " ") if n >= 10000 else str(n)
    return f"{count} {one if n == 1 else many}"


def is_remote(key: Optional[str]) -> bool:
    return bool(key) and key.startswith(REMOTE_PREFIX)


def is_artist(key: Optional[str]) -> bool:
    return bool(key) and key.startswith(ARTIST_PREFIX)


def is_library(key: Optional[str]) -> bool:
    """¿La vista es una lista de la biblioteca (y no una vista fija o ajena)?"""
    return (bool(key) and key not in FIXED_KEYS and key != NEW_LIST_KEY
            and not is_remote(key) and not is_artist(key))


def format_total(seconds: Optional[float]) -> str:
    """Duración total legible: "42 min", "5 h 12 min"."""
    if not seconds or seconds <= 0:
        return ""
    minutes = int(round(seconds / 60))
    hours, minutes = divmod(minutes, 60)
    if hours and minutes:
        return f"{hours} h {minutes} min"
    return f"{hours} h" if hours else f"{minutes} min"


class Bridge(QObject):
    """Lleva al hilo de la GUI los avisos que el extractor emite desde hilos de trabajo."""

    extractor_state = Signal(str)
    notice = Signal(str)
    progress = Signal(str)
    shelf = Signal(int, object)   # tanda de recomendaciones, fila lista


class MainWindow(QMainWindow):
    SIDE_MIN = 208
    SIDE_MAX = 340
    SIDE_COLLAPSED = 64
    CONTENT_MIN = 360
    NOW_MIN = 300

    def __init__(
        self,
        db: Database,
        extractor: ExtractorManager,
        mpv: MpvPlayer,
        player: PlaybackController,
        sync: LibrarySync,
        bridge: Bridge,
    ):
        super().__init__()
        self.db = db
        self.extractor = extractor
        self.mpv = mpv
        self.player = player
        self.sync = sync
        self.bridge = bridge
        self._view_key: Optional[str] = None
        self._syncing = False
        self._installing = False
        self._blocked_until = 0.0
        self._seeking = False
        self._duration = 0.0
        self._lyrics: Optional[Lyrics] = None
        self._lyrics_for: Optional[str] = None
        self._lyrics_index = -2
        self.art = ArtLoader(self)
        self._band_art = ""
        self._now_art = ""
        self._search_note = SEARCH_IDLE_NOTE
        # Búsqueda en vivo: cada petición lleva un número; al volver, la que no
        # sea la última se descarta (se escribió algo más mientras tanto).
        self._search_tracks: list[Track] = []
        self._searched_query = ""
        self._search_seq = 0
        self._suggest_seq = 0
        # Listas de la comunidad y artistas de la búsqueda (llegan aparte).
        self._search_scope = SONGS
        self._extras = SearchExtras()
        self._extras_state = "idle"          # idle, loading, done, failed
        self._top_artist: Optional[ArtistCard] = None
        # Páginas de artista abiertas en esta sesión.
        self._artists: dict[str, ArtistPage] = {}
        self._artist_cards: dict[str, ArtistCard] = {}
        self._artist_waiting: set[str] = set()
        self._artist_failed: set[str] = set()
        self._artist_fresh: Optional[str] = None   # recién llegada de la red: se funde
        # Navegación: vistas anteriores, para Alt+Izquierda y el botón «Volver».
        self._history: list[str] = []
        self._going_back = False
        # Listas ajenas abiertas en esta sesión.
        self._remote: dict[str, RemotePlaylist] = {}
        self._remote_cards: dict[str, PlaylistCard] = {}
        self._remote_waiting: dict[str, list[Callable]] = {}
        self._remote_failed: set[str] = set()
        # Recomendaciones del inicio.
        self._recs_shown = False
        self._recs_loading = False
        self._recs_token = 0
        self._recs_retry_after = 0.0
        # Panel "Ahora suena": para qué canción se pidió la información.
        self._now_for: Optional[str] = None
        self._now_failed: set[str] = set()
        self._side_collapsed = False
        self._side_width = 248
        # Paneles que se deslizan: el ancho al que vuelve cada uno y su animación en curso.
        self._pane_widths: dict = {}
        self._slides: dict = {}
        self._swapping = False
        self._flight: Optional[CoverFlight] = None
        self._band_flight: Optional[CoverFlight] = None
        self._extractor_state = ExtractorState.IDLE.value
        self._progress_text = ""
        self._versions = ""
        self._was_blocked = False
        self._pre_mute = 0

        self.setWindowTitle("YT Music Nativo")
        self.resize(1180, 760)
        # Lo justo para que quepan los tres paneles a su mínimo: si la ventana
        # pudiera ser más estrecha que la suma, el divisor los sacaría por los
        # bordes en lugar de encogerlos.
        gutter = theme.GUTTER
        self.setMinimumSize(self.SIDE_MIN + self.CONTENT_MIN + self.NOW_MIN + 4 * gutter, 560)

        self._build_menu()
        self._build_ui()
        # La tinta no salta de una pista a otra: se funde, como la banda.
        self._ink = Tween(self, QColor(theme.CREMA), self._paint_ink, ms=FADE_MS + 60, curve=EASE)
        self._connect()

        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self._on_tick)
        self._tick.start()

        self._sync_timer = QTimer(self)
        self._sync_timer.setInterval(30 * 60 * 1000)
        self._sync_timer.timeout.connect(self._auto_sync)

        self._suggest_timer = QTimer(self)
        self._suggest_timer.setSingleShot(True)
        self._suggest_timer.setInterval(config.SEARCH_SUGGEST_DEBOUNCE_MS)
        self._suggest_timer.timeout.connect(self._fetch_suggestions)
        self._live_timer = QTimer(self)
        self._live_timer.setSingleShot(True)
        self._live_timer.setInterval(config.SEARCH_LIVE_DEBOUNCE_MS)
        self._live_timer.timeout.connect(self._do_search)

    # ------------------------------------------------------------------ UI
    def _build_menu(self) -> None:
        """Menú de la aplicación: se abre desde el pie de la barra lateral.

        No hay barra de menús, así que cada acción se cuelga también de la
        ventana para que su atajo de teclado siga funcionando.
        """
        menu = self.app_menu = rounded_menu(QMenu(self))
        self._add_action(menu, "Cuenta y reproducción…", self._open_account)
        menu.addSeparator()
        self._add_action(menu, "Sincronizar ahora", lambda: self._start_sync(), "F5")
        self._add_action(menu, "Forzar sincronización completa", lambda: self._start_sync(force_all=True))
        menu.addSeparator()
        self.autoplay_action = QAction("Radio automática al terminar la cola", self)
        self.autoplay_action.setCheckable(True)
        self.autoplay_action.setChecked(self.player.autoplay)
        self.autoplay_action.toggled.connect(self.player.set_autoplay)
        menu.addAction(show_check(self.autoplay_action, self.devicePixelRatioF()))
        self.lyrics_action = QAction("Mostrar letra", self)
        self.lyrics_action.setCheckable(True)
        self.lyrics_action.setShortcut(QKeySequence("Ctrl+L"))
        self.lyrics_action.toggled.connect(self._toggle_lyrics)
        menu.addAction(show_check(self.lyrics_action, self.devicePixelRatioF()))
        self.addAction(self.lyrics_action)
        self.now_action = QAction("Mostrar la canción que suena", self)
        self.now_action.setCheckable(True)
        self.now_action.setShortcut(QKeySequence("Ctrl+I"))
        self.now_action.toggled.connect(self._toggle_now)
        menu.addAction(show_check(self.now_action, self.devicePixelRatioF()))
        self.addAction(self.now_action)
        self.collapse_action = QAction("Contraer la barra lateral", self)
        self.collapse_action.setCheckable(True)
        self.collapse_action.setShortcut(QKeySequence("Ctrl+B"))
        self.collapse_action.toggled.connect(self._set_sidebar_collapsed)
        menu.addAction(show_check(self.collapse_action, self.devicePixelRatioF()))
        self.addAction(self.collapse_action)
        menu.addSeparator()
        self._add_action(menu, "Nueva lista…", lambda: self._create_local_playlist(), "Ctrl+N")
        menu.addSeparator()
        self._add_action(menu, "Buscar actualización de yt-dlp", self._manual_update)
        self._add_action(menu, "Descargar binarios que faltan", self._install_binaries)
        self._add_action(menu, "Diagnóstico…", self._show_diagnostics)
        self._add_action(menu, "Abrir carpeta de datos", self._open_data_folder)
        menu.addSeparator()
        self._add_action(menu, "Salir", self.close, "Ctrl+Q")

    def _add_action(self, menu, text, slot, shortcut: Optional[str] = None) -> QAction:
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(lambda _checked=False: slot())
        menu.addAction(action)
        self.addAction(action)
        return action

    def _open_app_menu(self) -> None:
        # El menú nace pegado a su botón, encima de él: se ve de dónde sale.
        height = self.app_menu.sizeHint().height()
        corner = self.footer.mapToGlobal(QPoint(theme.GUTTER, 0))
        self.app_menu.popup(QPoint(corner.x(), corner.y() - height - 4))

    def _build_ui(self) -> None:
        gutter = theme.GUTTER
        central = QWidget()
        central.setObjectName("root")
        root = QVBoxLayout(central)
        root.setContentsMargins(gutter, 2, gutter, gutter)
        root.setSpacing(gutter)

        # Tres paneles flotantes sobre el fondo; el hueco entre ellos es el tirador.
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(gutter)
        splitter.setChildrenCollapsible(False)

        side = self.side_panel = QFrame()
        side.setObjectName("panel")
        side.setMinimumWidth(self.SIDE_MIN)
        side.setMaximumWidth(self.SIDE_MAX)
        side_lay = QVBoxLayout(side)
        side_lay.setContentsMargins(0, 0, 0, 0)
        side_lay.setSpacing(0)
        # Contraída, la barra se queda en una columna de iconos y carátulas.
        self.side_toggle = PaneButton("Contraer la barra lateral (Ctrl+B)")
        toggle_row = QHBoxLayout()
        toggle_row.setContentsMargins(14, 8, 14, 0)
        toggle_row.addWidget(self.side_toggle)
        toggle_row.addStretch(1)
        side_lay.addLayout(toggle_row)
        self.sidebar = QListWidget()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setAccessibleName("Vistas y listas")
        self.sidebar.setIconSize(QSize(32, 32))
        self.sidebar.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.sidebar.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.active_mark = ActiveMark(self.sidebar)
        self.footer = SidebarFooter()
        side_lay.addWidget(self.sidebar, 1)
        side_lay.addWidget(self.footer)
        splitter.addWidget(side)

        content = QFrame()
        content.setObjectName("panel")
        # Mínimo fijo: no depende de lo que muestre la cabecera, así el divisor
        # no se traba ni salta al arrastrarlo.
        content.setMinimumWidth(self.CONTENT_MIN)
        lay = QVBoxLayout(content)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # Cabecera: banda de color plano con la carátula y las notas de la lista.
        self.band = PlaylistBand(self.art)
        lay.addWidget(self.band)
        self.search_edit = self.band.search_edit
        self.suggestions = self.band.suggestions
        self.play_all_btn = self.band.play_btn
        self.shuffle_all_btn = self.band.shuffle_btn
        self.sync_list_btn = self.band.sync_btn

        # El aviso fijo va fuera de las vistas: también se ve en el inicio.
        self.banner = Banner()
        self.banner_label = self.banner.label
        self.banner_button = self.banner.button
        self._banner_action = None
        self.banner_button.clicked.connect(self._run_banner_action)
        self.banner_box = QWidget()
        banner_lay = QVBoxLayout(self.banner_box)
        banner_lay.setContentsMargins(12, 12, 12, 0)
        banner_lay.addWidget(self.banner)
        self.banner_box.hide()
        lay.addWidget(self.banner_box)

        self.home = HomePage(self.art)
        self.home.hide()
        lay.addWidget(self.home, 1)

        # Página del artista: bajo la banda, en lugar de la tabla.
        self.artist_view = ArtistView(self.art)
        self.artist_view.hide()
        lay.addWidget(self.artist_view, 1)

        self.body_box = QWidget()
        body = QVBoxLayout(self.body_box)
        body.setContentsMargins(12, 12, 6, 8)
        body.setSpacing(10)

        # Solo en la búsqueda: qué se enseña y, si se buscaba un artista, su fila.
        self.scope_bar = ScopeBar()
        self.scope_bar.hide()
        body.addWidget(self.scope_bar)
        self.artist_strip = ArtistStrip(self.art)
        self.artist_strip.hide()
        body.addWidget(self.artist_strip)
        self.search_cards = CardsPage(self.art)
        self.search_cards.hide()
        body.addWidget(self.search_cards, 1)

        self.model = TrackModel(self)
        self.table = TrackTable()
        self.table.setAccessibleName("Pistas")
        self.table.setModel(self.model)
        self.delegate = TrackDelegate(self.art, self._row_art_url, self.table)
        self.table.setItemDelegate(self.delegate)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(TrackDelegate.ROW_HEIGHT)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        head = self.table.horizontalHeader()
        head.setHighlightSections(False)
        head.setSectionsClickable(False)
        head.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        head.setMinimumSectionSize(0)
        head.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.fit_columns()
        body.addWidget(self.table, 1)

        self.empty = EmptyState()
        self.empty.hide()
        body.addWidget(self.empty, 1)
        lay.addWidget(self.body_box, 1)
        splitter.addWidget(content)

        self.lyrics = LyricsPanel()
        self.lyrics.setMinimumWidth(270)
        self.lyrics.setMaximumWidth(460)
        self.lyrics.hide()
        splitter.addWidget(self.lyrics)

        # "Ahora suena" ocupa el mismo sitio que la letra: uno sustituye al otro.
        self.now_panel = NowPanel(self.art)
        self.now_panel.setMinimumWidth(self.NOW_MIN)
        self.now_panel.setMaximumWidth(460)
        self.now_panel.hide()
        splitter.addWidget(self.now_panel)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setStretchFactor(3, 0)
        splitter.setSizes([self._side_width, 620, 330, 340])
        self.splitter = splitter
        root.addWidget(splitter, 1)
        self.player_bar = self._build_player_bar()
        root.addWidget(self.player_bar)
        self.setCentralWidget(central)
        # Avisos breves: sustituyen a la barra de estado y se apilan sobre el reproductor.
        self.toasts = ToastHost(central, lambda: self.player_bar.y())

    def _build_player_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("panel")
        bar.setFixedHeight(84)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(14, 0, 16, 0)
        lay.setSpacing(20)

        now_box = QWidget()
        now_row = QHBoxLayout(now_box)
        now_row.setContentsMargins(0, 0, 0, 0)
        now_row.setSpacing(12)
        self.now_cover = ClickableCover(self.art, 56, radius=theme.R_COVER)
        self.now_cover.setToolTip("Ver la canción en grande, su artista y música parecida (Ctrl+I)")
        now_row.addWidget(self.now_cover)
        now = QVBoxLayout()
        now.setSpacing(1)
        self.now_title = ElidedLabel(theme.ui_font(14, QFont.Weight.DemiBold), theme.HUMO, fade=True)
        self.now_title.setText("Nada en reproducción")
        self.now_artist = ElidedLabel(theme.ui_font(12), theme.HUMO, fade=True)
        now.addStretch(1)
        now.addWidget(self.now_title)
        now.addWidget(self.now_artist)
        now.addStretch(1)
        now_row.addLayout(now, 1)
        now_box.setMinimumWidth(170)
        lay.addWidget(now_box, 3)

        center_box = QWidget()
        center_box.setMaximumWidth(720)
        center = QVBoxLayout(center_box)
        center.setContentsMargins(0, 6, 0, 6)
        center.setSpacing(0)
        controls = QHBoxLayout()
        controls.setSpacing(6)
        self.shuffle_btn = IconButton("shuffle", "Aleatorio", checkable=True, quiet=True)
        self.prev_btn = IconButton("previous", "Anterior (Ctrl+Izquierda)")
        self.play_btn = PlayButton()
        self.next_btn = IconButton("next", "Siguiente (Ctrl+Derecha)")
        self.repeat_btn = IconButton("repeat_all", "Repetir: desactivado", checkable=True, quiet=True)
        controls.addStretch(1)
        for btn in (self.shuffle_btn, self.prev_btn, self.play_btn, self.next_btn, self.repeat_btn):
            controls.addWidget(btn)
        controls.addStretch(1)
        center.addLayout(controls)

        seek = QHBoxLayout()
        seek.setSpacing(4)
        time_font = theme.ui_font(12, tabular=True)
        self.pos_label = ElidedLabel(time_font, theme.HUMO, align=Qt.AlignmentFlag.AlignRight, width=44)
        self.pos_label.setText("0:00")
        self.seek_slider = SeekSlider(nudge_keys=True)
        self.seek_slider.setAccessibleName("Posición en la pista")
        self.seek_slider.setRange(0, 1000)
        self.seek_slider.setEnabled(False)
        self.seek_slider.hover_text = lambda f: format_time(f * self._duration) if self._duration > 0 else ""
        self.dur_label = ElidedLabel(time_font, theme.HUMO, width=44)
        self.dur_label.setText("0:00")
        seek.addWidget(self.pos_label)
        seek.addWidget(self.seek_slider, 1)
        seek.addWidget(self.dur_label)
        center.addLayout(seek)
        lay.addWidget(center_box, 5)

        right_box = QWidget()
        right = QHBoxLayout(right_box)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(4)
        self.lyrics_btn = PillButton("Letra", "quiet")
        self.lyrics_btn.setCheckable(True)
        self.lyrics_btn.setFixedHeight(30)
        self.lyrics_btn.setToolTip("Mostrar u ocultar la letra (Ctrl+L)")
        self.queue_btn = IconButton("queue", "Ver la cola de reproducción", quiet=True)
        self.mute_btn = IconButton("volume", "Silenciar", quiet=True)
        self.volume_slider = SeekSlider()
        self.volume_slider.setAccessibleName("Volumen")
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(self.db.get_int("volume", 80))
        self.volume_slider.setFixedWidth(108)
        right.addStretch(1)
        right.addWidget(self.lyrics_btn)
        right.addSpacing(6)
        right.addWidget(self.queue_btn)
        right.addWidget(self.mute_btn)
        right.addWidget(self.volume_slider)
        right_box.setMinimumWidth(170)
        lay.addWidget(right_box, 3)
        self._sync_volume_glyph(self.volume_slider.value())
        return bar

    def _connect(self) -> None:
        self.sidebar.currentItemChanged.connect(self._on_sidebar_changed)
        self.table.doubleClicked.connect(lambda index: self._play_from(index.row()))
        self.table.activated.connect(lambda index: self._play_from(index.row()))
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_table_menu)
        self.table.thumb_clicked.connect(self._on_thumb_clicked)
        self.lyrics.line_clicked.connect(self._on_lyric_clicked)
        self.lyrics.close_requested.connect(lambda: self.lyrics_action.setChecked(False))
        self.footer.menu_btn.clicked.connect(self._open_app_menu)
        self.queue_btn.clicked.connect(lambda: self._navigate(QUEUE_KEY))
        self.mute_btn.clicked.connect(self._toggle_mute)
        self.seek_slider.nudged.connect(self._nudge_seek)
        self.player.queue_changed.connect(self._on_queue_changed)
        self.play_all_btn.clicked.connect(lambda: self._play_from(0))
        self.shuffle_all_btn.clicked.connect(self._play_shuffled)
        self.sync_list_btn.clicked.connect(self._sync_current_list)
        self.search_edit.returnPressed.connect(lambda: self._do_search(commit=True))
        self.search_edit.textChanged.connect(self._on_search_text)
        self.search_edit.installEventFilter(self)
        self.suggestions.picked.connect(self._on_suggestion_picked)
        self.band.back_btn.clicked.connect(self._go_back)
        self.band.extra_btn.clicked.connect(self._on_band_extra)
        self.side_toggle.clicked.connect(self.collapse_action.toggle)
        self.sidebar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.sidebar.customContextMenuRequested.connect(self._on_sidebar_menu)
        self.now_cover.clicked.connect(self._on_now_cover_clicked)
        self.now_panel.close_requested.connect(lambda: self.now_action.setChecked(False))
        self.now_panel.play_similar.connect(self._play_similar)
        self.now_panel.similar_menu.connect(lambda track, pos: self._track_menu(track).exec(pos))
        self.now_panel.open_artist.connect(self._open_artist_from_now)
        self.scope_bar.changed.connect(self._set_search_scope)
        self.artist_strip.opened.connect(self._open_artist)
        self.search_cards.open_playlist.connect(self._open_remote)
        self.search_cards.play_playlist.connect(self._play_remote)
        self.search_cards.open_artist.connect(self._open_artist)
        self.artist_view.play_tracks.connect(self._play_list)
        self.artist_view.track_menu.connect(lambda track, pos: self._track_menu(track).exec(pos))
        self.artist_view.open_remote.connect(self._open_remote)
        self.artist_view.play_remote.connect(self._play_remote)
        self.artist_view.open_artist.connect(self._open_artist)
        self.artist_view.open_all_songs.connect(self._open_artist_songs)
        self.artist_view.retry.connect(self._retry_artist)
        self.home.open_library.connect(self._navigate)
        self.home.play_library.connect(self._play_library)
        self.home.open_remote.connect(self._open_remote)
        self.home.play_remote.connect(self._play_remote)
        self.home.refresh_requested.connect(lambda: self._load_recommendations(force=True))
        self.bridge.shelf.connect(self._on_shelf)
        self.art.loaded.connect(self._on_art_loaded)
        self.lyrics_btn.toggled.connect(self.lyrics_action.setChecked)
        self.lyrics_action.toggled.connect(self.lyrics_btn.setChecked)

        self.play_btn.clicked.connect(self.player.toggle_pause)
        self.next_btn.clicked.connect(self.player.next_track)
        self.prev_btn.clicked.connect(self.player.previous_track)
        self.shuffle_btn.clicked.connect(lambda checked: self.player.set_shuffle(checked))
        self.repeat_btn.clicked.connect(lambda _c: self.player.cycle_repeat())
        self.volume_slider.valueChanged.connect(self._on_volume)
        self.seek_slider.sliderPressed.connect(self._on_seek_pressed)
        self.seek_slider.sliderReleased.connect(self._on_seek_released)

        self.player.current_changed.connect(self._on_current_changed)
        self.player.state_changed.connect(self._on_player_state)
        self.player.position_changed.connect(self._on_position)
        self.player.notice.connect(self._notice)
        self.player.blocked.connect(self._on_blocked)
        self.player.track_flagged.connect(self._on_track_flagged)
        self.player.modes_changed.connect(self._on_modes_changed)

        self.bridge.extractor_state.connect(self._on_extractor_state)
        self.bridge.notice.connect(self._notice)
        self.bridge.progress.connect(self._on_progress)

        QShortcut(QKeySequence(Qt.Key.Key_Space), self, activated=self.player.toggle_pause)
        QShortcut(QKeySequence("Ctrl+Right"), self, activated=self.player.next_track)
        QShortcut(QKeySequence("Ctrl+Left"), self, activated=self.player.previous_track)
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self._focus_search)
        QShortcut(QKeySequence("Alt+Left"), self, activated=self._go_back)
        # El botón «atrás» del ratón llega al control que esté debajo: se
        # escucha en la aplicación entera.
        QApplication.instance().installEventFilter(self)

    # --------------------------------------------------------------- arranque
    def start(self) -> None:
        # La barra lateral vuelve como se dejó (contraerla ya recarga sus filas).
        self.collapse_action.setChecked(self.db.get_bool("sidebar_collapsed"))
        self._reload_sidebar()
        self._on_extractor_state(ExtractorState.IDLE.value)
        self._refresh_banner()
        self._on_player_state(self.player.state)
        if paths.mpv_exe().is_file():
            self.mpv.start()
        self._startup_checks()
        lo, hi = config.SYNC_STARTUP_DELAY_S
        QTimer.singleShot(int(random.uniform(lo, hi) * 1000), self._auto_sync)
        self._sync_timer.start()

    def _startup_checks(self) -> None:
        if not paths.ytdlp_exe().is_file():
            return
        # Un yt-dlp ya arrancado espera el primer clic (ahorra ~1,3 s).
        self.extractor.prewarm()

        def after_diag(diag, error) -> None:
            if error is None and diag:
                self._apply_diagnostics(diag)
            # Comprobación proactiva silenciosa (solo si pasaron 24 h).
            run_async(lambda: self.extractor.update("proactive"), self._on_update_done_silent)

        run_async(self.extractor.diagnostics, after_diag)

    def _apply_diagnostics(self, diag: dict) -> None:
        parts = []
        if diag.get("ytdlp_version"):
            parts.append(f"yt-dlp {diag['ytdlp_version']}")
        if diag.get("deno_version"):
            parts.append(f"deno {diag['deno_version']}")
        self._versions = ", ".join(parts)
        self._refresh_status()
        if not diag.get("deno_ok") and diag.get("deno_message"):
            self._notice(diag["deno_message"])

    def _refresh_banner(self) -> None:
        self._banner_action = None
        missing = bootstrap.missing_binaries()
        if self._installing:
            self.banner_label.setText("Descargando binarios… El progreso aparece al pie de la barra lateral.")
            self.banner_button.hide()
            self.banner_box.show()
        elif missing:
            self.banner_label.setText(
                "Faltan binarios en la carpeta bin: " + ", ".join(missing)
                + ". Sin ellos no se puede reproducir."
            )
            self.banner_button.setText("Descargar ahora")
            self.banner_button.show()
            self._banner_action = self._install_binaries
            self.banner_box.show()
        elif not paths.browser_json().is_file():
            self.banner_label.setText(
                "Aún no hay credenciales de la cuenta secundaria: la biblioteca no se puede "
                "sincronizar. La búsqueda funciona sin cuenta."
            )
            self.banner_button.setText("Configurar cuenta")
            self.banner_button.show()
            self._banner_action = self._open_account
            self.banner_box.show()
        elif self.sync.state() is SyncState.AUTH_EXPIRED:
            text = (
                "La sesión de la cuenta secundaria caducó. La sincronización está detenida "
                "hasta que renueves las credenciales."
            )
            try:
                if looks_device_bound(paths.browser_json().read_text(encoding="utf-8")):
                    text += " Esta vez cópialas desde una ventana privada de Firefox: las de Chrome duran pocas horas."
            except OSError:
                pass
            self.banner_label.setText(text)
            self.banner_button.setText("Renovar credenciales")
            self.banner_button.show()
            self._banner_action = self._open_account
            self.banner_box.show()
        else:
            self.banner_box.hide()

    def _run_banner_action(self) -> None:
        if self._banner_action is not None:
            self._banner_action()

    # ---------------------------------------------------------------- sidebar
    def _reload_sidebar(self) -> None:
        keep = self._view_key or HOME_KEY
        dpr = self.devicePixelRatioF()
        collapsed = self._side_collapsed
        self.sidebar.blockSignals(True)
        self.sidebar.clear()

        def add(icon: QIcon, name: str, key: str, tooltip: str = "") -> QListWidgetItem:
            # Contraída, la fila es solo su icono: el nombre pasa a la ayuda emergente.
            item = QListWidgetItem(icon, "" if collapsed else name)
            item.setData(ROLE_ID, key)
            item.setData(Qt.ItemDataRole.AccessibleTextRole, name)
            item.setToolTip(tooltip or (name if collapsed else ""))
            self.sidebar.addItem(item)
            return item

        def glyph_icon(glyph_name: str) -> QIcon:
            return self._fixed_icon(glyph_pixmap(glyph_name, 32, theme.HUMO, dpr))

        add(glyph_icon("home"), "Inicio", HOME_KEY)
        add(glyph_icon("search"), "Buscar", SEARCH_KEY)
        add(glyph_icon("queue"), "Cola de reproducción", QUEUE_KEY)
        heading = QListWidgetItem("" if collapsed else "Tus listas")
        heading.setFlags(Qt.ItemFlag.NoItemFlags)
        if collapsed:
            heading.setSizeHint(QSize(0, 14))     # sin texto queda como un respiro entre grupos
        self.sidebar.addItem(heading)
        add(glyph_icon("add"), "Nueva lista", NEW_LIST_KEY,
            "Crea una lista que se guarda solo en esta app (Ctrl+N)")
        for playlist in self.db.playlists():
            url = sized_url(self.db.playlist_cover(playlist), 96)
            item = add(self._sidebar_icon(url), playlist.title, playlist.playlist_id, playlist.title)
            item.setData(ROLE_ART, url)
        self.sidebar.blockSignals(False)
        if not (is_remote(keep) or is_artist(keep)) and self._sidebar_row(keep) < 0:
            keep = HOME_KEY          # la lista que se veía ya no está en la biblioteca
        self._select_in_sidebar(keep)
        self._show_view(keep, force=True)

    def _sidebar_row(self, key: str) -> int:
        for row in range(self.sidebar.count()):
            if self.sidebar.item(row).data(ROLE_ID) == key:
                return row
        return -1

    def _select_in_sidebar(self, key: str) -> None:
        """Marca la fila de ``key``; una lista ajena no tiene fila y no marca ninguna."""
        self.sidebar.blockSignals(True)
        self.sidebar.setCurrentRow(self._sidebar_row(key))
        self.sidebar.blockSignals(False)
        self.active_mark.follow(animate=not focus.keyboard())

    def _navigate(self, key: str) -> None:
        self._select_in_sidebar(key)
        self._show_view(key)

    def _view_exists(self, key: str) -> bool:
        if key in FIXED_KEYS:
            return True
        if is_remote(key):
            return key[len(REMOTE_PREFIX):] in self._remote_cards
        if is_artist(key):
            return key[len(ARTIST_PREFIX):] in self._artist_cards
        return self.db.get_playlist(key) is not None

    def _go_back(self) -> None:
        """Vuelve a la vista anterior que siga existiendo (o al inicio)."""
        while self._history:
            key = self._history.pop()
            if key != self._view_key and self._view_exists(key):
                break
        else:
            key = HOME_KEY
        self._going_back = True
        try:
            self._navigate(key)
        finally:
            self._going_back = False

    @staticmethod
    def _fixed_icon(pixmap) -> QIcon:
        """Icono que no se tiñe al seleccionar la fila."""
        icon = QIcon()
        for mode in (QIcon.Mode.Normal, QIcon.Mode.Selected, QIcon.Mode.Active):
            icon.addPixmap(pixmap, mode)
        return icon

    def _sidebar_icon(self, url: str) -> QIcon:
        dpr = self.devicePixelRatioF()
        pixmap = self.art.pixmap(url, 32, theme.R_THUMB, dpr) if url else None
        return self._fixed_icon(pixmap or placeholder_pixmap(32, theme.R_THUMB, dpr))

    def _on_sidebar_changed(self, item: Optional[QListWidgetItem], _prev) -> None:
        if item is None or not item.data(ROLE_ID):
            return
        if item.data(ROLE_ID) == NEW_LIST_KEY:
            # No es una vista: la selección vuelve a donde estaba y se abre el diálogo.
            self._select_in_sidebar(self._view_key or HOME_KEY)
            QTimer.singleShot(0, self._create_local_playlist)
            return
        url = item.data(ROLE_ART)
        if url:
            # La miniatura de la fila vuela hasta la cabecera de la lista.
            row = self.sidebar.visualItemRect(item)
            icon = QRectF(row.left() + 8, row.center().y() - 15.5, 32, 32)
            note_launch(self.sidebar.viewport(), icon, self.art.image(url), 5)
        self._show_view(item.data(ROLE_ID))

    # ------------------------------------------------------- barra contraíble
    def _set_sidebar_collapsed(self, collapsed: bool) -> None:
        """Con el ratón la barra se pliega o se despliega deslizándose; con
        Ctrl+B cambia de golpe (un atajo se repite demasiado para esperar)."""
        if collapsed == self._side_collapsed:
            return
        side = self.side_panel
        if collapsed:
            self._side_width = max(self.SIDE_MIN, side.width())
        self._side_collapsed = collapsed
        self.db.set_setting("sidebar_collapsed", "1" if collapsed else "0")
        self.side_toggle.set_collapsed(collapsed)
        self.side_toggle.setToolTip(
            ("Desplegar" if collapsed else "Contraer") + " la barra lateral (Ctrl+B)"
        )
        target = self.SIDE_COLLAPSED if collapsed else self._side_width

        def settle() -> None:
            side.setMinimumWidth(self.SIDE_COLLAPSED if collapsed else self.SIDE_MIN)
            side.setMaximumWidth(self.SIDE_COLLAPSED if collapsed else self.SIDE_MAX)
            self._set_pane_width(side, target)
            if collapsed:
                # Los nombres se quitan al final: mientras se pliega se van cortando.
                self.footer.set_collapsed(True)
                if self.sidebar.count():
                    self._reload_sidebar()

        if not collapsed:
            # Al desplegarse los nombres están desde el principio y van asomando.
            self.footer.set_collapsed(False)
            if self.sidebar.count():
                self._reload_sidebar()
        if not self._animate_panes():
            settle()
            return
        side.setMinimumWidth(self.SIDE_COLLAPSED)
        side.setMaximumWidth(self.SIDE_MAX)
        self._run_slide(side, side.width(), target, PANE_CLOSE_MS if collapsed else PANE_OPEN_MS, settle)

    # ------------------------------------------------------- paneles que se deslizan
    def _animate_panes(self) -> bool:
        """Con el ratón los paneles se deslizan; con el teclado, o con las
        animaciones de Windows apagadas, cambian de golpe."""
        return self.isVisible() and not focus.keyboard() and not reduced_motion()

    def _set_pane_width(self, pane: QWidget, width: int) -> None:
        """Da ``width`` al panel; lo que gana o pierde lo absorbe el contenido."""
        sizes = self.splitter.sizes()
        index = self.splitter.indexOf(pane)
        if index < 0 or len(sizes) < 2:
            return
        sizes[1] += sizes[index] - width
        sizes[index] = width
        self.splitter.setSizes(sizes)

    def _run_slide(self, pane: QWidget, start: int, end: int, ms: int, done) -> None:
        previous = self._slides.pop(pane, None)
        if previous is not None:
            previous.stop()

        def step(value, slide=None) -> None:
            t = float(value)
            self._set_pane_width(pane, round(start + (end - start) * t))
            if t >= 1.0 and self._slides.get(pane) is tween:
                del self._slides[pane]
                done()

        tween = Tween(self, 0.0, step, ms=ms, curve=EASE_DRAWER, movement=True)
        self._slides[pane] = tween
        step(0.0)
        tween.to(1.0)

    def _pane_min(self, pane: QWidget) -> int:
        return 270 if pane is self.lyrics else self.NOW_MIN

    def _slide_pane(self, pane: QWidget, opening: bool) -> None:
        """Abre o cierra un panel derecho (letra o «Ahora suena») deslizándolo
        desde el borde, en lugar de que aparezca o desaparezca de golpe."""
        natural = self._pane_min(pane)
        sliding = self._slides.pop(pane, None)
        if sliding is not None:
            sliding.stop()                    # se retoma desde el ancho actual
        if not opening:
            if pane.isHidden():
                return
            if sliding is None and pane.width() >= natural:
                self._pane_widths[pane] = pane.width()
            if not self._animate_panes():
                pane.setMinimumWidth(natural)
                pane.hide()
                return
            pane.setMinimumWidth(1)

            def closed() -> None:
                pane.hide()
                pane.setMinimumWidth(natural)

            self._run_slide(pane, pane.width(), 0, PANE_CLOSE_MS, closed)
            return
        sizes = self.splitter.sizes()
        content = sizes[1] if len(sizes) > 1 else 0
        start = 0 if pane.isHidden() else pane.width()
        # Nunca más ancho de lo que el contenido puede ceder sin bajar de su mínimo.
        room = content + start - self.CONTENT_MIN
        target = min(self._pane_widths.get(pane, 340), max(natural, room))
        if not self._animate_panes():
            pane.setMinimumWidth(natural)
            pane.show()
            self._set_pane_width(pane, target)
            return
        pane.setMinimumWidth(1)
        if pane.isHidden():
            pane.show()
            self._set_pane_width(pane, 0)
        self._run_slide(pane, start, target, PANE_OPEN_MS, lambda: pane.setMinimumWidth(natural))

    def _swap_panes(self, old: QWidget, new: QWidget, old_action: QAction) -> None:
        """La letra y «Ahora suena» comparten sitio: uno sustituye al otro en
        el acto, con el mismo ancho, sin cerrar y volver a abrir."""
        width = old.width()
        sliding = self._slides.pop(old, None)
        if sliding is not None:
            sliding.stop()
        self._swapping = True
        old_action.setChecked(False)
        self._swapping = False
        old.hide()
        old.setMinimumWidth(self._pane_min(old))
        new.setMinimumWidth(self._pane_min(new))
        new.show()
        self._set_pane_width(new, max(self._pane_min(new), width))

    def _on_sidebar_menu(self, point) -> None:
        item = self.sidebar.itemAt(point)
        key = item.data(ROLE_ID) if item is not None else None
        if not is_library(key) or key == NEW_LIST_KEY:
            return
        menu = rounded_menu(QMenu(self))
        menu.addAction("Reproducir", lambda: self._play_library(key))
        if is_local_playlist(key):
            menu.addSeparator()
            self._add_local_actions(menu, key)
        else:
            menu.addAction("Sincronizar esta lista", lambda: self._start_sync(only_playlist=key))
        menu.exec(self.sidebar.viewport().mapToGlobal(point))

    # ----------------------------------------------------------- listas locales
    def _ask_text(self, title: str, label: str, text: str = "", ok: str = "Aceptar") -> Optional[str]:
        dialog = QInputDialog(self)
        dialog.setWindowTitle(title)
        dialog.setLabelText(label)
        dialog.setTextValue(text)
        dialog.setOkButtonText(ok)
        dialog.setCancelButtonText("Cancelar")
        dialog.resize(380, dialog.sizeHint().height())
        if dialog.exec() != QInputDialog.DialogCode.Accepted:
            return None
        return dialog.textValue().strip() or None

    def _confirm(self, title: str, text: str, action: str) -> bool:
        box = QMessageBox(QMessageBox.Icon.NoIcon, title, text, parent=self)
        yes = box.addButton(action, QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return box.clickedButton() is yes

    def _create_local_playlist(self, tracks: tuple = (), suggested: str = "") -> Optional[str]:
        """Pregunta el nombre y crea la lista. Devuelve su id (o ``None``)."""
        name = self._ask_text(
            "Nueva lista",
            "Nombre de la lista. Se guarda solo en esta app, no en tu cuenta.",
            suggested, ok="Crear",
        )
        if not name:
            return None
        playlist_id = self.db.create_local_playlist(name, tracks)
        self._reload_sidebar()
        if tracks:
            self._notice(f"Lista «{name}» creada con {plural(len(tracks), 'canción', 'canciones')}.")
        else:
            self._navigate(playlist_id)
        return playlist_id

    def _add_local_actions(self, menu: QMenu, playlist_id: str) -> None:
        menu.addAction("Cambiar el nombre…", lambda: self._rename_local_playlist(playlist_id))
        menu.addAction("Eliminar la lista…", lambda: self._delete_local_playlist(playlist_id))

    def _rename_local_playlist(self, playlist_id: str) -> None:
        playlist = self.db.get_playlist(playlist_id)
        if playlist is None:
            return
        name = self._ask_text("Cambiar el nombre", "Nombre de la lista:", playlist.title, ok="Guardar")
        if name and name != playlist.title:
            self.db.rename_playlist(playlist_id, name)
            self._reload_sidebar()

    def _delete_local_playlist(self, playlist_id: str) -> None:
        playlist = self.db.get_playlist(playlist_id)
        if playlist is None:
            return
        if not self._confirm(
            "Eliminar la lista",
            f"«{playlist.title}» se eliminará de esta app. Sus canciones no se borran de "
            "ningún otro sitio. No se puede deshacer.",
            "Eliminar",
        ):
            return
        self.db.delete_local_playlist(playlist_id)
        self._reload_sidebar()            # si era la vista actual, vuelve al inicio
        self._notice(f"Lista «{playlist.title}» eliminada.")

    def _add_to_local_playlist(self, playlist_id: str, track: Track) -> None:
        playlist = self.db.get_playlist(playlist_id)
        if playlist is None:
            return
        if self.db.add_to_local_playlist(playlist_id, track):
            self._notice(f"«{track.title}» añadida a «{playlist.title}».")
            self._after_local_change(playlist_id)
        else:
            self._notice(f"«{track.title}» ya estaba en «{playlist.title}».")

    def _remove_from_local_playlist(self, playlist_id: str, track: Track) -> None:
        if self.db.remove_from_local_playlist(playlist_id, track.video_id):
            self._notice(f"«{track.title}» quitada de la lista.")
            self._after_local_change(playlist_id)

    def _after_local_change(self, playlist_id: str) -> None:
        """La lista cambió: se refresca donde se esté viendo (y su carátula)."""
        scroll = self.table.verticalScrollBar().value()
        self._reload_sidebar()
        if self._view_key == playlist_id:
            self.table.verticalScrollBar().setValue(scroll)

    def _on_band_extra(self) -> None:
        """Botón de la cabecera: opciones de una lista local o guardar una ajena."""
        key = self._view_key
        if is_local_playlist(key):
            menu = rounded_menu(QMenu(self))
            self._add_local_actions(menu, key)
            button = self.band.extra_btn
            menu.popup(button.mapToGlobal(QPoint(0, button.height() + 4)))
        elif is_remote(key):
            data = self._remote.get(key[len(REMOTE_PREFIX):])
            if data is not None and data.tracks:
                self._create_local_playlist(tuple(data.tracks), suggested=data.title)
        elif is_artist(key):
            self._play_artist_mix(shuffle=False)

    # ------------------------------------------------------- carátulas y tinta
    def _row_art_url(self, track: Track) -> str:
        return track_art_url(track.video_id, track.thumb_url, 96)

    def _on_art_loaded(self, url: str) -> None:
        self.table.viewport().update()
        if url == self._band_art:
            self._apply_band_tint()
        if url == self._now_art:
            self._apply_ink()
        for row in range(self.sidebar.count()):
            item = self.sidebar.item(row)
            if item.data(ROLE_ART) == url:
                item.setIcon(self._sidebar_icon(url))

    def _apply_band_tint(self) -> None:
        tint = self.art.tint(self._band_art) if self._band_art else None
        self.band.set_tint(tint or neutral_tint())

    def _apply_ink(self) -> None:
        """La tinta de la carátula que suena tiñe progreso, fila actual y letra."""
        tint = self.art.tint(self._now_art) if self._now_art else None
        self._ink.to(QColor((tint or neutral_tint()).ink))
        self.now_panel.set_tint(tint)

    def _paint_ink(self, color) -> None:
        ink = QColor(color)
        self.delegate.ink = ink
        for widget in (self.seek_slider, self.volume_slider, self.play_btn, self.shuffle_btn,
                       self.repeat_btn, self.lyrics):
            widget.set_ink(ink)
        self.table.viewport().update()

    def _playlist_facts(self, playlist, tracks: list[Track]) -> list[str]:
        facts = [plural(len(tracks), "canción", "canciones")]
        total = playlist.duration_s or sum(t.duration_s or 0 for t in tracks)
        if format_total(total):
            facts.append(format_total(total))
        if playlist.author:
            facts.append(f"De {playlist.author}")
        if playlist.year:
            facts.append(playlist.year)
        if playlist.local:
            facts.append("Lista local")          # vive solo en esta app: no hay nada que sincronizar
            return facts
        facts.append(f"Sincronizada {format_ago(playlist.last_full_sync)}")
        if playlist.last_error:
            facts.append(f"Error: {playlist.last_error[:60]}")
        return facts

    def _show_view(self, key: str, force: bool = False) -> None:
        if key == self._view_key and not force:
            return
        previous = self._view_key
        if previous and previous != key and not self._going_back:
            self._history.append(previous)
            del self._history[:-40]
        self._view_key = key
        home = key == HOME_KEY
        artist = is_artist(key)
        if key != SEARCH_KEY and self.search_edit.hasFocus():
            # El campo de búsqueda va a ocultarse: el foco pasa a la vista, no al azar.
            # (En el artista no a la barra lateral: sin fila marcada, el foco
            # elegiría la primera, el inicio.)
            target = self.sidebar if home else self.play_all_btn if artist else self.table
            target.setFocus(Qt.FocusReason.OtherFocusReason)
        self.band.setVisible(not home)
        self.body_box.setVisible(not home and not artist)
        self.artist_view.setVisible(artist)
        self.home.setVisible(home)
        self.scope_bar.setVisible(key == SEARCH_KEY)
        if key != SEARCH_KEY:
            self.artist_strip.hide()
            self.search_cards.hide()
        if home:
            self._band_art = ""
            self._refresh_home()
            return
        if is_remote(key):
            self._show_remote(key[len(REMOTE_PREFIX):])
        elif artist:
            self._show_artist(key[len(ARTIST_PREFIX):])
        elif key == QUEUE_KEY:
            tracks, pos = self.player.queue_snapshot()
            self.model.set_tracks(tracks)
            current = self.player.current_track()
            ahead = max(0, len(tracks) - pos - 1) if pos >= 0 else 0
            facts = [plural(len(tracks), "pista", "pistas"), f"{ahead} por delante"] if tracks else []
            self._band_art = track_art_url(current.video_id, current.thumb_url, 400) if current else ""
            self.band.set_content(
                "Cola de reproducción",
                "Lo que suena y lo que viene después. Un doble clic salta a esa pista.",
                facts, self._band_art, show_sync=False,
            )
            self._set_empty("" if tracks else "La cola está vacía", "Reproduce una lista o una búsqueda.",
                            glyph="queue")
        elif key == SEARCH_KEY:
            # La búsqueda se conserva al salir y volver: texto, resultados y nota.
            self.model.set_tracks(self._search_tracks)
            self._band_art = ""
            self.band.set_content(
                "Buscar", "", [self._scope_note()], "", show_search=True, show_sync=False, show_cover=False
            )
            self._render_search_cards()
            self._sync_search_empty()
            if not self.search_edit.text().strip():
                self._show_recent_searches()
            self.search_edit.setFocus()
        else:
            playlist = self.db.get_playlist(key)
            tracks = self.db.playlist_tracks(key)
            self.model.set_tracks(tracks)
            if playlist is not None:
                description = playlist.description.strip()
                if len(description) > 230:
                    description = description[:228].rstrip() + "…"
                self._band_art = sized_url(self.db.playlist_cover(playlist), 400)
                self.band.set_content(
                    playlist.title, description, self._playlist_facts(playlist, tracks), self._band_art,
                    show_sync=not playlist.local,
                    extra=("Opciones", "more") if playlist.local else None,
                )
            else:
                self._band_art = ""
                self.band.set_content("Lista", "", [], "")
            if playlist is not None and playlist.local:
                self._set_empty("" if tracks else "Esta lista está vacía",
                                "Añade canciones con el botón derecho sobre cualquier pista: "
                                "«Añadir a una lista».")
            else:
                self._set_empty("" if tracks else "Esta lista aún no tiene pistas guardadas",
                                "Sincronízala para verlas.")
        self._apply_band_tint()
        current = self.player.current_track()
        self.model.set_current(current.video_id if current else None)
        self.table.scrollToTop()
        self._update_buttons()
        self._fly_to_band()

    def _fly_to_band(self) -> None:
        """Si la vista se abrió con un clic en una carátula (una tarjeta, una
        lista de la barra lateral), una copia viaja desde allí hasta la
        cabecera y la de la banda se posa al llegar."""
        launch = take_launch()
        cover = self.band.cover
        if launch is None or reduced_motion() or not self.band.isVisible() or not cover.isVisible():
            return
        if self._band_flight is not None:
            self._band_flight.land()
        where, image, radius, _when = launch
        host = self.centralWidget()
        start = QRectF(QPointF(host.mapFromGlobal(where.topLeft().toPoint())), where.size())

        def end() -> QRectF:
            return cover.art_rect().translated(QPointF(cover.mapTo(host, QPoint(0, 0))))

        def landed() -> None:
            cover.set_held(False)
            if self._band_flight is flight:
                self._band_flight = None

        cover.set_held(True)
        flight = CoverFlight(host, image, lambda: start, end, (radius, cover.corner()), FLIGHT_MS, landed)
        self._band_flight = flight
        flight.fly()

    def _set_search_note(self, text: str) -> None:
        self._search_note = text
        self._refresh_search_band()

    def _refresh_search_band(self) -> None:
        if self._view_key == SEARCH_KEY:
            self.band.set_facts([self._scope_note()])

    def _cards_shown(self) -> bool:
        """¿La búsqueda enseña tarjetas (listas o artistas) en vez de canciones?"""
        return self._view_key == SEARCH_KEY and self._search_scope != SONGS

    def _set_empty(self, title: str, hint: str = "", glyph: str = "music") -> None:
        if title:
            self.empty.set_content(glyph, title, hint)
        self.empty.setVisible(bool(title))
        self.table.setVisible(not title and not self._cards_shown())
        self.search_cards.setVisible(not title and self._cards_shown())

    def _update_buttons(self) -> None:
        has_rows = self.model.rowCount() > 0
        # En la búsqueda los botones no existen hasta que hay algo que reproducir
        # (y con listas o artistas a la vista no hay una lista que reproducir).
        searching = self._view_key == SEARCH_KEY
        if self._cards_shown():
            has_rows = False
        self.play_all_btn.setVisible(has_rows or not searching)
        self.shuffle_all_btn.setVisible(has_rows or not searching)
        self.play_all_btn.setEnabled(has_rows)
        self.shuffle_all_btn.setEnabled(has_rows)
        self.sync_list_btn.setEnabled(not self._syncing and paths.browser_json().is_file())

    def _focus_search(self) -> None:
        self._navigate(SEARCH_KEY)
        self.search_edit.setFocus()
        self.search_edit.selectAll()

    # ------------------------------------------------------------------ inicio
    def _refresh_home(self) -> None:
        """Tus listas salen de SQLite al instante; las recomendaciones, de la
        caché, y se renuevan en segundo plano si ya tienen unas horas."""
        playlists = self.db.main_playlists()
        specs = [
            (
                p.title,
                plural(p.stored_tracks, "canción", "canciones") if p.stored_tracks
                else ("Lista local vacía" if p.local else "Sin sincronizar"),
                sized_url(self.db.playlist_cover(p), 400),
                p.playlist_id,
            )
            for p in playlists
        ]
        facts, empty_text = [], ""
        if playlists:
            facts = [
                plural(len(playlists), "lista", "listas"),
                plural(self.db.library_track_count(), "canción", "canciones"),
                f"Sincronizada {format_ago(self.db.get_float('last_sync_check') or None)}",
            ]
        elif not paths.browser_json().is_file():
            empty_text = (
                "Aquí aparecerán tus listas cuando conectes la cuenta. Mientras tanto puedes "
                "buscar y reproducir cualquier canción sin iniciar sesión."
            )
        else:
            empty_text = "Tu biblioteca aún no se ha sincronizado. Pulsa F5 para traer tus listas."
        self.home.set_library(specs, facts, empty_text)
        self._load_recommendations()

    def _load_recommendations(self, force: bool = False) -> None:
        cached = discover.load_shelves(self.db)
        if cached is not None and not self._recs_shown:
            self.home.set_shelves(cached[2])
            self._recs_shown = True
        if self._recs_loading:
            return
        if not force:
            if not discover.shelves_stale(self.db, cached):
                self.home.set_status("", "refresh" if self.home.shelf_count() else "")
                return
            if time.time() < self._recs_retry_after:
                return                    # acaba de fallar: no se insiste en cada visita
        self._recs_loading = True
        self._recs_token += 1
        token = self._recs_token
        # Sin nada en pantalla las filas se enseñan según llegan; si ya hay
        # recomendaciones, se cambian todas juntas al terminar.
        streaming = self.home.shelf_count() == 0
        self.home.set_status(
            "Buscando listas de la comunidad parecidas a tu música…" if streaming
            else "Buscando otras listas…"
        )

        def work():
            try:
                on_shelf = (lambda shelf: self.bridge.shelf.emit(token, shelf)) if streaming else None
                kind, shelves = discover.build_recommendations(self.db, on_shelf=on_shelf)
                if shelves:
                    discover.save_shelves(self.db, kind, shelves)
                return shelves
            finally:
                self.db.close_thread_connection()

        run_async(work, lambda shelves, error: self._on_recommendations(token, streaming, shelves, error))

    def _on_shelf(self, token: int, shelf) -> None:
        if token == self._recs_token:
            self.home.add_shelf(shelf)

    def _on_recommendations(self, token: int, streaming: bool, shelves, error) -> None:
        if token != self._recs_token:
            return
        self._recs_loading = False
        if error is not None:
            log.warning("recomendaciones del inicio: %s", error)
            self._recs_retry_after = time.time() + 120
            if self.home.shelf_count():
                self.home.set_status("No se pudieron renovar las recomendaciones.", "retry")
            else:
                self.home.set_status(
                    "No se pudieron cargar las recomendaciones. Comprueba la conexión.", "retry"
                )
            return
        self._recs_shown = True
        if shelves and not streaming:
            self.home.set_shelves(shelves)
        if self.home.shelf_count():
            self.home.set_status("", "refresh")
        else:
            self._recs_retry_after = time.time() + 600
            self.home.set_status("Por ahora no hay recomendaciones que enseñar.", "retry")

    def _play_library(self, playlist_id: str) -> None:
        """Disco de una tarjeta de «Tus listas»: reproduce sin abrir la lista."""
        tracks = self.db.playlist_tracks(playlist_id)
        if not tracks:
            self._notice("Esa lista aún no tiene pistas guardadas: sincronízala primero.")
            return
        if self._missing_binaries_notice():
            return
        self.db.note_playlist_played(playlist_id)
        self.player.play_tracks(tracks, random.randrange(len(tracks)) if self.player.shuffle else 0)

    # ------------------------------------------------------------ listas ajenas
    def _missing_binaries_notice(self) -> bool:
        if bootstrap.missing_binaries():
            self._notice("Faltan binarios: abre el menú (⋯) y elige «Descargar binarios que faltan».")
            return True
        return False

    def _open_remote(self, card: PlaylistCard) -> None:
        self._remote_cards[card.playlist_id] = card
        self._remote_failed.discard(card.playlist_id)    # abrirla de nuevo es reintentar
        self._navigate(REMOTE_PREFIX + card.playlist_id)

    def _play_remote(self, card: PlaylistCard) -> None:
        """Disco de una recomendación: la descarga (si hace falta) y la reproduce."""
        if self._missing_binaries_notice():
            return
        self._remote_cards[card.playlist_id] = card

        def play(data: Optional[RemotePlaylist]) -> None:
            if data is None:
                return
            if not data.tracks:
                self._notice(f"«{data.title}» no tiene canciones que se puedan reproducir.")
                return
            for track in data.tracks:
                self.db.upsert_track(track)
            start = random.randrange(len(data.tracks)) if self.player.shuffle else 0
            self.player.play_tracks(data.tracks, start)

        if card.playlist_id not in self._remote:
            self._notice(f"Cargando «{card.title}»…")
            self._remote_failed.discard(card.playlist_id)
        self._fetch_remote(card.playlist_id, play)

    def _fetch_remote(self, playlist_id: str, then: Optional[Callable] = None) -> None:
        """Descarga una lista ajena una sola vez por sesión. ``then`` recibe la
        lista (o ``None`` si falló)."""
        data = self._remote.get(playlist_id)
        if data is not None:
            if then is not None:
                then(data)
            return
        waiting = self._remote_waiting.get(playlist_id)
        if waiting is not None:          # ya está en camino: solo se apunta el interesado
            if then is not None:
                waiting.append(then)
            return
        self._remote_waiting[playlist_id] = [then] if then is not None else []

        def done(result, error) -> None:
            callbacks = self._remote_waiting.pop(playlist_id, [])
            if error is not None or result is None:
                log.warning("no se pudo abrir la lista %s: %s", playlist_id, error)
                self._remote_failed.add(playlist_id)
                self._notice("No se pudo abrir la lista. Comprueba la conexión.", "error")
            else:
                self._remote[playlist_id] = result
            if self._view_key == REMOTE_PREFIX + playlist_id:
                self._show_view(self._view_key, force=True)
            for callback in callbacks:
                callback(result if error is None else None)

        run_async(lambda: discover.fetch_playlist(playlist_id), done)

    def _show_remote(self, playlist_id: str) -> None:
        data = self._remote.get(playlist_id)
        card = self._remote_cards.get(playlist_id)
        if data is None:
            loading = playlist_id in self._remote_waiting
            self.model.set_tracks([])
            self._band_art = sized_url(card.thumb_url, 400) if card else ""
            self.band.set_content(
                card.title if card else "Lista", "", [card.note] if card and card.note else [],
                self._band_art, show_sync=False, show_back=True,
            )
            if playlist_id in self._remote_failed and not loading:
                self._set_empty("No se pudo abrir la lista",
                                "Comprueba la conexión y vuelve a abrirla desde el inicio.")
            else:
                self._set_empty("Cargando la lista…")
                self._fetch_remote(playlist_id)
            return
        self.model.set_tracks(data.tracks)
        description = data.description.strip()
        if len(description) > 230:
            description = description[:228].rstrip() + "…"
        facts = [plural(len(data.tracks), "canción", "canciones")]
        total = data.duration_s or sum(t.duration_s or 0 for t in data.tracks)
        if format_total(total):
            facts.append(format_total(total))
        if data.author:
            facts.append(f"De {data.author}")
        if data.year:
            facts.append(data.year)
        self._band_art = sized_url(data.thumb_url or (card.thumb_url if card else ""), 400)
        self.band.set_content(data.title, description, facts, self._band_art,
                              show_sync=False, show_back=True,
                              extra=("Guardar como lista local", "save") if data.tracks else None)
        self._set_empty("" if data.tracks else "Esta lista no tiene canciones que se puedan reproducir")

    # ---------------------------------------------------------------- artistas
    def _open_artist(self, card: ArtistCard) -> None:
        self._artist_cards[card.channel_id] = card
        self._artist_failed.discard(card.channel_id)     # abrirlo de nuevo es reintentar
        self._navigate(ARTIST_PREFIX + card.channel_id)

    def _open_artist_named(self, channel_id: str, name: str) -> None:
        """Abre la página de un artista. Sin su canal (pistas de la biblioteca,
        que no lo guardan) se busca primero por el nombre: una petición más."""
        if channel_id:
            known = self._artist_cards.get(channel_id)
            self._open_artist(known or ArtistCard(channel_id, name))
            return
        self._notice(f"Buscando a {name}…")

        def done(card, error) -> None:
            if error is not None:
                log.warning("artista %s: %s", name, error)
                self._notice("No se pudo buscar al artista. Comprueba la conexión.", "error")
            elif card is None:
                self._notice(f"YouTube Music no tiene una página de artista para «{name}».")
            else:
                self._open_artist(card)

        run_async(lambda: discover.find_artist(name), done)

    def _open_artist_from_now(self, channel_id: str, name: str) -> None:
        """«Ver artista» del panel: la página ocupa todo el contenido y repite lo
        del panel en grande, así que el panel se cierra para dejarle sitio."""
        self.now_action.setChecked(False)
        self._open_artist_named(channel_id, name)

    def _retry_artist(self) -> None:
        if is_artist(self._view_key):
            self._artist_failed.discard(self._view_key[len(ARTIST_PREFIX):])
            self._show_view(self._view_key, force=True)

    def _show_artist(self, channel_id: str) -> None:
        page = self._artists.get(channel_id)
        if page is None:
            page = discover.load_artist_page(self.db, channel_id)
            if page is not None:
                self._artists[channel_id] = page
        card = self._artist_cards.get(channel_id)
        if page is None:
            self.model.set_tracks([])
            self._band_art = sized_url(card.thumb_url, 400) if card else ""
            self.band.set_content(card.name if card else "Artista", "", [], self._band_art,
                                  show_sync=False, show_back=True, round_cover=True)
            if channel_id in self._artist_failed and channel_id not in self._artist_waiting:
                self.artist_view.set_error()
            else:
                self.artist_view.set_loading()
                self._fetch_artist(channel_id)
            return
        self._artist_cards.setdefault(channel_id, ArtistCard(channel_id, page.name, "", page.thumb_url))
        # Play y Aleatorio de la banda trabajan sobre la tabla: se carga con sus
        # canciones populares aunque la tabla no se vea.
        self.model.set_tracks(page.top_songs)
        # En la banda, solo la primera frase: la historia entera está más abajo.
        first = page.bio.split("\n")[0].strip() if page.bio else ""
        first = re.split(r"(?<=[.!?])\s", first, maxsplit=1)[0]
        if len(first) > 170:
            first = first[:168].rstrip() + "…"
        facts = [f"{value} {label[0].lower()}{label[1:]}" for label, value in page.stats]
        self._band_art = sized_url(page.thumb_url, 400)
        self.band.set_content(page.name, first, facts, self._band_art, show_sync=False, show_back=True,
                              extra=("Radio", "music") if page.radio_id or page.top_songs else None,
                              round_cover=True)
        self.band.extra_btn.setToolTip(f"Radio de {page.name}: sus canciones y otras parecidas")
        fresh, self._artist_fresh = self._artist_fresh == channel_id, None
        self.artist_view.set_page(page, animate=fresh)

    def _fetch_artist(self, channel_id: str) -> None:
        if channel_id in self._artist_waiting:
            return
        self._artist_waiting.add(channel_id)

        def work():
            try:
                page = discover.artist_page(channel_id)
                discover.save_artist_page(self.db, page)
                return page
            finally:
                self.db.close_thread_connection()

        def done(page, error) -> None:
            self._artist_waiting.discard(channel_id)
            if error is not None or page is None:
                log.warning("no se pudo abrir el artista %s: %s", channel_id, error)
                self._artist_failed.add(channel_id)
            else:
                self._artists[channel_id] = page
                self._artist_fresh = channel_id
            if self._view_key == ARTIST_PREFIX + channel_id:
                self._show_view(self._view_key, force=True)

        run_async(work, done)

    def _open_artist_songs(self) -> None:
        page = self.artist_view.page()
        if page is not None and page.songs_playlist:
            self._open_remote(PlaylistCard(page.songs_playlist, f"Canciones de {page.name}",
                                           f"De {page.name}", page.thumb_url))

    def _play_list(self, tracks: list, index: int) -> None:
        """Una pista de la página del artista: suena esa y siguen las de su apartado."""
        if not tracks or not (0 <= index < len(tracks)) or self._missing_binaries_notice():
            return
        for track in tracks:
            self.db.upsert_track(track)
        self.player.play_tracks(tracks, index)

    def _play_artist_mix(self, shuffle: bool) -> None:
        """Radio del artista (o su mezcla aleatoria), pedida a YouTube Music."""
        page = self.artist_view.page() if is_artist(self._view_key) else None
        if page is None or self._missing_binaries_notice():
            return
        self._notice(f"Cargando {'la mezcla' if shuffle else 'la radio'} de {page.name}…")

        def done(tracks, error) -> None:
            tracks = list(tracks or []) if error is None else []
            if error is not None:
                log.warning("radio de %s: %s", page.name, error)
            if not tracks:
                tracks = list(page.top_songs)
                if shuffle:
                    random.shuffle(tracks)
            if not tracks:
                self._notice("No se pudo cargar la radio. Comprueba la conexión.", "error")
                return
            self._play_list(tracks, 0)

        run_async(lambda: discover.artist_mix(page, shuffle=shuffle), done)

    # ------------------------------------------------------------ reproducción
    def _play_from(self, row: int) -> None:
        tracks = self.model.tracks()
        if not tracks or row < 0:
            return
        if bootstrap.missing_binaries():
            self._notice("Faltan binarios: abre el menú (⋯) y elige «Descargar binarios que faltan».")
            return
        if self._view_key == QUEUE_KEY:
            self.player.jump_to(row)
            return
        if self._view_key == SEARCH_KEY or is_remote(self._view_key) or is_artist(self._view_key):
            for track in tracks:
                self.db.upsert_track(track)
        if self._view_key == SEARCH_KEY:
            # Reproducir un resultado confirma la búsqueda: pasa a las recientes.
            discover.remember_search(self.db, self._searched_query)
        elif is_library(self._view_key):
            self.db.note_playlist_played(self._view_key)
        self.player.play_tracks(tracks, row)

    def _on_thumb_clicked(self, row: int) -> None:
        """Clic en la carátula de una fila: reproduce, o pausa si ya es la que suena."""
        if self.model.is_current(row) and self.player.current_track() is not None:
            self.player.toggle_pause()
        else:
            self._play_from(row)

    def _play_shuffled(self) -> None:
        if is_artist(self._view_key):
            # Su mezcla de YouTube Music: mucho más que las cinco populares.
            self._play_artist_mix(shuffle=True)
            return
        tracks = self.model.tracks()
        if not tracks:
            return
        if not self.player.shuffle:
            self.player.set_shuffle(True)
        self._play_from(random.randrange(len(tracks)))

    def _on_current_changed(self, track: Optional[Track]) -> None:
        if track is None:
            self.now_title.setText("Nada en reproducción")
            self.now_title.set_color(theme.HUMO)
            self.now_artist.setText("")
            self.now_cover.set_url("")
            self._now_art = ""
            self.model.set_current(None)
            self._apply_ink()
            if self.now_panel.isVisible():
                self._load_now(None)
            self.setWindowTitle("YT Music Nativo")
            return
        self.now_title.setText(track.title)
        self.now_title.set_color(theme.PAPEL)
        self.now_artist.setText(track.artists)
        self._now_art = track_art_url(track.video_id, track.thumb_url, 160)
        self.now_cover.set_url(self._now_art)
        self._apply_ink()
        self.model.set_current(track.video_id)
        self.setWindowTitle(f"{track.title} — YT Music Nativo")
        if self._view_key == QUEUE_KEY:
            self._show_view(QUEUE_KEY, force=True)
        if self.lyrics.isVisible():
            self._load_lyrics(track)
        if self.now_panel.isVisible():
            self._load_now(track)

    def _on_player_state(self, state: str) -> None:
        # El icono cambia sin transición: Espacio se pulsa muchas veces al día.
        self.play_btn.set_playing(state in (PLAYING, LOADING))
        self.play_btn.set_loading(state == LOADING)
        self.table.set_playing(state == PLAYING)
        has_track = self.player.current_track() is not None
        self.play_btn.setEnabled(has_track)
        self.next_btn.setEnabled(has_track)
        self.prev_btn.setEnabled(has_track)
        self.seek_slider.setEnabled(state in (PLAYING, PAUSED))

    def _on_position(self, pos: float, duration: float) -> None:
        self._duration = duration
        self.dur_label.setText(format_time(duration) if duration > 0 else "–:––")
        if self._seeking:
            return
        self.pos_label.setText(format_time(pos))
        self.seek_slider.setValue(int(pos / duration * 1000) if duration > 0 else 0)
        self._highlight_lyric(pos)

    def _on_seek_pressed(self) -> None:
        self._seeking = True

    def _on_seek_released(self) -> None:
        self._seeking = False
        if self._duration > 0:
            self.player.seek(self.seek_slider.value() / 1000 * self._duration)

    def _nudge_seek(self, direction: int) -> None:
        """Flechas con el foco en la barra de progreso: 5 segundos atrás o adelante."""
        if self._duration > 0 and self.seek_slider.isEnabled():
            position = self.seek_slider.value() / 1000 * self._duration + 5 * direction
            self.player.seek(max(0.0, min(self._duration - 1, position)))

    def _on_volume(self, value: int) -> None:
        self.player.set_volume(value)
        self.db.set_setting("volume", value)
        self._sync_volume_glyph(value)

    def _sync_volume_glyph(self, value: int) -> None:
        name = "mute" if value == 0 else "volume_low" if value < 34 else "volume_mid" if value < 67 else "volume"
        self.mute_btn.set_glyph(name)
        self.mute_btn.setToolTip("Restaurar el volumen" if value == 0 else "Silenciar")

    def _toggle_mute(self) -> None:
        if self.volume_slider.value() > 0:
            self._pre_mute = self.volume_slider.value()
            self.volume_slider.setValue(0)
        else:
            self.volume_slider.setValue(self._pre_mute or 60)

    def _on_modes_changed(self, shuffle: bool, repeat: str) -> None:
        self.shuffle_btn.setChecked(shuffle)
        self.repeat_btn.setChecked(repeat != REPEAT_OFF)
        self.repeat_btn.set_glyph("repeat_one" if repeat == REPEAT_ONE else "repeat_all")
        label = {REPEAT_OFF: "desactivado", REPEAT_ALL: "toda la cola", REPEAT_ONE: "una pista"}[repeat]
        self.repeat_btn.setToolTip(f"Repetir: {label}")

    def _on_track_flagged(self, video_id: str) -> None:
        track = self.db.get_track(video_id)
        if track is not None:
            self.model.update_track(track)

    def _on_blocked(self, until: float) -> None:
        self._blocked_until = until
        self._on_tick()

    # ------------------------------------------------------------ cola y menú
    def _on_queue_changed(self) -> None:
        if self._view_key == QUEUE_KEY:
            self._show_view(QUEUE_KEY, force=True)

    def _on_table_menu(self, point) -> None:
        index = self.table.indexAt(point)
        tracks = self.model.tracks()
        if not index.isValid() or not (0 <= index.row() < len(tracks)):
            return
        row, track = index.row(), tracks[index.row()]
        self._track_menu(track, row).exec(self.table.viewport().mapToGlobal(point))

    def _track_menu(self, track: Track, row: Optional[int] = None) -> QMenu:
        """Menú de una pista. ``row`` es su fila en la tabla; sin fila (música
        parecida del panel) «Reproducir» la pone a sonar a ella sola."""
        menu = rounded_menu(QMenu(self))
        if row is not None:
            menu.addAction("Reproducir", lambda: self._play_from(row))
        else:
            menu.addAction("Reproducir", lambda: self._play_single(track))
        menu.addAction("Reproducir a continuación", lambda: self._queue_track(track, next_up=True))
        menu.addAction("Añadir a la cola", lambda: self._queue_track(track, next_up=False))
        menu.addSeparator()
        add_menu = rounded_menu(menu.addMenu("Añadir a una lista"))
        locals_ = [p for p in self.db.playlists() if p.local]
        for playlist in locals_:
            add_menu.addAction(
                playlist.title, lambda pid=playlist.playlist_id: self._add_to_local_playlist(pid, track)
            )
        if locals_:
            add_menu.addSeparator()
        add_menu.addAction("Nueva lista…", lambda: self._create_local_playlist((track,)))
        if row is not None and is_local_playlist(self._view_key):
            view = self._view_key
            menu.addAction("Quitar de esta lista", lambda: self._remove_from_local_playlist(view, track))
        menu.addSeparator()
        menu.addAction("Iniciar radio a partir de esta pista", lambda: self._start_radio(track))
        lead = (discover.split_artists(track.artists) or [""])[0]
        if lead:
            menu.addAction(f"Ir a {lead}", lambda: self._open_artist_named("", lead))
        return menu

    def _play_single(self, track: Track) -> None:
        if self._missing_binaries_notice():
            return
        self.db.upsert_track(track)
        self.player.play_tracks([track], 0)

    def _queue_track(self, track: Track, next_up: bool) -> None:
        if bootstrap.missing_binaries():
            self._notice("Faltan binarios: abre el menú (⋯) y elige «Descargar binarios que faltan».")
            return
        self.db.upsert_track(track)
        if next_up:
            self.player.play_next(track)
        else:
            self.player.enqueue(track)
        self._notice(f"«{track.title}» añadida a la cola.")

    def _start_radio(self, track: Track) -> None:
        if bootstrap.missing_binaries():
            self._notice("Faltan binarios: abre el menú (⋯) y elige «Descargar binarios que faltan».")
            return
        self.db.upsert_track(track)
        self.player.start_radio(track)
        self._notice("Cargando radio…")

    # ------------------------------------------------------------------ letras
    def _toggle_lyrics(self, visible: bool) -> None:
        # Con Ctrl+L, inmediato; con el botón «Letra», el panel se desliza.
        if self._swapping:
            return
        if visible and self.now_panel.isVisible():
            self._land_flight()
            self._swap_panes(self.now_panel, self.lyrics, self.now_action)
        else:
            self._slide_pane(self.lyrics, visible)
        if visible:
            self._load_lyrics(self.player.current_track())

    # ------------------------------------------------------------- ahora suena
    def _on_now_cover_clicked(self) -> None:
        """Clic en la carátula del reproductor: abre (o cierra) el panel. Con
        el ratón la carátula viaja de la barra al panel mientras este se abre,
        y vuelve a la barra al cerrarlo."""
        opening = not self.now_action.isChecked()
        flying = self._animate_panes() and not self.lyrics.isVisible()
        if not opening and flying:
            self._fly_cover(opening=False)
        self.now_action.setChecked(opening)
        if opening and flying:
            self._fly_cover(opening=True)

    def _cover_rects(self):
        """Dónde está cada carátula, en coordenadas de la ventana (medido en vivo)."""
        host = self.centralWidget()

        def small() -> QRectF:
            return QRectF(QPointF(self.now_cover.mapTo(host, QPoint(0, 0))), QPointF(
                self.now_cover.mapTo(host, QPoint(self.now_cover.width(), self.now_cover.height()))))

        def big() -> QRectF:
            cover = self.now_panel.cover
            art = cover.art_rect()
            return art.translated(QPointF(cover.mapTo(host, QPoint(0, 0))))

        return small, big

    def _fly_cover(self, opening: bool) -> None:
        """La copia de la carátula viaja entre la barra y el panel; la grande
        no se pinta hasta que llega (o desde que se va)."""
        self._land_flight()
        cover = self.now_panel.cover
        image = cover.flight_image() or (self.art.image(self._now_art) if self._now_art else None)
        if image is None or image.isNull():
            return
        small, big = self._cover_rects()
        start, end = (small, big) if opening else (big, small)
        radii = (theme.R_COVER, theme.R_COVER + 2)
        cover.set_held(True)

        def landed() -> None:
            cover.set_held(False)
            if self._flight is flight:
                self._flight = None

        flight = CoverFlight(self.centralWidget(), image, start, end,
                             radii if opening else radii[::-1],
                             FLIGHT_MS if opening else PANE_CLOSE_MS, landed)
        flight.opening = opening
        self._flight = flight
        flight.fly()

    def _land_flight(self) -> None:
        if self._flight is not None:
            self._flight.land()

    def _toggle_now(self, visible: bool) -> None:
        if self._swapping:
            return
        if visible and self.lyrics.isVisible():
            self._swap_panes(self.lyrics, self.now_panel, self.lyrics_action)
        else:
            if not visible and self._flight is not None and self._flight.opening:
                self._land_flight()       # se cierra antes de que la carátula llegue
            self._slide_pane(self.now_panel, visible)
        if visible:
            self._load_now(self.player.current_track())

    def _load_now(self, track: Optional[Track]) -> None:
        """Rellena el panel. La información se pide solo con el panel abierto y
        una vez por canción (queda guardada una semana)."""
        if track is None:
            self._now_for = None
            self.now_panel.show_idle()
            return
        if self._now_for == track.video_id and self.now_panel.info() is not None:
            return
        video_id = self._now_for = track.video_id
        self.now_panel.set_track(track)
        self.now_panel.set_tint(self.art.tint(self._now_art) if self._now_art else None)
        cached = discover.load_now_info(self.db, video_id)
        if cached is not None:
            self.now_panel.set_info(cached)
            return
        self.now_panel.set_loading()

        def done(info, error) -> None:
            if error is None and info is not None:
                discover.save_now_info(self.db, info)
            if self._now_for != video_id:
                return                    # ya suena otra cosa
            if error is not None or info is None:
                log.warning("información de %s: %s", video_id, error)
                self.now_panel.set_error()
            else:
                self.now_panel.set_info(info)

        run_async(lambda: discover.now_playing_info(track), done)

    def _play_similar(self, index: int) -> None:
        """Clic en una pista de «Música parecida»: suena esa y siguen las demás."""
        info = self.now_panel.info()
        if info is None or not (0 <= index < len(info.similar)) or self._missing_binaries_notice():
            return
        for track in info.similar:
            self.db.upsert_track(track)
        self.player.play_tracks(info.similar, index)

    def _lyrics_message(self, text: str) -> None:
        self._lyrics = None
        self._lyrics_index = -2
        self.lyrics.show_message(text)

    def _load_lyrics(self, track: Optional[Track]) -> None:
        if track is None:
            self._lyrics_for = None
            self._lyrics_message("Reproduce una canción para ver su letra.")
            return
        if self._lyrics_for == track.video_id and self.lyrics.count() > 0:
            return
        video_id = self._lyrics_for = track.video_id
        entry = self.db.get_lyrics_entry(video_id)
        if entry is not None:
            fetched_at, payload = entry
            if payload is not None:
                self._show_lyrics(Lyrics.from_json(payload))
                return
            if time.time() - fetched_at < config.LYRICS_NEGATIVE_CACHE_S:
                self._lyrics_message("Sin letra disponible para esta canción.")
                return
        self._lyrics_message("Buscando letra…")

        def done(result, error) -> None:
            if error is None:
                self.db.set_lyrics_entry(video_id, result.to_json() if result else None)
            if self._lyrics_for != video_id:
                return
            if error is not None:
                self._lyrics_message("No se pudo buscar la letra (¿sin conexión?).")
            elif result is None or result.empty:
                self._lyrics_message("Sin letra disponible para esta canción.")
            else:
                self._show_lyrics(result)

        run_async(lambda: fetch_lyrics(track.title, track.artists, track.album, track.duration_s), done)

    def _show_lyrics(self, lyrics: Lyrics) -> None:
        self._lyrics = lyrics
        self._lyrics_index = -2
        texts = [ln.text or "♪" for ln in lyrics.lines] if lyrics.synced else lyrics.plain.splitlines()
        self.lyrics.show_lines(texts, lyrics.synced, "de LRCLIB" if lyrics.synced else "de LRCLIB, sin sincronizar")

    def _highlight_lyric(self, position: float) -> None:
        lyrics = self._lyrics
        if lyrics is None or not lyrics.synced or not self.lyrics.isVisible():
            return
        index = current_line_index(lyrics.lines, position + 0.25)
        if index == self._lyrics_index:
            return
        self._lyrics_index = index
        self.lyrics.highlight(index)

    def _on_lyric_clicked(self, row: int) -> None:
        lyrics = self._lyrics
        if lyrics is not None and lyrics.synced and 0 <= row < len(lyrics.lines):
            self.player.seek(lyrics.lines[row].start_s)

    # ------------------------------------------------------------------ avisos
    def _notice(self, text: str, kind: str = "info") -> None:
        self.toasts.show(text, kind)

    def _on_progress(self, text: str) -> None:
        # Progreso continuo (descargas): va al pie fijo, no a una ráfaga de avisos.
        self._progress_text = text
        self._refresh_status()

    def _on_tick(self) -> None:
        blocked = self._blocked_until - time.time() > 0
        if blocked or self._was_blocked:
            self._refresh_status()
        if self._was_blocked and not blocked:
            self._notice("El tiempo de espera del bloqueo terminó: ya puedes reintentar.")
        self._was_blocked = blocked

    def _on_extractor_state(self, state: str) -> None:
        self._extractor_state = state
        self._refresh_status()

    # ---------------------------------------------------------- sincronización
    def _refresh_status(self) -> None:
        """Una sola línea de estado al pie de la barra lateral: lo más urgente gana."""
        remaining = self._blocked_until - time.time()
        alert = False
        if remaining > 0:
            text, alert = f"Anti-bot: reintento en {format_time(remaining)}", True
        elif self._progress_text:
            text = self._progress_text
        elif self._syncing:
            text = "Sincronizando la biblioteca…"
        elif self._extractor_state == ExtractorState.UPDATING.value:
            text = "Actualizando yt-dlp…"
        elif self._extractor_state == ExtractorState.EXTRACTING.value:
            text = "Resolviendo el audio…"
        elif self.sync.state() is SyncState.AUTH_EXPIRED:
            text, alert = "Sesión caducada", True
        elif self.sync.state() is SyncState.ERROR:
            text, alert = "Error en la última sincronización", True
        elif not paths.browser_json().is_file():
            text = "Sin credenciales"
        else:
            text = f"Sincronizada {format_ago(self.db.get_float('last_sync_check') or None)}"
        self.footer.set_status(text, alert, "\n".join(part for part in (text, self._versions) if part))

    def _auto_sync(self) -> None:
        if not self._syncing and self.sync.due():
            self._start_sync(silent=True)

    def _sync_current_list(self) -> None:
        if is_library(self._view_key) and not is_local_playlist(self._view_key):
            self._start_sync(only_playlist=self._view_key)

    def _start_sync(self, force_all: bool = False, only_playlist: Optional[str] = None, silent: bool = False) -> None:
        if self._syncing:
            return
        if not paths.browser_json().is_file():
            if not silent:
                self._notice("Configura primero las credenciales de la cuenta secundaria.")
            return
        if not silent:
            # Una sincronización pedida a mano sí reintenta tras AUTH_EXPIRED.
            self.sync.credentials_renewed()
        self._syncing = True
        self._refresh_status()
        self._update_buttons()
        run_async(
            lambda: self.sync.run(force_all=force_all, only_playlist=only_playlist),
            self._on_sync_done,
        )

    def _on_sync_done(self, report: Optional[SyncReport], error) -> None:
        self._syncing = False
        if error is not None:
            self._notice(f"Error de sincronización: {error}", "error")
        elif report is not None:
            self._notice(report.message)
        # La GUI lee solo de SQLite: refrescar los modelos al terminar.
        self._reload_sidebar()
        self._refresh_status()
        self._refresh_banner()
        self._update_buttons()

    # ------------------------------------------------------------ herramientas
    def _open_account(self) -> None:
        dialog = AccountDialog(self.db, self)
        dialog.credentials_changed.connect(self._on_credentials_changed)
        dialog.exec()
        self.extractor.prewarm()   # el modo (con o sin cookies) pudo cambiar
        self._refresh_banner()
        self._update_buttons()

    def _on_credentials_changed(self) -> None:
        self.sync.credentials_renewed()
        self.extractor.clear_bot_block()
        self._blocked_until = 0.0
        self._refresh_banner()
        self._refresh_status()
        # Con cabeceras nuevas se sincroniza enseguida, sin esperar al temporizador.
        if paths.browser_json().is_file():
            QTimer.singleShot(0, lambda: self._start_sync(silent=True))

    def _open_data_folder(self) -> None:
        paths.ensure_dirs()
        if paths.IS_WINDOWS:
            os.startfile(str(paths.app_home()))  # noqa: S606 - carpeta propia de la app

    def _manual_update(self) -> None:
        if not paths.ytdlp_exe().is_file():
            self._notice("Falta yt-dlp: descarga primero los binarios.")
            return
        self._notice("Buscando actualización de yt-dlp…")
        run_async(lambda: self.extractor.update("manual"), self._on_update_done)

    def _on_update_done(self, result: Optional[UpdateResult], error) -> None:
        if error is not None or result is None:
            self._notice(f"La actualización falló: {error}", "error")
            return
        text = {
            UpdateStatus.UPDATED: f"yt-dlp actualizado a {result.new_version}.",
            UpdateStatus.UP_TO_DATE: f"yt-dlp ya está al día ({result.old_version}).",
            UpdateStatus.SKIPPED_COOLDOWN: "Actualización omitida por el cooldown.",
        }.get(result.status, result.message or result.status.value)
        self._notice(text)
        run_async(self.extractor.diagnostics, lambda d, e: d and self._apply_diagnostics(d))

    def _on_update_done_silent(self, result: Optional[UpdateResult], error) -> None:
        if error is None and result is not None and result.status not in (
            UpdateStatus.SKIPPED_COOLDOWN, UpdateStatus.UP_TO_DATE
        ):
            self._on_update_done(result, None)

    def _install_binaries(self) -> None:
        if self._installing:
            return
        missing = bootstrap.missing_binaries()
        if not missing:
            self._notice("No falta ningún binario.")
            return
        answer = QMessageBox.question(
            self,
            "Descargar binarios",
            "Se descargarán desde sus repositorios oficiales en GitHub: "
            + ", ".join(missing)
            + f"\n\nDestino: {paths.bin_dir()}\n\n¿Continuar?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._installing = True
        self._refresh_banner()
        run_async(lambda: bootstrap.install_all(self.bridge.progress.emit), self._on_install_done)

    def _on_install_done(self, errors, error) -> None:
        self._installing = False
        self._progress_text = ""
        self._refresh_status()
        problems = [str(error)] if error is not None else list(errors or [])
        self._refresh_banner()
        if problems:
            QMessageBox.warning(self, "Descargar binarios", "\n".join(problems))
        else:
            self._notice("Binarios instalados.")
        if paths.mpv_exe().is_file() and not self.mpv.is_ready():
            self.mpv.start()
        self._startup_checks()

    def _show_diagnostics(self) -> None:
        def show(diag, error) -> None:
            if error is not None or not diag:
                QMessageBox.warning(self, "Diagnóstico", f"No se pudo obtener el diagnóstico: {error}")
                return
            now = time.time()
            mode = {"hybrid": "híbrido", "cookies": "siempre con cookies", "anon": "siempre anónimo"}[
                diag["playback_mode"]
            ]
            sticky = self.db.get_float("cookies_sticky_until")
            fast_off = self.db.get_float("fast_client_off_until")
            if not config.FAST_PLAYER_CLIENT:
                fast = "desactivado"
            elif fast_off > now:
                fast = f"{config.FAST_PLAYER_CLIENT}, en pausa {format_time(fast_off - now)} más (fallaba)"
            else:
                fast = f"{config.FAST_PLAYER_CLIENT} (solo en modo anónimo)"
            lines = [
                f"Carpeta de datos: {paths.app_home()}",
                f"yt-dlp: {diag['ytdlp_version'] or 'no instalado'}",
                f"deno: {diag['deno_version'] or 'no instalado'}"
                + ("" if diag["deno_ok"] else f" — {diag['deno_message']}"),
                f"mpv: {'conectado' if self.mpv.is_ready() else 'no conectado'}",
                f"Modo de resolución: {mode}",
                f"Cliente rápido de YouTube: {fast}",
                f"cookies.txt: {'presente' if diag['cookies_present'] else 'ausente'}"
                + (" (en uso permitido)" if diag["cookies_usable"] else " (yt-dlp no las usa)"),
                "Modo pegajoso con cookies: "
                + (f"activo {format_time(sticky - now)} más" if sticky > now else "inactivo"),
                f"Resoluciones en la última hora: {self.db.resolves_since(now - 3600)}"
                f" (tope para prefetch: {config.MAX_RESOLVES_PER_HOUR})",
                f"Resoluciones en 24 h: {self.db.resolves_since(now - 86400)}",
                f"Última comprobación de yt-dlp: {format_ago(self.db.get_float('last_update_timestamp') or None)}",
                f"Estado de sincronización: {self.sync.state().value}",
            ]
            QMessageBox.information(self, "Diagnóstico", "\n".join(lines))

        run_async(self.extractor.diagnostics, show)

    # ---------------------------------------------------------------- búsqueda
    def _on_search_text(self, text: str) -> None:
        """Búsqueda en vivo: sugerencias tras una pausa corta y resultados tras
        una algo más larga. Intro sigue buscando en el acto."""
        query = text.strip()
        if query:
            self._suggest_timer.start()
            if len(query) >= config.SEARCH_LIVE_MIN_CHARS:
                self._live_timer.start()
            else:
                self._live_timer.stop()
            return
        # Campo vacío: se descarta lo que venga de camino y vuelve el punto de partida.
        self._suggest_timer.stop()
        self._live_timer.stop()
        self._suggest_seq += 1
        self._search_seq += 1
        self._searched_query = ""
        self._search_tracks = []
        self._search_note = SEARCH_IDLE_NOTE
        self._extras = SearchExtras()
        self._extras_state = "idle"
        self._top_artist = None
        self._show_recent_searches()
        if self._view_key == SEARCH_KEY:
            self.model.set_tracks([])
            self._refresh_search_band()
            self._render_search_cards()
            self._sync_search_empty()
            self._update_buttons()

    def _show_recent_searches(self) -> None:
        self.suggestions.set_items(
            discover.recent_searches(self.db), lead="Recientes", glyph="history", strong=False
        )

    def _fetch_suggestions(self) -> None:
        query = self.search_edit.text().strip()
        if not query:
            return
        self._suggest_seq += 1
        seq = self._suggest_seq

        def done(texts, error) -> None:
            # Si falla no se avisa: las sugerencias son una ayuda, no la tarea.
            if seq == self._suggest_seq and error is None:
                self.suggestions.set_items(texts or [], typed=query)

        run_async(lambda: discover.search_suggestions(query), done)

    def _on_suggestion_picked(self, text: str) -> None:
        self.search_edit.setText(text)
        self.search_edit.setFocus()
        self._suggest_timer.stop()
        self._fetch_suggestions()
        self._do_search(commit=True)

    def _sync_search_empty(self) -> None:
        query = self.search_edit.text().strip()
        searched = bool(self._searched_query) and self._searched_query == query
        idle = ("Busca música nueva",
                "Empieza a escribir: las sugerencias y los resultados aparecen solos. "
                "No hace falta iniciar sesión.")
        self._sync_artist_strip(searched)
        if self._search_scope == SONGS:
            if self.model.rowCount():
                self._set_empty("")
            elif searched and not self._search_note.startswith("Buscando"):
                self._set_empty(f"Sin resultados para «{query}»",
                                "Prueba con otro título o artista, o con una de las sugerencias de arriba.",
                                glyph="search")
            else:
                self._set_empty(*idle, glyph="search")
            return
        items = self._extras.playlists if self._search_scope == PLAYLISTS else self._extras.artists
        what = "listas de la comunidad" if self._search_scope == PLAYLISTS else "artistas"
        if items:
            self._set_empty("")
        elif not searched:
            self._set_empty(*idle, glyph="search")
        elif self._extras_state == "loading":
            self._set_empty(f"Buscando {what}…", glyph="search")
        elif self._extras_state == "failed":
            self._set_empty(f"No se pudieron buscar {what}", "Comprueba la conexión y pulsa Intro para reintentar.",
                            glyph="search")
        else:
            self._set_empty(f"Sin {what} para «{query}»", "Prueba con otro nombre o con menos palabras.",
                            glyph="search")

    def _sync_artist_strip(self, searched: bool) -> None:
        """La fila del artista va sobre las canciones si YouTube Music dio a un
        artista como resultado principal: la búsqueda iba de él."""
        show = (self._view_key == SEARCH_KEY and self._search_scope == SONGS and searched
                and self._top_artist is not None)
        if show:
            self.artist_strip.set_card(self._top_artist)
        self.artist_strip.setVisible(show)

    def _scope_note(self) -> str:
        """La nota de la banda habla de lo que se está viendo."""
        query = self._searched_query
        if self._search_scope == SONGS or not query:
            return self._search_note
        if self._extras_state == "loading":
            return f"Buscando «{query}»…"
        if self._extras_state == "failed":
            return "La búsqueda falló. Pulsa Intro para reintentar."
        if self._search_scope == PLAYLISTS:
            count = plural(len(self._extras.playlists), "lista de la comunidad", "listas de la comunidad")
        else:
            count = plural(len(self._extras.artists), "artista", "artistas")
        return f"{count} para «{query}»"

    def _set_search_scope(self, scope: str) -> None:
        """Canciones, listas de la comunidad o artistas. Sin animación: se cambia
        a menudo y la búsqueda ya está hecha (las tres llegan a la vez)."""
        self.scope_bar.set_scope(scope)
        if scope == self._search_scope:
            return
        self._search_scope = scope
        if self._view_key == SEARCH_KEY:
            self._refresh_search_band()
            self._render_search_cards()
            self._sync_search_empty()
            self._update_buttons()

    def _render_search_cards(self) -> None:
        if self._search_scope == PLAYLISTS:
            self.search_cards.show_playlists(self._extras.playlists)
        elif self._search_scope == ARTISTS:
            self.search_cards.show_artists(self._extras.artists)

    def _fetch_extras(self, query: str, seq: int) -> None:
        """Listas de la comunidad y artistas: aparte de las canciones, para que
        estas no esperen a la petición más lenta."""
        self._extras = SearchExtras()
        self._extras_state = "loading"
        self._refresh_extras_view()

        def done(extras, error) -> None:
            if seq != self._search_seq:
                return
            if error is not None:
                log.warning("listas y artistas de «%s»: %s", query, error)
                self._extras_state = "failed"
            else:
                self._extras = extras or SearchExtras()
                self._extras_state = "done"
            self._refresh_extras_view()

        run_async(lambda: discover.search_extras(query), done)

    def _refresh_extras_view(self) -> None:
        if self._view_key == SEARCH_KEY and self._search_scope != SONGS:
            self._refresh_search_band()
            self._render_search_cards()
            self._sync_search_empty()
            self._update_buttons()

    def _do_search(self, commit: bool = False) -> None:
        """``commit``: la búsqueda se pidió a propósito (Intro o una sugerencia),
        no por una pausa al escribir: se recuerda y sus errores se avisan."""
        query = self.search_edit.text().strip()
        self._live_timer.stop()
        if not query:
            return
        if commit:
            discover.remember_search(self.db, query)
        if query == self._searched_query:
            if self._extras_state == "failed":
                self._fetch_extras(query, self._search_seq)     # Intro reintenta lo que falló
            return                        # ya está en pantalla o de camino
        self._searched_query = query
        self._search_seq += 1
        seq = self._search_seq
        self._top_artist = None
        self._set_search_note(f"Buscando «{query}»…")
        top: dict = {}

        def done(tracks, error) -> None:
            if seq != self._search_seq:
                return                    # se siguió escribiendo: esta respuesta ya no vale
            if error is not None:
                self._searched_query = ""          # Intro lo reintenta
                self._set_search_note("La búsqueda falló. Pulsa Intro para reintentar.")
                if commit:
                    self._notice(f"Error de búsqueda: {error}", "error")
                return
            self._top_artist = discover.artist_card_from_item(top.get("artist"))
            self._search_tracks = list(tracks or [])
            self._set_search_note(
                f"{plural(len(self._search_tracks), 'resultado', 'resultados')} para «{query}»"
            )
            if self._view_key != SEARCH_KEY:
                return                    # se verán al volver a la búsqueda
            self.model.set_tracks(self._search_tracks)
            self._sync_search_empty()
            current = self.player.current_track()
            self.model.set_current(current.video_id if current else None)
            self.table.scrollToTop()
            self._update_buttons()

        run_async(lambda: search_tracks(query, limit=config.SEARCH_RESULTS, top=top), done)
        self._fetch_extras(query, seq)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - API de Qt
        kind = event.type()
        if kind == QEvent.Type.MouseButtonPress:
            if (event.button() == Qt.MouseButton.BackButton and isinstance(obj, QWidget)
                    and obj.window() is self):
                self._go_back()
                return True
        elif kind == QEvent.Type.KeyPress and obj is self.search_edit:
            if event.key() == Qt.Key.Key_Down and self._cards_shown():
                card = self.search_cards.first_card()
                if card is not None:
                    card.setFocus(Qt.FocusReason.TabFocusReason)
                    return True
            elif event.key() == Qt.Key.Key_Down and self.model.rowCount():
                # De escribir a elegir sin soltar el teclado: flecha abajo e Intro.
                self.table.setFocus(Qt.FocusReason.TabFocusReason)
                self.table.selectRow(0)
                return True
            if event.key() == Qt.Key.Key_Escape and self.search_edit.text():
                self.search_edit.clear()
                return True
        return super().eventFilter(obj, event)

    # ----------------------------------------------------------------- ventana
    def resizeEvent(self, event) -> None:  # noqa: N802 - API de Qt
        super().resizeEvent(event)
        # En ventanas bajas la banda se encoge para dejarle sitio a la lista.
        self.band.set_compact(self.height() < 700)

    def changeEvent(self, event) -> None:  # noqa: N802 - API de Qt
        super().changeEvent(event)
        if event.type() == event.Type.ActivationChange and hasattr(self, "toasts"):
            self.toasts.set_inactive(not self.isActiveWindow())

    # ------------------------------------------------------------------ cierre
    def closeEvent(self, event) -> None:  # noqa: N802 - API de Qt
        self._tick.stop()
        self._sync_timer.stop()
        self.player.stop()
        self.mpv.shutdown()
        self.extractor.shutdown()
        super().closeEvent(event)
