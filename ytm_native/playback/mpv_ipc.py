"""Control de ``mpv`` como subproceso aislado vía IPC JSON.

* ``mpv`` corre en su propio proceso: un fallo del decodificador no cierra la
  ventana Qt. Si muere, se relanza (con tope de reinicios por minuto) y la capa
  de reproducción restaura pista, posición y cola.
* Se lanza con ``--ytdl=no`` para que nunca use un ``yt-dlp`` del PATH (otra
  versión, sin cookies) y con ``--no-config`` para no heredar ajustes ajenos.
* El IPC usa ``QLocalSocket`` (tubería con nombre en Windows, socket Unix en
  el resto): todo es asíncrono en el hilo de la GUI, sin hilos bloqueantes.
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

from ytm_native.core import config, paths

log = logging.getLogger("ytmn.mpv")

_OBS_TIME, _OBS_DURATION, _OBS_PAUSE = 1, 2, 3


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
        self._proc: Optional[QProcess] = None
        self._sock: Optional[QLocalSocket] = None
        self._pipe = ""
        self._counter = 0
        self._buffer = b""
        self._request_id = 0
        self._connect_attempts = 0
        self._connected = False
        self._shutting_down = False
        self._restarts: list[float] = []
        self.last_time_pos = 0.0          # sin limitar, para detectar finales prematuros
        self.duration = 0.0
        self._last_emitted_pos = -1.0
        self._retry = QTimer(self)
        self._retry.setSingleShot(True)
        self._retry.timeout.connect(self._try_connect)

    # -- ciclo de vida ------------------------------------------------------
    def is_ready(self) -> bool:
        return self._connected

    def start(self) -> None:
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
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(self._on_proc_error)
        self._proc = proc
        self._connect_attempts = 0
        proc.start()
        self._retry.start(150)

    def shutdown(self) -> None:
        self._shutting_down = True
        self._retry.stop()
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
        self._connected = False
        if self._sock is not None:
            self._sock.abort()
            self._sock.deleteLater()
            self._sock = None
        if self._proc is not None:
            self._proc.deleteLater()
            self._proc = None
        self._buffer = b""

    def _on_proc_error(self, error) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self._retry.stop()
            self._cleanup()
            self.gave_up.emit("mpv no pudo iniciarse.")

    def _on_finished(self, *_args) -> None:
        self._retry.stop()
        self._cleanup()
        if self._shutting_down:
            return
        now = time.monotonic()
        self._restarts = [t for t in self._restarts if now - t < 60] + [now]
        if len(self._restarts) > config.MPV_MAX_RESTARTS_PER_MIN:
            self.gave_up.emit("mpv se cerró varias veces seguidas; no se reiniciará solo.")
            return
        self.crashed.emit("mpv se cerró de forma inesperada; reiniciando…")
        QTimer.singleShot(300, self.start)

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
            self._on_connected()
            return
        sock.abort()
        sock.deleteLater()
        if self._connect_attempts < 100:
            self._retry.start(100)
        else:
            log.error("no se pudo conectar a la tubería de mpv: %s", self._pipe)
            self._shutting_down = True
            if self._proc is not None:
                self._proc.kill()
            self.gave_up.emit("No se pudo conectar con mpv por IPC.")

    def _on_connected(self) -> None:
        self.last_time_pos = 0.0
        self.duration = 0.0
        self.command("observe_property", _OBS_TIME, "time-pos")
        self.command("observe_property", _OBS_DURATION, "duration")
        self.command("observe_property", _OBS_PAUSE, "pause")
        # mpv registra los controles multimedia de Windows (--media-controls).
        # Con los atajos por defecto desactivados hay que enlazar sus teclas:
        # pausa la gestiona mpv; siguiente/anterior se reenvían a la cola.
        for key in ("PLAYPAUSE", "PLAY", "PAUSE"):
            self.command("keybind", key, "cycle pause")
        self.command("keybind", "NEXT", "script-message ytmn-media next")
        self.command("keybind", "PREV", "script-message ytmn-media prev")
        self.ready.emit()

    def _on_disconnected(self) -> None:
        self._connected = False

    # -- protocolo ----------------------------------------------------------
    def command(self, *args) -> int:
        if not self._connected or self._sock is None:
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
            if isinstance(msg, dict) and "event" in msg:
                self._on_event(msg)

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
    def load(self, url: str, user_agent: str = "") -> None:
        self.duration = 0.0
        if user_agent:
            self.command("set_property", "user-agent", user_agent)
        self.command("loadfile", url, "replace")

    def set_media_title(self, title: str) -> None:
        """Título que muestran los controles multimedia del sistema."""
        self.command("set_property", "force-media-title", title)

    def stop(self) -> None:
        self.command("stop")

    def set_pause(self, paused: bool) -> None:
        self.command("set_property", "pause", bool(paused))

    def seek(self, seconds: float) -> None:
        self.command("seek", float(seconds), "absolute")

    def set_volume(self, volume: int) -> None:
        self._volume = max(0, min(100, int(volume)))
        self.command("set_property", "volume", self._volume)
