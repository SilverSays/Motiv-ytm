"""Mini reproductor: una ventana pequeña, sin marco y siempre encima.

Lleva la carátula, el título y el artista (que se deslizan si no caben), los
controles esenciales y una barra de progreso fina. Opera sobre el mismo
``PlaybackController`` que la ventana principal: lo que se pulsa en uno se ve
en el otro. Se arrastra desde cualquier zona que no sea un control y recuerda
dónde se dejó.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QElapsedTimer, QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from ytm_native.core.db import Database, Track
from ytm_native.playback.player import LOADING, PLAYING, PlaybackController

from . import theme
from .art import ArtLoader, track_art_url
from .components import CoverLabel, ElidedLabel, IconButton, SeekSlider
from .motion import FRAME_MS, MARQUEE_PX_S, MARQUEE_REST_MS, reduced_motion
from .tray import app_icon
from .widgets import format_time
from ytm_native.core.i18n import tr

POS_KEY = "mini_player_pos"
COVER_PX = 64
ART_PX = 160            # se pide más grande que se pinta: pantallas al 150 % y más


class MarqueeLabel(QWidget):
    """Una línea de texto. Si no cabe, se desliza despacio en bucle, con una
    pausa al principio de cada vuelta; con las animaciones reducidas se
    recorta con puntos suspensivos.

    El texto se dibuja una vez en una imagen y cada fotograma solo la mueve,
    y solo se repinta cuando el desplazamiento avanza un píxel de pantalla."""

    GAP = 40            # separación entre el final del texto y su repetición
    EDGE = 16           # fundido en los bordes mientras se desliza

    def __init__(self, font: QFont, color: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._font = font
        self._color = QColor(color)
        self._background = QColor(theme.PANEL)
        self._text = ""
        self._text_width = 0
        self._cache: Optional[QPixmap] = None
        self._offset = 0.0
        self._drawn = -1
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(FRAME_MS)
        self._timer.timeout.connect(self._tick)
        self.setFixedHeight(QFontMetrics(font).height() + 2)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)   # se arrastra la ventana

    def text(self) -> str:
        return self._text

    def setText(self, text: str) -> None:  # noqa: N802 - mismo nombre que QLabel
        if text == self._text:
            return
        self._text = text
        self._text_width = QFontMetrics(self._font).horizontalAdvance(text)
        self._cache = None
        self.setAccessibleName(text)
        self._restart()

    def set_color(self, color: str | QColor) -> None:
        self._color = QColor(color)
        self._cache = None
        self.update()

    def retheme(self, previous: str) -> None:
        self._background = QColor(theme.PANEL)
        self.set_color(theme.translate(self._color, previous))

    def overflows(self) -> bool:
        return self._text_width > self.width()

    def scrolling(self) -> bool:
        return self._timer.isActive()

    def offset(self) -> float:
        return self._offset

    def _restart(self) -> None:
        self._offset = 0.0
        self._drawn = -1
        self._clock.restart()
        self._sync()
        self.update()

    def _sync(self) -> None:
        run = self.overflows() and self.isVisible() and not reduced_motion()
        if run and not self._timer.isActive():
            self._clock.restart()
            self._timer.start()
        elif not run and self._timer.isActive():
            self._timer.stop()
            self._offset = 0.0
            self.update()

    def _tick(self) -> None:
        travel = self._text_width + self.GAP
        travel_ms = travel / MARQUEE_PX_S * 1000
        t = self._clock.elapsed() % (MARQUEE_REST_MS + travel_ms)
        self._offset = 0.0 if t < MARQUEE_REST_MS else (t - MARQUEE_REST_MS) / 1000 * MARQUEE_PX_S
        step = round(self._offset * self.devicePixelRatioF())
        if step != self._drawn:
            self._drawn = step
            self.update()

    def _pixmap(self) -> QPixmap:
        dpr = self.devicePixelRatioF()
        if self._cache is None or self._cache.devicePixelRatio() != dpr:
            metrics = QFontMetrics(self._font)
            pixmap = QPixmap(max(1, round((self._text_width + 2) * dpr)), max(1, round(self.height() * dpr)))
            pixmap.setDevicePixelRatio(dpr)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
            painter.setFont(self._font)
            painter.setPen(self._color)
            baseline = (self.height() - metrics.height()) / 2 + metrics.ascent()
            painter.drawText(QPointF(0, baseline), self._text)
            painter.end()
            self._cache = pixmap
        return self._cache

    def paintEvent(self, _event) -> None:  # noqa: N802
        if not self._text:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        if not self.overflows():
            painter.drawPixmap(0, 0, self._pixmap())
            return
        if reduced_motion():
            painter.setFont(self._font)
            painter.setPen(self._color)
            elided = QFontMetrics(self._font).elidedText(self._text, Qt.TextElideMode.ElideRight, self.width())
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elided)
            return
        dpr = self.devicePixelRatioF()
        x = -round(self._offset * dpr) / dpr
        pixmap = self._pixmap()
        painter.drawPixmap(QRectF(x, 0, pixmap.width() / dpr, pixmap.height() / dpr), pixmap,
                           QRectF(pixmap.rect()))
        second = x + self._text_width + self.GAP
        if second < self.width():
            painter.drawPixmap(QRectF(second, 0, pixmap.width() / dpr, pixmap.height() / dpr), pixmap,
                               QRectF(pixmap.rect()))
        # Bordes que se funden con el fondo: el texto entra y sale, no se corta.
        clear = QColor(self._background)
        clear.setAlpha(0)
        right = QLinearGradient(self.width() - self.EDGE, 0, self.width(), 0)
        right.setColorAt(0.0, clear)
        right.setColorAt(1.0, self._background)
        painter.fillRect(QRectF(self.width() - self.EDGE, 0, self.EDGE, self.height()), right)
        if self._offset > 0:
            left = QLinearGradient(0, 0, self.EDGE, 0)
            left.setColorAt(0.0, self._background)
            left.setColorAt(1.0, clear)
            painter.fillRect(QRectF(0, 0, self.EDGE, self.height()), left)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._sync()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._restart()

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self._sync()


class MiniPlayer(QWidget):
    """La ventana compacta. ``restore_requested`` pide volver a la principal."""

    restore_requested = Signal()
    WIDTH, HEIGHT = 332, 104
    SAVE_DELAY_MS = 400

    def __init__(self, player: PlaybackController, art: ArtLoader, db: Database,
                 parent: Optional[QWidget] = None):
        flags = Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        super().__init__(parent, flags)
        self.player = player
        self.db = db
        self._duration = 0.0
        self._seeking = False
        self._drag_from: Optional[QPoint] = None
        self._placing = False
        self._quiet = False
        self.setWindowTitle("YT Music Nativo")
        self.setWindowIcon(app_icon())
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # Cerrarla no cierra la app: con la principal oculta, la devuelve.
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self.setFixedSize(self.WIDTH, self.HEIGHT)

        self.cover = CoverLabel(art, COVER_PX, radius=theme.R_COVER, shadow=3)
        self.cover.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.title = MarqueeLabel(theme.ui_font(14, QFont.Weight.DemiBold), theme.PAPEL)
        self.artist = MarqueeLabel(theme.ui_font(12), theme.HUMO)
        self.window_btn = IconButton("window", tr("Volver a la ventana principal"), size=28, glyph_px=13, quiet=True)
        self.prev_btn = IconButton("previous", tr("Anterior"), size=32, glyph_px=14)
        self.play_btn = IconButton("play", tr("Reproducir"), size=36, glyph_px=16)
        self.next_btn = IconButton("next", tr("Siguiente"), size=32, glyph_px=14)
        self.time_label = ElidedLabel(theme.ui_font(11, tabular=True), theme.TENUE)
        self.seek = SeekSlider()
        self.seek.setAccessibleName(tr("Posición en la pista"))
        self.seek.setRange(0, 1000)
        self.seek.setFixedHeight(16)
        self.seek.hover_text = lambda f: format_time(f * self._duration) if self._duration > 0 else ""

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(6)
        title_row.addWidget(self.title, 1)
        title_row.addWidget(self.window_btn, 0, Qt.AlignmentFlag.AlignTop)
        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(2)
        for button in (self.prev_btn, self.play_btn, self.next_btn):
            controls.addWidget(button)
        controls.addSpacing(8)
        controls.addWidget(self.time_label, 1)
        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(0)
        text.addLayout(title_row)
        text.addWidget(self.artist)
        text.addStretch(1)
        text.addLayout(controls)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(12)
        top.addWidget(self.cover, 0, Qt.AlignmentFlag.AlignTop)
        top.addLayout(text, 1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 8, 4)
        outer.setSpacing(2)
        outer.addLayout(top, 1)
        outer.addWidget(self.seek)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(self.SAVE_DELAY_MS)
        self._save_timer.timeout.connect(self.save_position)

        self.window_btn.clicked.connect(self.restore_requested.emit)
        self.play_btn.clicked.connect(player.toggle_pause)
        self.prev_btn.clicked.connect(player.previous_track)
        self.next_btn.clicked.connect(player.next_track)
        self.seek.sliderPressed.connect(self._on_seek_pressed)
        self.seek.sliderReleased.connect(self._on_seek_released)
        player.current_changed.connect(self._on_current)
        player.state_changed.connect(self._on_state)
        player.position_changed.connect(self._on_position)
        self._on_current(player.current_track())
        self._on_state(player.state)

    # -- reproducción ---------------------------------------------------------
    def _on_current(self, track: Optional[Track]) -> None:
        if track is None:
            self.title.setText(tr("Nada en reproducción"))
            self.title.set_color(theme.HUMO)
            self.artist.setText("")
            self.cover.set_url("")
            self.time_label.setText("")
            self.seek.setValue(0)
        else:
            self.title.setText(track.title)
            self.title.set_color(theme.PAPEL)
            self.artist.setText(track.artists)
            self.cover.set_url(track_art_url(track.video_id, track.thumb_url, ART_PX))
            self.setWindowTitle(f"{track.title} — YT Music Nativo")
        self._on_state(self.player.state)

    def _on_state(self, state: str) -> None:
        playing = state in (PLAYING, LOADING)
        self.play_btn.set_glyph("pause" if playing else "play")
        self.play_btn.setToolTip(tr("Pausar") if playing else tr("Reproducir"))
        has_track = self.player.current_track() is not None
        for button in (self.play_btn, self.prev_btn, self.next_btn):
            button.setEnabled(has_track)
        self.seek.setEnabled(has_track and state != LOADING)

    def _on_position(self, pos: float, duration: float) -> None:
        self._duration = duration
        if self._seeking:
            return
        self.seek.setValue(int(pos / duration * 1000) if duration > 0 else 0)
        self.time_label.setText(f"{format_time(pos)} / {format_time(duration)}" if duration > 0 else "")

    def _on_seek_pressed(self) -> None:
        self._seeking = True

    def _on_seek_released(self) -> None:
        self._seeking = False
        if self._duration > 0:
            self.player.seek(self.seek.value() / 1000 * self._duration)

    def set_ink(self, color: QColor) -> None:
        self.seek.set_ink(color)

    # -- posición -------------------------------------------------------------
    def place(self) -> None:
        """Donde se dejó, si sigue dentro de alguna pantalla; si no, abajo a
        la derecha de la principal."""
        self._placing = True
        try:
            self.move(self._saved_position() or self._default_position())
        finally:
            self._placing = False

    def _saved_position(self) -> Optional[QPoint]:
        raw = self.db.get_setting(POS_KEY) or ""
        try:
            x, y = (int(part) for part in raw.split(","))
        except ValueError:
            return None
        point = QPoint(x, y)
        screen = QGuiApplication.screenAt(point + QPoint(self.width() // 2, self.height() // 2))
        if screen is None:
            return None
        area = screen.availableGeometry()
        x = max(area.left(), min(x, area.right() - self.width() + 1))
        y = max(area.top(), min(y, area.bottom() - self.height() + 1))
        return QPoint(x, y)

    def _default_position(self) -> QPoint:
        screen = QGuiApplication.primaryScreen()
        area = screen.availableGeometry() if screen is not None else QRect(0, 0, 1280, 720)
        return QPoint(area.right() - self.width() - 24, area.bottom() - self.height() - 24)

    def save_position(self) -> None:
        self._save_timer.stop()
        self.db.set_setting(POS_KEY, f"{self.x()},{self.y()}")

    def moveEvent(self, event) -> None:  # noqa: N802
        super().moveEvent(event)
        if not self._placing and self.isVisible():
            self._save_timer.start()

    # -- arrastre -------------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        handle = self.windowHandle()
        # El arrastre del sistema es el más suave (y respeta el ajuste a bordes).
        if handle is not None and handle.startSystemMove():
            self._drag_from = None
        else:
            self._drag_from = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_from is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_from)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._drag_from is not None:
            self._drag_from = None
            self.save_position()
        super().mouseReleaseEvent(event)

    # -- ventana --------------------------------------------------------------
    def close_quietly(self) -> None:
        """Cerrarla sin pedir que vuelva la principal (ya vuelve, o se sale)."""
        self._quiet = True
        self.close()

    def showEvent(self, event) -> None:  # noqa: N802
        self._quiet = False
        super().showEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._save_timer.isActive():
            self.save_position()
        super().closeEvent(event)
        if not self._quiet:
            # Alt+F4 en la mini: la app no puede quedarse sin ninguna ventana.
            self._quiet = True
            QTimer.singleShot(0, self.restore_requested.emit)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        # Llega aquí lo que ningún control usó: un botón con el foco atiende
        # su propio Espacio; si no, Espacio pausa o reanuda.
        if event.key() == Qt.Key.Key_Escape:
            self.restore_requested.emit()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self.player.toggle_pause()
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(QColor(theme.LINEA_CLARA), 1))
        painter.setBrush(QColor(theme.PANEL))
        painter.drawRoundedRect(rect, theme.R_PANEL, theme.R_PANEL)
