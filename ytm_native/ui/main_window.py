"""Ventana principal: biblioteca, búsqueda, barra de reproducción y estado."""

from __future__ import annotations

import logging
import os
import random
import re
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, QSize, QSizeF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QAction, QActionGroup, QColor, QDesktopServices, QFont, QFontMetrics, QIcon, QKeySequence, QPixmap, QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QAbstractSlider,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ytm_native.playback import bootstrap
from ytm_native.core import config, credentials, paths
from ytm_native.services import discover
from ytm_native.services.discover import ArtistCard, ArtistPage, PlaylistCard, RemotePlaylist, SearchExtras
from ytm_native.services.lyrics import Lyrics, current_line_index, fetch_lyrics
from ytm_native.core.db import Database, Track, is_local_playlist
from ytm_native.playback.extractor import ExtractorManager, ExtractorState, UpdateResult, UpdateStatus
from ytm_native.playback.history import PlayTracker
from ytm_native.playback.mpv_ipc import AUTO_DEVICE, EQ_FLAT, EQ_PRESETS, MpvPlayer
from ytm_native.playback.player import (
    CROSSFADE_DEFAULT_S, CROSSFADE_KEY, DEFAULT_VOLUME, LOADING, PAUSED, PLAYING, REPEAT_ALL, REPEAT_OFF, REPEAT_ONE,
    TRANSITION_GAPLESS, TRANSITION_KEY, PlaybackController, saved_volume,
)
from ytm_native.playback import history as play_history
from ytm_native.playback import session as play_session
from ytm_native.playback.session import SessionKeeper
from ytm_native.playback.sleep_timer import PRESETS_MIN, TIME, TRACK, SleepTimer
from ytm_native.services import backup
from ytm_native.services.account_actions import AccountActions, is_local_track
from ytm_native.services.discord_rpc import DiscordPresence, valid_client_id
from ytm_native.services.lastfm import LastfmScrobbler
from ytm_native.services.updater import AppUpdateChecker
from ytm_native.services.sync import LibrarySync, SyncReport, SyncState, looks_device_bound
from . import art as art_style
from . import chrome, focus, theme
from .art import BAR_ART_PX, ArtLoader, neutral_tint, placeholder_pixmap, sized_url, track_art_url
from .artist import ArtistView
from .chrome import menu_section, rounded_menu, show_check
from .dialogs import FirefoxLoginDialog
from .components import (
    ActiveMark,
    ListFocusRing,
    CoverFlight,
    note_launch,
    take_launch,
    Banner,
    ClickableCover,
    ElidedLabel,
    EmptyState,
    FilterBox,
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
from .cards import ArtistStrip, CardGrid
from .home import HomePage
from .mini_player import MiniPlayer
from .motion import EASE, EASE_DRAWER, FADE_MS, FLIGHT_MS, PANE_CLOSE_MS, PANE_OPEN_MS, Tween, reduced_motion
from .now import NowPanel
from .settings import OptionsDialog
from .search import SONGS, CardsPage, ScopeBar
from .taskbar import ThumbBar
from .tray import Autostart, TrayIcon, app_icon
from .widgets import AccountDialog, TrackFilterProxyModel, TrackModel, format_ago, format_time, run_async
from ytm_native.core.i18n import plural, tr
from .views.common import (  # noqa: F401 - las pruebas y otras piezas las leen de aquí
    HOME_KEY,
    SEARCH_KEY,
    QUEUE_KEY,
    AUDIO_DEVICE_KEY,
    AUDIO_DEVICE_NAME_KEY,
    EQUALIZER_KEY,
    HISTORY_KEY,
    LOCAL_KEY,
    FIXED_KEYS,
    HEARD_RECENT,
    HEARD_MONTH,
    HEARD_ALL,
    HEARD_LABELS,
    NEW_LIST_KEY,
    REMOTE_PREFIX,
    ARTIST_PREFIX,
    ROLE_ID,
    ROLE_ART,
    SEARCH_IDLE_NOTE,
    is_remote,
    is_artist,
    is_library,
    format_total,
)
from .views import (
    QueueView,
    SearchView,
    HistoryView,
    LocalView,
    PlaylistView,
)

if sys.platform == "win32":
    from ctypes import wintypes

log = logging.getLogger("ytmn.ui")


class Bridge(QObject):
    """Lleva al hilo de la GUI los avisos que el extractor emite desde hilos de trabajo."""

    extractor_state = Signal(str)
    notice = Signal(str)
    progress = Signal(str)
    shelf = Signal(int, object)   # tanda de recomendaciones, fila lista

class MainWindow(QueueView, SearchView, HistoryView, LocalView, PlaylistView, QMainWindow):
    SIDE_MIN = 208
    SIDE_MAX = 340
    SIDE_COLLAPSED = 64
    CONTENT_MIN = 360
    NOW_MIN = 300
    NAV_DEBOUNCE_MS = 180     # pausa en las flechas de la barra lateral antes de cargar la vista
    TIME_LABEL_MIN = 44       # ancho de las etiquetas de tiempo junto a la barra de progreso
    _SIDEBAR_NAV_KEYS = (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown,
                         Qt.Key.Key_Home, Qt.Key.Key_End)

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
        # Guarda la cola y el segundo por el que va, y los devuelve al abrir.
        self.session = SessionKeeper(db, player, self)
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
        self._sidebar_entries: list[tuple[str, str, str]] = []    # listas que enseña la barra
        # Paneles que se deslizan: el ancho al que vuelve cada uno y su animación en curso.
        self._pane_widths: dict = {}
        self._slides: dict = {}
        self._pane_targets: dict = {}       # ancho al que va cada panel que se abre
        self._view_switching = False         # cambiando de vista: nada de animar anchos
        # Preparando un deslizamiento (anchos mínimos que cambian antes del
        # primer fotograma) o recién acabado (Qt aún reparte el sitio del
        # panel que se ocultó): cuenta como deslizar.
        self._slide_hold = 0
        self._settle_pending = False
        self._swapping = False
        self._flight: Optional[CoverFlight] = None
        self._band_flight: Optional[CoverFlight] = None
        self._extractor_state = ExtractorState.IDLE.value
        self._progress_text = ""
        self._versions = ""
        self._was_blocked = False
        self._pre_mute = 0
        self._position = 0.0
        # Integraciones opcionales, apagadas por defecto: Last.fm (las
        # escuchas las decide el mismo contador del historial), Discord y el
        # aviso de versiones nuevas de la app.
        self.lastfm = LastfmScrobbler(db, runner=run_async, parent=self)
        self.discord = DiscordPresence(db, parent=self)
        self.app_updates = AppUpdateChecker(db, runner=run_async, parent=self)
        # Cambios en la cuenta de YouTube Music (me gusta, biblioteca, listas e
        # historial): apagados hasta que el usuario los permite.
        self.account = AccountActions(db, runner=run_async, parent=self)
        # Historial de escucha: anota en la base local cada canción que se oye de verdad.
        self.tracker = PlayTracker(db, player, self, scrobbler=self.lastfm, reporter=self.account)
        self._heard_scope = HEARD_RECENT
        self._recent_tracks: list[Track] = []
        # Escritorio: bandeja, miniatura de la barra de tareas, mini reproductor,
        # temporizador de apagado e inicio con Windows.
        self.sleep = SleepTimer(player, self)
        self.autostart = Autostart()
        self.tray: Optional[TrayIcon] = None
        self.thumbbar: Optional[ThumbBar] = None
        self.mini: Optional[MiniPlayer] = None
        self._quitting = False          # salir de verdad (no a la bandeja)
        self._shut_down = False
        self._exit_app: Callable[[], None] = QApplication.quit
        # Canciones que se están quitando de una lista de la cuenta: su copia exacta.
        self._pending_removals: dict[tuple[str, str], str] = {}
        # Música del disco: la lectura de la carpeta va en segundo plano.
        self._scanning = False
        self._scan_cancel: Optional[threading.Event] = None
        # Tema: "system" sigue a Windows; se aplica antes de construir nada.
        self._theme_mode = theme.normalize_mode(db.get_setting(config.THEME_KEY, "system"))
        self._apply_theme(rebuild=False)

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
        self._setup_desktop()

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
        # Navegar la barra lateral con flechas: la vista espera a que se pare.
        self._sidebar_keys = False
        self._nav_timer = QTimer(self)
        self._nav_timer.setSingleShot(True)
        self._nav_timer.setInterval(self.NAV_DEBOUNCE_MS)
        self._nav_timer.timeout.connect(self._settle_sidebar_nav)

        # La comprobación de versiones se repite con la app abierta mucho
        # tiempo (en la bandeja); ella misma no pregunta más de una vez al día.
        self._theme_timer = QTimer(self)
        self._theme_timer.setSingleShot(True)
        self._theme_timer.setInterval(250)
        self._theme_timer.timeout.connect(self._apply_theme)

        self._app_update_timer = QTimer(self)
        self._app_update_timer.setInterval(6 * 3600 * 1000)
        self._app_update_timer.timeout.connect(lambda: self.app_updates.check())

    # ------------------------------------------------------------------ UI
    def _build_menu(self) -> None:
        """Menú de la aplicación: se abre desde el pie de la barra lateral.

        Solo lleva lo que se usa a diario, por grupos con rótulo (biblioteca,
        reproducción y vista); las preferencias viven en Opciones (Ctrl+,),
        una ventana con cada ajuste explicado a la vista (``ui/settings.py``).
        Lo que tiene casilla es un interruptor; lo que no, una acción. En una
        pantalla baja el menú se aprieta antes que partirse
        (``_fit_app_menu``).

        Los interruptores de Opciones son acciones de esta ventana que no
        cuelgan de ningún menú (``_build_settings_actions``): así su estado
        tiene un único dueño, lo cambie Opciones, la bandeja o una copia de
        seguridad importada.

        No hay barra de menús, así que cada acción se cuelga también de la
        ventana para que su atajo de teclado siga funcionando.
        """
        self._build_settings_actions()
        menu = self.app_menu = rounded_menu(QMenu(self))
        menu.setObjectName("appMenu")
        menu.setToolTipsVisible(True)
        # Solo cuando hay una versión nueva de la app: abre su página.
        self.app_update_action = self._add_action(menu, "", self._open_app_release)
        self.app_update_action.setVisible(False)

        menu_section(menu, tr("Biblioteca"), first=True)
        self._add_action(menu, tr("Sincronizar ahora"), lambda: self.start_sync(), "F5",
                         tip=tr("Trae de tu cuenta las listas que han cambiado."))
        self._add_action(menu, tr("Nueva lista…"), lambda: self._create_local_playlist(), "Ctrl+N")
        self._build_content_menu(menu)

        menu_section(menu, tr("Reproducción"))
        self._build_sleep_menu(self._submenu(menu, tr("Temporizador de apagado")))

        menu_section(menu, tr("Vista"))
        self.lyrics_action = self._add_toggle(menu, tr("Letra"), False, self._toggle_lyrics, shortcut="Ctrl+L")
        self.now_action = self._add_toggle(menu, tr("Ahora suena"), False, self._toggle_now, shortcut="Ctrl+I",
                                           tip=tr("Panel con la canción que suena y su artista."))
        self.collapse_action = self._add_toggle(menu, tr("Barra lateral contraída"), False,
                                                self._set_sidebar_collapsed, shortcut="Ctrl+B")
        self.mini_action = self._add_action(menu, tr("Mini reproductor"), self.toggle_mini, "Ctrl+M")

        menu.addSeparator()
        self.options_action = self._add_action(menu, tr("Opciones…"), self.open_options, "Ctrl+,",
                                               tip=tr("Idioma, tema, cuenta, integraciones y mantenimiento."))
        self._add_action(menu, tr("Salir"), self.quit_app, "Ctrl+Q")

    def _build_settings_actions(self) -> None:
        """Las preferencias: acciones sin menú que Opciones enseña como
        interruptores (y que la ventana usa como su estado)."""
        self.autoplay_action = self._make_toggle(
            tr("Radio al terminar la cola"), self.player.autoplay, self.player.set_autoplay)
        self.resume_action = self._make_toggle(
            tr("Seguir donde lo dejaste"), self.session.enabled, self.session.set_enabled)
        self.equalize_action = self._make_toggle(
            tr("Igualar el volumen"), self.db.get_bool("equalize_volume", False), self._set_equalize)
        self.history_action = self._make_toggle(
            tr("Guardar el historial de escucha"), self.tracker.enabled, self._set_history_enabled)
        self.close_to_tray_action = self._make_toggle(
            tr("Al cerrar, seguir sonando en la bandeja"), self.db.get_bool("tray_close_to_tray", True),
            lambda on: self.db.set_setting("tray_close_to_tray", "1" if on else "0"))
        self.minimize_to_tray_action = self._make_toggle(
            tr("Al minimizar, ir a la bandeja"), self.db.get_bool("tray_minimize_to_tray", False),
            lambda on: self.db.set_setting("tray_minimize_to_tray", "1" if on else "0"))
        self.autostart_action = self._make_toggle(
            tr("Iniciar con Windows (en la bandeja)"), self.db.get_bool("autostart_with_windows", False),
            self._set_autostart)
        self.autostart_action.setEnabled(self.autostart.supported)
        self.discord_action = self._make_toggle(
            tr("Mostrar en Discord lo que escucho"), self.discord.enabled, self._set_discord)
        self.lastfm_action = self._make_toggle(tr("Enviar mis escuchas a Last.fm"), False, self._set_lastfm)
        self.lastfm_account_action = QAction("", self)
        self.lastfm_account_action.triggered.connect(lambda _checked=False: self.manage_lastfm_account())
        self._sync_lastfm_actions()
        self.mutations_action = self._make_toggle(
            tr("Permitir cambios en mi cuenta de YouTube Music"), self.account.enabled, self._set_account_mutations)
        self.account_history_action = self._make_toggle(
            tr("Registrar mis escuchas en el historial de YouTube"), self.account.history_enabled,
            self.account.set_history_enabled)
        self.account_history_action.setEnabled(self.account.enabled)
        self.account_history_action.setToolTip(tr("Necesita los cambios en tu cuenta permitidos."))
        self.app_updates_action = self._make_toggle(
            tr("Avisar de versiones nuevas de la app"), self.app_updates.enabled, self.app_updates.set_enabled)
        # El tema: una opción entre tres (Opciones lo enseña como lista).
        self.theme_actions: dict[str, QAction] = {}
        group = QActionGroup(self)
        group.setExclusive(True)
        for mode, label in (("system", tr("Como Windows")), ("light", tr("Claro")), ("dark", tr("Oscuro"))):
            action = QAction(label, self)
            action.setCheckable(True)
            group.addAction(action)
            action.setChecked(self._theme_mode == mode)
            action.toggled.connect(lambda on, m=mode: on and self.set_theme_mode(m))
            self.theme_actions[mode] = action

    def _make_toggle(self, text: str, checked: bool, slot) -> QAction:
        """Un interruptor sin menú: estado inicial sin avisar a ``slot``."""
        action = QAction(text, self)
        action.setCheckable(True)
        action.setChecked(checked)
        action.toggled.connect(slot)
        return action

    def open_options(self, page: str = "general") -> None:
        """Abre Opciones (en ``page``). Si ya está abierta, la trae delante."""
        dialog = getattr(self, "options_dialog", None)
        if dialog is not None and dialog.isVisible():
            dialog.show_page(page)
            dialog.raise_()
            dialog.activateWindow()
            return
        dialog = self.options_dialog = OptionsDialog(self, page)
        dialog.exec()
        self.options_dialog = None
        dialog.deleteLater()

    def restart_app(self) -> None:
        """Sale y vuelve a abrirse (para aplicar el idioma). La nueva copia
        la lanza ``app.main`` cuando esta ya soltó el cerrojo de instancia."""
        app = QApplication.instance()
        if app is not None:
            app.setProperty("ytmn_restart", True)
        dialog = getattr(self, "options_dialog", None)
        if dialog is not None:
            dialog.accept()
        self.quit_app()

    def _fit_app_menu(self, room: int) -> None:
        """Con poca altura (portátil, escala de Windows alta) el menú aprieta
        sus filas antes que partirse en dos columnas, que es lo que hace Qt
        cuando no cabe y deja las opciones revueltas."""
        menu = self.app_menu
        for sheet in ("", theme.MENU_COMPACT_QSS):
            if menu.styleSheet() != sheet:
                menu.setStyleSheet(sheet)        # vuelve a pulir el menú y sus rótulos
            if self._app_menu_height() <= room - 8:
                return

    def _app_menu_height(self) -> int:
        """Lo que mide el menú en una sola columna (``sizeHint`` ya viene
        recortado a la pantalla cuando Qt lo parte)."""
        menu = self.app_menu
        menu.ensurePolished()                    # el QSS, también antes de abrirlo por primera vez
        menu.sizeHint()                          # recoloca las filas con el estilo en uso
        rows = sum(menu.actionGeometry(a).height() for a in menu.actions() if a.isVisible())
        return rows + 18                         # relleno (8 + 8) y borde del QSS

    def _submenu(self, parent: QMenu, title: str) -> QMenu:
        """Submenú del menú de la app, con su mismo aire y sus notas."""
        sub = rounded_menu(parent.addMenu(title))
        sub.setObjectName("appMenu")
        sub.setToolTipsVisible(True)
        return sub

    def _add_toggle(self, menu: QMenu, text: str, checked: bool, slot, *, tip: str = "",
                    shortcut: Optional[str] = None) -> QAction:
        """Un interruptor del menú: casilla propia, estado inicial sin avisar a
        ``slot`` y, si tiene atajo, colgado también de la ventana."""
        action = QAction(text, self)
        action.setCheckable(True)
        action.setChecked(checked)
        if tip:
            action.setToolTip(tip)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
            self.addAction(action)
        action.toggled.connect(slot)
        menu.addAction(show_check(action, self.devicePixelRatioF()))
        return action

    def _build_sleep_menu(self, menu: QMenu) -> None:
        self.sleep_menu = menu
        for minutes in PRESETS_MIN:
            label = tr("1 hora") if minutes == 60 else tr("{minutes} minutos", minutes=minutes)
            menu.addAction(label, lambda m=minutes: self.sleep.start_minutes(m))
        menu.addAction(tr("Otro tiempo…"), self._ask_sleep_minutes)
        menu.addAction(tr("Al terminar esta canción"), self.sleep.stop_after_track)
        menu.addSeparator()
        self.sleep_quit_action = self._add_toggle(
            menu, tr("Cerrar la aplicación al terminar"), self.db.get_bool("sleep_quit_app", False),
            lambda on: self.db.set_setting("sleep_quit_app", "1" if on else "0"))
        menu.addSeparator()
        self.sleep_cancel_action = menu.addAction(tr("Cancelar el temporizador"), self.sleep.cancel)
        self.sleep_cancel_action.setEnabled(False)

    def _build_content_menu(self, menu: QMenu) -> None:
        local = self._submenu(menu, tr("Música de tu equipo"))
        local.addAction(tr("Elegir la carpeta de música…"), self._choose_music_folder)
        self.rescan_action = local.addAction(tr("Volver a leer la carpeta"), lambda: self._scan_local(manual=True))
        self.forget_folder_action = local.addAction(tr("Dejar de usar la carpeta"), self._forget_music_folder)
        local.aboutToShow.connect(self._sync_local_actions)
        self._sync_local_actions()

    def _add_action(self, menu, text, slot, shortcut: Optional[str] = None, *, tip: str = "") -> QAction:
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        if tip:
            action.setToolTip(tip)
        action.triggered.connect(lambda _checked=False: slot())
        menu.addAction(action)
        self.addAction(action)
        return action

    def _open_app_menu(self) -> None:
        # El menú nace pegado a su botón, encima de él: se ve de dónde sale.
        corner = self.footer.mapToGlobal(QPoint(theme.GUTTER, 0))
        screen = QApplication.screenAt(corner) or self.screen()
        self._fit_app_menu(screen.availableGeometry().height())
        height = self.app_menu.sizeHint().height()
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
        self.side_toggle = PaneButton(tr("Contraer la barra lateral (Ctrl+B)"))
        toggle_row = QHBoxLayout()
        toggle_row.setContentsMargins(14, 8, 14, 0)
        toggle_row.addWidget(self.side_toggle)
        toggle_row.addStretch(1)
        side_lay.addLayout(toggle_row)
        self.sidebar = QListWidget()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setAccessibleName(tr("Vistas y listas"))
        self.sidebar.setIconSize(QSize(32, 32))
        self.sidebar.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.sidebar.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.active_mark = ActiveMark(self.sidebar)
        self.sidebar_ring = ListFocusRing(self.sidebar)
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
        # Fila de herramientas sobre la tabla: las pestañas del historial (solo
        # allí) y, a la derecha, el filtro de la lista a la vista.
        self.tools_box = QWidget()
        tools = QHBoxLayout(self.tools_box)
        tools.setContentsMargins(0, 0, 0, 0)
        tools.setSpacing(12)
        self.heard_bar = ScopeBar(labels=HEARD_LABELS)
        self.heard_bar.hide()
        tools.addWidget(self.heard_bar, 1)
        tools.addStretch(1)
        self.filter_note = QLabel()
        self.filter_note.setObjectName("filterNote")
        self.filter_note.hide()
        tools.addWidget(self.filter_note)
        self.list_filter = FilterBox()
        tools.addWidget(self.list_filter)
        self.tools_box.hide()
        body.addWidget(self.tools_box)
        self.artist_strip = ArtistStrip(self.art)
        self.artist_strip.hide()
        body.addWidget(self.artist_strip)
        self.search_cards = CardsPage(self.art)
        self.search_cards.hide()
        body.addWidget(self.search_cards, 1)

        self.model = TrackModel(self)
        # La tabla ve la lista a través del filtro y el orden; sus filas no
        # son las del modelo: se traducen con ``proxy.source_row``.
        self.proxy = TrackFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.table = TrackTable()
        self.table.setAccessibleName(tr("Pistas"))
        self.table.setModel(self.proxy)
        self.delegate = TrackDelegate(self.art, self._row_art_url, self.table)
        self.table.setItemDelegate(self.delegate)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(TrackDelegate.ROW_HEIGHT)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        head = self.table.horizontalHeader()
        head.setHighlightSections(False)
        # Clic en una columna: ascendente, descendente y vuelta al orden de la lista.
        head.setSectionsClickable(True)
        head.setSortIndicatorClearable(True)
        head.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)      # se abre en el orden de la lista
        self.table.setSortingEnabled(True)
        head.setSortIndicatorShown(False)         # la flecha la pone el rótulo (``headerData``)
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
        self.now_cover.setToolTip(tr("Ver la canción en grande, su artista y música parecida (Ctrl+I)"))
        now_row.addWidget(self.now_cover)
        now = QVBoxLayout()
        now.setSpacing(1)
        self.now_title = ElidedLabel(theme.ui_font(14, QFont.Weight.DemiBold), theme.HUMO, fade=True)
        self.now_title.setText(tr("Nada en reproducción"))
        self.now_artist = ElidedLabel(theme.ui_font(12), theme.HUMO, fade=True)
        now.addStretch(1)
        now.addWidget(self.now_title)
        now.addWidget(self.now_artist)
        now.addStretch(1)
        now_row.addLayout(now, 1)
        # Me gusta en la cuenta: solo con los cambios en la cuenta permitidos
        # y una canción de YouTube sonando.
        self.like_btn = IconButton("heart", tr("Me gusta"), size=34, glyph_px=15, checkable=True, quiet=True)
        self.like_btn.hide()
        now_row.addWidget(self.like_btn)
        now_box.setMinimumWidth(170)
        lay.addWidget(now_box, 3)

        center_box = QWidget()
        center_box.setMaximumWidth(720)
        center = QVBoxLayout(center_box)
        center.setContentsMargins(0, 6, 0, 6)
        center.setSpacing(0)
        controls = QHBoxLayout()
        controls.setSpacing(6)
        self.shuffle_btn = IconButton("shuffle", tr("Aleatorio"), checkable=True, quiet=True)
        self.prev_btn = IconButton("previous", tr("Anterior (Ctrl+Izquierda)"))
        self.play_btn = PlayButton()
        self.next_btn = IconButton("next", tr("Siguiente (Ctrl+Derecha)"))
        self.repeat_btn = IconButton("repeat_all", tr("Repetir: desactivado"), checkable=True, quiet=True)
        # Temporizador de apagado: solo se ve mientras hay uno programado, al
        # final de la fila; un hueco igual al principio mantiene centrado el
        # botón de reproducir.
        self.sleep_btn = PillButton("", "quiet", glyph="sleep", tabular=True)
        self.sleep_btn.setFixedHeight(30)
        self.sleep_btn.hide()
        self.sleep_twin = QWidget()
        self.sleep_twin.hide()
        controls.addWidget(self.sleep_twin)
        controls.addStretch(1)
        for btn in (self.shuffle_btn, self.prev_btn, self.play_btn, self.next_btn, self.repeat_btn):
            controls.addWidget(btn)
        controls.addStretch(1)
        controls.addWidget(self.sleep_btn)
        center.addLayout(controls)

        seek = QHBoxLayout()
        seek.setSpacing(4)
        time_font = self._time_font = theme.ui_font(12, tabular=True)
        self.pos_label = ElidedLabel(time_font, theme.HUMO, align=Qt.AlignmentFlag.AlignRight,
                                     width=self.TIME_LABEL_MIN)
        self.pos_label.setText("0:00")
        self.seek_slider = SeekSlider(nudge_keys=True)
        self.seek_slider.setAccessibleName(tr("Posición en la pista"))
        self.seek_slider.setRange(0, 1000)
        self.seek_slider.setEnabled(False)
        self.seek_slider.hover_text = lambda f: format_time(f * self._duration) if self._duration > 0 else ""
        self.dur_label = ElidedLabel(time_font, theme.HUMO, width=self.TIME_LABEL_MIN)
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
        self.lyrics_btn = PillButton(tr("Letra"), "quiet")
        self.lyrics_btn.setCheckable(True)
        self.lyrics_btn.setFixedHeight(30)
        self.lyrics_btn.setToolTip(tr("Mostrar u ocultar la letra (Ctrl+L)"))
        self.queue_btn = IconButton("queue", tr("Ver la cola de reproducción"), quiet=True)
        self.mute_btn = IconButton("volume", tr("Silenciar"), quiet=True)
        self.volume_slider = SeekSlider()
        self.volume_slider.setAccessibleName(tr("Volumen"))
        self.volume_slider.setRange(0, 100)
        self._pre_mute = saved_volume(self.db)
        self.volume_slider.setValue(self._pre_mute)
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
        self.table.row_moved.connect(self._on_row_moved)
        self.table.delete_requested.connect(self._on_rows_delete)
        # Filtro y orden: el arrastre solo vale con la lista tal cual es.
        self.list_filter.textChanged.connect(self._on_filter_text)
        self.list_filter.down_pressed.connect(self._filter_to_table)
        self.table.horizontalHeader().sortIndicatorChanged.connect(lambda *_: self._sync_list_tools())
        for signal in (self.model.modelReset, self.model.rowsInserted, self.model.rowsRemoved):
            signal.connect(lambda *_: self._sync_list_tools())
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
        self.heard_bar.changed.connect(self._set_heard_scope)
        self.tracker.logged.connect(self._on_play_logged)
        self.home.play_recent.connect(self._play_recent)
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
        self.sleep.changed.connect(self._on_sleep_changed)
        self.sleep.remaining_changed.connect(self._on_sleep_remaining)
        self.sleep.expired.connect(self._on_sleep_expired)
        self.sleep_btn.clicked.connect(self._open_sleep_badge_menu)

        self.bridge.extractor_state.connect(self._on_extractor_state)
        self.bridge.notice.connect(self._notice)
        self.bridge.progress.connect(self._on_progress)

        self.lastfm.changed.connect(self._sync_lastfm_actions)
        self.lastfm.session_lost.connect(lambda text: self._notice(text, "error"))
        self.lastfm.halted.connect(lambda text: self._notice(text, "error"))
        self.app_updates.available.connect(self._on_app_update)
        self.app_updates.checked.connect(
            lambda found: found or self._notice(tr("Tienes la última versión de la app.")))
        self.app_updates.failed.connect(lambda text: self._notice(text, "error"))
        self.footer.update_btn.clicked.connect(self._open_app_release)
        self.player.state_changed.connect(lambda _state: self._sync_discord())
        self.player.current_changed.connect(lambda _track: self._sync_discord())
        self.like_btn.clicked.connect(self._on_like_clicked)
        self.account.status_changed.connect(self._on_account_status)
        self.account.notice.connect(self._notice)
        self.account.playlist_created.connect(lambda _pid, _title: self.start_sync(silent=True))
        self.account.playlist_item_removed.connect(self._on_account_item_removed)
        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.setInterval(config.ACCOUNT_STATUS_DELAY_MS)
        self._status_timer.timeout.connect(lambda: self.account.refresh_status(self.player.current_track()))

        # Espacio pausa o reanuda salvo que el foco esté en un control que lo
        # usa (un botón se pulsa, un campo escribe el espacio): entonces el
        # atajo se aparta y la tecla le llega a ese control.
        self.space_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Space), self, activated=self.player.toggle_pause)
        QApplication.instance().focusChanged.connect(self._on_focus_changed)
        self.sidebar.itemActivated.connect(self._on_sidebar_activated)
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
        # La cola de la última vez vuelve detenida en su punto: no suena sola.
        self.session.restore()
        self._on_player_state(self.player.state)
        if paths.mpv_exe().is_file():
            self.mpv.start()
        self._startup_checks()
        if self.autostart_action.isChecked():
            # Si la carpeta del proyecto o el Python cambiaron, la orden se renueva.
            self.autostart.sync(True)
        lo, hi = config.SYNC_STARTUP_DELAY_S
        QTimer.singleShot(int(random.uniform(lo, hi) * 1000), self._auto_sync)
        self._sync_timer.start()
        # Lo que quedó en la cola de Last.fm (sin red la última vez) sale ahora.
        self.lastfm.flush()
        self._sync_discord()
        # La carpeta de música se repasa al poco de abrir (solo lo que cambió).
        QTimer.singleShot(4000, self, self._scan_local)
        QTimer.singleShot(config.APP_UPDATE_STARTUP_DELAY_S * 1000, lambda: self.app_updates.check())
        self._app_update_timer.start()

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
            self.banner_label.setText(tr("Descargando binarios… El progreso aparece al pie de la barra lateral."))
            self.banner_button.hide()
            self.banner_box.show()
        elif missing:
            self.banner_label.setText(
                tr("Faltan binarios en la carpeta bin: {names}. Sin ellos no se puede reproducir.",
                   names=", ".join(missing))
            )
            self.banner_button.setText(tr("Descargar ahora"))
            self.banner_button.show()
            self._banner_action = self.install_binaries
            self.banner_box.show()
        elif not paths.browser_json().is_file():
            self.banner_label.setText(
                tr("Aún no hay credenciales de la cuenta secundaria: la biblioteca no se puede "
                "sincronizar. La búsqueda funciona sin cuenta.")
            )
            self.banner_button.setText(tr("Iniciar sesión"))
            self.banner_button.show()
            self._banner_action = self.open_firefox_login
            self.banner_box.show()
        elif self.sync.state() is SyncState.AUTH_EXPIRED:
            text = (
                tr("La sesión de la cuenta secundaria caducó. La sincronización está detenida "
                "hasta que renueves las credenciales.")
            )
            if looks_device_bound(credentials.read_text_or_empty(paths.browser_json())):
                text += tr(" Esta vez inicia sesión con Firefox: las sesiones de Chrome duran pocas horas.")
            self.banner_label.setText(text)
            self.banner_button.setText(tr("Iniciar sesión otra vez"))
            self.banner_button.show()
            self._banner_action = self.open_firefox_login
            self.banner_box.show()
        else:
            self.banner_box.hide()

    def _run_banner_action(self) -> None:
        if self._banner_action is not None:
            self._banner_action()

    # ---------------------------------------------------------------- sidebar
    def _playlist_entries(self) -> list[tuple[str, str, str]]:
        """Lo que la barra lateral enseña de cada lista: clave, título y carátula."""
        return [(p.playlist_id, p.title, sized_url(self.db.playlist_cover(p), 96)) for p in self.db.playlists()]

    def _style_playlist_item(self, item: QListWidgetItem, title: str, url: str) -> None:
        collapsed = self._side_collapsed
        text = "" if collapsed else title
        if item.text() != text:
            item.setText(text)
        if item.data(Qt.ItemDataRole.AccessibleTextRole) != title:
            item.setData(Qt.ItemDataRole.AccessibleTextRole, title)
            item.setToolTip(title)
        if item.data(ROLE_ART) != url or item.icon().isNull():
            item.setData(ROLE_ART, url)
            item.setIcon(self._sidebar_icon(url))

    def _patch_sidebar(self, entries: list[tuple[str, str, str]]) -> None:
        """Pone las listas de la barra al día tocando solo las filas que
        cambian: la fila marcada y el desplazamiento se quedan donde estaban."""
        bar = self.sidebar.verticalScrollBar()
        scroll = bar.value()
        base = self._sidebar_row(NEW_LIST_KEY) + 1
        wanted = {key for key, _title, _url in entries}
        self.sidebar.blockSignals(True)
        try:
            for row in range(self.sidebar.count() - 1, base - 1, -1):
                if self.sidebar.item(row).data(ROLE_ID) not in wanted:
                    self.sidebar.takeItem(row)
            for offset, (key, title, url) in enumerate(entries):
                row = base + offset
                item = self.sidebar.item(row)
                if item is None or item.data(ROLE_ID) != key:
                    found = self._sidebar_row(key)
                    if found >= 0:
                        item = self.sidebar.takeItem(found)
                    else:
                        item = QListWidgetItem()
                        item.setData(ROLE_ID, key)
                    self.sidebar.insertItem(row, item)
                self._style_playlist_item(item, title, url)
            self.sidebar.setCurrentRow(self._sidebar_row(self._view_key or HOME_KEY))
        finally:
            self.sidebar.blockSignals(False)
        self._sidebar_entries = entries
        bar.setValue(scroll)
        self.active_mark.follow(animate=False)

    def _refresh_after_sync(self) -> None:
        entries = self._playlist_entries()
        if entries != self._sidebar_entries:
            self._patch_sidebar(entries)
        key = self._view_key or HOME_KEY
        if is_library(key):
            if self.db.get_playlist(key) is None:
                self._navigate(HOME_KEY)           # la lista que se veía ya no existe
            else:
                self._refresh_library_view(key)
        elif key == HOME_KEY:
            self._refresh_home()                   # reutiliza sus tarjetas: no parpadea

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

        add(glyph_icon("home"), tr("Inicio"), HOME_KEY)
        add(glyph_icon("search"), tr("Buscar"), SEARCH_KEY)
        add(glyph_icon("queue"), tr("Cola de reproducción"), QUEUE_KEY)
        add(glyph_icon("history"), tr("Historial"), HISTORY_KEY)
        if self._music_folder():
            add(glyph_icon("folder"), tr("En tu equipo"), LOCAL_KEY, self._music_folder())
        heading = QListWidgetItem("" if collapsed else tr("Tus listas"))
        heading.setFlags(Qt.ItemFlag.NoItemFlags)
        if collapsed:
            heading.setSizeHint(QSize(0, 14))     # sin texto queda como un respiro entre grupos
        self.sidebar.addItem(heading)
        add(glyph_icon("add"), tr("Nueva lista"), NEW_LIST_KEY,
            tr("Crea una lista que se guarda solo en esta app (Ctrl+N)"))
        entries = self._playlist_entries()
        for key, title, url in entries:
            item = add(self._sidebar_icon(url), title, key, title)
            item.setData(ROLE_ART, url)
        self._sidebar_entries = entries
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
        by_keys, self._sidebar_keys = self._sidebar_keys, False
        if by_keys:
            # Con las flechas se pasa por muchas filas seguidas: la marca sigue
            # a cada una al instante, pero la vista (base de datos, tabla,
            # cabecera) se carga solo cuando el usuario se detiene. Llegar a
            # «Nueva lista» de paso no abre el diálogo: lo abre Intro.
            self._nav_timer.start()
            return
        self._nav_timer.stop()
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

    def _settle_sidebar_nav(self) -> None:
        """Las flechas se detuvieron: se abre la vista de la fila marcada."""
        item = self.sidebar.currentItem()
        key = item.data(ROLE_ID) if item is not None else None
        if key and key != NEW_LIST_KEY:
            self._show_view(key)

    def _on_sidebar_activated(self, item: QListWidgetItem) -> None:
        """Intro (o doble clic) sobre una fila: sin esperar al reposo."""
        key = item.data(ROLE_ID)
        # Con el ratón, el primer clic ya actuó (y la marca volvió a la vista
        # abierta): el doble clic no debe abrir el diálogo una segunda vez.
        if not key or item is not self.sidebar.currentItem():
            return
        self._nav_timer.stop()
        if key == NEW_LIST_KEY:
            self._select_in_sidebar(self._view_key or HOME_KEY)
            QTimer.singleShot(0, self._create_local_playlist)
        else:
            self._show_view(key)

    @staticmethod
    def _uses_space(widget: Optional[QWidget]) -> bool:
        """¿El control con foco hace algo con la barra espaciadora?"""
        return isinstance(widget, (QAbstractButton, QAbstractSlider, QAbstractSpinBox, QComboBox,
                                   QLineEdit, QPlainTextEdit, QTextEdit, ClickableCover))

    def _on_focus_changed(self, _old, new) -> None:
        self.space_shortcut.setEnabled(not self._uses_space(new))

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
            tr("Desplegar la barra lateral (Ctrl+B)") if collapsed else tr("Contraer la barra lateral (Ctrl+B)")
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
        self._hold_slide(True)
        try:
            side.setMinimumWidth(self.SIDE_COLLAPSED)
            side.setMaximumWidth(self.SIDE_MAX)
            self._run_slide(side, side.width(), target, PANE_CLOSE_MS if collapsed else PANE_OPEN_MS, settle)
        finally:
            self._hold_slide(False)

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

    def is_sliding(self) -> bool:
        """¿Hay un panel deslizándose? El contenido cambia de ancho en cada
        fotograma: lo que se recoloca a mano espera al final."""
        return bool(self._slides) or self._slide_hold > 0 or self._settle_pending

    def layout_settling(self) -> bool:
        """¿Deslizando un panel o cambiando de vista? Los anchos se ajustan
        sin animarse (si no, sus tweens competirían con el deslizamiento)."""
        return self.is_sliding() or self._view_switching

    def _hold_slide(self, hold: bool) -> None:
        self._slide_hold += 1 if hold else -1

    def _finish_slides(self) -> None:
        """Acabó el último deslizamiento. El reajuste espera a que Qt
        termine de repartir el sitio (ocultar un panel se resuelve en el
        siguiente ciclo): así es uno solo, al ancho definitivo."""
        if not self._settle_pending:
            self._settle_pending = True
            QTimer.singleShot(0, self, self._settle_layouts)

    def _settle_layouts(self) -> None:
        """Fin del deslizamiento: un único reajuste al ancho definitivo de
        lo que esperó (rejillas de tarjetas, columnas de la tabla)."""
        self._settle_pending = False
        if self.is_sliding():
            return                      # empezó otro mientras tanto: ya se reajustará
        for grid in self.findChildren(CardGrid):
            grid.settle()
        self.table.fit_columns()

    def _run_slide(self, pane: QWidget, start: int, end: int, ms: int, done) -> None:
        previous = self._slides.pop(pane, None)
        if previous is not None:
            previous.stop()

        def step(value, slide=None) -> None:
            t = float(value)
            self._set_pane_width(pane, round(start + (end - start) * t))
            if t >= 1.0 and self._slides.get(pane) is tween:
                self._hold_slide(True)          # lo que haga ``done`` también cuenta
                try:
                    del self._slides[pane]
                    done()
                finally:
                    self._hold_slide(False)
                if not self._slides:
                    self._finish_slides()

        tween = Tween(self, 0.0, step, ms=ms, curve=EASE_DRAWER, movement=True)
        self._slides[pane] = tween
        step(0.0)
        tween.to(1.0)

    def _pane_min(self, pane: QWidget) -> int:
        return 270 if pane is self.lyrics else self.NOW_MIN

    def _slide_pane(self, pane: QWidget, opening: bool) -> None:
        """Abre o cierra un panel derecho (letra o «Ahora suena») deslizándolo
        desde el borde, en lugar de que aparezca o desaparezca de golpe."""
        animate = self._animate_panes()
        if animate:
            self._hold_slide(True)          # los anchos mínimos cambian antes del primer paso
        try:
            self._slide_pane_now(pane, opening)
        finally:
            if animate:
                self._hold_slide(False)
                if not self._slides:
                    self._finish_slides()   # no hubo deslizamiento: reajuste igualmente

    def _slide_pane_now(self, pane: QWidget, opening: bool) -> None:
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
        if room < natural and len(sizes) > 1:
            # Ni el mínimo del panel cabe: lo que falta lo cede la barra lateral
            # (hasta su propio mínimo) en vez de aplastar el contenido.
            side_min = self.SIDE_COLLAPSED if self._side_collapsed else self.SIDE_MIN
            give = min(natural - room, max(0, sizes[0] - side_min))
            if give > 0:
                sizes[0] -= give
                sizes[1] += give
                self.splitter.setSizes(sizes)
                room += give
        target = min(self._pane_widths.get(pane, 340), max(natural, room))
        self._pane_targets[pane] = target
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
        menu.addAction(tr("Reproducir"), lambda: self._play_library(key))
        if is_local_playlist(key):
            menu.addSeparator()
            self._add_local_actions(menu, key)
        else:
            menu.addAction(tr("Sincronizar esta lista"), lambda: self.start_sync(only_playlist=key))
        menu.exec(self.sidebar.viewport().mapToGlobal(point))

    # ----------------------------------------------------------- listas locales
    def _ask_text(self, title: str, label: str, text: str = "", ok: str = tr("Aceptar")) -> Optional[str]:
        dialog = QInputDialog(self)
        dialog.setWindowTitle(title)
        dialog.setLabelText(label)
        dialog.setTextValue(text)
        dialog.setOkButtonText(ok)
        dialog.setCancelButtonText(tr("Cancelar"))
        dialog.resize(380, dialog.sizeHint().height())
        if dialog.exec() != QInputDialog.DialogCode.Accepted:
            return None
        return dialog.textValue().strip() or None

    def _confirm(self, title: str, text: str, action: str) -> bool:
        box = QMessageBox(QMessageBox.Icon.NoIcon, title, text, parent=self)
        yes = box.addButton(action, QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(tr("Cancelar"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return box.clickedButton() is yes

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
        elif key == HISTORY_KEY:
            self._clear_history()
        elif key == QUEUE_KEY:
            self._clear_queue()
        elif key == LOCAL_KEY:
            menu = rounded_menu(QMenu(self))
            menu.addAction(tr("Volver a leer la carpeta"), lambda: self._scan_local(manual=True))
            menu.addAction(tr("Elegir otra carpeta…"), self._choose_music_folder)
            menu.addAction(tr("Dejar de usar la carpeta"), self._forget_music_folder)
            button = self.band.extra_btn
            menu.popup(button.mapToGlobal(QPoint(0, button.height() + 4)))

    # ------------------------------------------------------- carátulas y tinta
    def _row_art_url(self, track: Track) -> str:
        return track_art_url(track.video_id, track.thumb_url, 96)

    def _on_art_loaded(self, url: str) -> None:
        self.table.viewport().update()
        if url == self._band_art:
            self._apply_band_tint()
        if url == self._now_art:
            self._apply_ink()
            self._sync_tray_cover()
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
        if self.mini is not None:
            self.mini.set_ink(ink)
        self.table.viewport().update()

    def _show_view(self, key: str, force: bool = False) -> None:
        self._nav_timer.stop()            # una vista pedida de otro modo gana a las flechas
        if key == self._view_key and not force:
            return
        # Mientras la vista nueva se coloca (también los reajustes que Qt deja
        # para el siguiente ciclo), los anchos no se animan.
        if not self._view_switching:
            self._view_switching = True
            QTimer.singleShot(0, self, self._end_view_switch)
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
        self.heard_bar.setVisible(key == HISTORY_KEY)
        if key != previous:
            # Otra lista: llega entera y en su orden, sin el filtro de la anterior.
            self.list_filter.clear()
            self.table.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        self._sync_list_tools()
        # El botón de la cabecera es de cada vista: no hereda el estado de otra.
        self.band.extra_btn.setEnabled(True)
        self.band.extra_btn.setToolTip("")
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
            tracks, facts = self._queue_contents()
            self.model.set_tracks(tracks)
            current = self.player.current_track()
            self._band_art = track_art_url(current.video_id, current.thumb_url, 400) if current else ""
            self.band.set_content(
                tr("Cola de reproducción"),
                tr("Lo que suena y lo que viene después. Un doble clic salta a esa pista; "
                   "arrastra una fila para cambiarla de sitio."),
                facts, self._band_art, show_sync=False,
                extra=(tr("Vaciar la cola"), "close") if tracks else None,
            )
            self.band.extra_btn.setToolTip(tr("Quita de la cola todo menos lo que suena"))
            self.band.extra_btn.setEnabled(len(tracks) > 1)
            self._set_empty("" if tracks else tr("La cola está vacía"), tr("Reproduce una lista o una búsqueda."),
                            glyph="queue")
        elif key == HISTORY_KEY:
            self._fill_history()
        elif key == LOCAL_KEY:
            self._fill_local()
        elif key == SEARCH_KEY:
            # La búsqueda se conserva al salir y volver: texto, resultados y nota.
            self.model.set_tracks(self._search_tracks)
            self._band_art = ""
            self.band.set_content(
                tr("Buscar"), "", [self._scope_note()], "", show_search=True, show_sync=False, show_cover=False
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
                    extra=(tr("Opciones"), "more") if playlist.local else None,
                )
            else:
                self._band_art = ""
                self.band.set_content(tr("Lista"), "", [], "")
            if playlist is not None and playlist.local:
                self._set_empty("" if tracks else tr("Esta lista está vacía"),
                                tr("Añade canciones con el botón derecho sobre cualquier pista: "
                                "«Añadir a una lista»."))
            else:
                self._set_empty("" if tracks else tr("Esta lista aún no tiene pistas guardadas"),
                                tr("Sincronízala para verlas."))
        self._apply_band_tint()
        current = self.player.current_track()
        self.model.set_current(current.video_id if current else None)
        self.table.scrollToTop()
        self._update_buttons()
        self._fly_to_band()

    def _end_view_switch(self) -> None:
        self._view_switching = False

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

    def _set_empty(self, title: str, hint: str = "", glyph: str = "music") -> None:
        if title:
            self.empty.set_content(glyph, title, hint)
        self.empty.setVisible(bool(title))
        self.table.setVisible(not title and not self._cards_shown())
        self.search_cards.setVisible(not title and self._cards_shown())

    def _update_buttons(self) -> None:
        has_rows = self.proxy.rowCount() > 0
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

    # ------------------------------------------------------------------ inicio
    def _refresh_home(self) -> None:
        """Tus listas salen de SQLite al instante; las recomendaciones, de la
        caché, y se renuevan en segundo plano si ya tienen unas horas."""
        playlists = self.db.main_playlists()
        specs = [
            (
                p.title,
                plural(p.stored_tracks, "{n} canción", "{n} canciones") if p.stored_tracks
                else (tr("Lista local vacía") if p.local else tr("Sin sincronizar")),
                sized_url(self.db.playlist_cover(p), 400),
                p.playlist_id,
            )
            for p in playlists
        ]
        facts, empty_text = [], ""
        if playlists:
            facts = [
                plural(len(playlists), "{n} lista", "{n} listas"),
                plural(self.db.library_track_count(), "{n} canción", "{n} canciones"),
                tr("Sincronizada {format_ago}", format_ago=format_ago(self.db.get_float('last_sync_check') or None)),
            ]
        elif not paths.browser_json().is_file():
            empty_text = (
                tr("Aquí aparecerán tus listas cuando conectes la cuenta. Mientras tanto puedes "
                "buscar y reproducir cualquier canción sin iniciar sesión.")
            )
        else:
            empty_text = tr("Tu biblioteca aún no se ha sincronizado. Pulsa F5 para traer tus listas.")
        self.home.set_library(specs, facts, empty_text)
        self._refresh_recent()
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
            tr("Buscando listas de la comunidad parecidas a tu música…") if streaming
            else tr("Buscando otras listas…")
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
                self.home.set_status(tr("No se pudieron renovar las recomendaciones."), "retry")
            else:
                self.home.set_status(
                    tr("No se pudieron cargar las recomendaciones. Comprueba la conexión."), "retry"
                )
            return
        self._recs_shown = True
        if shelves and not streaming:
            self.home.set_shelves(shelves)
        if self.home.shelf_count():
            self.home.set_status("", "refresh")
        else:
            self._recs_retry_after = time.time() + 600
            self.home.set_status(tr("Por ahora no hay recomendaciones que enseñar."), "retry")

    # ------------------------------------------------------------ listas ajenas
    def _missing_binaries_notice(self) -> bool:
        if bootstrap.missing_binaries():
            self._notice(tr("Faltan binarios: abre Opciones (Ctrl+,) › «Mantenimiento» › «Binarios que faltan»."))
            return True
        return False

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
        self._notice(tr("Buscando a {name}…", name=name))

        def done(card, error) -> None:
            if error is not None:
                log.warning("artista %s: %s", name, error)
                self._notice(tr("No se pudo buscar al artista. Comprueba la conexión."), "error")
            elif card is None:
                self._notice(tr("YouTube Music no tiene una página de artista para «{name}».", name=name))
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
            self.band.set_content(card.name if card else tr("Artista"), "", [], self._band_art,
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
                              extra=(tr("Radio"), "music") if page.radio_id or page.top_songs else None,
                              round_cover=True)
        self.band.extra_btn.setToolTip(tr("Radio de {name}: sus canciones y otras parecidas", name=page.name))
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
            self._open_remote(PlaylistCard(page.songs_playlist, tr("Canciones de {name}", name=page.name),
                                           tr("De {name}", name=page.name), page.thumb_url))

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
        self._notice(tr("Cargando {value} de {name}…", value=tr("la mezcla") if shuffle else tr("la radio"), name=page.name))

        def done(tracks, error) -> None:
            tracks = list(tracks or []) if error is None else []
            if error is not None:
                log.warning("radio de %s: %s", page.name, error)
            if not tracks:
                tracks = list(page.top_songs)
                if shuffle:
                    random.shuffle(tracks)
            if not tracks:
                self._notice(tr("No se pudo cargar la radio. Comprueba la conexión."), "error")
                return
            self._play_list(tracks, 0)

        run_async(lambda: discover.artist_mix(page, shuffle=shuffle), done)

    # ------------------------------------------------------------ reproducción
    def _play_from(self, row: int) -> None:
        """Reproduce desde la fila ``row`` de la tabla (la que se ve). Suena
        lo que se ve y en el orden en que se ve: con un filtro, solo las que
        coinciden; ordenada por una columna, en ese orden."""
        if not (0 <= row < self.proxy.rowCount()):
            return
        if bootstrap.missing_binaries():
            self._notice(tr("Faltan binarios: abre Opciones (Ctrl+,) › «Mantenimiento» › «Binarios que faltan»."))
            return
        if self._view_key == QUEUE_KEY:
            # La fila es la de la vista; la cola salta a su posición real.
            self.player.jump_to(self.proxy.source_row(row))
            return
        tracks = self.proxy.tracks()
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
        if self.proxy.is_current(row) and self.player.current_track() is not None:
            self.player.toggle_pause()
        else:
            self._play_from(row)

    def _play_shuffled(self) -> None:
        if is_artist(self._view_key):
            # Su mezcla de YouTube Music: mucho más que las cinco populares.
            self._play_artist_mix(shuffle=True)
            return
        rows = self.proxy.rowCount()            # las que se ven: con un filtro, solo esas
        if not rows:
            return
        if not self.player.shuffle:
            self.player.set_shuffle(True)
        self._play_from(random.randrange(rows))

    def _on_current_changed(self, track: Optional[Track]) -> None:
        if track is None:
            self.now_title.setText(tr("Nada en reproducción"))
            self.now_title.set_color(theme.HUMO)
            self.now_artist.setText("")
            self.now_cover.set_url("")
            self._now_art = ""
            self.model.set_current(None)
            self._apply_ink()
            if self.now_panel.isVisible():
                self._load_now(None)
            self.setWindowTitle("YT Music Nativo")
            self._sync_tray_track()
            self._sync_like_button()
            return
        self._sync_like_button()
        if self.account.can_change(track):
            self._status_timer.start()      # pausa: saltar de pista en pista no pregunta por cada una
        self.now_title.setText(track.title)
        self.now_title.set_color(theme.PAPEL)
        self.now_artist.setText(track.artists)
        self._now_art = track_art_url(track.video_id, track.thumb_url, BAR_ART_PX)
        self.now_cover.set_url(self._now_art)
        self._apply_ink()
        self.model.set_current(track.video_id)
        self.setWindowTitle(f"{track.title} — YT Music Nativo")
        self._sync_tray_track()
        if self._view_key == QUEUE_KEY:
            self._refresh_queue()
        if self.lyrics.isVisible():
            self._load_lyrics(track)
        if self.now_panel.isVisible():
            self._load_now(track)

    def _on_player_state(self, state: str) -> None:
        # El icono cambia sin transición: Espacio se pulsa muchas veces al día.
        self.play_btn.set_playing(state in (PLAYING, LOADING))
        self.play_btn.set_loading(state == LOADING)
        self.table.set_playing(state == PLAYING)
        # Los aros de la cabecera giran con la música y frenan al pararse.
        self.band.set_playing(state == PLAYING)
        has_track = self.player.current_track() is not None
        self.play_btn.setEnabled(has_track)
        self.next_btn.setEnabled(has_track)
        self.prev_btn.setEnabled(has_track)
        # Detenida con una pista a medias (la cola recuperada al abrir) también
        # se puede mover: cambia el punto desde el que seguirá.
        self.seek_slider.setEnabled(has_track and state != LOADING)
        playing = state in (PLAYING, LOADING)
        if self.tray is not None:
            self.tray.set_playing(playing)
        if self.thumbbar is not None:
            self.thumbbar.set_state(playing, has_track)

    def _on_position(self, pos: float, duration: float) -> None:
        self._duration = duration
        self._position = pos
        self._sync_discord()
        self._fit_time_labels(duration)
        self.dur_label.setText(format_time(duration) if duration > 0 else "–:––")
        self.band.set_position(pos)
        if self._seeking:
            return
        self.pos_label.setText(format_time(pos))
        self.seek_slider.setValue(int(pos / duration * 1000) if duration > 0 else 0)
        self._highlight_lyric(pos)

    def _fit_time_labels(self, duration: float) -> None:
        """Las etiquetas de tiempo, del ancho de lo más largo que pueden
        enseñar: "59:59" casi siempre; "9:59:59" con pistas de una hora o más
        (antes se recortaban con puntos suspensivos)."""
        sample = "0:00:00" if duration >= 3600 else "00:00"
        width = max(self.TIME_LABEL_MIN, QFontMetrics(self._time_font).horizontalAdvance(sample) + 6)
        if width != self.pos_label.width():
            for label in (self.pos_label, self.dur_label):
                label.setFixedWidth(width)

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
        self.sleep.note_volume(value)        # si el temporizador está bajándolo, sigue desde aquí
        # Silenciar (o bajar la barra hasta 0) no es el volumen que el usuario
        # prefiere: se guarda el último audible, que es el que vuelve al abrir
        # y al quitar el silencio.
        if value > 0:
            self._pre_mute = value
            self.db.set_setting("volume", value)
        self._sync_volume_glyph(value)

    def _sync_volume_glyph(self, value: int) -> None:
        name = "mute" if value == 0 else "volume_low" if value < 34 else "volume_mid" if value < 67 else "volume"
        self.mute_btn.set_glyph(name)
        self.mute_btn.setToolTip(tr("Restaurar el volumen") if value == 0 else tr("Silenciar"))

    def _toggle_mute(self) -> None:
        if self.volume_slider.value() > 0:
            self._pre_mute = self.volume_slider.value()
            self.volume_slider.setValue(0)
        else:
            self.volume_slider.setValue(self._pre_mute or DEFAULT_VOLUME)

    def _on_modes_changed(self, shuffle: bool, repeat: str) -> None:
        self.shuffle_btn.setChecked(shuffle)
        self.repeat_btn.setChecked(repeat != REPEAT_OFF)
        self.repeat_btn.set_glyph("repeat_one" if repeat == REPEAT_ONE else "repeat_all")
        label = {REPEAT_OFF: tr("desactivado"), REPEAT_ALL: tr("toda la cola"), REPEAT_ONE: tr("una pista")}[repeat]
        self.repeat_btn.setToolTip(tr("Repetir: {label}", label=label))

    def _on_track_flagged(self, video_id: str) -> None:
        track = self.db.get_track(video_id)
        if track is not None:
            self.model.update_track(track)

    def _on_blocked(self, until: float) -> None:
        self._blocked_until = until
        self._on_tick()

    def _tracks_at(self, rows: list[int]) -> list[Track]:
        """Las pistas de esas filas de la tabla, en el orden en que se ven."""
        count = self.proxy.rowCount()
        return [self.proxy.track_at(row) for row in sorted(rows) if 0 <= row < count]

    def _on_table_menu(self, point) -> None:
        index = self.table.indexAt(point)
        if not index.isValid() or not (0 <= index.row() < self.proxy.rowCount()):
            return
        row = index.row()
        track = self.proxy.track_at(row)
        rows = self.table.selected_rows()
        global_pos = self.table.viewport().mapToGlobal(point)
        if row in rows and len(rows) > 1:
            # Clic derecho sobre una selección de varias: el menú es para todas.
            self._tracks_menu(self._tracks_at(rows)).exec(global_pos)
        else:
            self._track_menu(track, row).exec(global_pos)

    def _tracks_menu(self, tracks: list[Track]) -> QMenu:
        """Menú de varias pistas seleccionadas, en el orden de la tabla."""
        menu = rounded_menu(QMenu(self))
        menu_section(menu, plural(len(tracks), "{n} canción", "{n} canciones"), first=True)
        menu.addAction(tr("Reproducir a continuación"), lambda: self._queue_tracks(tracks, next_up=True))
        menu.addAction(tr("Añadir a la cola"), lambda: self._queue_tracks(tracks, next_up=False))
        menu.addSeparator()
        add_menu = rounded_menu(menu.addMenu(tr("Añadir a una lista")))
        locals_ = [p for p in self.db.playlists() if p.local]
        for playlist in locals_:
            add_menu.addAction(
                playlist.title, lambda pid=playlist.playlist_id: self._add_tracks_to_local_playlist(pid, tracks)
            )
        if locals_:
            add_menu.addSeparator()
        add_menu.addAction(tr("Nueva lista…"), lambda: self._create_local_playlist(tuple(tracks)))
        if is_local_playlist(self._view_key):
            view = self._view_key
            menu.addAction(tr("Quitar de esta lista"), lambda: self._remove_tracks_from_local_playlist(view, tracks))
        return menu

    def _track_menu(self, track: Track, row: Optional[int] = None) -> QMenu:
        """Menú de una pista. ``row`` es su fila en la tabla (la que se ve,
        no la del modelo); sin fila (música parecida del panel) «Reproducir»
        la pone a sonar a ella sola."""
        menu = rounded_menu(QMenu(self))
        if row is not None:
            menu.addAction(tr("Reproducir"), lambda: self._play_from(row))
        else:
            menu.addAction(tr("Reproducir"), lambda: self._play_single(track))
        menu.addAction(tr("Reproducir a continuación"), lambda: self._queue_track(track, next_up=True))
        menu.addAction(tr("Añadir a la cola"), lambda: self._queue_track(track, next_up=False))
        menu.addSeparator()
        add_menu = rounded_menu(menu.addMenu(tr("Añadir a una lista")))
        locals_ = [p for p in self.db.playlists() if p.local]
        for playlist in locals_:
            add_menu.addAction(
                playlist.title, lambda pid=playlist.playlist_id: self._add_to_local_playlist(pid, track)
            )
        if locals_:
            add_menu.addSeparator()
        add_menu.addAction(tr("Nueva lista…"), lambda: self._create_local_playlist((track,)))
        if row is not None and is_local_playlist(self._view_key):
            view = self._view_key
            menu.addAction(tr("Quitar de esta lista"), lambda: self._remove_from_local_playlist(view, track))
        if row is not None and self._view_key == QUEUE_KEY:
            # La fila de la tabla puede no ser la posición en la cola (filtro, orden).
            queue_pos = self.proxy.source_row(row)
            # La que suena no se quita de la cola: se pasa a la siguiente.
            if queue_pos != self.player.queue_snapshot()[1]:
                menu.addAction(tr("Quitar de la cola"), lambda: self._remove_from_queue(queue_pos, track))
        self._add_account_actions(menu, track, row)
        menu.addSeparator()
        if not is_local_track(track.video_id):
            menu.addAction(tr("Iniciar radio a partir de esta pista"), lambda: self._start_radio(track))
        lead = (discover.split_artists(track.artists) or [""])[0]
        if lead:
            menu.addAction(tr("Ir a {lead}", lead=lead), lambda: self._open_artist_named("", lead))
        return menu

    # ------------------------------------------------------- cuenta (opcional)
    def _add_account_actions(self, menu: QMenu, track: Track, row: Optional[int]) -> None:
        """Me gusta, biblioteca y listas de la cuenta: solo con los cambios en
        la cuenta permitidos. Quitar algo pide confirmación."""
        if not self.account.can_change(track):
            return
        menu.addSeparator()
        if self.account.liked(track.video_id):
            menu.addAction(tr("Quitar el me gusta"), lambda: self.account.set_liked(track, False))
        else:
            menu.addAction(tr("Me gusta"), lambda: self.account.set_liked(track, True))
        if self.account.in_library(track.video_id):
            menu.addAction(tr("Quitar de tu biblioteca de YouTube Music…"), lambda: self._remove_from_library(track))
        else:
            menu.addAction(tr("Guardar en tu biblioteca de YouTube Music"),
                           lambda: self.account.set_in_library(track, True))
        view = self._view_key
        if row is not None and is_library(view) and not is_local_playlist(view):
            set_video_id = self.db.playlist_set_video_id(view, track.video_id)
            if set_video_id:
                menu.addAction(tr("Quitar de esta lista en YouTube Music…"),
                               lambda: self._remove_from_account_playlist(view, track, set_video_id))

    def _set_account_mutations(self, enabled: bool) -> None:
        if enabled and not self.account.enabled and not self._confirm(
            tr("Cambios en tu cuenta"),
            tr("Con esta opción, «Me gusta», guardar en la biblioteca y crear o retocar listas "
            "modifican tu cuenta de YouTube Music en los servidores de Google, igual que en la web.\n\n"
            "Los cambios se envían de uno en uno y con pausas, pero cada uno es una petición más "
            "con tu cuenta. ¿Permitirlos?"),
            tr("Permitir"),
        ):
            self.mutations_action.setChecked(False)     # vuelve a pasar por aquí, apagada
            return
        self.account.set_enabled(enabled)
        self.account_history_action.setEnabled(enabled)
        self._sync_like_button()
        track = self.player.current_track()
        if enabled and self.account.can_change(track):
            self._status_timer.start()

    def _sync_like_button(self) -> None:
        track = self.player.current_track()
        visible = self.account.can_change(track)
        self.like_btn.setVisible(visible)
        if not visible:
            return
        liked = self.account.liked(track.video_id)
        self.like_btn.setChecked(liked)
        self.like_btn.set_glyph("heart_fill" if liked else "heart")
        self.like_btn.setToolTip(tr("Quitar el me gusta") if liked else tr("Me gusta"))

    def _on_like_clicked(self, checked: bool) -> None:
        track = self.player.current_track()
        if track is None or not self.account.set_liked(track, checked):
            self._sync_like_button()          # no se pudo: el botón vuelve a como estaba

    def _on_account_status(self, video_id: str) -> None:
        track = self.player.current_track()
        if track is not None and track.video_id == video_id:
            self._sync_like_button()

    def _remove_from_library(self, track: Track) -> None:
        if self._confirm(tr("Quitar de la biblioteca"),
                         tr("¿Quitar «{title}» de tu biblioteca de YouTube Music?", title=track.title), tr("Quitar")):
            self.account.set_in_library(track, False)

    def _remove_from_account_playlist(self, playlist_id: str, track: Track, set_video_id: str) -> None:
        playlist = self.db.get_playlist(playlist_id)
        name = playlist.title if playlist else tr("la lista")
        if self._confirm(tr("Quitar de la lista"),
                         tr("¿Quitar «{title}» de «{name}» en tu cuenta de YouTube Music?", title=track.title, name=name), tr("Quitar")):
            self._pending_removals[(playlist_id, track.video_id)] = set_video_id
            self.account.remove_from_playlist(playlist_id, track, set_video_id)

    def _on_account_item_removed(self, playlist_id: str, video_id: str) -> None:
        set_video_id = self._pending_removals.pop((playlist_id, video_id), None)
        self.db.remove_playlist_entry(playlist_id, video_id, set_video_id)
        if self._view_key == playlist_id:
            self._refresh_library_view(playlist_id)

    def _copy_to_account(self, playlist_id: str) -> None:
        playlist = self.db.get_playlist(playlist_id)
        if playlist is None:
            return
        tracks = [t for t in self.db.playlist_tracks(playlist_id) if not is_local_track(t.video_id)]
        if not tracks:
            self._notice(tr("La lista no tiene canciones de YouTube Music que copiar."))
            return
        title = self._ask_text(tr("Copiar en YouTube Music"), tr("Nombre de la lista en tu cuenta (privada):"),
                               playlist.title, tr("Crear"))
        if title:
            self.account.create_playlist(title, tracks)

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
        # Con la carátula desplazada fuera de la vista no hay de dónde salir:
        # la copia nacería entera sobre la cabecera. El panel se cierra sin vuelo.
        if not opening and flying and self.now_panel.cover_in_view():
            self._fly_cover(opening=False)
        self.now_action.setChecked(opening)
        if opening and flying:
            self._fly_cover(opening=True)

    def _projected_now_cover(self) -> QRectF:
        """Dónde quedará la carátula grande con el panel ya abierto a su ancho
        final, en coordenadas de la ventana. El panel es el último del
        divisor: acaba en su borde derecho."""
        panel = self.now_panel
        width = self._pane_targets.get(panel) or panel.width()
        origin = self.splitter.mapTo(self.centralWidget(), QPoint(self.splitter.width() - width, 0))
        return panel.projected_cover_rect(width, self.splitter.height()).translated(QPointF(origin))

    def _cover_rects(self, opening: bool = True) -> tuple[QRectF, QRectF]:
        """Origen y destino del vuelo, fijos para todo el trayecto (en
        coordenadas de la ventana): la carátula del reproductor y la grande.
        Al abrir, la grande es la proyectada con el panel a su ancho final;
        al cerrar, la que hay ahora (luego el panel se va encogiendo)."""
        host = self.centralWidget()
        small = QRectF(QPointF(self.now_cover.mapTo(host, QPoint(0, 0))),
                       QSizeF(self.now_cover.width(), self.now_cover.height()))
        if opening:
            big = self._projected_now_cover()
        else:
            cover = self.now_panel.cover
            big = cover.art_rect().translated(QPointF(cover.mapTo(host, QPoint(0, 0))))
        return small, big

    def _fly_cover(self, opening: bool) -> None:
        """La copia de la carátula viaja entre la barra y el panel; la grande
        no se pinta hasta que llega (o desde que se va)."""
        self._land_flight()
        cover = self.now_panel.cover
        image = cover.flight_image() or (self.art.image(self._now_art) if self._now_art else None)
        if image is None or image.isNull():
            return
        small, big = self._cover_rects(opening)
        if opening:
            cover.prepare(round(big.width()))     # lista para pintarse al aterrizar
        start, end = (small, big) if opening else (big, small)
        radii = (theme.R_COVER, theme.R_COVER + 2)
        cover.set_held(True)

        def landed() -> None:
            cover.set_held(False)
            if self._flight is flight:
                self._flight = None

        flight = CoverFlight(self.centralWidget(), image, start, end,
                             radii if opening else radii[::-1],
                             FLIGHT_MS if opening else PANE_CLOSE_MS, landed, aspect=cover.aspect())
        flight.opening = opening
        self._flight = flight
        flight.fly()

    def _land_flight(self) -> None:
        if self._flight is not None:
            self._flight.land()

    def _toggle_now(self, visible: bool) -> None:
        if self._swapping:
            return
        if visible:
            # Antes del vuelo: su destino es la carátula con el panel arriba del todo.
            self.now_panel.scroll_to_top()
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
            self._lyrics_message(tr("Reproduce una canción para ver su letra."))
            return
        if self._lyrics_for == track.video_id and self.lyrics.count() > 0:
            return
        video_id = self._lyrics_for = track.video_id
        entry = self.db.get_lyrics_entry(video_id)
        if entry is not None:
            fetched_at, payload = entry
            if payload is not None:
                cached = Lyrics.from_json(payload)
                if not cached.empty:
                    self._show_lyrics(cached)
                    return
                # Guardada pero ilegible: se busca de nuevo.
            elif time.time() - fetched_at < config.LYRICS_NEGATIVE_CACHE_S:
                self._lyrics_message(tr("Sin letra disponible para esta canción."))
                return
        self._lyrics_message(tr("Buscando letra…"))

        def done(result, error) -> None:
            if error is None:
                self.db.set_lyrics_entry(video_id, result.to_json() if result else None)
            if self._lyrics_for != video_id:
                return
            if error is not None:
                self._lyrics_message(tr("No se pudo buscar la letra (¿sin conexión?)."))
            elif result is None or result.empty:
                self._lyrics_message(tr("Sin letra disponible para esta canción."))
            else:
                self._show_lyrics(result)

        run_async(lambda: fetch_lyrics(track.title, track.artists, track.album, track.duration_s,
                                       video_id=video_id), done)

    def _show_lyrics(self, lyrics: Lyrics) -> None:
        self._lyrics = lyrics
        self._lyrics_index = -2
        texts = [ln.text or "♪" for ln in lyrics.lines] if lyrics.synced else lyrics.plain.splitlines()
        source = tr("de {source}", source=lyrics.source) if lyrics.synced else tr("de {source}, sin sincronizar", source=lyrics.source)
        self.lyrics.show_lines(texts, lyrics.synced, source)

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
            self._notice(tr("El tiempo de espera del bloqueo terminó: ya puedes reintentar."))
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
            text, alert = tr("Anti-bot: reintento en {format_time}", format_time=format_time(remaining)), True
        elif self._progress_text:
            text = self._progress_text
        elif self._syncing:
            text = tr("Sincronizando la biblioteca…")
        elif self._extractor_state == ExtractorState.UPDATING.value:
            text = tr("Actualizando yt-dlp…")
        elif self._extractor_state == ExtractorState.EXTRACTING.value:
            text = tr("Resolviendo el audio…")
        elif self.sync.state() is SyncState.AUTH_EXPIRED:
            text, alert = tr("Sesión caducada"), True
        elif self.sync.state() is SyncState.ERROR:
            text, alert = tr("Error en la última sincronización"), True
        elif not paths.browser_json().is_file():
            text = tr("Sin credenciales")
        else:
            text = tr("Sincronizada {format_ago}", format_ago=format_ago(self.db.get_float('last_sync_check') or None))
        self.footer.set_status(text, alert, "\n".join(part for part in (text, self._versions) if part))

    def _auto_sync(self) -> None:
        if not self._syncing and self.sync.due():
            self.start_sync(silent=True)

    def _sync_current_list(self) -> None:
        if is_library(self._view_key) and not is_local_playlist(self._view_key):
            self.start_sync(only_playlist=self._view_key)

    def start_sync(self, force_all: bool = False, only_playlist: Optional[str] = None, silent: bool = False) -> None:
        if self._syncing:
            return
        if not paths.browser_json().is_file():
            if not silent:
                self._notice(tr("Configura primero las credenciales de la cuenta secundaria."))
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
            self._notice(tr("Error de sincronización: {error}", error=error), "error")
        elif report is not None:
            self._notice(report.message)
        # La GUI lee solo de SQLite: se refresca lo que cambió, sin reconstruir
        # la barra lateral ni la vista (ni robar el foco o mover el desplazamiento).
        self._refresh_after_sync()
        self._refresh_status()
        self._refresh_banner()
        self._update_buttons()

    # ------------------------------------------------------------ herramientas
    def open_account(self) -> None:
        dialog = AccountDialog(self.db, self)
        dialog.credentials_changed.connect(self._on_credentials_changed)
        wants_firefox = []
        dialog.firefox_login_requested.connect(lambda: wants_firefox.append(True))
        dialog.exec()
        self.extractor.prewarm()   # el modo (con o sin cookies) pudo cambiar
        self._refresh_banner()
        self._update_buttons()
        if wants_firefox:
            self.open_firefox_login()

    def open_firefox_login(self) -> None:
        """Inicio de sesión guiado con un Firefox desechable; desde él se
        puede pasar a la importación manual de cabeceras."""
        dialog = FirefoxLoginDialog(parent=self)
        dialog.credentials_changed.connect(self._on_credentials_changed)
        wants_manual = []
        dialog.manual_requested.connect(lambda: wants_manual.append(True))
        dialog.exec()
        self.extractor.prewarm()
        self._refresh_banner()
        self._update_buttons()
        if wants_manual:
            self.open_account()

    def _on_credentials_changed(self) -> None:
        self.sync.credentials_renewed()
        self.extractor.clear_bot_block()
        self._blocked_until = 0.0
        self._refresh_banner()
        self._refresh_status()
        # Con cabeceras nuevas se sincroniza enseguida, sin esperar al temporizador.
        if paths.browser_json().is_file():
            QTimer.singleShot(0, lambda: self.start_sync(silent=True))

    def open_data_folder(self) -> None:
        paths.ensure_dirs()
        if paths.IS_WINDOWS:
            os.startfile(str(paths.app_home()))  # noqa: S606 - carpeta propia de la app

    def update_ytdlp(self) -> None:
        if not paths.ytdlp_exe().is_file():
            self._notice(tr("Falta yt-dlp: descarga primero los binarios."))
            return
        self._notice(tr("Buscando actualización de yt-dlp…"))
        run_async(lambda: self.extractor.update("manual"), self._on_update_done)

    def _on_update_done(self, result: Optional[UpdateResult], error) -> None:
        if error is not None or result is None:
            self._notice(tr("La actualización falló: {error}", error=error), "error")
            return
        text = {
            UpdateStatus.UPDATED: tr("yt-dlp actualizado a {new_version}.", new_version=result.new_version),
            UpdateStatus.UP_TO_DATE: tr("yt-dlp ya está al día ({old_version}).", old_version=result.old_version),
            UpdateStatus.SKIPPED_COOLDOWN: tr("Actualización omitida por el cooldown."),
        }.get(result.status, result.message or result.status.value)
        self._notice(text)
        run_async(self.extractor.diagnostics, lambda d, e: d and self._apply_diagnostics(d))

    def _on_update_done_silent(self, result: Optional[UpdateResult], error) -> None:
        if error is None and result is not None and result.status not in (
            UpdateStatus.SKIPPED_COOLDOWN, UpdateStatus.UP_TO_DATE
        ):
            self._on_update_done(result, None)

    def install_binaries(self) -> None:
        if self._installing:
            return
        missing = bootstrap.missing_binaries()
        if not missing:
            self._notice(tr("No falta ningún binario."))
            return
        answer = QMessageBox.question(
            self,
            tr("Descargar binarios"),
            tr("Se descargarán desde sus repositorios oficiales en GitHub: {names}.\n\n"
               "Destino: {bin_dir}\n\n¿Continuar?", names=", ".join(missing), bin_dir=paths.bin_dir()),
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
            QMessageBox.warning(self, tr("Descargar binarios"), "\n".join(problems))
        else:
            self._notice(tr("Binarios instalados."))
        if paths.mpv_exe().is_file() and not self.mpv.is_ready():
            self.mpv.start()
        self._startup_checks()

    def show_diagnostics(self) -> None:
        def show(diag, error) -> None:
            if error is not None or not diag:
                QMessageBox.warning(self, tr("Diagnóstico"), tr("No se pudo obtener el diagnóstico: {error}", error=error))
                return
            now = time.time()
            mode = {"hybrid": tr("híbrido"), "cookies": tr("siempre con cookies"), "anon": tr("siempre anónimo")}[
                diag["playback_mode"]
            ]
            sticky = self.db.get_float("cookies_sticky_until")
            fast_off = self.db.get_float("fast_client_off_until")
            if not config.FAST_PLAYER_CLIENT:
                fast = tr("desactivado")
            elif fast_off > now:
                fast = tr("{FAST_PLAYER_CLIENT}, en pausa {format_time} más (fallaba)", FAST_PLAYER_CLIENT=config.FAST_PLAYER_CLIENT, format_time=format_time(fast_off - now))
            else:
                fast = tr("{FAST_PLAYER_CLIENT} (solo en modo anónimo)", FAST_PLAYER_CLIENT=config.FAST_PLAYER_CLIENT)
            lines = [
                tr("Carpeta de datos: {app_home}", app_home=paths.app_home()),
                f"yt-dlp: {diag['ytdlp_version'] or tr('no instalado')}",
                f"deno: {diag['deno_version'] or tr('no instalado')}"
                + ("" if diag["deno_ok"] else f" — {diag['deno_message']}"),
                f"mpv: {tr('conectado') if self.mpv.is_ready() else tr('no conectado')}",
                tr("Modo de resolución: {mode}", mode=mode),
                tr("Cliente rápido de YouTube: {fast}", fast=fast),
                f"cookies.txt: {tr('presente') if diag['cookies_present'] else tr('ausente')}"
                + (tr(" (en uso permitido)") if diag["cookies_usable"] else tr(" (yt-dlp no las usa)")),
                tr("Modo pegajoso con cookies: ")
                + (tr("activo {format_time} más", format_time=format_time(sticky - now)) if sticky > now else tr("inactivo")),
                tr("Resoluciones en la última hora: {resolves_since} (tope para prefetch: "
                   "{MAX_RESOLVES_PER_HOUR})", resolves_since=self.db.resolves_since(now - 3600), MAX_RESOLVES_PER_HOUR=config.MAX_RESOLVES_PER_HOUR),
                tr("Resoluciones en 24 h: {resolves_since}", resolves_since=self.db.resolves_since(now - 86400)),
                tr("Última comprobación de yt-dlp: {format_ago}", format_ago=format_ago(self.db.get_float('last_update_timestamp') or None)),
                tr("Estado de sincronización: {value}", value=self.sync.state().value),
            ]
            QMessageBox.information(self, tr("Diagnóstico"), "\n".join(lines))

        run_async(self.extractor.diagnostics, show)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - API de Qt
        kind = event.type()
        if kind == QEvent.Type.KeyPress and obj is self.sidebar:
            # Lo anota antes de que la lista mueva su fila: ``_on_sidebar_changed``
            # sabrá que el cambio vino de las flechas.
            self._sidebar_keys = event.key() in self._SIDEBAR_NAV_KEYS
            if self._sidebar_keys:
                # Si la tecla no movió la fila (ya estaba arriba), no cuenta para la próxima.
                QTimer.singleShot(0, self, lambda: setattr(self, "_sidebar_keys", False))
        elif kind == QEvent.Type.MouseButtonPress:
            self._sidebar_keys = False
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
        elif event.type() == event.Type.WindowStateChange and hasattr(self, "band"):
            # Minimizada nadie ve los aros: su temporizador se detiene.
            self.band.set_window_shown(not self.isMinimized())
            if self.isMinimized() and self._minimizes_to_tray():
                # Fuera de este evento: ocultar desde dentro deja a Windows
                # con un botón fantasma en la barra de tareas.
                QTimer.singleShot(0, self._hide_minimized)

    def nativeEvent(self, event_type, message):  # noqa: N802 - API de Qt
        # Solo Windows: los clics en los botones de la miniatura de la barra
        # de tareas llegan como mensajes a la ventana.
        # ``message`` llega como VoidPtr, que es falso en un ``if``: hay que
        # mirar su dirección. La implementación de Qt no hace nada más que
        # devolver "no atendido", así que no se la llama.
        if event_type != b"windows_generic_MSG":
            return False, 0
        address = int(message)
        if not address:
            return False, 0
        msg = wintypes.MSG.from_address(address)
        # Windows avisa así de que el usuario cambió entre claro y oscuro.
        if msg.message == theme.WM_SETTINGCHANGE and theme.is_color_set_change(msg.lParam or 0):
            self._on_system_theme_changed()
            return False, 0
        bar = self.thumbbar
        if bar is not None and bar.active:
            if bar.handle(int(msg.hWnd or 0), msg.message, msg.wParam or 0, msg.lParam or 0):
                return True, 0
        return False, 0

    # -------------------------------------------------------------------- tema
    @property
    def theme_mode(self) -> str:
        """"system", "light" o "dark": lo elegido, no el esquema en uso."""
        return self._theme_mode

    def set_theme_mode(self, mode: str) -> None:
        mode = theme.normalize_mode(mode)
        self._theme_mode = mode
        self.db.set_setting(config.THEME_KEY, mode)
        action = self.theme_actions.get(mode)
        if action is not None and not action.isChecked():
            action.setChecked(True)
        self._apply_theme()

    def _on_system_theme_changed(self) -> None:
        # Windows manda varios avisos seguidos: se espera a que pare.
        timer = getattr(self, "_theme_timer", None)
        if timer is not None and self._theme_mode == "system":
            timer.start()

    def _apply_theme(self, rebuild: bool = True) -> bool:
        """Pone el esquema que toca (el de Windows en modo "system") sin
        reiniciar nada: mpv sigue sonando y las vistas conservan su estado.
        Devuelve si cambió."""
        previous = theme.scheme()
        target = theme.resolve_mode(self._theme_mode)
        if target == previous:
            return False
        theme.use_scheme(target)
        art_style.on_theme_changed()
        app = QApplication.instance()
        if app is not None:
            chrome.apply_theme(app)
        if not rebuild:
            return True                      # aún no hay nada construido
        log.info("tema: %s", target)
        self.art.forget_tints()
        chrome.refresh_checks()
        for widget in QApplication.allWidgets():
            retheme = getattr(widget, "retheme", None)
            if callable(retheme):
                try:
                    retheme(previous)
                except Exception:  # noqa: BLE001 - un detalle de color no debe tumbar la app
                    log.exception("no se pudo recolorear %s", type(widget).__name__)
        self._refresh_sidebar_icons()
        self._apply_band_tint()
        self._apply_ink()
        for window in QApplication.topLevelWidgets():
            if isinstance(window, (QMainWindow, QDialog)) and window.isVisible():
                theme.tint_title_bar(window)
        return True

    def _refresh_sidebar_icons(self) -> None:
        glyphs = {HOME_KEY: "home", SEARCH_KEY: "search", QUEUE_KEY: "queue", HISTORY_KEY: "history",
                  LOCAL_KEY: "folder", NEW_LIST_KEY: "add"}
        dpr = self.devicePixelRatioF()
        for row in range(self.sidebar.count()):
            item = self.sidebar.item(row)
            key = item.data(ROLE_ID)
            if key in glyphs:
                item.setIcon(self._fixed_icon(glyph_pixmap(glyphs[key], 32, theme.HUMO, dpr)))
            elif key:
                item.setIcon(self._sidebar_icon(item.data(ROLE_ART) or ""))

    # -------------------------------------------------------------- escritorio
    def _setup_desktop(self) -> None:
        self.setWindowIcon(app_icon())
        if TrayIcon.available():
            tray = self.tray = TrayIcon(self)
            tray.show_requested.connect(self.bring_to_front)
            tray.toggle_requested.connect(self.player.toggle_pause)
            tray.next_requested.connect(self.player.next_track)
            tray.previous_requested.connect(self.player.previous_track)
            tray.mini_requested.connect(self.show_mini)
            tray.quit_requested.connect(self.quit_app)
            tray.show()
        self.thumbbar = ThumbBar(self)
        self.thumbbar.clicked.connect(self._on_thumb_button)
        self.mpv.set_normalization("loudnorm" if self.equalize_action.isChecked() else None)
        self._apply_audio_preferences()
        self.mpv.device_lost.connect(self._on_audio_device_lost)

    def tray_ready(self) -> bool:
        """¿Hay icono en la bandeja? (Sin él, ocultar la ventana la perdería.)"""
        return self.tray is not None and self.tray.is_visible()

    def _hides_on_close(self) -> bool:
        return self.tray_ready() and self.close_to_tray_action.isChecked()

    def _minimizes_to_tray(self) -> bool:
        return self.tray_ready() and self.minimize_to_tray_action.isChecked()

    def _hide_minimized(self) -> None:
        if self.isMinimized() and self._minimizes_to_tray():
            self.hide()

    def _hide_to_tray(self) -> None:
        self.hide()
        if self.tray is not None and not self.db.get_bool("tray_hint_shown"):
            self.db.set_setting("tray_hint_shown", "1")
            self.tray.notify(
                tr("La música sigue sonando"),
                tr("YT Music Nativo queda en la bandeja. Para cerrarlo del todo, "
                "usa «Salir definitivamente» en el menú de este icono."),
            )

    def bring_to_front(self) -> None:
        """Vuelve la ventana (desde la bandeja, el mini reproductor o al abrir
        la app otra vez) y la pone delante."""
        if self.mini is not None and self.mini.isVisible():
            self.mini.close_quietly()
        state = self.windowState() & ~Qt.WindowState.WindowMinimized
        self.setWindowState(state | Qt.WindowState.WindowActive)
        self.show()
        self.raise_()
        self.activateWindow()

    def quit_app(self) -> None:
        """Salir de verdad: cierra todo aunque la opción de la bandeja esté activa."""
        self._quitting = True
        self.close()
        self._exit_app()

    def _sync_tray_track(self) -> None:
        if self.tray is None:
            return
        track = self.player.current_track()
        self.tray.set_track(track, self._tray_cover() if track is not None else None)

    def _sync_tray_cover(self) -> None:
        if self.tray is not None and self.player.current_track() is not None:
            self.tray.set_cover(self._tray_cover())

    def _tray_cover(self) -> Optional[QPixmap]:
        if not self._now_art:
            return None
        return self.art.pixmap(self._now_art, 32, radius=theme.R_THUMB, dpr=self.devicePixelRatioF())

    def _on_thumb_button(self, action: str) -> None:
        {"previous": self.player.previous_track, "toggle": self.player.toggle_pause,
         "next": self.player.next_track}[action]()

    def _set_autostart(self, enabled: bool) -> None:
        done = self.autostart.enable() if enabled else self.autostart.disable()
        if not done:
            self.autostart_action.blockSignals(True)
            self.autostart_action.setChecked(not enabled)
            self.autostart_action.blockSignals(False)
            self._notice(tr("No se pudo cambiar el inicio con Windows."), "error")
            return
        self.db.set_setting("autostart_with_windows", "1" if enabled else "0")

    def _set_equalize(self, enabled: bool) -> None:
        self.db.set_setting("equalize_volume", "1" if enabled else "0")
        self.mpv.set_normalization("loudnorm" if enabled else None)

    # -- salida, ecualizador y transición (Opciones › Reproducción) ----------
    def audio_devices(self) -> list[tuple[str, str]]:
        """Las salidas de audio que hay ahora (nombre, descripción), sin la
        predeterminada de Windows."""
        return self.mpv.audio_devices()

    def audio_device(self) -> tuple[str, str]:
        """La salida elegida (nombre, descripción), aunque ahora no esté conectada."""
        return (self.db.get_setting(AUDIO_DEVICE_KEY, AUTO_DEVICE) or AUTO_DEVICE,
                self.db.get_setting(AUDIO_DEVICE_NAME_KEY, "") or "")

    def set_audio_device(self, name: str) -> None:
        name = name or AUTO_DEVICE
        description = dict(self.mpv.audio_devices()).get(name, "") if name != AUTO_DEVICE else ""
        if name == self.audio_device()[0] and not description:
            description = self.audio_device()[1]     # elegida otra vez estando desconectada
        self.db.set_setting(AUDIO_DEVICE_KEY, name)
        self.db.set_setting(AUDIO_DEVICE_NAME_KEY, description or None)
        self.mpv.set_audio_device(name, description)

    def _on_audio_device_lost(self, name: str) -> None:
        self._notice(tr("Se desconectó «{name}»: la música sigue por la salida predeterminada de Windows.",
                        name=name))

    def equalizer_preset(self) -> str:
        preset = self.db.get_setting(EQUALIZER_KEY, EQ_FLAT) or EQ_FLAT
        return preset if preset in EQ_PRESETS else EQ_FLAT

    def set_equalizer_preset(self, preset: str) -> None:
        preset = preset if preset in EQ_PRESETS else EQ_FLAT
        self.db.set_setting(EQUALIZER_KEY, preset)
        self.mpv.set_equalizer(preset)

    def transition_mode(self) -> str:
        return self.player.transition

    def set_transition_mode(self, mode: str) -> None:
        self.player.set_transition(mode)

    def crossfade_seconds(self) -> int:
        return self.player.crossfade_s

    def set_crossfade_seconds(self, seconds: int) -> None:
        self.player.set_crossfade_seconds(seconds)

    def _apply_audio_preferences(self) -> None:
        """Salida y ecualizador guardados (al arrancar y al importar una copia)."""
        name, description = self.audio_device()
        self.mpv.set_audio_device(name, description)
        self.mpv.set_equalizer(self.equalizer_preset())

    # -- Discord ------------------------------------------------------------
    def _set_discord(self, enabled: bool) -> None:
        if enabled and not valid_client_id(self.discord.client_id):
            client_id = self._ask_discord_client_id()
            if not client_id:
                self.discord_action.blockSignals(True)
                self.discord_action.setChecked(False)
                self.discord_action.blockSignals(False)
                return
            self.discord.set_client_id(client_id)
        self.discord.set_enabled(enabled)
        self._sync_discord()

    def _ask_discord_client_id(self) -> Optional[str]:
        text, ok = QInputDialog.getText(
            self, tr("Mostrar en Discord"),
            tr("Discord necesita el id de una aplicación registrada en\n"
            "discord.com/developers/applications (su nombre es lo que\n"
            "aparece tras «Escuchando»). Pega aquí su «Application ID»:"),
        )
        text = text.strip()
        if not ok or not text:
            return None
        if not valid_client_id(text):
            self._notice(tr("Ese id no es válido: son solo cifras (unas 18 o 19)."), "error")
            return None
        return text

    def _sync_discord(self) -> None:
        """Le cuenta a Discord lo que suena (el servicio descarta lo repetido)."""
        if not self.discord.enabled:
            return
        state = self.player.state
        track = self.player.current_track()
        if state == LOADING:
            return                           # entre dos pistas: se espera a la nueva
        if track is None or state not in (PLAYING, PAUSED):
            self.discord.update(None)
            return
        self.discord.update(track, track_art_url(track.video_id, track.thumb_url, 512),
                            state == PLAYING, self._position, self._duration)

    # -- Last.fm --------------------------------------------------------------
    def _sync_lastfm_actions(self) -> None:
        connected = self.lastfm.connected
        self.lastfm_action.blockSignals(True)
        self.lastfm_action.setChecked(self.lastfm.active)
        self.lastfm_action.blockSignals(False)
        self.lastfm_action.setEnabled(connected)
        if connected:
            who = self.lastfm.user or tr("la cuenta")
            self.lastfm_account_action.setText(tr("Desconectar Last.fm ({who})", who=who))
            self.lastfm_action.setToolTip("")
        else:
            self.lastfm_account_action.setText(tr("Conectar con Last.fm…"))
            self.lastfm_action.setToolTip(tr("Conecta antes una cuenta de Last.fm."))

    def _set_lastfm(self, enabled: bool) -> None:
        self.lastfm.set_enabled(enabled and self.lastfm.connected)

    def manage_lastfm_account(self) -> None:
        if self.lastfm.connected:
            if self._confirm(tr("Desconectar Last.fm"),
                             tr("Se olvidará la sesión de Last.fm de este equipo y las escuchas "
                             "que esperaban en la cola. Tu perfil de Last.fm no cambia."),
                             tr("Desconectar")):
                self.lastfm.disconnect()
                self._notice(tr("Last.fm desconectado."))
            return
        if not self.lastfm.has_app_credentials and not self._ask_lastfm_app():
            return
        self._notice(tr("Abriendo Last.fm en el navegador…"))
        self.lastfm.request_authorization(self._on_lastfm_token)

    def _ask_lastfm_app(self) -> bool:
        key, ok = QInputDialog.getText(
            self, tr("Conectar con Last.fm"),
            tr("Last.fm pide una clave de aplicación propia. Créala gratis en\n"
            "last.fm/api/account/create y pega aquí su «API key»:"),
        )
        if not ok or not key.strip():
            return False
        secret, ok = QInputDialog.getText(
            self, tr("Conectar con Last.fm"), tr("Y ahora su «Shared secret» (se guarda cifrado):"),
            QLineEdit.EchoMode.Password,
        )
        if not ok or not secret.strip():
            return False
        try:
            self.lastfm.set_app_credentials(key, secret)
        except Exception as exc:  # noqa: BLE001 - p. ej. sin cifrado disponible
            log.warning("no se pudo guardar la clave de Last.fm (%s)", type(exc).__name__)
            self._notice(tr("No se pudo guardar la clave de Last.fm de forma segura."), "error")
            return False
        return True

    def _on_lastfm_token(self, url: Optional[str], error) -> None:
        if error is not None or not url:
            log.info("Last.fm no dio un token: %s", error)
            self._notice(tr("No se pudo conectar con Last.fm: {error}", error=error), "error")
            return
        QDesktopServices.openUrl(QUrl(url))
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle(tr("Conectar con Last.fm"))
        box.setText(tr("Se abrió Last.fm en el navegador."))
        box.setInformativeText(tr("Inicia sesión allí, pulsa «Sí, permitir acceso» y vuelve aquí."))
        done = box.addButton(tr("Ya lo permití"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(tr("Cancelar"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(done)
        box.exec()
        if box.clickedButton() is done:
            self.lastfm.complete_authorization(self._on_lastfm_connected)

    def _on_lastfm_connected(self, user: Optional[str], error) -> None:
        if error is not None:
            log.info("Last.fm no dio la sesión: %s", error)
            self._notice(tr("Last.fm no confirmó el permiso. Vuelve a intentarlo."), "error")
            return
        self._notice(tr("Last.fm conectado como {value}: tus escuchas se enviarán allí.", value=user or tr("tu cuenta")))

    # -- versión nueva de la app ------------------------------------------
    def _on_app_update(self, version: str, _url: str) -> None:
        self.footer.set_update(version)
        self.app_update_action.setText(tr("Versión {version} disponible: ver novedades", version=version))
        self.app_update_action.setVisible(True)
        if self.db.get_setting("app_update_announced") != version:
            self.db.set_setting("app_update_announced", version)
            self._notice(tr("Hay una versión nueva de la app ({version}). Su enlace está al pie de la "
                            "barra lateral.", version=version))

    def _open_app_release(self) -> None:
        release = self.app_updates.release
        if release is not None:
            # Solo se abre la página: nada se descarga ni se instala solo.
            QDesktopServices.openUrl(QUrl(release.url))

    # -- mini reproductor ---------------------------------------------------
    def show_mini(self) -> None:
        if self.mini is None:
            self.mini = MiniPlayer(self.player, self.art, self.db)
            self.mini.restore_requested.connect(self.bring_to_front)
            self.mini.set_ink(QColor(self._ink.value))
        self.mini.place()
        self.mini.show()
        self.mini.raise_()
        self.mini.activateWindow()
        self.hide()

    def toggle_mini(self) -> None:
        if self.mini is not None and self.mini.isVisible():
            self.bring_to_front()
        else:
            self.show_mini()

    # -- temporizador de apagado -------------------------------------------
    def _ask_sleep_minutes(self) -> None:
        minutes, ok = QInputDialog.getInt(
            self, tr("Temporizador de apagado"), tr("Parar la música dentro de (minutos):"), 90, 1, 600, 5)
        if ok:
            self.sleep.start_minutes(minutes)

    def _on_sleep_changed(self) -> None:
        active = self.sleep.active
        self.sleep_cancel_action.setEnabled(active)
        if not active:
            self.sleep_btn.hide()
            self.sleep_twin.hide()
            return
        metrics = QFontMetrics(theme.ui_font(13, QFont.Weight.Medium, tabular=True))
        if self.sleep.mode == TRACK:
            self.sleep_btn.set_label(tr("Al acabar"), "sleep")
            self.sleep_btn.setToolTip(tr("La música se parará al terminar esta canción. Pulsa para cancelar."))
            template = tr("Al acabar")
        else:
            left = self.sleep.remaining_seconds() or 0
            self.sleep_btn.set_label(format_time(left), "sleep")
            ends = time.strftime("%H:%M", time.localtime(time.time() + left))
            self.sleep_btn.setToolTip(tr("La música se parará a las {ends}. Pulsa para posponer o cancelar.", ends=ends))
            template = "8:88:88" if left >= 3600 else "88:88"
        # Ancho fijo para la cuenta atrás: la barra no se recoloca cada segundo.
        width = metrics.horizontalAdvance(template) + 22 + 2 * PillButton.PAD
        self.sleep_btn.setFixedWidth(width)
        self.sleep_twin.setFixedWidth(width)
        self.sleep_btn.show()
        self.sleep_twin.show()

    def _on_sleep_remaining(self, seconds: int) -> None:
        if self.sleep.mode == TIME:
            if seconds in (3599, 3600):
                self._on_sleep_changed()          # cambia el ancho al pasar de la hora
            self.sleep_btn.setText(format_time(seconds))
            self.sleep_btn.update()

    def _open_sleep_badge_menu(self) -> None:
        menu = rounded_menu(QMenu(self))
        if self.sleep.mode == TIME:
            menu.addAction(tr("Posponer 10 minutos"), lambda: self.sleep.postpone(10))
        menu.addAction(tr("Cancelar el temporizador"), self.sleep.cancel)
        menu.popup(self.sleep_btn.mapToGlobal(QPoint(0, -menu.sizeHint().height() - 4)))

    def _on_sleep_expired(self) -> None:
        if self.sleep_quit_action.isChecked():
            self.quit_app()
            return
        self._notice(tr("Temporizador de apagado: la música se detuvo."))

    # -- copia de seguridad -------------------------------------------------
    def _backup_folder(self) -> str:
        documents = Path.home() / "Documents"
        return str(documents if documents.is_dir() else Path.home())

    def export_backup(self) -> None:
        suggested = Path(self._backup_folder()) / f"YT Music Nativo {time.strftime('%Y-%m-%d')}.json"
        path, _ = QFileDialog.getSaveFileName(
            self, tr("Exportar copia de seguridad"), str(suggested), tr("Copia de seguridad (*.json)"))
        if not path:
            return

        def work():
            try:
                return backup.export_backup(self.db, path)
            finally:
                self.db.close_thread_connection()

        def done(result, error) -> None:
            if error is not None:
                log.warning("no se pudo exportar la copia", exc_info=error)
                self._notice(tr("No se pudo guardar la copia: {error}", error=error), "error")
            else:
                self._notice(tr("Copia guardada: {summary}.", summary=result.summary()))

        run_async(work, done)

    def import_backup(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("Importar copia de seguridad"), self._backup_folder(),
            tr("Copia de seguridad (*.json);;Todos los archivos (*)"))
        if path:
            run_async(lambda: backup.read_backup(path), self._on_backup_read)

    def _on_backup_read(self, data: Optional[backup.Backup], error) -> None:
        if error is not None:
            message = str(error) if isinstance(error, backup.BackupError) else tr("No se pudo leer la copia.")
            self._notice(message, "error")
            return
        strategy = self._ask_backup_strategy(data)
        if strategy is None:
            return

        def work():
            try:
                return backup.import_backup(self.db, data, strategy)
            finally:
                self.db.close_thread_connection()

        run_async(work, self._on_backup_imported)

    def _ask_backup_strategy(self, data: backup.Backup) -> Optional[str]:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(tr("Importar copia de seguridad"))
        box.setText(tr("La copia tiene {summary}.", summary=data.summary()))
        box.setInformativeText(
            tr("Fusionar añade lo que falta sin borrar nada ni repetir canciones. "
            "Sobrescribir deja tus listas locales, el historial y las preferencias "
            "como estaban en la copia (las listas de tu cuenta no se tocan).")
        )
        merge = box.addButton(tr("Fusionar"), QMessageBox.ButtonRole.AcceptRole)
        replace = box.addButton(tr("Sobrescribir"), QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(tr("Cancelar"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(merge)
        box.exec()
        clicked = box.clickedButton()
        if clicked is merge:
            return backup.MERGE
        if clicked is replace:
            return backup.REPLACE
        return None

    def _on_backup_imported(self, report: Optional[backup.ImportReport], error) -> None:
        if error is not None:
            log.warning("no se pudo importar la copia", exc_info=error)
            self._notice(tr("No se pudo importar la copia: no se cambió nada."), "error")
            return
        self._apply_saved_preferences()
        dialog = getattr(self, "options_dialog", None)
        if dialog is not None:
            dialog.refresh()                 # el idioma o la cuenta pudieron cambiar con la copia
        self._refresh_after_sync()
        if self._view_key == HISTORY_KEY:
            self._show_view(HISTORY_KEY, force=True)
        self._notice(tr("Copia importada: {summary}.", summary=report.summary()))

    def _apply_saved_preferences(self) -> None:
        """Lo que trae una copia importada se ve enseguida, sin reiniciar."""
        db = self.db
        self.history_action.setChecked(db.get_bool(play_history.ENABLED_KEY, True))
        self.autoplay_action.setChecked(db.get_bool("autoplay_radio", False))
        self.resume_action.setChecked(db.get_bool(play_session.ENABLED_KEY, True))
        self.equalize_action.setChecked(db.get_bool("equalize_volume", False))
        self._apply_audio_preferences()
        self.player.set_transition(db.get_setting(TRANSITION_KEY, TRANSITION_GAPLESS) or TRANSITION_GAPLESS)
        self.player.set_crossfade_seconds(db.get_int(CROSSFADE_KEY, CROSSFADE_DEFAULT_S))
        self.collapse_action.setChecked(db.get_bool("sidebar_collapsed"))
        self.close_to_tray_action.setChecked(db.get_bool("tray_close_to_tray", True))
        self.minimize_to_tray_action.setChecked(db.get_bool("tray_minimize_to_tray", False))
        self.sleep_quit_action.setChecked(db.get_bool("sleep_quit_app", False))
        self.set_theme_mode(db.get_setting(config.THEME_KEY, "system"))
        if self.volume_slider.value() > 0:
            self.volume_slider.setValue(saved_volume(db))

    # ------------------------------------------------------------------ cierre
    def closeEvent(self, event) -> None:  # noqa: N802 - API de Qt
        if not self._quitting and self._hides_on_close():
            # La X de la ventana no para la música: queda en la bandeja.
            event.ignore()
            self._hide_to_tray()
            return
        self._quitting = True
        self.shutdown()
        super().closeEvent(event)

    def shutdown(self) -> None:
        """Cierre ordenado. Se puede llamar más de una vez (al cerrar la
        ventana y al terminar la aplicación, p. ej. al cerrar sesión en Windows)."""
        if self._shut_down:
            return
        self._shut_down = True
        # Cada paso va por separado: si uno falla (la base bloqueada al
        # guardar la sesión), los demás siguen y mpv y yt-dlp no quedan vivos.
        for name, step in (
            ("temporizadores", self._stop_timers),
            ("temporizador de apagado", self.sleep.cancel),
            ("lectura de la carpeta de música", lambda: self._scan_cancel and self._scan_cancel.set()),
            ("mini reproductor", self._close_mini_for_good),
            ("bandeja", self._close_tray),
            (tr("Discord"), self.discord.shutdown),
            (tr("Last.fm"), self.lastfm.stop),
            ("guardar la sesión", self.session.save),
            ("detener la reproducción", self.player.stop),
            ("cerrar mpv", self.mpv.shutdown),
            ("cerrar yt-dlp", self.extractor.shutdown),
        ):
            try:
                step()
            except Exception:  # noqa: BLE001 - el cierre debe completarse igual
                log.exception("al cerrar, falló: %s", name)

    def _close_mini_for_good(self) -> None:
        mini, self.mini = self.mini, None
        if mini is not None:
            mini.close_quietly()
            mini.deleteLater()

    def _close_tray(self) -> None:
        if self.thumbbar is not None:
            self.thumbbar.close()
        if self.tray is not None:
            self.tray.close()

    def _stop_timers(self) -> None:
        self._tick.stop()
        self._sync_timer.stop()
        self._app_update_timer.stop()
