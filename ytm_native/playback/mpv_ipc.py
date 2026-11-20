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
* Sin pausas: ``append`` deja la pista siguiente en la lista de mpv, que la
  empieza en cuanto acaba la actual sin cerrar la salida de audio.
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
from ytm_native.core.i18n import tr

log = logging.getLogger("ytmn.mpv")

_OBS_TIME, _OBS_DURATION, _OBS_PAUSE, _OBS_DEVICES = 1, 2, 3, 4

# Volumen igualado entre canciones. ``loudnorm`` sigue la norma EBU R128 (el
# mismo nivel de sonoridad para todo); ``dynaudnorm`` es más suave y adapta la
# ganancia sobre la marcha.
LOUDNORM = "loudnorm=I=-16:TP=-1.5:LRA=11"
DYNAUDNORM = "dynaudnorm=f=150:g=15"
NORMALIZERS = {"loudnorm": LOUDNORM, "dynaudnorm": DYNAUDNORM}

# Salida de audio: "auto" es la que Windows tenga como predeterminada.
AUTO_DEVICE = "auto"

# Ecualizador: seis bandas de pico (Hz) y, por preajuste, su ganancia en dB.
# Va antes de la normalización: ``loudnorm`` nivela ya con el color aplicado.
EQ_BANDS = (60, 230, 910, 3600, 8000, 14000)
EQ_FLAT = "flat"
EQ_PRESETS = {
    EQ_FLAT: (0, 0, 0, 0, 0, 0),
    "bass": (6, 4, 1, 0, 0, 0),
    "vocal": (-2, -1, 2, 4, 2, 0),
    "rock": (4, 2, -2, 1, 3, 3),
    "pop": (-1, 2, 4, 2, -1, -1),
    "electronic": (5, 3, 0, 1, 3, 4),
    "acoustic": (3, 1, 1, 2, 2, 1),
}
# Etiqueta del grafo del ecualizador en ``af``: con ella se le cambian las
# ganancias en marcha (``af-command``) sin rehacer la cadena de filtros.
EQ_LABEL = "ytmn-eq"


def eq_preamp(gains) -> float:
    """Atenuación previa (lineal) para que lo que el preajuste realza no sature."""
    return 10 ** (-max(0, max(gains)) / 20)


def equalizer_graph(gains) -> str:
    """El ecualizador como filtro de ``af``: una atenuación y una banda por
    frecuencia, en un solo grafo de libavfilter con etiqueta."""
    bands = ",".join(f"equalizer=f={f}:t=q:w=1:g={g}" for f, g in zip(EQ_BANDS, gains))
    return f"@{EQ_LABEL}:lavfi=[volume=volume={eq_preamp(gains):.6f},{bands}]"


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
    devices_changed = Signal()            # la lista de salidas de audio cambió
    device_lost = Signal(str)             # la salida elegida desapareció (su nombre): suena por la de Windows
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
        self._audio_filter = ""           # lo último enviado a ``af`` ("" = ninguno)
        self._filter_request = -1
        # ``af`` se compone de dos partes independientes: cambiar una nunca
        # quita la otra (un preajuste no apaga la normalización ni al revés).
        self._normalization_filter = ""
        self._eq_preset = EQ_FLAT
        self._eq_gains = EQ_PRESETS[EQ_FLAT]
        self._eq_in_graph = False         # el grafo del ecualizador está en el ``af`` de mpv
        self._af_stale = False            # ``af`` aún dice las ganancias de antes de un af-command
        self._eq_requests: set[int] = set()
        # Atenuación del fundido entre canciones: multiplica el volumen elegido.
        self._fade = 1.0
        # Salida de audio: la elegida, la que mpv usa ahora y las que hay.
        self._device = AUTO_DEVICE
        self._device_name = ""
        self._device_applied = AUTO_DEVICE
        self._devices: list[tuple[str, str]] = []
        self._devices_known = False
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
            self.gave_up.emit(tr("No se encontró {name} en la carpeta bin.", name=self._mpv_path.name))
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
            # Sin pausas entre pistas: la salida de audio sigue abierta de una
            # a otra, y la siguiente de la lista de mpv (la que encadena el
            # reproductor con ``append``) se abre antes de que acabe la actual,
            # para que abrir su URL no deje un hueco.
            "--gapless-audio=yes",
            "--prefetch-playlist=yes",
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
            self.gave_up.emit(tr("mpv no pudo iniciarse."))

    def _on_finished(self, *_args) -> None:
        self._retry.stop()
        self._cleanup()
        if self._shutting_down or self._closed:
            return
        now = time.monotonic()
        self._restarts = [t for t in self._restarts if now - t < 60] + [now]
        if len(self._restarts) > config.MPV_MAX_RESTARTS_PER_MIN:
            self.gave_up.emit(tr("mpv se cerró varias veces seguidas; no se reiniciará solo."))
            return
        self.crashed.emit(tr("mpv se cerró de forma inesperada; reiniciando…"))
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
            self.gave_up.emit(tr("No se pudo conectar con mpv por IPC."))

    def _on_connected(self) -> None:
        self.last_time_pos = 0.0
        self.duration = 0.0
        self._fade = 1.0                  # arranca con ``--volume``, sin atenuar
        self._device_applied = AUTO_DEVICE
        self._devices_known = False
        self._setup_session()
        self.ready.emit()

    def _setup_session(self) -> None:
        """Lo que vive en la conexión y no en mpv: las propiedades observadas
        (también al reconectar) y los atajos de las teclas multimedia."""
        self.command("observe_property", _OBS_TIME, "time-pos")
        self.command("observe_property", _OBS_DURATION, "duration")
        self.command("observe_property", _OBS_PAUSE, "pause")
        # Las salidas de audio: mpv manda la lista enseguida y otra vez cada
        # vez que se conecta o desconecta algo (unos auriculares).
        self.command("observe_property", _OBS_DEVICES, "audio-device-list")
        if not self._media_controls:
            self._apply_media_controls()      # también tras relanzarse
        self._apply_device()
        self._eq_in_graph = self._eq_gains != EQ_PRESETS[EQ_FLAT]
        if self._compose_filter():
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
        request = msg.get("request_id")
        if request in self._eq_requests:
            self._eq_requests.discard(request)
            if not ok and self._eq_in_graph:
                # Un mpv que no atiende ``af-command``: se rehace la cadena con
                # las ganancias nuevas (una vez, aunque fallen varias bandas).
                log.info("mpv no cambió el ecualizador en marcha (%s): se rehace el filtro", msg.get("error"))
                self._eq_requests.clear()
                self._apply_audio_filter()
            return
        if request == self._filter_request:
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
            # Una pista encadenada empieza sin ``load``: la duración de la
            # anterior no vale para ella (ni para detectar un final prematuro).
            self.duration = 0.0
            if self._af_stale:
                # La cadena se monta de nuevo para esta pista: es el momento de
                # dejar en ``af`` las ganancias que se cambiaron en marcha.
                self._af_stale = False
                self._eq_in_graph = self._eq_gains != EQ_PRESETS[EQ_FLAT]
                self._apply_audio_filter()
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
        elif obs_id == _OBS_DEVICES:
            if isinstance(data, list):
                self._on_devices(data)

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

    def append(self, url: str, user_agent: str = "", token: int = 0) -> int:
        """Añade ``url`` tras la que suena, sin cortarla: mpv pasa a ella sola
        al terminar la actual. La respuesta llega por ``load_acked`` con
        ``token`` y el ``playlist_entry_id`` de la entrada nueva. Devuelve el
        ``request_id`` (-1 si no hay conexión)."""
        if user_agent:
            # Se aplica al abrir el archivo: la que suena ya está abierta.
            self.command("set_property", "user-agent", user_agent)
        request = self.command("loadfile", url, "append")
        if request > 0:
            self._pending_loads[request] = token
        return request

    def playlist_clear(self) -> None:
        """Quita de la lista de mpv todo menos lo que suena (lo encadenado)."""
        self.command("playlist-clear")

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
        self._send_volume()

    def set_fade(self, factor: float) -> None:
        """Atenuación del fundido entre canciones (1 = el volumen elegido, 0 =
        silencio). Va aparte del volumen: el usuario (o el temporizador) puede
        moverlo durante un fundido y el fundido sigue sobre lo nuevo."""
        factor = max(0.0, min(1.0, float(factor)))
        if abs(factor - self._fade) < 0.01 and not (factor in (0.0, 1.0) and factor != self._fade):
            return
        self._fade = factor
        self._send_volume()

    @property
    def fade(self) -> float:
        return self._fade

    def _send_volume(self) -> None:
        level = self._volume if self._fade >= 1.0 else round(self._volume * self._fade, 2)
        self.command("set_property", "volume", level)

    # -- salida de audio ----------------------------------------------------
    def audio_devices(self) -> list[tuple[str, str]]:
        """Las salidas que mpv ve ahora (nombre, descripción), sin "auto"."""
        return list(self._devices)

    @property
    def audio_device(self) -> str:
        """La salida elegida (aunque ahora no esté conectada)."""
        return self._device

    @property
    def audio_device_in_use(self) -> str:
        return self._device_applied

    def set_audio_device(self, name: str, description: str = "") -> None:
        """Cambia la salida en marcha (mpv reabre el audio; la pista sigue
        donde iba). Si la elegida no está conectada suena por la de Windows
        hasta que vuelva."""
        self._device = name or AUTO_DEVICE
        self._device_name = description if self._device != AUTO_DEVICE else ""
        self._apply_device(announce=False)

    def _on_devices(self, data: list) -> None:
        devices = []
        for item in data:
            if not isinstance(item, dict):
                continue
            name, description = item.get("name"), item.get("description")
            if isinstance(name, str) and name and name != AUTO_DEVICE:
                devices.append((name, description if isinstance(description, str) and description else name))
        self._devices = devices
        self._devices_known = True
        for name, description in devices:
            if name == self._device:
                self._device_name = description
        self._apply_device()
        self.devices_changed.emit()

    def _apply_device(self, announce: bool = True) -> None:
        if not self._connected:
            return                        # se aplica al conectar (``_setup_session``)
        wanted = self._device
        if wanted != AUTO_DEVICE and not self._devices_known:
            # Hasta saber qué salidas hay, no se le pide a mpv una que quizá ya
            # no exista: la lista llega enseguida (``observe_property``).
            return
        missing = (wanted != AUTO_DEVICE and self._devices_known
                   and wanted not in {name for name, _ in self._devices})
        target = AUTO_DEVICE if missing else wanted
        if target == self._device_applied:
            return
        if missing and announce and self._device_applied == wanted:
            # Se desconectó mientras sonaba por ella: la música sigue, por la
            # salida predeterminada de Windows, sin relanzar mpv ni cortar la cola.
            log.info("la salida de audio %s ya no está: se usa la de Windows", wanted)
            self.device_lost.emit(self._device_name or wanted)
        self._device_applied = target
        self.command("set_property", "audio-device", target)

    @property
    def audio_filter(self) -> str:
        return self._audio_filter

    def set_normalization(self, mode: Optional[str]) -> None:
        """Iguala el volumen entre canciones (``"loudnorm"`` o ``"dynaudnorm"``)
        o lo deja como viene (``None``). Se aplica en marcha, sin relanzar mpv
        ni cortar la pista; si mpv aún no está conectado, al conectar. El
        ecualizador que haya sigue puesto."""
        wanted = NORMALIZERS.get(mode or "", "")
        if wanted == self._normalization_filter:
            return
        self._normalization_filter = wanted
        # La cadena se rehace igualmente: el ecualizador plano ya no hace falta en ella.
        self._eq_in_graph = self._eq_gains != EQ_PRESETS[EQ_FLAT]
        self._af_stale = False
        self._apply_audio_filter()

    @property
    def equalizer(self) -> str:
        return self._eq_preset

    def set_equalizer(self, preset: str) -> None:
        """Pone un preajuste del ecualizador (``EQ_PRESETS``; "flat" lo quita).

        Si el ecualizador ya está en la cadena, solo cambian sus ganancias en
        marcha (``af-command``): ni corte ni una normalización que vuelve a
        medir desde cero. La primera vez se añade a la cadena."""
        preset = preset if preset in EQ_PRESETS else EQ_FLAT
        gains = EQ_PRESETS[preset]
        self._eq_preset = preset
        if gains == self._eq_gains:
            return
        self._eq_gains = gains
        if not self._connected:
            # Se aplica al conectar (``_setup_session``); ``audio_filter`` ya lo dice.
            self._eq_in_graph = gains != EQ_PRESETS[EQ_FLAT]
            self._audio_filter = self._compose_filter()
            return
        if self._eq_in_graph:
            requests = [self.command("af-command", EQ_LABEL, "volume", f"{eq_preamp(gains):.6f}", "Parsed_volume_0")]
            for i, gain in enumerate(gains, start=1):
                requests.append(self.command("af-command", EQ_LABEL, "gain", str(gain), f"Parsed_equalizer_{i}"))
            self._eq_requests.update(r for r in requests if r > 0)
            self._af_stale = True         # ``af`` se pone al día al empezar la siguiente pista
        elif gains != EQ_PRESETS[EQ_FLAT]:
            self._eq_in_graph = True
            self._apply_audio_filter()

    def _compose_filter(self) -> str:
        parts = []
        if self._eq_in_graph:
            parts.append(equalizer_graph(self._eq_gains))
        if self._normalization_filter:
            parts.append(self._normalization_filter)
        return ",".join(parts)

    def _apply_audio_filter(self) -> None:
        self._audio_filter = self._compose_filter()
        if self._connected:
            self._filter_request = self.command("set_property", "af", self._audio_filter)
