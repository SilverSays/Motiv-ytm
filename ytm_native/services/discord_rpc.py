"""Discord Rich Presence opcional: «Escuchando» con la canción que suena.

Viene **desactivado**: hasta que el usuario lo activa no se abre ninguna
conexión con Discord ni se crea el hilo que la lleva.

Habla directamente con el cliente de escritorio de Discord por su tubería
local (``\\\\.\\pipe\\discord-ipc-N``), sin bibliotecas de terceros: cada
mensaje es un código de operación y una longitud (dos enteros de 32 bits
*little-endian*) seguidos de JSON. Primero un saludo con el id de la
aplicación; después, ``SET_ACTIVITY`` con la actividad o ``null`` para
quitarla.

Todo ocurre en un hilo aparte: la ventana solo deja la última actividad
deseada y sigue. Si Discord no está abierto o se cierra a mitad, el hilo lo
anota, cierra la tubería y vuelve a probar con esperas crecientes (de 15 s a
5 min); mientras espera está dormido, sin gastar CPU. Las lecturas tienen
tiempo límite: un Discord colgado no bloquea nada.

Se publica solo título, artista, álbum, carátula y tiempos.
"""

from __future__ import annotations

import json
import logging
import os
import re
import struct
import sys
import threading
import time
import uuid
from typing import Callable, Optional

from PySide6.QtCore import QObject

from ytm_native.core import config
from ytm_native.core.db import Database, Track
from ytm_native.core.i18n import tr

log = logging.getLogger("ytmn.discord")

ENABLED_KEY = "discord_rpc_enabled"
CLIENT_ID_KEY = "discord_client_id"
OP_HANDSHAKE, OP_FRAME, OP_CLOSE, OP_PING, OP_PONG = 0, 1, 2, 3, 4
ACTIVITY_LISTENING = 2
IO_TIMEOUT_S = 3.0
_MAX_FRAME = 1 << 20
_CLIENT_ID = re.compile(r"^\d{15,22}$")


class DiscordError(Exception):
    pass


def valid_client_id(value: str) -> bool:
    return bool(_CLIENT_ID.match((value or "").strip()))


# ---------------------------------------------------------------------------
# Protocolo
# ---------------------------------------------------------------------------
def encode(op: int, payload: dict) -> bytes:
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return struct.pack("<II", op, len(body)) + body


def read_frame(transport, timeout: float = IO_TIMEOUT_S) -> tuple[int, dict]:
    op, length = struct.unpack("<II", transport.read(8, timeout))
    if length > _MAX_FRAME:
        raise DiscordError(tr("mensaje de Discord demasiado grande"))
    body = transport.read(length, timeout) if length else b""
    try:
        data = json.loads(body.decode("utf-8")) if body else {}
    except (UnicodeDecodeError, ValueError) as exc:
        raise DiscordError(tr("mensaje de Discord ilegible")) from exc
    return op, data if isinstance(data, dict) else {}


def _fit(text: str, limit: int = 128) -> str:
    """Discord pide entre 2 y 128 caracteres en cada texto."""
    text = " ".join((text or "").split())
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text if len(text) >= 2 else f"«{text}»"


def build_activity(track: Track, art_url: str, playing: bool, position: float, duration: float,
                   now: float) -> dict:
    """La actividad de Discord para la canción que suena (o en pausa)."""
    title = track.title.strip() or tr("Sin título")
    artist = (track.artists or "").strip() or tr("Artista desconocido")
    activity: dict = {
        "type": ACTIVITY_LISTENING,
        "details": _fit(title),
        "state": _fit(artist if playing else tr("{artist} (en pausa)", artist=artist)),
        "instance": False,
    }
    assets: dict = {}
    if art_url.startswith("https://") and len(art_url) <= 256:
        assets["large_image"] = art_url
    album = (track.album or "").strip()
    if album:
        assets["large_text"] = _fit(album)
    if assets:
        activity["assets"] = assets
    if playing:
        start = now - max(0.0, position)
        stamps = {"start": int(start * 1000)}
        if duration and duration > 0:
            stamps["end"] = int((start + duration) * 1000)
        activity["timestamps"] = stamps
    return activity


# ---------------------------------------------------------------------------
# Transporte: la tubería con nombre de Discord
# ---------------------------------------------------------------------------
class PipeTransport:
    """Un extremo de ``\\\\.\\pipe\\discord-ipc-N`` con lecturas acotadas."""

    def __init__(self, handle):
        self._fh = handle

    @classmethod
    def open(cls) -> "PipeTransport":
        if sys.platform != "win32":
            raise OSError(tr("solo en Windows"))
        last: Optional[OSError] = None
        for n in range(10):
            try:
                return cls(open(rf"\\.\pipe\discord-ipc-{n}", "r+b", buffering=0))  # noqa: SIM115
            except OSError as exc:
                last = exc
        raise last or OSError(tr("Discord no está abierto"))

    def _available(self) -> int:
        import ctypes
        import msvcrt
        from ctypes import wintypes

        avail = wintypes.DWORD(0)
        handle = msvcrt.get_osfhandle(self._fh.fileno())
        if not ctypes.windll.kernel32.PeekNamedPipe(
            wintypes.HANDLE(handle), None, 0, None, ctypes.byref(avail), None
        ):
            raise OSError(tr("la tubería de Discord se cerró"))
        return avail.value

    def read(self, size: int, timeout: float) -> bytes:
        out = b""
        deadline = time.monotonic() + timeout
        while len(out) < size:
            # Solo se lee lo que ya llegó: un Discord colgado no deja el hilo
            # parado para siempre.
            if self._available() == 0:
                if time.monotonic() > deadline:
                    raise TimeoutError(tr("Discord no responde"))
                time.sleep(0.02)
                continue
            chunk = self._fh.read(size - len(out))
            if not chunk:
                raise OSError(tr("la tubería de Discord se cerró"))
            out += chunk
        return out

    def write(self, data: bytes) -> None:
        self._fh.write(data)

    def close(self) -> None:
        try:
            self._fh.close()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Hilo de la conexión
# ---------------------------------------------------------------------------
class _Link(threading.Thread):
    """Lleva la conexión: envía la última actividad deseada cuando puede."""

    def __init__(self, client_id: str, connect: Callable[[], object]):
        super().__init__(name="discord-rpc", daemon=True)
        self.client_id = client_id
        self._connect = connect
        self._cond = threading.Condition()
        self._wanted: Optional[dict] = None
        self._version = 0
        self._stop_flag = False
        self._conn = None
        self.attempts = 0                # conexiones intentadas (para las pruebas y el registro)
        self.sent = 0

    # -- desde el hilo de la ventana -----------------------------------------
    def want(self, activity: Optional[dict]) -> None:
        with self._cond:
            self._wanted = activity
            self._version += 1
            self._cond.notify_all()

    def stop(self, wait: float = 1.5) -> None:
        with self._cond:
            self._stop_flag = True
            self._cond.notify_all()
        if self.is_alive() and threading.current_thread() is not self:
            self.join(wait)

    # -- en el hilo -----------------------------------------------------------
    def run(self) -> None:
        sent_version = 0
        retry_at = 0.0
        last_send = -1e9
        step = 0
        while True:
            with self._cond:
                while not self._stop_flag and self._version == sent_version:
                    self._cond.wait()
                if self._stop_flag:
                    break
                wanted, version = self._wanted, self._version
                now = time.monotonic()
                pause = max(retry_at - now, last_send + config.DISCORD_MIN_UPDATE_S - now)
                if pause > 0:
                    # Dormido hasta poder enviar (o hasta que cambie algo).
                    self._cond.wait(pause)
                    continue
            if wanted is None and self._conn is None:
                sent_version = version       # nada que quitar: no se conecta para eso
                continue
            try:
                if self._conn is None:
                    self.attempts += 1
                    self._conn = self._open()
                self._set_activity(wanted)
                self.sent += 1
                sent_version = version
                last_send = time.monotonic()
                step = 0
                retry_at = 0.0
            except Exception as exc:  # noqa: BLE001 - Discord ausente o cerrado: se reintenta
                self._drop()
                delays = config.DISCORD_RETRY_DELAYS_S
                delay = delays[min(step, len(delays) - 1)]
                step += 1
                retry_at = time.monotonic() + delay
                if step == 1:
                    log.info("Discord no disponible (%s): se reintenta en %d s", type(exc).__name__, delay)
                else:
                    log.debug("Discord sigue sin responder: reintento en %d s", delay)
        # Al parar: se quita la actividad si había conexión, y se cierra.
        if self._conn is not None:
            try:
                self._set_activity(None)
            except Exception:  # noqa: BLE001 - al cerrar da igual
                pass
            self._drop()

    def _open(self):
        conn = self._connect()
        try:
            conn.write(encode(OP_HANDSHAKE, {"v": 1, "client_id": self.client_id}))
            op, data = read_frame(conn)
            if op == OP_CLOSE:
                raise DiscordError(tr("Discord rechazó la conexión: {get}", get=data.get('message', '')))
        except BaseException:
            conn.close()
            raise
        log.info("conectado con Discord")
        return conn

    def _set_activity(self, activity: Optional[dict]) -> None:
        conn = self._conn
        conn.write(encode(OP_FRAME, {
            "cmd": "SET_ACTIVITY",
            "args": {"pid": os.getpid(), "activity": activity},
            "nonce": uuid.uuid4().hex,
        }))
        # La respuesta se lee siempre (si no, la tubería se llenaría). Un ping
        # se contesta y se sigue esperando la respuesta.
        for _ in range(4):
            op, data = read_frame(conn)
            if op == OP_PING:
                conn.write(encode(OP_PONG, data))
                continue
            if op == OP_CLOSE:
                raise DiscordError(tr("Discord cerró la conexión"))
            if data.get("evt") == "ERROR":
                # La actividad no gustó (p. ej. un campo no válido): la
                # conexión sigue sirviendo, así que no se insiste con esta.
                log.warning("Discord rechazó la actividad: %s", (data.get("data") or {}).get("message", ""))
            return
        raise DiscordError(tr("Discord no confirmó la actividad"))

    def _drop(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            conn.close()


# ---------------------------------------------------------------------------
# Servicio
# ---------------------------------------------------------------------------
class DiscordPresence(QObject):
    """Lo que usa la ventana: ``update`` con lo que suena, sin esperar nunca."""

    def __init__(self, db: Database, connect: Optional[Callable[[], object]] = None,
                 clock: Callable[[], float] = time.time, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.db = db
        self._connect = connect or PipeTransport.open
        self._clock = clock
        self._enabled = db.get_bool(ENABLED_KEY, False)
        self._link: Optional[_Link] = None
        self._shown: Optional[tuple] = None    # (videoId, sonando, inicio, duración) publicado
        self._last: Optional[tuple] = None     # último ``update`` recibido, para reenviarlo

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def client_id(self) -> str:
        saved = (self.db.get_setting(CLIENT_ID_KEY) or "").strip()
        return saved or os.environ.get("YTMN_DISCORD_CLIENT_ID", "").strip() or config.DISCORD_CLIENT_ID

    @property
    def link(self) -> Optional[_Link]:
        """El hilo de la conexión (``None`` mientras no haga falta)."""
        return self._link

    def set_client_id(self, client_id: str) -> None:
        client_id = client_id.strip()
        if not valid_client_id(client_id):
            raise ValueError(tr("el id de la aplicación de Discord son solo cifras"))
        self.db.set_setting(CLIENT_ID_KEY, client_id)
        if self._link is not None:
            self._stop_link()
            self._resend()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        self.db.set_setting(ENABLED_KEY, "1" if self._enabled else "0")
        if self._enabled:
            self._resend()
        else:
            self._stop_link()

    def update(self, track: Optional[Track], art_url: str = "", playing: bool = False,
               position: float = 0.0, duration: float = 0.0) -> None:
        self._last = (track, art_url, playing, position, duration)
        if not self._enabled:
            return
        if track is None:
            if self._shown is not None:
                self._shown = None
                self._push(None)
            return
        now = self._clock()
        start = now - max(0.0, position)
        shown = self._shown
        if (shown is not None and shown[0] == track.video_id and shown[1] == playing
                and abs((shown[3] or 0) - (duration or 0)) < 1
                and (not playing or abs(shown[2] - start) < config.DISCORD_SEEK_TOLERANCE_S)):
            return                           # nada que Discord no sepa ya
        self._shown = (track.video_id, playing, start, duration)
        self._push(build_activity(track, art_url, playing, position, duration, now))

    def _resend(self) -> None:
        self._shown = None
        if self._last is not None:
            self.update(*self._last)

    def _push(self, activity: Optional[dict]) -> None:
        if self._link is None:
            if activity is None:
                return
            client_id = self.client_id
            if not valid_client_id(client_id):
                log.info("Discord: falta el id de la aplicación; no se conecta")
                return
            self._link = _Link(client_id, self._connect)
            self._link.start()
        self._link.want(activity)

    def _stop_link(self, wait: float = 0.0) -> None:
        # Sin esperar por defecto: el hilo quita la actividad y cierra solo;
        # la ventana no se queda parada si Discord tarda en contestar.
        link, self._link = self._link, None
        self._shown = None
        if link is not None:
            link.stop(wait)

    def shutdown(self) -> None:
        self._stop_link(wait=1.0)
