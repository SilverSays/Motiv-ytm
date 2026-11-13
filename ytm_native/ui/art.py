"""Carátulas: descarga asíncrona con caché en disco y "tinta" extraída de la imagen.

Las imágenes salen del CDN público de miniaturas (sin cookies ni cuenta) y se
guardan en ``cache/caratulas``, de modo que cada carátula se pide una sola vez.
Todo ocurre en el hilo de la GUI con ``QNetworkAccessManager`` (sin hilos).
"""

from __future__ import annotations

import colorsys
import hashlib
import math
import re
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QRectF, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from ytm_native.core import paths
from . import theme

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
    return f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg" if video_id else ""


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

    La banda usa ese tono pastel, muy claro (la tinta pizarra siempre se lee
    encima), y la tinta, el mismo tono hondo para resaltar sobre el panel claro.
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
    band = QColor.fromHslF(hue, min(0.50, max(0.26, sat * 0.6)), 0.86)
    ink = QColor.fromHslF(hue, min(0.78, max(0.46, sat)), 0.33)
    return Tint(band, ink)


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


def placeholder_pixmap(size: int, radius: float, dpr: float = 1.0) -> QPixmap:
    """Funda sin carátula: un disco esquemático sobre el color de relieve."""
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

    # -- consulta -----------------------------------------------------------
    def image(self, url: str) -> Optional[QImage]:
        """La imagen si ya está (memoria o disco); si no, la pide y devuelve None."""
        if not url or url in self._failed:
            return None
        image = self._images.get(url)
        if image is not None:
            self._images.move_to_end(url)
            return image
        path = self._path(url)
        if path.is_file():
            image = QImage(str(path))
            if not image.isNull():
                self._remember(url, image)
                return image
        self._enqueue(url)
        return None

    def failed(self, url: str) -> bool:
        """¿Ya se intentó descargar y no hay imagen? (no se reintenta en esta sesión)."""
        return url in self._failed

    def pixmap(self, url: str, size: int, radius: float = 3.0, dpr: float = 1.0) -> Optional[QPixmap]:
        key = (url, size, radius, round(dpr, 2))
        cached = self._pixmaps.get(key)
        if cached is not None:
            return cached
        image = self.image(url)
        if image is None:
            return None
        pixmap = square_pixmap(image, size, radius, dpr)
        self._pixmaps[key] = pixmap
        return pixmap

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
            try:
                path = self._path(url)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            except OSError:
                pass
        self.loaded.emit(url)
        self._pump()
