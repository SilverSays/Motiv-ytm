"""Música del disco: lectura de etiquetas y escaneo de una carpeta.

Las pistas del disco suenan en mpv directamente desde el archivo
(``file:///…``): no pasan por yt-dlp ni gastan red. Cada una lleva el id
``local:<sha1 de la ruta normalizada>`` y su ruta queda en la tabla
``local_files``.

Las etiquetas se leen con un lector propio, solo con la biblioteca estándar,
para no sumar dependencias al ejecutable. Cubre lo que usan casi todos los
archivos de música:

* MP3: ID3v2.2/2.3/2.4 (con desincronización), ID3v1 de respaldo y la
  duración por la cabecera Xing/Info o VBRI (o, sin ellas, por la tasa fija);
* FLAC: STREAMINFO, comentarios Vorbis y PICTURE;
* Ogg Vorbis y Opus: comentarios (y la carátula ``METADATA_BLOCK_PICTURE``)
  y la duración por la última posición de granulado;
* M4A/AAC en MP4: ``mvhd`` y los átomos ``©nam``, ``©ART``, ``aART``,
  ``©alb`` y ``covr``;
* AAC en bruto (ADTS): ID3v2 al principio y la duración contando tramas;
* WAV: ``fmt``/``data`` para la duración, ``LIST/INFO`` e ``id3 ``.

Si un archivo está dañado o no trae etiquetas, se usa el nombre del archivo
como título: un archivo raro nunca detiene el escaneo.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import stat
import struct
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Callable, Iterator, Optional

from ytm_native.core import config, paths
from ytm_native.core.db import Database, Track

log = logging.getLogger("ytmn.local")

LOCAL_PREFIX = "local:"
_HIDDEN = getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0x2)
_SYSTEM = getattr(stat, "FILE_ATTRIBUTE_SYSTEM", 0x4)
_MAX_TAG_BYTES = 64 * 1024 * 1024          # una etiqueta más grande que esto está dañada


def normalized_path(path: "str | os.PathLike") -> str:
    """Ruta absoluta y normalizada (mayúsculas y barras como las compara Windows)."""
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def local_id(path: "str | os.PathLike") -> str:
    digest = hashlib.sha1(normalized_path(path).encode("utf-8", "surrogatepass")).hexdigest()
    return LOCAL_PREFIX + digest


def is_local(video_id: Optional[str]) -> bool:
    return bool(video_id) and video_id.startswith(LOCAL_PREFIX)


def file_uri(path: "str | os.PathLike") -> str:
    """``file:///C:/M%C3%BAsica/…``: lo que se le da a mpv."""
    return Path(os.path.abspath(os.fspath(path))).as_uri()


@dataclass
class LocalTags:
    title: str = ""
    artists: list[str] = field(default_factory=list)
    album: str = ""
    album_artist: str = ""
    duration_s: Optional[float] = None
    cover: bytes = b""
    cover_mime: str = ""

    def add_artist(self, value: str) -> None:
        for part in (value or "").split("\x00"):
            part = part.strip()
            if part and part not in self.artists:
                self.artists.append(part)


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def _read_exact(fh: BinaryIO, n: int) -> bytes:
    data = fh.read(n)
    if len(data) != n:
        raise EOFError("archivo cortado")
    return data


def _syncsafe(data: bytes) -> int:
    return (data[0] & 0x7F) << 21 | (data[1] & 0x7F) << 14 | (data[2] & 0x7F) << 7 | (data[3] & 0x7F)


def _decode_text(encoding: int, data: bytes) -> str:
    try:
        if encoding == 0:
            text = data.decode("latin-1")
        elif encoding == 1:
            text = data.decode("utf-16")             # con BOM
        elif encoding == 2:
            text = data.decode("utf-16-be")
        else:
            text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1", "replace")
    return text.replace("\ufeff", "").strip("\x00").strip()


def _split_terminated(encoding: int, data: bytes) -> tuple[bytes, bytes]:
    """Separa un texto terminado en cero (1 o 2 bytes según la codificación) del resto."""
    if encoding in (1, 2):
        i = 0
        while i + 1 < len(data):
            if data[i] == 0 and data[i + 1] == 0:
                return data[:i], data[i + 2:]
            i += 2
        return data, b""
    i = data.find(b"\x00")
    return (data, b"") if i < 0 else (data[:i], data[i + 1:])


def _apply_comment(tags: LocalTags, key: str, value: str) -> None:
    """Un comentario Vorbis o INFO de WAV ya decodificado."""
    key = key.strip().upper()
    value = value.strip()
    if not value:
        return
    if key in ("TITLE", "INAM") and not tags.title:
        tags.title = value
    elif key in ("ARTIST", "IART"):
        tags.add_artist(value)
    elif key in ("ALBUM", "IPRD") and not tags.album:
        tags.album = value
    elif key in ("ALBUMARTIST", "ALBUM ARTIST") and not tags.album_artist:
        tags.album_artist = value


def _picture_block(data: bytes) -> tuple[bytes, str]:
    """Bloque PICTURE de FLAC (también va en base64 dentro de Ogg)."""
    pos = 4
    mime_len = struct.unpack(">I", data[pos:pos + 4])[0]
    pos += 4
    mime = data[pos:pos + mime_len].decode("ascii", "replace")
    pos += mime_len
    desc_len = struct.unpack(">I", data[pos:pos + 4])[0]
    pos += 4 + desc_len + 16
    length = struct.unpack(">I", data[pos:pos + 4])[0]
    pos += 4
    return data[pos:pos + length], mime


def _keep_cover(tags: LocalTags, data: bytes, mime: str, front: bool = True) -> None:
    if not data or len(data) > config.LOCAL_ART_MAX_BYTES:
        return
    if tags.cover and not front:
        return                                       # ya hay una; la portada manda
    tags.cover, tags.cover_mime = data, mime or _sniff_mime(data)


def _sniff_mime(data: bytes) -> str:
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return ""


# ---------------------------------------------------------------------------
# ID3
# ---------------------------------------------------------------------------
_ID3_TEXT = {
    "TIT2": "title", "TT2": "title",
    "TPE1": "artist", "TP1": "artist",
    "TALB": "album", "TAL": "album",
    "TPE2": "album_artist", "TP2": "album_artist",
    "TLEN": "length", "TLE": "length",
}


def _unsync(data: bytes) -> bytes:
    return data.replace(b"\xff\x00", b"\xff")


def read_id3v2(fh: BinaryIO, tags: LocalTags) -> int:
    """Lee una etiqueta ID3v2 al principio del archivo. Devuelve su tamaño
    total (0 si no hay), para saber dónde empieza el audio."""
    fh.seek(0)
    header = fh.read(10)
    if len(header) < 10 or header[:3] != b"ID3":
        return 0
    major, flags = header[3], header[5]
    size = _syncsafe(header[6:10])
    total = 10 + size + (10 if flags & 0x10 else 0)
    if major not in (2, 3, 4) or size > _MAX_TAG_BYTES:
        return total
    body = fh.read(size)
    if flags & 0x80 and major < 4:
        body = _unsync(body)
    pos = 0
    if flags & 0x40 and major >= 3:                  # cabecera extendida
        ext = struct.unpack(">I", body[:4])[0] if len(body) >= 4 else 0
        pos = (_syncsafe(body[:4]) if major == 4 else ext + 4)
    id_len, size_len = (3, 3) if major == 2 else (4, 4)
    head_len = 6 if major == 2 else 10
    while pos + head_len <= len(body):
        frame_id = body[pos:pos + id_len]
        if not frame_id.strip(b"\x00") or not frame_id.isalnum():
            break                                    # relleno
        raw_size = body[pos + id_len:pos + id_len + size_len]
        if major == 2:
            fsize = int.from_bytes(raw_size, "big")
            fflags = 0
        elif major == 4:
            fsize = _syncsafe(raw_size)
            fflags = int.from_bytes(body[pos + 8:pos + 10], "big")
        else:
            fsize = struct.unpack(">I", raw_size)[0]
            fflags = int.from_bytes(body[pos + 8:pos + 10], "big")
        start = pos + head_len
        data = body[start:start + fsize]
        pos = start + fsize
        if fsize <= 0 or len(data) < fsize:
            break
        if major == 4:
            if fflags & 0x0001:                      # longitud de datos delante
                data = data[4:]
            if fflags & 0x0002:
                data = _unsync(data)
            if fflags & 0x000C:                      # comprimido o cifrado: se salta
                continue
        elif major == 3 and fflags & 0x00C0:
            continue
        _id3_frame(frame_id.decode("latin-1"), data, tags, major)
    return total


def _id3_frame(frame_id: str, data: bytes, tags: LocalTags, major: int) -> None:
    kind = _ID3_TEXT.get(frame_id)
    if kind is not None and data:
        text = _decode_text(data[0], data[1:])
        if kind == "artist":
            # ID3v2.4 separa varios valores con un cero; la 2.3 suele usar "/".
            tags.add_artist(text if "\x00" in text or major == 4 else text.replace(" / ", "\x00"))
        elif kind == "length":
            try:
                ms = float(text.strip("\x00"))
                if ms > 0 and tags.duration_s is None:
                    tags.duration_s = ms / 1000
            except ValueError:
                pass
        elif kind == "title" and not tags.title:
            tags.title = text.split("\x00")[0]
        elif kind == "album" and not tags.album:
            tags.album = text.split("\x00")[0]
        elif kind == "album_artist" and not tags.album_artist:
            tags.album_artist = text.split("\x00")[0]
        return
    if frame_id in ("APIC", "PIC") and len(data) > 4:
        encoding = data[0]
        if frame_id == "PIC":
            fmt = data[1:4].decode("latin-1", "replace").upper()
            mime = {"JPG": "image/jpeg", "PNG": "image/png"}.get(fmt, "")
            rest = data[4:]
        else:
            mime_raw, rest = _split_terminated(0, data[1:])
            mime = mime_raw.decode("latin-1", "replace").lower()
            if mime in ("jpg", "jpeg"):
                mime = "image/jpeg"
            elif mime == "png":
                mime = "image/png"
        if not rest:
            return
        picture_type = rest[0]
        _desc, image = _split_terminated(encoding, rest[1:])
        _keep_cover(tags, image, mime or _sniff_mime(image), front=picture_type in (3, 0))


def read_id3v1(fh: BinaryIO, tags: LocalTags) -> None:
    try:
        fh.seek(-128, os.SEEK_END)
    except OSError:
        return
    block = fh.read(128)
    if len(block) != 128 or block[:3] != b"TAG":
        return

    def text(raw: bytes) -> str:
        return raw.split(b"\x00")[0].decode("latin-1", "replace").strip()

    tags.title = tags.title or text(block[3:33])
    if not tags.artists:
        tags.add_artist(text(block[33:63]))
    tags.album = tags.album or text(block[63:93])


# ---------------------------------------------------------------------------
# MP3: duración
# ---------------------------------------------------------------------------
_MPEG_BITRATES = {
    (1, 1): [0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448],
    (1, 2): [0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384],
    (1, 3): [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320],
    (2, 1): [0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256],
    (2, 2): [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],
    (2, 3): [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],
}
_MPEG_RATES = {1: (44100, 48000, 32000), 2: (22050, 24000, 16000), 25: (11025, 12000, 8000)}


def mp3_duration(fh: BinaryIO, audio_start: int, file_size: int) -> Optional[float]:
    fh.seek(audio_start)
    window = fh.read(64 * 1024)
    i = 0
    while i + 4 <= len(window):
        if window[i] == 0xFF and (window[i + 1] & 0xE0) == 0xE0:
            header = struct.unpack(">I", window[i:i + 4])[0]
            version_bits = (header >> 19) & 3
            layer_bits = (header >> 17) & 3
            bitrate_idx = (header >> 12) & 0xF
            rate_idx = (header >> 10) & 3
            if version_bits != 1 and layer_bits != 0 and bitrate_idx not in (0, 15) and rate_idx != 3:
                version = {3: 1, 2: 2, 0: 25}[version_bits]
                layer = 4 - layer_bits
                rate = _MPEG_RATES[version][rate_idx]
                bitrate = _MPEG_BITRATES[(1 if version == 1 else 2, layer)][bitrate_idx] * 1000
                mono = ((header >> 6) & 3) == 3
                if layer == 1:
                    samples = 384
                elif layer == 3 and version != 1:
                    samples = 576
                else:
                    samples = 1152
                frame = window[i:i + 200]
                if version == 1:
                    side = 17 if mono else 32
                else:
                    side = 9 if mono else 17
                xing_at = 4 + side
                tag = frame[xing_at:xing_at + 4]
                if tag in (b"Xing", b"Info") and len(frame) >= xing_at + 12:
                    flags = struct.unpack(">I", frame[xing_at + 4:xing_at + 8])[0]
                    if flags & 1:
                        frames = struct.unpack(">I", frame[xing_at + 8:xing_at + 12])[0]
                        if frames > 0:
                            return frames * samples / rate
                if frame[36:40] == b"VBRI" and len(frame) >= 36 + 18:
                    frames = struct.unpack(">I", frame[36 + 14:36 + 18])[0]
                    if frames > 0:
                        return frames * samples / rate
                if bitrate > 0:
                    audio_bytes = max(0, file_size - (audio_start + i))
                    return audio_bytes * 8 / bitrate
            i += 1
        else:
            i += 1
    return None


# ---------------------------------------------------------------------------
# FLAC
# ---------------------------------------------------------------------------
def _vorbis_comments(data: bytes, tags: LocalTags) -> None:
    pos = 0
    vendor_len = struct.unpack("<I", data[pos:pos + 4])[0]
    pos += 4 + vendor_len
    count = struct.unpack("<I", data[pos:pos + 4])[0]
    pos += 4
    for _ in range(min(count, 10000)):
        if pos + 4 > len(data):
            break
        length = struct.unpack("<I", data[pos:pos + 4])[0]
        pos += 4
        entry = data[pos:pos + length]
        pos += length
        key, sep, value = entry.partition(b"=")
        if not sep:
            continue
        name = key.decode("ascii", "replace").upper()
        if name == "METADATA_BLOCK_PICTURE":
            try:
                picture = base64.b64decode(value, validate=False)
                image, mime = _picture_block(picture)
                ptype = struct.unpack(">I", picture[:4])[0]
                _keep_cover(tags, image, mime, front=ptype in (3, 0))
            except (ValueError, struct.error):
                pass
            continue
        _apply_comment(tags, name, value.decode("utf-8", "replace"))


def read_flac(fh: BinaryIO, tags: LocalTags) -> None:
    start = read_id3v2(fh, LocalTags())              # algunos FLAC llevan ID3 delante
    fh.seek(start)
    if fh.read(4) != b"fLaC":
        raise ValueError("no es FLAC")
    last = False
    while not last:
        head = _read_exact(fh, 4)
        last = bool(head[0] & 0x80)
        kind = head[0] & 0x7F
        length = int.from_bytes(head[1:4], "big")
        if kind == 0:
            info = _read_exact(fh, length)
            rate = (info[10] << 12 | info[11] << 4 | info[12] >> 4)
            total = ((info[13] & 0x0F) << 32) | struct.unpack(">I", info[14:18])[0]
            if rate > 0 and total > 0:
                tags.duration_s = total / rate
        elif kind == 4:
            _vorbis_comments(_read_exact(fh, length), tags)
        elif kind == 6 and length <= config.LOCAL_ART_MAX_BYTES + 1024:
            block = _read_exact(fh, length)
            image, mime = _picture_block(block)
            _keep_cover(tags, image, mime, front=struct.unpack(">I", block[:4])[0] in (3, 0))
        else:
            fh.seek(length, os.SEEK_CUR)


# ---------------------------------------------------------------------------
# Ogg (Vorbis y Opus)
# ---------------------------------------------------------------------------
def _ogg_packets(fh: BinaryIO, limit: int = 3) -> Iterator[bytes]:
    """Los primeros paquetes del flujo (pueden ocupar varias páginas)."""
    packet = b""
    sent = 0
    while sent < limit:
        head = fh.read(27)
        if len(head) < 27 or head[:4] != b"OggS":
            return
        segments = head[26]
        table = _read_exact(fh, segments)
        body = _read_exact(fh, sum(table))
        pos = 0
        for lacing in table:
            packet += body[pos:pos + lacing]
            pos += lacing
            if lacing < 255:
                yield packet
                packet = b""
                sent += 1
                if sent >= limit:
                    return
            if len(packet) > _MAX_TAG_BYTES:
                return


def _ogg_last_granule(fh: BinaryIO, size: int) -> int:
    span = min(size, 256 * 1024)
    fh.seek(size - span)
    tail = fh.read(span)
    at = tail.rfind(b"OggS")
    while at >= 0:
        if at + 14 <= len(tail):
            granule = struct.unpack("<q", tail[at + 6:at + 14])[0]
            if granule > 0:
                return granule
        at = tail.rfind(b"OggS", 0, at)
    return 0


def read_ogg(fh: BinaryIO, tags: LocalTags, size: int) -> None:
    fh.seek(0)
    packets = list(_ogg_packets(fh, 2))
    if not packets:
        raise ValueError("no es Ogg")
    ident = packets[0]
    rate, pre_skip = 0, 0
    if ident.startswith(b"\x01vorbis") and len(ident) >= 16:
        rate = struct.unpack("<I", ident[12:16])[0]
        comment_prefix = b"\x03vorbis"
    elif ident.startswith(b"OpusHead") and len(ident) >= 12:
        rate, pre_skip = 48000, struct.unpack("<H", ident[10:12])[0]
        comment_prefix = b"OpusTags"
    else:
        raise ValueError("Ogg sin Vorbis ni Opus")
    if len(packets) > 1 and packets[1].startswith(comment_prefix):
        _vorbis_comments(packets[1][len(comment_prefix):], tags)
    granule = _ogg_last_granule(fh, size)
    if rate > 0 and granule > pre_skip:
        tags.duration_s = (granule - pre_skip) / rate


# ---------------------------------------------------------------------------
# MP4 / M4A
# ---------------------------------------------------------------------------
_MP4_CONTAINERS = {b"moov", b"udta", b"ilst", b"trak", b"mdia"}
_MP4_TEXT = {b"\xa9nam": "title", b"\xa9ART": "artist", b"aART": "album_artist", b"\xa9alb": "album"}


def _mp4_atoms(fh: BinaryIO, start: int, end: int) -> Iterator[tuple[bytes, int, int]]:
    pos = start
    while pos + 8 <= end:
        fh.seek(pos)
        head = fh.read(8)
        if len(head) < 8:
            return
        size, kind = struct.unpack(">I", head[:4])[0], head[4:8]
        header = 8
        if size == 1:
            size = struct.unpack(">Q", _read_exact(fh, 8))[0]
            header = 16
        elif size == 0:
            size = end - pos
        if size < header or pos + size > end:
            return
        yield kind, pos + header, pos + size
        pos += size


def read_mp4(fh: BinaryIO, tags: LocalTags, size: int) -> None:
    fh.seek(4)
    if fh.read(4) != b"ftyp":
        raise ValueError("no es MP4")

    def walk(start: int, end: int, depth: int) -> None:
        for kind, body, stop in _mp4_atoms(fh, start, end):
            if kind == b"mvhd":
                fh.seek(body)
                data = fh.read(32)
                if data and data[0] == 1 and len(data) >= 32:
                    scale, length = struct.unpack(">IQ", data[20:32])
                elif len(data) >= 20:
                    scale, length = struct.unpack(">II", data[12:20])
                else:
                    continue
                if scale > 0 and length > 0:
                    tags.duration_s = length / scale
            elif kind == b"meta":
                walk(body + 4, stop, depth + 1)       # caja completa: versión y banderas
            elif kind in _MP4_CONTAINERS and depth < 8:
                walk(body, stop, depth + 1)
            elif kind in _MP4_TEXT or kind == b"covr":
                for sub, sbody, sstop in _mp4_atoms(fh, body, stop):
                    if sub != b"data" or sstop - sbody < 8:
                        continue
                    if kind == b"covr" and sstop - sbody - 8 > config.LOCAL_ART_MAX_BYTES:
                        continue
                    fh.seek(sbody)
                    payload = fh.read(sstop - sbody)
                    value_type = struct.unpack(">I", payload[:4])[0] & 0xFFFFFF
                    value = payload[8:]
                    if kind == b"covr":
                        mime = {13: "image/jpeg", 14: "image/png"}.get(value_type, "")
                        _keep_cover(tags, value, mime or _sniff_mime(value))
                        break
                    text = value.decode("utf-8", "replace").strip()
                    field_name = _MP4_TEXT[kind]
                    if field_name == "artist":
                        tags.add_artist(text)
                    elif field_name == "title" and not tags.title:
                        tags.title = text
                    elif field_name == "album" and not tags.album:
                        tags.album = text
                    elif field_name == "album_artist" and not tags.album_artist:
                        tags.album_artist = text
                    break

    walk(0, size, 0)


# ---------------------------------------------------------------------------
# AAC en bruto (ADTS)
# ---------------------------------------------------------------------------
_ADTS_RATES = (96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050, 16000, 12000, 11025, 8000, 7350)


def adts_duration(fh: BinaryIO, start: int, size: int, max_frames: int = 200000) -> Optional[float]:
    pos, frames, rate = start, 0, 0
    while pos + 7 <= size and frames < max_frames:
        fh.seek(pos)
        head = fh.read(7)
        if len(head) < 7 or head[0] != 0xFF or (head[1] & 0xF6) != 0xF0:
            break
        rate_idx = (head[2] >> 2) & 0xF
        if rate_idx >= len(_ADTS_RATES):
            break
        rate = _ADTS_RATES[rate_idx]
        length = ((head[3] & 0x03) << 11) | (head[4] << 3) | (head[5] >> 5)
        if length < 7:
            break
        frames += 1 + (head[6] & 0x03)
        pos += length
    return frames * 1024 / rate if frames and rate else None


# ---------------------------------------------------------------------------
# WAV
# ---------------------------------------------------------------------------
def read_wav(fh: BinaryIO, tags: LocalTags, size: int) -> None:
    fh.seek(0)
    head = fh.read(12)
    if head[:4] != b"RIFF" or head[8:12] != b"WAVE":
        raise ValueError("no es WAV")
    byte_rate = 0
    data_size = 0
    pos = 12
    while pos + 8 <= size:
        fh.seek(pos)
        chunk = fh.read(8)
        if len(chunk) < 8:
            break
        kind, length = chunk[:4], struct.unpack("<I", chunk[4:8])[0]
        body = pos + 8
        if kind == b"fmt " and length >= 16:
            fmt = fh.read(16)
            byte_rate = struct.unpack("<I", fmt[8:12])[0]
        elif kind == b"data":
            data_size = min(length, size - body)
        elif kind == b"LIST" and length <= 1024 * 1024:
            block = fh.read(length)
            if block[:4] == b"INFO":
                sub = 4
                while sub + 8 <= len(block):
                    name, sub_len = block[sub:sub + 4], struct.unpack("<I", block[sub + 4:sub + 8])[0]
                    value = block[sub + 8:sub + 8 + sub_len].split(b"\x00")[0]
                    _apply_comment(tags, name.decode("ascii", "replace"), value.decode("utf-8", "replace")
                                   if _is_utf8(value) else value.decode("latin-1"))
                    sub += 8 + sub_len + (sub_len & 1)
        elif kind in (b"id3 ", b"ID3 ") and length <= _MAX_TAG_BYTES:
            block = fh.read(length)
            import io

            read_id3v2(io.BytesIO(block), tags)
        pos = body + length + (length & 1)
    if byte_rate > 0 and data_size > 0:
        tags.duration_s = data_size / byte_rate


def _is_utf8(data: bytes) -> bool:
    try:
        data.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


# ---------------------------------------------------------------------------
# Entrada
# ---------------------------------------------------------------------------
def read_tags(path: "str | os.PathLike") -> LocalTags:
    """Etiquetas, duración y carátula de un archivo de audio. Nunca lanza:
    lo que no se pueda leer queda vacío (y el título será el nombre del archivo)."""
    tags = LocalTags()
    path = os.fspath(path)
    ext = os.path.splitext(path)[1].lower()
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            magic = fh.read(12)
            if magic[:4] == b"fLaC" or ext == ".flac":
                read_flac(fh, tags)
            elif magic[:4] == b"OggS":
                read_ogg(fh, tags, size)
            elif magic[4:8] == b"ftyp":
                read_mp4(fh, tags, size)
            elif magic[:4] == b"RIFF":
                read_wav(fh, tags, size)
            else:
                audio_start = read_id3v2(fh, tags)
                fh.seek(audio_start)
                first = fh.read(2)
                if ext == ".aac" or (len(first) == 2 and first[0] == 0xFF and (first[1] & 0xF6) == 0xF0):
                    duration = adts_duration(fh, audio_start, size)
                else:
                    duration = mp3_duration(fh, audio_start, size)
                    if not tags.title or not tags.artists:
                        read_id3v1(fh, tags)
                if tags.duration_s is None:
                    tags.duration_s = duration
    except (OSError, EOFError, ValueError, struct.error, IndexError, KeyError, OverflowError) as exc:
        log.info("etiquetas ilegibles en %s: %s", os.path.basename(path), type(exc).__name__)
    if tags.duration_s is not None and not (0 < tags.duration_s < 7 * 86400):
        tags.duration_s = None
    return tags


def track_from_file(path: "str | os.PathLike", tags: LocalTags, thumb_url: str = "") -> Track:
    path = os.fspath(path)
    title = tags.title or os.path.splitext(os.path.basename(path))[0]
    artists = ", ".join(tags.artists) or tags.album_artist
    duration = round(tags.duration_s) if tags.duration_s else None
    return Track(local_id(path), title, artists, tags.album, duration, thumb_url=thumb_url)


# ---------------------------------------------------------------------------
# Escaneo de la carpeta
# ---------------------------------------------------------------------------
def art_dir() -> Path:
    return paths.cache_dir() / "caratulas_locales"


def _visible(entry: os.DirEntry) -> bool:
    if entry.name.startswith((".", "$")):
        return False
    try:
        attrs = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return False
    return not attrs & (_HIDDEN | _SYSTEM)


def audio_files(folder: "str | os.PathLike", cancel: Optional[threading.Event] = None,
                limit: int = config.LOCAL_SCAN_MAX_FILES) -> Iterator[tuple[str, os.stat_result]]:
    """Archivos de audio de la carpeta y sus subcarpetas, sin las ocultas ni
    las de sistema, y sin seguir enlaces (no hay bucles)."""
    pending = [os.fspath(folder)]
    seen = 0
    while pending:
        current = pending.pop()
        try:
            with os.scandir(current) as it:
                entries = sorted(it, key=lambda e: e.name.lower())
        except OSError:
            continue
        subdirs = []
        for entry in entries:
            if cancel is not None and cancel.is_set():
                return
            if not _visible(entry):
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    subdirs.append(entry.path)
                elif (entry.is_file(follow_symlinks=False)
                      and os.path.splitext(entry.name)[1].lower() in config.LOCAL_AUDIO_EXTENSIONS):
                    yield entry.path, entry.stat(follow_symlinks=False)
                    seen += 1
                    if seen >= limit:
                        return
            except OSError:
                continue
        pending.extend(reversed(subdirs))


@dataclass
class ScanReport:
    folder: str = ""
    total: int = 0
    read: int = 0
    removed: int = 0
    cancelled: bool = False


def _save_cover(video_id: str, tags: LocalTags, mtime: float, folder: Path) -> str:
    """Guarda la carátula incrustada aparte (un archivo por versión, para que
    la caché de imágenes no enseñe la vieja si cambia) y devuelve su URL."""
    for old in folder.glob(f"{video_id[len(LOCAL_PREFIX):]}_*"):
        try:
            old.unlink()
        except OSError:
            pass
    if not tags.cover:
        return ""
    ext = {"image/png": ".png", "image/webp": ".webp"}.get(tags.cover_mime, ".jpg")
    target = folder / f"{video_id[len(LOCAL_PREFIX):]}_{int(mtime)}{ext}"
    tmp = target.with_name(target.name + ".tmp")
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with open(tmp, "wb") as fh:
            fh.write(tags.cover)
        os.replace(tmp, target)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        return ""
    return target.as_uri()


def scan_folder(db: Database, folder: "str | os.PathLike", cancel: Optional[threading.Event] = None,
                progress: Optional[Callable[[int, int], None]] = None,
                covers: Optional[Path] = None, batch: int = 200) -> ScanReport:
    """Lee la carpeta (bloqueante; llamar desde un hilo de trabajo).

    Solo se leen las etiquetas de los archivos nuevos o que cambiaron de
    tamaño o de fecha; lo que desapareció se olvida.
    """
    root = normalized_path(folder)
    covers = covers or art_dir()
    report = ScanReport(folder=root)
    # La ruta se guarda tal cual (para mpv y para enseñarla); se compara normalizada.
    known = {normalized_path(p): row for p, row in db.local_file_index(root).items()}
    present: set[str] = set()
    pending: list[tuple[Track, str, int, float]] = []
    for path, info in audio_files(os.path.abspath(os.fspath(folder)), cancel):
        report.total += 1
        norm = normalized_path(path)
        present.add(norm)
        old = known.get(norm)
        if old is not None and old[1] == info.st_size and abs(old[2] - info.st_mtime) < 1e-3:
            continue
        tags = read_tags(path)
        video_id = local_id(path)
        thumb = _save_cover(video_id, tags, info.st_mtime, covers)
        pending.append((track_from_file(path, tags, thumb), os.path.abspath(path), info.st_size, info.st_mtime))
        report.read += 1
        if len(pending) >= batch:
            db.save_local_files(root, pending)
            pending = []
        if progress is not None and report.read % 25 == 0:
            progress(report.read, report.total)
    if cancel is not None and cancel.is_set():
        report.cancelled = True
        if pending:
            db.save_local_files(root, pending)
        return report
    gone = [vid for path, (vid, _size, _mtime) in known.items() if path not in present]
    report.removed = len(gone)
    db.save_local_files(root, pending, gone)
    for video_id in gone:
        _save_cover(video_id, LocalTags(), 0, covers)        # borra su carátula
    return report


def resolve_path(db: Database, video_id: str) -> Optional[str]:
    """Ruta en disco de una pista local, si sigue existiendo."""
    path = db.local_file_path(video_id)
    if path and os.path.isfile(path):
        return path
    return None


def scan_in_thread(db: Database, folder: str, cancel: threading.Event) -> ScanReport:
    """Para ``run_async``: escanea y cierra la conexión del hilo al terminar."""
    started = time.monotonic()
    try:
        report = scan_folder(db, folder, cancel)
    finally:
        db.close_thread_connection()
    log.info("carpeta de música leída: %d archivos (%d leídos, %d olvidados) en %.1f s",
             report.total, report.read, report.removed, time.monotonic() - started)
    return report
