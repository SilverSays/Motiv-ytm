"""Control de ``mpv`` como subproceso aislado vía IPC JSON.

* ``mpv`` corre en su propio proceso: un fallo del decodificador no cierra la
  ventana Qt. Si muere, se relanza (con tope de reinicios por minuto) y la capa
  de reproducción restaura pista, posición y cola.
* Se lanza con ``--ytdl=no`` para que nunca use un ``yt-dlp`` del PATH (otra
  versión, sin cookies) y con ``--no-config`` para no heredar ajustes ajenos.
* El IPC usa ``QLocalSocket`` (tubería con nombre en Windows, socket Unix en
  el resto): todo es asíncrono en el hilo de la GUI, sin hilos bloqueantes.
* Si la tubería se corta y ``mpv`` sigue vivo, se vuelve a conectar (y a
  observar sus propiedades) sin relanzarlo: la música no se interrumpe.
* Cada ``loadfile`` lleva una ficha de quien lo pide; la respuesta de mpv
  (``load_acked``) la devuelve con el ``playlist_entry_id`` que le asignó, para
  que los eventos de una carga anterior no se confundan con los de esta.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QProcess, QTimer, Signal
from PySide6.QtNetwork import QLocalSocket

from ytm_native.core import config, paths, proc_guard

log = logging.getLogger("ytmn.mpv")

_OBS_TIME, _OBS_DURATION, _OBS_PAUSE = 1, 2, 3

# Volumen igualado entre canciones. ``loudnorm`` sigue la norma EBU R128 (el
# mismo nivel de sonoridad para todo); ``dynaudnorm`` es más suave y adapta la
# ganancia sobre la marcha.
LOUDNORM = "loudnorm=I=-16:TP=-1.5:LRA=11"
DYNAUDNORM = "dynaudnorm=f=150:g=15"
NORMALIZERS = {"loudnorm": LOUDNORM, "dynaudnorm": DYNAUDNORM}


def make_pipe_name(counter: int) -> str:
    name = f"ytmn-mpv-{os.getpid()}-{counter}"
    if paths.IS_WINDOWS:
        return rf"\\.\pipe\{name}"
    return str(Path(tempfile.gettempdir()) / f"{name}.sock")


class MpvPlayer(QObject):
    ready = Signal()
    crashed = Signal(str)                 # murió; se está relanzando
    gave_up = Signal(str)                 # demasiados reinicios o no arranca
    position_changed = Signal(float)
    duration_changed = Signal(float)
    pause_changed = Signal(bool)
    file_started = Signal(int)            # playlist_entry_id
    # Respuesta a ``load``: ficha que se pasó, playlist_entry_id que asignó mpv
    # (0 si es una versión que no lo dice) y si aceptó la orden.
    load_acked = Signal(int, int, bool)
    reconnected = Signal()                # la tubería se cortó y se recuperó con mpv vivo
    file_loaded = Signal()
    file_ended = Signal(int, str, str)    # playlist_entry_id, reason, file_error
    media_key = Signal(str)               # "next" | "prev" (teclas multimedia de Windows)

    def __init__(
        self,
        mpv_path: Optional[Path] = None,
        parent: Optional[QObject] = None,
        volume: int = 80,
        pre_args: Optional[list[str]] = None,
    ):
        super().__init__(parent)
        self._mpv_path = Path(mpv_path) if mpv_path else paths.mpv_exe()
        self._pre_args = list(pre_args or [])   # solo para pruebas (mpv simulado)
        self._volume = volume
        self._audio_filter = ""           # filtro de audio vigente ("" = ninguno)
        self._filter_request = -1
        self._media_controls = True       # mpv publica su propia sesión multimedia en Windows
        self._proc: Optional[QProcess] = None
        self._sock: Optional[QLocalSocket] = None
        self._pipe = ""
        self._counter = 0
        self._buffer = b""
        self._request_id = 0
        self._connect_attempts = 0
        self._connected = False
        self._reconnecting = False
        self._dropped = 0                 # órdenes descartadas por falta de conexión
        self._pending_loads: dict[int, int] = {}   # request_id de loadfile -> ficha
        self._shutting_down = False
        # Tras ``shutdown`` (cierre de la app) ya no se relanza nunca: ni por
        # un reinicio pendiente tras un fallo, ni por una llamada a ``start``.
        self._closed = False
        self._restarts: list[float] = []
        self.last_time_pos = 0.0          # sin limitar, para detectar finales prematuros
        self.duration = 0.0
        self._last_emitted_pos = -1.0
        self._retry = QTimer(self)
        self._retry.setSingleShot(True)
        self._retry.timeout.connect(self._try_connect)
        self._relaunch = QTimer(self)
        self._relaunch.setSingleShot(True)
        self._relaunch.setInterval(300)
        self._relaunch.timeout.connect(self.start)

    # -- ciclo de vida ------------------------------------------------------
    def is_ready(self) -> bool:
        return self._connected

    def start(self) -> None:
        if self._closed:
            log.info("mpv ya se apagó con la app: no se vuelve a lanzar")
            return
        if self._proc is not None:
            return
        if not self._mpv_path.is_file():
            self.gave_up.emit(f"No se encontró {self._mpv_path.name} en la carpeta bin.")
            return
        self._shutting_down = False
        self._counter += 1
        self._pipe = make_pipe_name(self._counter)
        args = [
            "--no-config",
            "--no-video",
            "--idle=yes",
            "--ytdl=no",
            "--no-terminal",
            "--audio-display=no",
            # --no-config no desactiva los scripts integrados (OSC, estadísticas…).
            "--osc=no",
            "--load-scripts=no",
            "--load-stats-overlay=no",
            "--load-auto-profiles=no",
            "--input-default-bindings=no",
            "--network-timeout=20",
            f"--volume={self._volume}",
            f"--input-ipc-server={self._pipe}",
        ]
        if os.environ.get("YTMN_MPV_LOG"):
            # Diagnóstico: log detallado de mpv (se trunca en cada arranque).
            args.append(f"--log-file={paths.log_dir() / 'mpv.log'}")
        proc = QProcess(self)
        proc.setProgram(str(self._mpv_path))
        proc.setArguments(self._pre_args + args)
        proc.started.connect(self._on_started)
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(self._on_proc_error)
        self._proc = proc
        self._connect_attempts = 0
        self._reconnecting = False
        proc.start()
        self._retry.start(150)

    def shutdown(self) -> None:
        """Cierra mpv para siempre. Se puede llamar varias veces (cierre de la
        ventana, fin de la aplicación, salida de Python)."""
        if self._closed and self._proc is None:
            return
        self._closed = True
        self._shutting_down = True
        self._retry.stop()
        self._relaunch.stop()
        if self._connected:
            self.command("quit")
        if self._sock is not None:
            self._sock.flush()
        if self._proc is not None:
            if not self._proc.waitForFinished(1500):
                self._proc.kill()
                self._proc.waitForFinished(1500)
        self._cleanup()

    def _cleanup(self) -> None:
        self._drop_socket()
        self._reconnecting = False
        if self._proc is not None:
            self._proc.deleteLater()
            self._proc = None

    def _drop_socket(self) -> None:
        """Suelta la conexión actual (sin avisos: el corte lo pide la app)."""
        self._connected = False
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.disconnected.disconnect(self._on_disconnected)
                sock.readyRead.disconnect(self._on_ready_read)
            except (RuntimeError, TypeError):
                pass
            sock.abort()
            sock.deleteLater()
        self._buffer = b""
        self._pending_loads.clear()       # sus respuestas ya no llegarán

    def _on_started(self) -> None:
        proc = self.sender()
        if isinstance(proc, QProcess):
            # Si la app muere de golpe, Windows cierra este mpv con ella.
            proc_guard.adopt(int(proc.processId()))

    def _on_proc_error(self, error) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self._retry.stop()
            self._cleanup()
            self.gave_up.emit("mpv no pudo iniciarse.")

    def _on_finished(self, *_args) -> None:
        self._retry.stop()
        self._cleanup()
        if self._shutting_down or self._closed:
            return
        now = time.monotonic()
        self._restarts = [t for t in self._restarts if now - t < 60] + [now]
        if len(self._restarts) > config.MPV_MAX_RESTARTS_PER_MIN:
            self.gave_up.emit("mpv se cerró varias veces seguidas; no se reiniciará solo.")
            return
        self.crashed.emit("mpv se cerró de forma inesperada; reiniciando…")
        self._relaunch.start()      # temporizador propio: ``shutdown`` lo cancela

    # -- conexión IPC -------------------------------------------------------
    def _try_connect(self) -> None:
        if self._proc is None or self._connected:
            return
        self._connect_attempts += 1
        sock = QLocalSocket(self)
        sock.connectToServer(self._pipe)
        if sock.waitForConnected(50):
            self._sock = sock
            sock.readyRead.connect(self._on_ready_read)
            sock.disconnected.connect(self._on_disconnected)
            self._connected = True
            if self._dropped:
                log.info("conexión con mpv lista; se descartaron %d órdenes mientras faltaba", self._dropped)
                self._dropped = 0
            if self._reconnecting:
                self._reconnecting = False
                log.info("conexión IPC con mpv recuperada")
                self._setup_session()
                self.reconnected.emit()
            else:
                self._on_connected()
            return
        sock.abort()
        sock.deleteLater()
        if self._connect_attempts < 100:
            self._retry.start(100)
        elif self._reconnecting:
            # mpv sigue vivo pero ya no atiende: se cierra y el relanzamiento
            # normal (``_on_finished``) restaura la pista.
            log.error("no se pudo volver a conectar con mpv: se reinicia")
            self._reconnecting = False
            if self._proc is not None:
                self._proc.kill()
        else:
            log.error("no se pudo conectar a la tubería de mpv: %s", self._pipe)
            self._shutting_down = True
            if self._proc is not None:
                self._proc.kill()
            self.gave_up.emit("No se pudo conectar con mpv por IPC.")

    def _on_connected(self) -> None:
        self.last_time_pos = 0.0
        self.duration = 0.0
        self._setup_session()
        self.ready.emit()

    def _setup_session(self) -> None:
        """Lo que vive en la conexión y no en mpv: las propiedades observadas
        (también al reconectar) y los atajos de las teclas multimedia."""
        self.command("observe_property", _OBS_TIME, "time-pos")
        self.command("observe_property", _OBS_DURATION, "duration")
        self.command("observe_property", _OBS_PAUSE, "pause")
        if not self._media_controls:
            self._apply_media_controls()      # también tras relanzarse
        if self._audio_filter:
            self._apply_audio_filter()        # un mpv relanzado arranca sin filtros
        # mpv registra los controles multimedia de Windows (--media-controls).
        # Con los atajos por defecto desactivados hay que enlazar sus teclas:
        # pausa la gestiona mpv; siguiente/anterior se reenvían a la cola.
        for key in ("PLAYPAUSE", "PLAY", "PAUSE"):
            self.command("keybind", key, "cycle pause")
        self.command("keybind", "NEXT", "script-message ytmn-media next")
        self.command("keybind", "PREV", "script-message ytmn-media prev")

    def _on_disconnected(self) -> None:
        self._connected = False
        if self._shutting_down or self._closed:
            return
        proc = self._proc
        if proc is None or proc.state() == QProcess.ProcessState.NotRunning:
            return                        # mpv murió: lo relanza ``_on_finished``
        # La tubería se cortó con mpv vivo: se reconecta en vez de seguir
        # descartando órdenes. Si en realidad está muriendo, ``_on_finished``
        # para estos intentos.
        log.warning("se cortó la conexión IPC con mpv, que sigue en marcha: reconectando")
        self._drop_socket()
        self._reconnecting = True
        self._connect_attempts = 0
        self._retry.start(100)

    # -- protocolo ----------------------------------------------------------
    def command(self, *args) -> int:
        if not self._connected or self._sock is None:
            if not self._closed:
                self._dropped += 1
                name = args[0] if args else "?"
                if self._dropped == 1:
                    log.warning("mpv no está conectado: se descarta la orden «%s»", name)
                else:
                    log.debug("mpv no está conectado: se descarta la orden «%s»", name)
            return -1
        self._request_id += 1
        payload = json.dumps({"command": list(args), "request_id": self._request_id})
        self._sock.write(payload.encode("utf-8") + b"\n")
        self._sock.flush()
        return self._request_id

    def _on_ready_read(self) -> None:
        if self._sock is None:
            return
        self._buffer += bytes(self._sock.readAll())
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line.decode("utf-8", "replace"))
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            if "event" in msg:
                self._on_event(msg)
            elif "request_id" in msg:
                self._on_reply(msg)

    def _on_reply(self, msg: dict) -> None:
        ok = msg.get("error") == "success"
        if msg.get("request_id") == self._filter_request:
            self._filter_request = -1
            if not ok:
                log.warning("mpv rechazó el filtro de audio «%s»: %s", self._audio_filter, msg.get("error"))
            return
        token = self._pending_loads.pop(msg.get("request_id"), None)
        if token is None:
            if not ok:
                log.debug("mpv rechazó una orden: %s", msg.get("error"))
            return
        data = msg.get("data")
        entry = data.get("playlist_entry_id") if isinstance(data, dict) else None
        if not ok:
            log.warning("mpv rechazó la orden de carga: %s", msg.get("error"))
        self.load_acked.emit(token, entry if type(entry) is int and entry > 0 else 0, ok)

    def _on_event(self, msg: dict) -> None:
        event = msg.get("event")
        if event == "property-change":
            self._on_property(msg.get("id"), msg.get("data"))
        elif event == "start-file":
            self.last_time_pos = 0.0
            self._last_emitted_pos = -1.0
            self.file_started.emit(int(msg.get("playlist_entry_id") or 0))
        elif event == "file-loaded":
            self.file_loaded.emit()
        elif event == "client-message":
            args = msg.get("args") or []
            if len(args) >= 2 and args[0] == "ytmn-media" and args[1] in ("next", "prev"):
                self.media_key.emit(args[1])
        elif event == "end-file":
            reason = str(msg.get("reason") or "unknown")
            # "stop"/"quit"/"redirect" son consecuencia de nuestras propias
            # órdenes; solo interesan el final natural y los errores.
            if reason in ("eof", "error", "unknown"):
                self.file_ended.emit(
                    int(msg.get("playlist_entry_id") or 0),
                    "error" if reason == "unknown" else reason,
                    str(msg.get("file_error") or ""),
                )

    def _on_property(self, obs_id, data) -> None:
        if obs_id == _OBS_TIME:
            if isinstance(data, (int, float)):
                self.last_time_pos = float(data)
                if abs(self.last_time_pos - self._last_emitted_pos) >= 0.25:
                    self._last_emitted_pos = self.last_time_pos
                    self.position_changed.emit(self.last_time_pos)
        elif obs_id == _OBS_DURATION:
            if isinstance(data, (int, float)) and data > 0:
                self.duration = float(data)
                self.duration_changed.emit(self.duration)
        elif obs_id == _OBS_PAUSE:
            if isinstance(data, bool):
                self.pause_changed.emit(data)

    # -- órdenes de alto nivel ---------------------------------------------
    def load(self, url: str, user_agent: str = "", token: int = 0) -> int:
        """Carga ``url``. La respuesta de mpv llega por ``load_acked`` con
        ``token``. Devuelve el ``request_id`` (-1 si no hay conexión)."""
        self.duration = 0.0
        if user_agent:
            self.command("set_property", "user-agent", user_agent)
        request = self.command("loadfile", url, "replace")
        if request > 0:
            self._pending_loads[request] = token
        return request

    def set_media_controls(self, enabled: bool) -> None:
        """``False`` cuando la app publica su propia sesión multimedia (con
        artista y carátula): la de mpv sobraría y Windows enseñaría dos."""
        self._media_controls = bool(enabled)
        self._apply_media_controls()

    def _apply_media_controls(self) -> None:
        # En marcha y no como argumento: un mpv antiguo que no conozca la
        # opción solo rechaza la orden; con el argumento no arrancaría.
        if not self._connected:
            return                        # se aplica al conectar (``_setup_session``)
        self.command("set_property", "media-controls", "no" if not self._media_controls else "player")

    def set_media_title(self, title: str) -> None:
        """Título que muestran los controles multimedia del sistema."""
        self.command("set_property", "force-media-title", title)

    def stop(self) -> None:
        self.command("stop")

    def set_pause(self, paused: bool) -> None:
        self.command("set_property", "pause", bool(paused))

    def seek(self, seconds: float) -> None:
        self.command("seek", float(seconds), "absolute")

    @property
    def volume(self) -> int:
        return self._volume

    def set_volume(self, volume: int) -> None:
        self._volume = max(0, min(100, int(volume)))
        self.command("set_property", "volume", self._volume)

    @property
    def audio_filter(self) -> str:
        return self._audio_filter

    def set_normalization(self, mode: Optional[str]) -> None:
        """Iguala el volumen entre canciones (``"loudnorm"`` o ``"dynaudnorm"``)
        o lo deja como viene (``None``). Se aplica en marcha, sin relanzar mpv
        ni cortar la pista; si mpv aún no está conectado, al conectar."""
        wanted = NORMALIZERS.get(mode or "", "")
        if wanted == self._audio_filter:
            return
        self._audio_filter = wanted
        self._apply_audio_filter()

    def _apply_audio_filter(self) -> None:
        if self._connected:
            self._filter_request = self.command("set_property", "af", self._audio_filter)
