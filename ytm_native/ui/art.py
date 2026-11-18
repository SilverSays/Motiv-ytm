"""Carátulas: descarga asíncrona con caché en disco y "tinta" extraída de la imagen.

Las imágenes salen del CDN público de miniaturas (sin cookies ni cuenta) y se
guardan en ``cache/caratulas``, de modo que cada carátula se pide una sola vez.
Todo ocurre en el hilo de la GUI con ``QNetworkAccessManager`` (sin hilos).
"""

from __future__ import annotations

import colorsys
import hashlib
import math
import os
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QImage, QImageReader, QPainter, QPainterPath, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from ytm_native.core import paths
from . import theme

# Carátula del reproductor (la barra de abajo): el panel la usa de reserva.
BAR_ART_PX = 160

_SIZE_WH = re.compile(r"=w\d+-h\d+")
_SIZE_S = re.compile(r"=s\d+")


def sized_url(url: str, px: int) -> str:
    """Pide al CDN la miniatura al tamaño que se va a pintar."""
    if not url:
        return ""
    if _SIZE_WH.search(url):
        return _SIZE_WH.sub(f"=w{px}-h{px}", url, count=1)
    if _SIZE_S.search(url):
        return _SIZE_S.sub(f"=s{px}", url, count=1)
    return url


def track_art_url(video_id: str, thumb_url: str, px: int) -> str:
    if thumb_url:
        return sized_url(thumb_url, px)
    if not video_id or video_id.startswith("local:"):
        return ""                  # un archivo del disco sin carátula incrustada
    return f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg"


@dataclass(frozen=True)
class Tint:
    """Colores derivados de una carátula: banda pastel y tinta honda."""

    band: QColor
    ink: QColor
    from_art: bool = True


def neutral_tint() -> Tint:
    return Tint(QColor(theme.BANDA_NEUTRA), QColor(theme.CREMA), from_art=False)


def tint_from_image(image: QImage) -> Tint:
    """Tono dominante de la carátula, ponderado por saturación.

    En el tema claro la banda usa ese tono pastel, muy claro (la tinta pizarra
    siempre se lee encima), y la tinta, el mismo tono hondo para resaltar sobre
    el panel. En el oscuro, al revés: banda honda y tinta clara.
    """
    if image.isNull():
        return neutral_tint()
    small = image.scaled(24, 24, Qt.AspectRatioMode.IgnoreAspectRatio,
                         Qt.TransformationMode.SmoothTransformation).convertToFormat(QImage.Format.Format_RGB32)
    buckets = [[0.0, 0.0, 0.0, 0.0] for _ in range(12)]   # peso, x, y, saturación
    for y in range(small.height()):
        for x in range(small.width()):
            c = small.pixelColor(x, y)
            h, l, s = colorsys.rgb_to_hls(c.redF(), c.greenF(), c.blueF())
            if s < 0.2 or l < 0.12 or l > 0.9:
                continue
            weight = s * (1.0 - abs(l - 0.5))
            b = buckets[int(h * 12) % 12]
            b[0] += weight
            b[1] += math.cos(h * math.tau) * weight
            b[2] += math.sin(h * math.tau) * weight
            b[3] += s * weight
    best = max(buckets, key=lambda b: b[0])
    if best[0] < 12.0:          # carátula casi sin color (blanco y negro, grises)
        return neutral_tint()
    hue = (math.atan2(best[2], best[1]) / math.tau) % 1.0
    sat = best[3] / best[0]
    band = readable_band(hue, min(0.50, max(0.26, sat * 0.6)))
    ink = readable_ink(hue, min(0.78, max(0.46, sat)))
    return Tint(band, ink)


# Fondo sobre el que se lee la tinta: la fila que suena seleccionada (la tinta
# al 17 % sobre el panel). El texto de la banda es el más tenue que se pinta
# encima de ella (cambia con el tema: ver ``on_theme_changed``).
INK_ROW_ALPHA = 0.17


def band_text() -> QColor:
    r, g, b = theme.BANDA_TINTA
    return QColor(r, g, b, round(0.80 * 255))


BAND_TEXT = band_text()


def on_theme_changed() -> None:
    """Tras ``theme.use_scheme``: el texto de la banda y las fundas sin carátula."""
    global BAND_TEXT
    BAND_TEXT = band_text()
    _PLACEHOLDERS.clear()


def readable_ink(hue: float, sat: float, lightness: Optional[float] = None) -> QColor:
    """Tinta del tono de la carátula que se lee con contraste AA sobre el
    panel y sobre la fila que suena. Los amarillos, verdes y cianes son mucho
    más luminosos que un azul con la misma luminosidad HSL: en el tema claro
    se oscurece hasta llegar al contraste mínimo; en el oscuro, se aclara."""
    panel = QColor(theme.PANEL)
    dark = theme.is_dark()
    if lightness is None:
        lightness = 0.66 if dark else 0.33
    step = 0.01 if dark else -0.01
    while True:
        ink = QColor.fromHslF(hue, sat, lightness)
        tinted = QColor(ink)
        tinted.setAlphaF(INK_ROW_ALPHA)
        row = theme.composite(tinted, panel)
        limit = lightness >= 0.95 if dark else lightness <= 0.12
        if limit or min(theme.contrast_ratio(ink, panel),
                        theme.contrast_ratio(ink, row)) >= theme.MIN_TEXT_CONTRAST:
            return ink
        lightness += step


def readable_band(hue: float, sat: float, lightness: Optional[float] = None) -> QColor:
    """Banda sobre la que el texto más tenue de la cabecera se lee con
    contraste AA: pastel que se aclara (tema claro) u honda que se oscurece
    (tema oscuro) hasta llegar."""
    dark = theme.is_dark()
    if lightness is None:
        lightness = 0.22 if dark else 0.86
    step = -0.01 if dark else 0.01
    text = band_text()
    while True:
        band = QColor.fromHslF(hue, sat, lightness)
        limit = lightness <= 0.05 if dark else lightness >= 0.97
        if limit or theme.contrast_ratio(text, band) >= theme.MIN_TEXT_CONTRAST:
            return band
        lightness += step


def square_pixmap(image: QImage, size: int, radius: float, dpr: float = 1.0) -> QPixmap:
    """Recorte cuadrado centrado, escalado y con esquinas apenas redondeadas."""
    side = min(image.width(), image.height())
    crop = image.copy((image.width() - side) // 2, (image.height() - side) // 2, side, side)
    px = max(1, round(size * dpr))
    scaled = crop.scaled(px, px, Qt.AspectRatioMode.IgnoreAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    out = QPixmap(px, px)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, px, px), radius * dpr, radius * dpr)
    painter.setClipPath(path)
    painter.drawImage(0, 0, scaled)
    painter.end()
    out.setDevicePixelRatio(dpr)
    return out


_PLACEHOLDERS: dict[tuple, QPixmap] = {}


def placeholder_pixmap(size: int, radius: float, dpr: float = 1.0) -> QPixmap:
    """Funda sin carátula: un disco esquemático sobre el color de relieve.

    Se dibuja una vez por tamaño: las filas sin carátula la piden en cada
    repintado y crear una imagen nueva cada vez se notaba al desplazarse."""
    key = (size, round(radius, 2), round(dpr, 2))
    cached = _PLACEHOLDERS.get(key)
    if cached is None:
        cached = _PLACEHOLDERS[key] = _draw_placeholder(size, radius, dpr)
    return cached


def _draw_placeholder(size: int, radius: float, dpr: float) -> QPixmap:
    px = max(1, round(size * dpr))
    out = QPixmap(px, px)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(theme.RELIEVE))
    painter.drawRoundedRect(QRectF(0, 0, px, px), radius * dpr, radius * dpr)
    center = px / 2
    painter.setBrush(QColor(theme.GRAFITO))
    painter.drawEllipse(QRectF(center - px * 0.30, center - px * 0.30, px * 0.60, px * 0.60))
    painter.setBrush(QColor(theme.LINEA))
    painter.drawEllipse(QRectF(center - px * 0.10, center - px * 0.10, px * 0.20, px * 0.20))
    painter.end()
    out.setDevicePixelRatio(dpr)
    return out


class ArtLoader(QObject):
    loaded = Signal(str)   # URL disponible (o fallida: no se reintenta en esta sesión)

    MAX_PARALLEL = 6
    MEMORY_IMAGES = 400
    # Carátulas que se leen del disco y se escalan dentro de un mismo pintado.
    # Al desplazar deprisa una lista larga entran decenas de filas de golpe:
    # el resto se lee en los fotogramas siguientes y no se pierde ninguno.
    DECODES_PER_FRAME = 4
    # La caché en disco no crece sin fin: pasado este tamaño se borran las
    # carátulas que hace más tiempo que no se usan.
    DISK_LIMIT_BYTES = 200 * 1024 * 1024
    PRUNE_EVERY = 200      # escrituras entre una poda y la siguiente
    LOCAL_ART_PX = 640     # lado máximo al leer la carátula de un archivo del disco

    def __init__(self, parent: Optional[QObject] = None, cache_dir: Optional[Path] = None):
        super().__init__(parent)
        self._dir = Path(cache_dir) if cache_dir else paths.cache_dir() / "caratulas"
        self._net = QNetworkAccessManager(self)
        self._images: "OrderedDict[str, QImage]" = OrderedDict()
        self._pixmaps: dict[tuple, QPixmap] = {}
        self._tints: dict[str, Tint] = {}
        self._failed: set[str] = set()
        self._active: dict[str, QNetworkReply] = {}
        self._queue: list[str] = []
        self._budget = self.DECODES_PER_FRAME
        self._deferred: "OrderedDict[str, None]" = OrderedDict()
        self._frame = QTimer(self)
        self._frame.setSingleShot(True)
        self._frame.setInterval(0)
        self._frame.timeout.connect(self._next_frame)
        self._writes = 0
        self._pruning = False
        # Una poda al poco de arrancar, fuera del camino del primer pintado.
        QTimer.singleShot(8000, self, self.prune_disk)

    # -- consulta -----------------------------------------------------------
    def image(self, url: str) -> Optional[QImage]:
        """La imagen si ya está (memoria o disco); si no, la pide y devuelve None."""
        if not url or url in self._failed:
            return None
        image = self._images.get(url)
        if image is not None:
            self._images.move_to_end(url)
            return image
        if url.startswith("file:"):
            # Carátula de un archivo del disco: se lee de su sitio, sin red ni
            # copia en caché, y ya reducida (las incrustadas pueden medir 3000 px).
            reader = QImageReader(QUrl(url).toLocalFile())
            size = reader.size()
            if size.isValid() and max(size.width(), size.height()) > self.LOCAL_ART_PX:
                reader.setScaledSize(size.scaled(self.LOCAL_ART_PX, self.LOCAL_ART_PX,
                                                 Qt.AspectRatioMode.KeepAspectRatio))
            image = reader.read()
            if image.isNull():
                self._failed.add(url)
                return None
            self._remember(url, image)
            return image
        path = self._path(url)
        if path.is_file():
            image = QImage(str(path))
            if not image.isNull():
                self._remember(url, image)
                self._touch(path)
                return image
        self._enqueue(url)
        return None

    def failed(self, url: str) -> bool:
        """¿Ya se intentó descargar y no hay imagen? (no se reintenta en esta sesión)."""
        return url in self._failed

    def pixmap(self, url: str, size: int, radius: float = 3.0, dpr: float = 1.0,
               lazy: bool = False) -> Optional[QPixmap]:
        """``lazy``: para las filas de una lista. Si la imagen no está ya en
        memoria y este fotograma ya gastó su cupo de lecturas, devuelve None y
        avisa con ``loaded`` cuando la tenga lista (en el fotograma siguiente)."""
        key = (url, size, radius, round(dpr, 2))
        cached = self._pixmaps.get(key)
        if cached is not None:
            return cached
        if lazy and url and url not in self._images and url not in self._failed:
            if self._budget <= 0:
                self._deferred[url] = None
                self._frame.start()
                return None
            self._budget -= 1
            self._frame.start()
        image = self.image(url)
        if image is None:
            return None
        pixmap = square_pixmap(image, size, radius, dpr)
        self._pixmaps[key] = pixmap
        return pixmap

    def _next_frame(self) -> None:
        """Nuevo cupo de lecturas; las aplazadas se leen ahora y avisan a quien
        las pidió para que vuelva a pintar su fila."""
        self._budget = self.DECODES_PER_FRAME
        ready = []
        while self._deferred and self._budget > 0:
            url, _ = self._deferred.popitem(last=False)
            self._budget -= 1
            if self.image(url) is not None:
                ready.append(url)
        for url in ready:
            self.loaded.emit(url)
        if self._deferred:
            self._frame.start()
        else:
            self._budget = self.DECODES_PER_FRAME

    def forget_tints(self) -> None:
        """Al cambiar de tema: los tonos se recalculan para el esquema nuevo."""
        self._tints.clear()

    def tint(self, url: str) -> Optional[Tint]:
        cached = self._tints.get(url)
        if cached is not None:
            return cached
        image = self.image(url)
        if image is None:
            return None
        tint = tint_from_image(image)
        self._tints[url] = tint
        return tint

    # -- descarga -----------------------------------------------------------
    def _path(self, url: str) -> Path:
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
        return self._dir / digest[:2] / f"{digest}.img"

    def _remember(self, url: str, image: QImage) -> None:
        self._images[url] = image
        while len(self._images) > self.MEMORY_IMAGES:
            old, _ = self._images.popitem(last=False)
            for key in [k for k in self._pixmaps if k[0] == old]:
                del self._pixmaps[key]

    def _enqueue(self, url: str) -> None:
        if url in self._active or url in self._queue:
            return
        self._queue.append(url)
        self._pump()

    def _pump(self) -> None:
        while self._queue and len(self._active) < self.MAX_PARALLEL:
            url = self._queue.pop()            # lo último pedido (lo visible) primero
            request = QNetworkRequest(QUrl(url))
            request.setRawHeader(b"User-Agent", b"Mozilla/5.0")
            request.setTransferTimeout(15000)
            reply = self._net.get(request)
            self._active[url] = reply
            reply.finished.connect(lambda u=url, r=reply: self._on_finished(u, r))

    def _on_finished(self, url: str, reply: QNetworkReply) -> None:
        self._active.pop(url, None)
        data = bytes(reply.readAll()) if reply.error() == QNetworkReply.NetworkError.NoError else b""
        reply.deleteLater()
        image = QImage.fromData(data) if data else QImage()
        if image.isNull():
            self._failed.add(url)
        else:
            self._remember(url, image)
            self._store(url, data)
        self.loaded.emit(url)
        self._pump()

    # -- caché en disco -----------------------------------------------------
    def _store(self, url: str, data: bytes) -> None:
        """Escritura atómica: se escribe aparte y se renombra encima. Un
        cierre a medias deja, como mucho, un temporal huérfano (lo borra la
        poda), nunca una carátula truncada que luego se leería rota."""
        path = self._path(url)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)
        except OSError:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return
        self._writes += 1
        if self._writes % self.PRUNE_EVERY == 0:
            self.prune_disk()

    @staticmethod
    def _touch(path: Path) -> None:
        """Marca la carátula como usada: la poda empieza por las más olvidadas."""
        try:
            os.utime(path, None)
        except OSError:
            pass

    def prune_disk(self) -> None:
        """Poda la caché en disco en un hilo aparte (recorre miles de archivos)."""
        if self._pruning or not self._dir.is_dir():
            return
        self._pruning = True
        directory, limit = self._dir, self.DISK_LIMIT_BYTES

        def work() -> None:
            try:
                prune_cache(directory, limit)
            finally:
                self._pruning = False

        threading.Thread(target=work, name="poda-caratulas", daemon=True).start()


def prune_cache(directory: Path, limit_bytes: int, stale_tmp_s: float = 3600.0) -> int:
    """Borra las carátulas usadas hace más tiempo hasta que la carpeta quepa en
    ``limit_bytes``, y los temporales que llevan una hora huérfanos. Devuelve
    cuántos archivos borró."""
    entries = []
    removed = 0
    now = time.time()
    for path in directory.glob("*/*"):
        try:
            stat = path.stat()
        except OSError:
            continue
        if path.name.endswith(".tmp"):
            if now - stat.st_mtime > stale_tmp_s:
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    pass
            continue
        entries.append((stat.st_mtime, stat.st_size, path))
    total = sum(size for _, size, _ in entries)
    if total <= limit_bytes:
        return removed
    entries.sort()
    for _, size, path in entries:
        if total <= limit_bytes:
            break
        try:
            path.unlink()
        except OSError:
            continue
        total -= size
        removed += 1
    return removed
