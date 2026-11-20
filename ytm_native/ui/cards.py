"""Carátulas y tarjetas.

* La carátula: la etiqueta con su fundido, la que se pulsa y el vuelo de una
  carátula de una vista a otra.
* Las tarjetas (Tarea 10.6, antes en ``home`` y ``search``): la funda de una
  lista o el retrato de un artista (``SleeveCard``), la rejilla que las
  reparte (``CardGrid``) y la fila del artista sobre los resultados de una
  búsqueda (``ArtistStrip``).
"""

from __future__ import annotations

import time
from typing import Callable, Optional

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget

from ytm_native.services.discover import ArtistCard
from . import focus, theme
from .art import ArtLoader, neutral_tint, placeholder_pixmap, sized_url, square_pixmap
from .buttons import _Pressable
from .motion import CARD_STAGGER, EASE, EASE_IN_OUT, EASE_OUT, ENTER_MS, FADE_MS, HOVER_MS, STAGGER_CAP, Tween, mix
from .primitives import Reveal, _alpha, _draw_glyph, window_sliding
from ytm_native.core.i18n import tr


class CoverLabel(QWidget):
    """Carátula cuadrada. Con ``shadow`` lleva una sombra dura y desplazada,
    como una funda apoyada sobre papel de color (sin desenfoque).

    Al cambiar de imagen funde la anterior con la nueva: la nueva sube de
    opacidad sobre la anterior, que sigue opaca debajo, así el conjunto nunca
    se oscurece a mitad del cambio.
    """

    def __init__(self, loader: ArtLoader, size: int, radius: float = theme.R_COVER, shadow: int = 0,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._loader = loader
        self._size = size
        self._radius = radius
        self._round = False
        self._shadow = shadow
        self._url = ""
        self._shadow_color = QColor(theme.LINEA_CLARA)
        self._shown: Optional[QPixmap] = None
        self._previous: Optional[QPixmap] = None
        self._fade = Tween(self, 1.0, lambda _v: self.update(), ms=FADE_MS, curve=EASE)
        # Cuánto se ha separado el eco de la funda (0 = debajo, 1 = en su sitio).
        self._echo = Tween(self, 1.0, lambda _v: self.update(), ms=ENTER_MS, curve=EASE_OUT, movement=True)
        self._display: Optional[float] = None    # tamaño de paso mientras se anima
        self._presence = 1.0
        self._held = False      # la imagen está de viaje: aquí no se pinta
        self._waiting = False   # se sigue enseñando la anterior hasta que llegue la nueva
        self._hold_for = ""     # la dirección por la que se esperó (una sola vez)
        self._serial = 0
        self.setFixedSize(size + shadow, size + shadow)
        loader.loaded.connect(self._on_loaded)

    def set_url(self, url: str) -> None:
        if url != self._url:
            self._url = url
            self._swap()

    def art_rect(self) -> QRectF:
        """Dónde se pinta la imagen, en coordenadas de este widget."""
        side = self._display if self._display is not None else self._size
        return QRectF(0, 0, side, side)

    def corner(self) -> float:
        return self._corner()

    def set_held(self, held: bool) -> None:
        """Retiene la imagen mientras una copia vuela hacia aquí. Al soltarla
        el eco se separa de la funda, como si la carátula acabara de posarse."""
        if held == self._held:
            return
        self._held = held
        if not held and self._shadow:
            self._echo.set(0.0)
            self._echo.to(1.0)
        self.update()

    def set_display_size(self, px: float, presence: float = 1.0) -> None:
        """Tamaño de paso durante una animación: se pinta la imagen que ya hay,
        escalada, sin pedir otra a cada fotograma. ``set_size`` fija el final."""
        self._display = px
        self._presence = max(0.0, min(1.0, presence))
        scale = px / self._size
        self.setFixedSize(round((self._size + self._shadow) * scale), round((self._size + self._shadow) * scale))
        self.update()

    def set_size(self, size: int, shadow: int) -> None:
        if self._display is not None:
            self._display = None
            self._presence = 1.0
            self.setFixedSize(self._size + self._shadow, self._size + self._shadow)
            self.update()
        if (size, shadow) != (self._size, self._shadow):
            self._size, self._shadow = size, shadow
            self.setFixedSize(size + shadow, size + shadow)
            self._shown = self._resolve()
            self._previous = None
            self._fade.set(1.0)
            self.update()

    def set_shadow_color(self, color: QColor) -> None:
        self._shadow_color = color
        self.update()

    def retheme(self, previous: str) -> None:
        """La funda vacía y la sombra se pintan con colores del tema."""
        self._shadow_color = theme.translate(self._shadow_color, previous)
        self._shown = self._resolve()
        self._previous = None
        self._fade.set(1.0)
        self.update()

    def set_round(self, round_art: bool) -> None:
        """Retrato redondo (un artista) en vez de funda cuadrada."""
        if round_art != self._round:
            self._round = round_art
            self._shown = self._resolve()
            self._previous = None
            self._fade.set(1.0)
            self.update()

    def _corner(self) -> float:
        return self._size / 2 if self._round else self._radius

    def _resolve(self) -> QPixmap:
        dpr = self.devicePixelRatioF()
        pixmap = self._loader.pixmap(self._url, self._size, self._corner(), dpr) if self._url else None
        return pixmap or placeholder_pixmap(self._size, self._corner(), dpr)

    # Lo que se espera a la carátula nueva antes de dar paso a la funda vacía.
    HOLD_MS = 1200

    def _swap(self) -> None:
        url = self._url
        new_art = (self._loader.pixmap(url, self._size, self._corner(), self.devicePixelRatioF())
                   if url else None)
        pending = new_art is None and bool(url) and not self._loader.failed(url)
        if pending and self._shown is not None and self._hold_for != url:
            # La nueva aún viene de camino: la de antes sigue entera en vez de
            # fundirse con la funda vacía y luego saltar a la buena (dos
            # cambios seguidos se ven como un parpadeo). Si tarda, la funda.
            self._hold_for = url
            self._waiting = True
            self._serial += 1
            serial = self._serial
            QTimer.singleShot(self.HOLD_MS, self, lambda: self._give_up(serial))
            return
        if pending and self._waiting:
            return
        self._waiting = False
        self._serial += 1
        new = new_art or placeholder_pixmap(self._size, self._corner(), self.devicePixelRatioF())
        if self._shown is not None and new.cacheKey() == self._shown.cacheKey():
            return
        self._previous = self._shown
        self._shown = new
        if self._previous is None:
            self._fade.set(1.0)
        else:
            self._fade.set(0.0)
            self._fade.to(1.0)
            if self._shadow:
                # Ocasional (cambiar de lista o de canción): el eco nace bajo la
                # funda y se desliza a su sitio, como una estela de la imagen nueva.
                self._echo.set(0.0)
                self._echo.to(1.0)
        self.update()

    def _give_up(self, serial: int) -> None:
        if serial == self._serial and self._waiting:
            self._waiting = False
            self._swap()

    def _on_loaded(self, url: str) -> None:
        if url == self._url:
            self._swap()

    def _refresh_resolution(self) -> None:
        """La ventana pasó a una pantalla con otra escala: la misma imagen,
        rehecha a la densidad nueva y sin fundido (no ha cambiado nada)."""
        self._shown = self._resolve()
        self._previous = None
        self._fade.set(1.0)
        self.update()

    def event(self, event) -> bool:  # noqa: D401 - API de Qt
        if event.type() == QEvent.Type.DevicePixelRatioChange:
            self._refresh_resolution()
        return super().event(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._held:
            return
        if self._shown is None:
            self._shown = self._resolve()
        if self._display is not None:
            scale = self._display / self._size
            painter.scale(scale, scale)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.setOpacity(self._presence)
        if self._shadow:
            offset = self._shadow * float(self._echo.value)
            if offset > 0.5:
                echo = QRectF(offset, offset, self._size, self._size).adjusted(0.75, 0.75, -0.75, -0.75)
                painter.setPen(QPen(self._shadow_color, 1.5))
                painter.setBrush(_alpha(self._shadow_color, 0.38))
                painter.drawRoundedRect(echo, self._corner(), self._corner())
        progress = float(self._fade.value)
        if progress < 1.0 and self._previous is not None:
            painter.drawPixmap(0, 0, self._previous)
            painter.setOpacity(progress)
        painter.drawPixmap(0, 0, self._shown)


_launch: Optional[tuple] = None


def note_launch(widget: QWidget, rect: QRectF, image: Optional[QImage], radius: float) -> None:
    """Apunta de dónde sale la carátula de lo que se va a abrir (una tarjeta,
    una fila de la barra lateral). La ventana la recoge al enseñar la vista
    y la hace volar hasta la cabecera."""
    global _launch
    if image is None or image.isNull() or focus.keyboard():
        _launch = None
        return
    top_left = widget.mapToGlobal(rect.topLeft().toPoint())
    _launch = (QRectF(QPointF(top_left), rect.size()), image, radius, time.monotonic())


def take_launch(max_age: float = 0.6) -> Optional[tuple]:
    """El origen apuntado, si es reciente (y lo olvida)."""
    global _launch
    launch, _launch = _launch, None
    if launch is None or time.monotonic() - launch[3] > max_age:
        return None
    return launch


def fit_aspect(rect: QRectF, aspect: float) -> QRectF:
    """El rectángulo más grande de proporción ``aspect`` (ancho / alto) que
    cabe en ``rect``, centrado en él."""
    if rect.isEmpty() or aspect <= 0:
        return QRectF(rect)
    width, height = rect.width(), rect.height()
    if width / height > aspect:
        width = height * aspect
    else:
        height = width / aspect
    out = QRectF(0, 0, width, height)
    out.moveCenter(rect.center())
    return out


class CoverFlight(QWidget):
    """La carátula viajando del reproductor al panel «Ahora suena» (o de vuelta).

    Es una capa transparente sobre la ventana que pinta solo la imagen en
    camino. Origen y destino pueden ser rectángulos fijos (el vuelo es una
    recta, siempre igual) o funciones que se vuelven a medir en cada
    fotograma (cuando el destino aún se está colocando).

    Con ``aspect`` la imagen conserva su proporción durante todo el vuelo (una
    miniatura de vídeo apaisada no se estira ni se recorta a medias): el marco
    de los extremos se ajusta a esa proporción. El dibujo no sale del pasillo
    que une origen y destino, así que nunca asoma sobre la cabecera.
    """

    def __init__(self, host: QWidget, image: QImage, start, end, radii: tuple[float, float], ms: int,
                 on_done: Callable[[], None], aspect: Optional[float] = None):
        super().__init__(host)
        self._image = image
        self._aspect = aspect
        self._start = self._fixed(start)
        self._end = self._fixed(end)
        self._radii = radii
        self._on_done = on_done
        self._done = False
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setGeometry(host.rect())
        self._t = Tween(self, 0.0, self._step, ms=ms, curve=EASE_IN_OUT, movement=True)
        self.show()
        self.raise_()

    def _fixed(self, where):
        """Un extremo fijo se ajusta una vez a la proporción; uno medido en
        vivo, en cada fotograma."""
        if callable(where):
            if self._aspect is None:
                return where
            return lambda: fit_aspect(where(), self._aspect)
        rect = QRectF(where) if self._aspect is None else fit_aspect(QRectF(where), self._aspect)
        return lambda: rect

    def start_rect(self) -> QRectF:
        return self._start()

    def end_rect(self) -> QRectF:
        return self._end()

    def fly(self) -> None:
        self._t.to(1.0)

    def land(self) -> None:
        """Termina ya (otra acción la interrumpe)."""
        self._t.set(1.0)

    def _step(self, value) -> None:
        if float(value) >= 1.0 and not self._done:
            self._done = True
            # Primero aparece la carátula de destino y después se va la copia:
            # en ningún fotograma falta la imagen.
            self._on_done()
            self.hide()
            self.deleteLater()
            return
        self.update()

    def rect_at(self, t: float) -> QRectF:
        a, b = self._start(), self._end()
        return QRectF(
            a.x() + (b.x() - a.x()) * t, a.y() + (b.y() - a.y()) * t,
            a.width() + (b.width() - a.width()) * t, a.height() + (b.height() - a.height()) * t,
        )

    def paintEvent(self, _event) -> None:  # noqa: N802
        t = float(self._t.value)
        rect = self.rect_at(t)
        if rect.width() < 2 or rect.height() < 2:
            return
        radius = self._radii[0] + (self._radii[1] - self._radii[0]) * t
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        # El pasillo entre origen y destino: fuera de él no se pinta nada.
        painter.setClipRect(self._start().united(self._end()).adjusted(-1, -1, 1, 1))
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        painter.setClipPath(path, Qt.ClipOperation.IntersectClip)
        # Rellena el marco recortando lo que sobra, como las carátulas.
        iw, ih = self._image.width(), self._image.height()
        scale = max(rect.width() / iw, rect.height() / ih)
        sw, sh = rect.width() / scale, rect.height() / scale
        painter.drawImage(rect, self._image, QRectF((iw - sw) / 2, (ih - sh) / 2, sw, sh))


class ClickableCover(CoverLabel):
    """Carátula del reproductor: un clic abre el panel de la canción.

    Al acercar el ratón se oscurece y enseña el icono de ampliar, para que se
    vea que responde antes de pulsarla.
    """

    clicked = Signal()

    def __init__(self, loader: ArtLoader, size: int, radius: float = theme.R_COVER,
                 parent: Optional[QWidget] = None):
        super().__init__(loader, size, radius=radius, parent=parent)
        self._veil = Tween(self, 0.0, lambda _v: self.update(), ms=HOVER_MS, curve=EASE)
        self._pressed = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAccessibleName(tr("Ver la canción que suena"))

    def enterEvent(self, event) -> None:  # noqa: N802
        self._veil.to(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._veil.to(0.0)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self._pressed = event.button() == Qt.MouseButton.LeftButton

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        pressed, self._pressed = self._pressed, False
        if pressed and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        veil = float(self._veil.value)
        ring = self.hasFocus() and focus.keyboard()
        if veil <= 0 and not ring:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0, 0, self._size, self._size)
        if veil > 0:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_alpha(theme.VELO, theme.VELO.alphaF() * veil))
            painter.drawRoundedRect(rect, self._radius, self._radius)
            painter.setOpacity(veil)
            _draw_glyph(painter, rect, "expand", 16, QColor(theme.SOBRE_SOLIDO))
            painter.setOpacity(1.0)
        if ring:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(theme.PAPEL), 2))
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), self._radius, self._radius)


# ---------------------------------------------------------------------------
# Tarjetas: fundas de listas, retratos de artistas y su rejilla
# ---------------------------------------------------------------------------
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
        self._plan: tuple = ()          # el último reparto aplicado
        self._deferred = False          # un reajuste espera al fin del deslizamiento
        self.relayouts = 0              # reajustes hechos (para medir)
        self.relayouts_skipped = 0
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

    def _relayout(self, force: bool = False) -> None:
        cols = self.columns()
        width = max(SleeveCard.PAD * 2 + 40,
                    min(self.MAX_CARD, (self.width() - self.GAP * (cols - 1)) // cols))
        height = SleeveCard.height_for(width)
        shown = min(self._count, cols * self._max_rows)
        # Mismas columnas, mismo tamaño de tarjeta y mismas tarjetas: no hay
        # nada que mover. Se evita invalidar el diseño del área que la
        # contiene y avisar de nuevo (cada aviso recoloca más cosas).
        plan = (cols, width, shown, self._count)
        if plan == self._plan and not force:
            self.relayouts_skipped += 1
            return
        self._plan = plan
        self.relayouts += 1
        for i, card in enumerate(self._cards):
            if i < shown:
                row, col = divmod(i, cols)
                card.setGeometry(col * (width + self.GAP), row * (height + self.GAP), width, height)
            card.setVisible(i < shown)
        rows = -(-shown // cols)
        self.setFixedHeight(rows * height + max(0, rows - 1) * self.GAP)
        self._shown = shown
        self.fitted.emit(shown, self._count)

    def settle(self) -> None:
        """Fin de un deslizamiento de panel: el reajuste que se aplazó."""
        if self._deferred:
            self._deferred = False
            self._relayout()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if event.size().width() == event.oldSize().width():
            return
        if window_sliding(self):
            # Un panel se está deslizando: el ancho cambia en cada fotograma.
            # Recolocar todas las tarjetas en cada paso era lo que daba
            # tirones; se hace una sola vez cuando el panel termina.
            self._deferred = True
            return
        self._relayout()


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
            self.setAccessibleName(tr("{name}. Abrir la página del artista", name=card.name))
            self.setToolTip(tr("Abrir la página del artista"))
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
        note = tr("Artista") if self.card.note in ("", "Artista", tr("Artista")) else tr("Artista con {note}", note=self.card.note)
        painter.drawText(QRectF(left, 33, width, 18), flags, note)
        # Una flecha discreta dice que esto lleva a otra parte.
        arrow = _alpha(theme.PAPEL, 0.55 + 0.45 * hover)
        painter.save()
        painter.translate(self.width() - 22, self.height() / 2)
        painter.rotate(180)
        _draw_glyph(painter, QRectF(-7, -self.height() / 2, 14, self.height()), "back", 13, arrow)
        painter.restore()
