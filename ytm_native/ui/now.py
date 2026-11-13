"""Panel "Ahora suena": la carátula en grande, quién hace la canción y música parecida.

Se abre al pulsar la carátula del reproductor y ocupa el mismo sitio que la
letra (uno sustituye al otro). Lo esencial se ve sin tocar nada: carátula,
título, el artista con una nota breve y la música parecida. La historia
completa del artista y los datos de la canción quedan detrás de «Más
información», para quien quiera leer.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap, QTextLayout, QTextOption
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
from ytm_native.services.discover import NowInfo
from . import theme
from .art import ArtLoader, Tint, neutral_tint, placeholder_pixmap, sized_url, square_pixmap, track_art_url
from .components import CoverLabel, ElidedLabel, IconButton, PillButton, Reveal, _alpha, _Pressable
from .motion import EASE, EASE_OUT, FADE_MS, Tween, mix


def whole_pixmap(image, width: int, height: int, radius: float, dpr: float = 1.0) -> QPixmap:
    """La imagen entera (sin recorte) a ``width`` x ``height``, con las esquinas redondeadas."""
    w, h = max(1, round(width * dpr)), max(1, round(height * dpr))
    scaled = image.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
    out = QPixmap(w, h)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, w, h), radius * dpr, radius * dpr)
    painter.setClipPath(path)
    painter.drawImage(0, 0, scaled)
    painter.end()
    out.setDevicePixelRatio(dpr)
    return out


class BigCover(QWidget):
    """La carátula a todo el ancho del panel, con la sombra dura de la funda.

    Al abrir el panel con el ratón crece un poco hasta su sitio (de 0,94 a 1):
    es la carátula pequeña del reproductor "agrandándose". Con el teclado
    aparece ya en su tamaño.
    """

    SHADOW = 8
    MAX_SIDE = 420

    def __init__(self, loader: ArtLoader, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._loader = loader
        self._url = ""
        self._wide = False
        self._pix: Optional[QPixmap] = None
        self._pix_key: tuple = ()
        self._previous: Optional[QPixmap] = None
        self._shadow = QColor(theme.LINEA_CLARA)
        self._fade = Tween(self, 1.0, lambda _v: self.update(), ms=FADE_MS + 40, curve=EASE)
        self._grow = Tween(self, 1.0, lambda _v: self.update(), ms=240, curve=EASE_OUT, movement=True)
        self._held = False      # la imagen está de viaje: aquí no se pinta
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(200)
        loader.loaded.connect(self._on_loaded)

    def _side(self) -> int:
        return max(40, min(self.MAX_SIDE, self.width() - self.SHADOW))

    def _art_height(self) -> int:
        """Alto de la imagen: cuadrada, o apaisada si es la miniatura de un vídeo."""
        side = self._side()
        if not self._wide:
            return side
        image = self._loader.image(self._url) if self._url else None
        if image is None or image.width() <= image.height():
            return round(side * 9 / 16) if image is None else side
        return max(40, round(side * image.height() / image.width()))

    def _fit_height(self) -> None:
        self.setFixedHeight(self._art_height() + self.SHADOW)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._fit_height()

    def set_url(self, url: str, wide: bool = False) -> None:
        """``wide``: la imagen es la miniatura de un vídeo y se enseña entera,
        sin recortarla a un cuadrado (se perdería medio fotograma)."""
        if url == self._url and wide == self._wide:
            return
        self._previous = self._current()
        self._url = url
        self._wide = wide
        self._pix = None
        self._fit_height()
        if self._previous is not None and self.isVisible():
            self._fade.set(0.0)
            self._fade.to(1.0)
        else:
            self._fade.set(1.0)
        self.update()

    def set_shadow_color(self, color: QColor) -> None:
        self._shadow = QColor(color)
        self.update()

    def appear(self) -> None:
        self._grow.set(0.94)
        self._grow.to(1.0)

    def set_held(self, held: bool) -> None:
        if held != self._held:
            self._held = held
            self.update()

    def art_rect(self) -> QRectF:
        """Dónde se pinta la imagen, en coordenadas de este widget."""
        return QRectF(0, 0, self._side(), self._art_height())

    def flight_image(self):
        """La imagen original, para la copia que viaja (``None`` si aún no ha llegado)."""
        return self._loader.image(self._url) if self._url else None

    def _current(self) -> Optional[QPixmap]:
        side, dpr = self._side(), self.devicePixelRatioF()
        key = (self._url, side, self._wide, round(dpr, 2))
        if self._pix is None or self._pix_key != key:
            image = self._loader.image(self._url) if self._url else None
            if image is None:
                return None
            radius = theme.R_COVER + 2
            if self._wide and image.width() > image.height():
                self._pix = whole_pixmap(image, side, self._art_height(), radius, dpr)
            else:
                self._pix = square_pixmap(image, side, radius, dpr)
            self._pix_key = key
        return self._pix

    def _on_loaded(self, url: str) -> None:
        if url and url == self._url:
            self._pix = None
            self._fit_height()
            self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if self._held:
            return
        side, height = self._side(), self._art_height()
        grow = float(self._grow.value)
        if grow != 1.0:
            center = QPointF(side / 2, height / 2)
            painter.translate(center)
            painter.scale(grow, grow)
            painter.translate(-center)
            painter.setOpacity(max(0.0, min(1.0, (grow - 0.94) / 0.06)))
        radius = theme.R_COVER + 2
        area = QRectF(0, 0, side, height)
        # El mismo eco que la carátula de la banda: copia desplazada y perfilada.
        painter.setPen(QPen(self._shadow, 1.5))
        painter.setBrush(_alpha(self._shadow, 0.38))
        painter.drawRoundedRect(area.translated(self.SHADOW, self.SHADOW).adjusted(0.75, 0.75, -0.75, -0.75),
                                radius, radius)
        painter.setPen(Qt.PenStyle.NoPen)
        pixmap = self._current()
        if pixmap is None:
            if self._wide:
                painter.setBrush(QColor(theme.RELIEVE))
                painter.drawRoundedRect(area, radius, radius)
                return
            pixmap = placeholder_pixmap(side, radius, self.devicePixelRatioF())
        progress = float(self._fade.value)
        base = painter.opacity()
        if progress < 1.0 and self._previous is not None:
            painter.drawPixmap(area, self._previous, QRectF(self._previous.rect()))
            painter.setOpacity(base * progress)
        painter.drawPixmap(0, 0, pixmap)


class ClampText(QWidget):
    """Párrafo que se corta a unas pocas líneas, con puntos suspensivos."""

    def __init__(self, lines: int = 4, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._lines = lines
        self._text = ""
        self._font = theme.ui_font(13)
        self._color = QColor(theme.PAPEL_SUAVE)
        self._laid: list[tuple[int, int]] = []     # (inicio, longitud) de cada línea
        self.clipped = False
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(0)

    def setText(self, text: str) -> None:  # noqa: N802
        self._text = " ".join((text or "").split())
        self.setAccessibleName(self._text)
        self._relayout()

    def _line_height(self) -> int:
        return QFontMetrics(self._font).lineSpacing() + 2

    def _relayout(self) -> None:
        self._laid = []
        if self._text and self.width() > 20:
            layout = QTextLayout(self._text, self._font)
            layout.setTextOption(QTextOption(Qt.AlignmentFlag.AlignLeft))
            layout.beginLayout()
            while True:
                line = layout.createLine()
                if not line.isValid():
                    break
                line.setLineWidth(self.width())
                self._laid.append((line.textStart(), line.textLength()))
            layout.endLayout()
        self.clipped = len(self._laid) > self._lines
        self.setFixedHeight(min(len(self._laid), self._lines) * self._line_height())
        self.update()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width():
            self._relayout()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setFont(self._font)
        painter.setPen(self._color)
        metrics = QFontMetrics(self._font)
        height = self._line_height()
        for i, (start, length) in enumerate(self._laid[: self._lines]):
            last = i == self._lines - 1 and self.clipped
            text = self._text[start:] if last else self._text[start:start + length]
            if last:
                text = metrics.elidedText(text, Qt.TextElideMode.ElideRight, self.width())
            painter.drawText(QRectF(0, i * height, self.width(), height),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)


class SimilarRow(_Pressable):
    """Fila de «Música parecida»: carátula pequeña, título y artista."""

    PRESS_SCALE = 0.985
    THUMB = 40
    HEIGHT = 52

    def __init__(self, loader: ArtLoader, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._loader = loader
        self.track: Optional[Track] = None
        self._url = ""
        self._title_font = theme.ui_font(14)
        self._artist_font = theme.ui_font(12)
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        loader.loaded.connect(lambda url: url == self._url and self.update())
        self.reveal = Reveal(self)

    def set_track(self, track: Track) -> None:
        self.track = track
        self._url = track_art_url(track.video_id, track.thumb_url, 96)
        self.setAccessibleName(f"{track.title}, {track.artists}")
        self.setToolTip(f"{track.title}\n{track.artists}" if track.artists else track.title)
        self.update()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.click()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        if self.track is None or self.reveal.value <= 0.001:
            return
        self._paint_row(event)
        if self.reveal.value < 1.0:
            painter = QPainter(self)
            self.reveal.cover(painter)
            painter.end()

    def _paint_row(self, _event) -> None:
        painter = QPainter(self)
        self._start(painter)
        hover = float(self._hover.value)
        rect = QRectF(self.rect()).adjusted(0, 1, 0, -1)
        if hover > 0:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_alpha(theme.RELIEVE, hover))
            painter.drawRoundedRect(rect, theme.R_CONTROL, theme.R_CONTROL)
        if self._ring():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(theme.PAPEL), 2))
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), theme.R_CONTROL, theme.R_CONTROL)
        dpr = self.devicePixelRatioF()
        x, y = 6, (self.height() - self.THUMB) // 2
        pixmap = self._loader.pixmap(self._url, self.THUMB, theme.R_THUMB, dpr)
        painter.drawPixmap(x, y, pixmap or placeholder_pixmap(self.THUMB, theme.R_THUMB, dpr))
        if hover > 0:
            thumb = QRectF(x, y, self.THUMB, self.THUMB)
            painter.setOpacity(hover)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(theme.VELO)
            painter.drawRoundedRect(thumb, theme.R_THUMB, theme.R_THUMB)
            center = thumb.center()
            path = QPainterPath()
            path.moveTo(center.x() - 3.5, center.y() - 6)
            path.lineTo(center.x() + 6.5, center.y())
            path.lineTo(center.x() - 3.5, center.y() + 6)
            path.closeSubpath()
            pen = QPen(QColor(theme.SOBRE_SOLIDO), 1.4)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(QColor(theme.SOBRE_SOLIDO))
            painter.drawPath(path)
            painter.setOpacity(1.0)
        left = x + self.THUMB + 12
        width = max(10, self.width() - left - 8)
        flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        painter.setFont(self._title_font)
        painter.setPen(QColor(theme.PAPEL))
        painter.drawText(QRectF(left, 7, width, 20), flags,
                         QFontMetrics(self._title_font).elidedText(self.track.title, Qt.TextElideMode.ElideRight, width))
        painter.setFont(self._artist_font)
        painter.setPen(mix(QColor(theme.HUMO), QColor(theme.PAPEL_SUAVE), hover))
        painter.drawText(QRectF(left, 27, width, 18), flags,
                         QFontMetrics(self._artist_font).elidedText(self.track.artists, Qt.TextElideMode.ElideRight, width))


class NowPanel(QFrame):
    close_requested = Signal()
    play_similar = Signal(int)            # posición en la lista de música parecida
    similar_menu = Signal(object, object)  # pista, posición global del ratón
    open_artist = Signal(str, str)         # canal del artista (puede faltar), nombre

    def __init__(self, loader: ArtLoader, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("panel")
        self._loader = loader
        self._info: Optional[NowInfo] = None
        self._expanded = False
        self._rows: list[SimilarRow] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(18, 10, 8, 4)
        title = QLabel("Ahora suena")
        title.setObjectName("panelTitle")
        close = IconButton("close", "Ocultar el panel (Ctrl+I)", size=32, glyph_px=11, quiet=True)
        close.clicked.connect(self.close_requested)
        head.addWidget(title, 1)
        head.addWidget(close)
        outer.addLayout(head)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.scroll.verticalScrollBar().setSingleStep(28)
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(18, 8, 12, 20)
        lay.setSpacing(0)

        self.message = QLabel()
        self.message.setObjectName("homeStatus")
        self.message.setWordWrap(True)
        lay.addWidget(self.message)

        self.body = QWidget()
        body = QVBoxLayout(self.body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.cover = BigCover(loader)
        body.addWidget(self.cover)
        body.addSpacing(14)
        self.title = QLabel()
        self.title.setObjectName("nowTitle")
        self.title.setWordWrap(True)
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.artists = ElidedLabel(theme.ui_font(14), theme.HUMO)
        body.addWidget(self.title)
        body.addSpacing(2)
        body.addWidget(self.artists)

        # -- el artista ------------------------------------------------------
        self.artist_box = QWidget()
        artist = QVBoxLayout(self.artist_box)
        artist.setContentsMargins(0, 22, 0, 0)
        artist.setSpacing(10)
        who = QHBoxLayout()
        who.setSpacing(12)
        self.avatar = CoverLabel(loader, 44, radius=22)
        names = QVBoxLayout()
        names.setSpacing(1)
        self.artist_name = ElidedLabel(theme.ui_font(14, QFont.Weight.DemiBold), theme.PAPEL)
        self.artist_stats = ElidedLabel(theme.ui_font(12), theme.HUMO)
        names.addStretch(1)
        names.addWidget(self.artist_name)
        names.addWidget(self.artist_stats)
        names.addStretch(1)
        who.addWidget(self.avatar)
        who.addLayout(names, 1)
        artist.addLayout(who)
        self.excerpt = ClampText(4)
        artist.addWidget(self.excerpt)

        # Lo opcional: historia completa y datos de la canción.
        self.more = QWidget()
        more = QVBoxLayout(self.more)
        more.setContentsMargins(0, 0, 0, 0)
        more.setSpacing(14)
        self.bio = QLabel()
        self.bio.setObjectName("nowBio")
        self.bio.setWordWrap(True)
        self.bio.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.bio.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        more.addWidget(self.bio)

        def pairs() -> QGridLayout:
            grid = QGridLayout()
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(16)
            grid.setVerticalSpacing(6)
            grid.setColumnStretch(1, 1)
            return grid

        self.artist_facts = pairs()
        self._artist_labels: list[tuple[QLabel, QLabel]] = []
        more.addLayout(self.artist_facts)
        self.facts_title = QLabel("Sobre esta canción")
        self.facts_title.setObjectName("nowHeading")
        self.facts_title.setContentsMargins(0, 8, 0, 0)
        more.addWidget(self.facts_title)
        self.facts = pairs()
        self._fact_labels: list[tuple[QLabel, QLabel]] = []
        more.addLayout(self.facts)
        self.more.hide()
        self._more_effect = QGraphicsOpacityEffect(self.more)
        self._more_effect.setEnabled(False)
        self.more.setGraphicsEffect(self._more_effect)
        self._more_fade = Tween(self.more, 1.0, self._apply_more_fade, ms=FADE_MS, curve=EASE)
        artist.addWidget(self.more)

        self.toggle = PillButton("Más información", "quiet", glyph="chevron_down")
        self.toggle.setFixedHeight(32)
        self.toggle.clicked.connect(lambda: self.set_expanded(not self._expanded))
        # La página entera del artista: discografía, listas y artistas parecidos.
        self.artist_btn = PillButton("Ver artista", "quiet", glyph="expand")
        self.artist_btn.setFixedHeight(32)
        self.artist_btn.setToolTip("Abrir la página del artista: canciones, álbumes, listas y música parecida")
        self.artist_btn.clicked.connect(self._emit_artist)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addWidget(self.toggle)
        buttons.addWidget(self.artist_btn)
        buttons.addStretch(1)
        artist.addLayout(buttons)
        body.addWidget(self.artist_box)

        # -- música parecida -------------------------------------------------
        self.similar_box = QWidget()
        similar = QVBoxLayout(self.similar_box)
        similar.setContentsMargins(0, 26, 0, 0)
        similar.setSpacing(0)
        heading = QLabel("Música parecida")
        heading.setObjectName("nowHeading")
        similar.addWidget(heading)
        similar.addSpacing(6)
        self._similar_rows = QVBoxLayout()
        self._similar_rows.setContentsMargins(-6, 0, 0, 0)   # la carátula se alinea con el texto de arriba
        self._similar_rows.setSpacing(0)
        similar.addLayout(self._similar_rows)
        body.addWidget(self.similar_box)

        self.status = QLabel()
        self.status.setObjectName("homeStatus")
        self.status.setWordWrap(True)
        self.status.setContentsMargins(0, 20, 0, 0)
        body.addWidget(self.status)
        lay.addWidget(self.body)
        lay.addStretch(1)
        self.scroll.setWidget(page)
        outer.addWidget(self.scroll, 1)
        self.show_idle()

    # -- estados ------------------------------------------------------------
    def show_idle(self) -> None:
        self._info = None
        self.body.hide()
        self.message.setText("Reproduce una canción para verla aquí en grande, con su artista y música parecida.")
        self.message.show()

    def set_track(self, track: Track) -> None:
        """Lo que ya se sabe sin pedir nada: carátula, título y artistas."""
        self.message.hide()
        self.body.show()
        # La miniatura de un vídeo es apaisada: se enseña entera, no recortada.
        self.cover.set_url(track_art_url(track.video_id, track.thumb_url, 544),
                           wide="i.ytimg.com/vi" in (track.thumb_url or ""))
        self.title.setText(track.title)
        self.artists.setText(track.artists)
        self.scroll.verticalScrollBar().setValue(0)

    def set_loading(self) -> None:
        self._info = None
        self.artist_box.hide()
        self.similar_box.hide()
        self.status.setText("Buscando información y música parecida…")
        self.status.show()

    def set_error(self) -> None:
        self._info = None
        self.artist_box.hide()
        self.similar_box.hide()
        self.status.setText("No se pudo cargar la información de esta canción (¿sin conexión?).")
        self.status.show()

    def set_info(self, info: NowInfo) -> None:
        self._info = info
        self.status.hide()
        self.set_expanded(False, animate=False)

        self.artist_box.setVisible(bool(info.artist))
        self.artist_name.setText(info.artist)
        # Junto al nombre, un solo dato; el resto queda en «Más información».
        lead = info.artist_stats[0] if info.artist_stats else None
        self.artist_stats.setText(f"{lead[1]} {lead[0].lower()}" if lead else "")
        self.artist_stats.setVisible(lead is not None)
        self._fill(self.artist_facts, self._artist_labels, info.artist_stats)
        self.avatar.set_url(sized_url(info.artist_thumb, 120))
        self.avatar.setVisible(bool(info.artist_thumb))
        first = info.bio.split("\n")[0] if info.bio else ""
        self.excerpt.setText(first)
        self.excerpt.setVisible(bool(first))
        self.bio.setText(info.bio)
        self.bio.setVisible(bool(info.bio))
        self._fill(self.facts, self._fact_labels, info.facts)
        self.facts_title.setVisible(bool(info.facts))
        # Sin historia ni datos no hay nada que desplegar.
        self.toggle.setVisible(bool(info.bio or info.facts or info.artist_stats))
        # Quien sube un vídeo no tiene página de artista; sin id se busca por el nombre.
        self.artist_btn.setVisible(bool(info.artist_id or info.artist_stats))

        while len(self._rows) < len(info.similar):
            row = SimilarRow(self._loader)
            index = len(self._rows)
            row.clicked.connect(lambda _checked=False, i=index: self.play_similar.emit(i))
            row.customContextMenuRequested.connect(
                lambda point, r=row: self.similar_menu.emit(r.track, r.mapToGlobal(point))
            )
            self._rows.append(row)
            self._similar_rows.addWidget(row)
        for i, row in enumerate(self._rows):
            row.setVisible(i < len(info.similar))
            if i < len(info.similar):
                row.set_track(info.similar[i])
        self.similar_box.setVisible(bool(info.similar))
        if not info.similar and not info.artist:
            self.status.setText("No hay más información sobre esta canción.")
            self.status.show()

    @staticmethod
    def _fill(grid: QGridLayout, labels: list, facts: list[list[str]]) -> None:
        """Pares etiqueta-valor en dos columnas (datos del artista o de la canción)."""
        while len(labels) < len(facts):
            label, value = QLabel(), QLabel()
            label.setObjectName("nowFactLabel")
            value.setObjectName("nowFactValue")
            value.setWordWrap(True)
            value.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            row = len(labels)
            grid.addWidget(label, row, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(value, row, 1)
            labels.append((label, value))
        for i, (label, value) in enumerate(labels):
            shown = i < len(facts)
            label.setVisible(shown)
            value.setVisible(shown)
            if shown:
                label.setText(facts[i][0])
                value.setText(facts[i][1])

    def info(self) -> Optional[NowInfo]:
        return self._info

    def _emit_artist(self) -> None:
        if self._info is not None and self._info.artist:
            self.open_artist.emit(self._info.artist_id, self._info.artist)

    # -- más información ----------------------------------------------------
    def _apply_more_fade(self, value) -> None:
        opacity = float(value)
        self._more_effect.setOpacity(opacity)
        self._more_effect.setEnabled(opacity < 1.0)

    def is_expanded(self) -> bool:
        return self._expanded

    def set_expanded(self, expanded: bool, animate: bool = True) -> None:
        self._expanded = expanded
        # El extracto y la historia completa no se enseñan a la vez: sería leer lo mismo dos veces.
        self.excerpt.setVisible(not expanded and bool(self._info and self._info.bio))
        self.more.setVisible(expanded)
        self.toggle.set_label("Menos información" if expanded else "Más información",
                              "chevron_up" if expanded else "chevron_down")
        if expanded and animate:
            self._more_fade.set(0.0)
            self._more_fade.to(1.0)
        else:
            self._more_fade.set(1.0)

    # -- color y entrada ----------------------------------------------------
    def set_tint(self, tint: Optional[Tint]) -> None:
        tint = tint or neutral_tint()
        self.cover.set_shadow_color(QColor(tint.band).darker(118) if tint.from_art else QColor(theme.LINEA_CLARA))

    def appear(self) -> None:
        self.cover.appear()
