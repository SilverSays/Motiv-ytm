"""Cola de reproducción: une la GUI, el ``ExtractorManager`` y ``mpv``.

* **Debounce**: los saltos rápidos (Siguiente x5) solo lanzan un ``yt-dlp``,
  cuando el usuario se detiene en una pista.
* **Caché de URLs**: toda URL resuelta se recuerda mientras siga vigente
  (caduca a las ~6 h; aquí se usa como mucho 2 h). Volver a una pista ya
  resuelta, repetirla o retroceder no lanza otro ``yt-dlp``: suena en ~0,4 s.
* **Prefetch**: en cuanto la pista actual suena se resuelven las siguientes
  de la cola (``PREFETCH_AHEAD``), de una en una y en paralelo con cualquier
  reproducción. "Siguiente" y el avance automático encuentran la URL lista.
  El tope por hora de ``ExtractorManager`` sigue limitando la tasa de
  peticiones; no pretende imitar a un navegador.
* **URL caducada** (Caso 2): un ``end-file`` con error, o un final antes de
  tiempo, re-resuelve el mismo ``videoId`` y reanuda en la posición actual.
* **Recuperación de mpv**: si el proceso muere y se relanza, se restaura la
  pista, la posición y el estado de pausa (la cola vive en Python).
* **Eventos de cada carga**: cada ``loadfile`` lleva una ficha. Solo cuenta el
  ``start-file`` que llega después de que mpv confirme *esa* carga (y, si mpv
  lo dice, con el mismo ``playlist_entry_id``): con saltos rápidos, el inicio
  tardío de una pista anterior no se toma por el de la nueva.
* **Archivos del disco**: una pista ``local:…`` se carga en mpv directamente
  desde su archivo (``file:///…``), sin yt-dlp ni red; tampoco se resuelve por
  adelantado ni siembra la radio automática.
* **Sin pausas**: con la pista actual sonando y la URL de la siguiente ya a
  mano (precargada, o un archivo del disco), se encadena en mpv con
  ``append``. Al acabar la actual mpv sigue con ella sin hueco y la cola
  avanza sin pasar por "detenida" ni recargar nada. Cualquier cambio (un salto,
  editar la cola, repetir una) descarta lo encadenado; si lo encadenado no
  abre, se resuelve de nuevo y se carga como siempre.
* **Transición entre canciones** (``transition``): "gapless" (sin pausas, lo
  de arriba), "crossfade" (fundido: además, la que acaba baja en sus últimos
  segundos y la siguiente sube en los primeros) u "off" (cada pista se carga
  al acabar la anterior, como antes). El fundido es una atenuación del
  volumen sobre el elegido (``MpvPlayer.set_fade``); con una sola instancia
  de mpv las dos canciones no llegan a sonar a la vez.
"""

from __future__ import annotations

import logging
import math
import random
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Optional

from PySide6.QtCore import QObject, QTimer, Signal

from ytm_native.core import config
from ytm_native.core.db import Database, Track
from ytm_native.core.errors import ErrorKind
from ytm_native.playback.extractor import ExtractorManager, ResolveResult
from ytm_native.services import local_media
from ytm_native.services.sync import radio_tracks
from ytm_native.core.i18n import tr

log = logging.getLogger("ytmn.player")

STOPPED, LOADING, PLAYING, PAUSED = "stopped", "loading", "playing", "paused"
REPEAT_OFF, REPEAT_ALL, REPEAT_ONE = "off", "all", "one"
DEFAULT_VOLUME = 80
# Transición entre canciones y duración del fundido (segundos).
TRANSITION_GAPLESS, TRANSITION_CROSSFADE, TRANSITION_OFF = "gapless", "crossfade", "off"
TRANSITIONS = (TRANSITION_GAPLESS, TRANSITION_CROSSFADE, TRANSITION_OFF)
TRANSITION_KEY = "transition_mode"
CROSSFADE_KEY = "crossfade_seconds"
CROSSFADE_DEFAULT_S, CROSSFADE_MIN_S, CROSSFADE_MAX_S = 4, 2, 8
FADE_TICK_MS = 40


def saved_volume(db: Database) -> int:
    """Volumen con el que arranca la app: el último audible que eligió el
    usuario. Un 0 guardado (versiones anteriores lo guardaban al silenciar)
    no se respeta: la app no debe abrirse muda sin decir por qué."""
    volume = db.get_int("volume", DEFAULT_VOLUME)
    return volume if 0 < volume <= 100 else DEFAULT_VOLUME


@dataclass(eq=False)
class _Job:
    video_id: str
    purpose: str            # "play" | "prefetch"
    gen: int
    cancel: threading.Event = field(default_factory=threading.Event)
    done: bool = False


@dataclass(eq=False)
class _Chain:
    """La pista encadenada en mpv tras la que suena (``loadfile … append``)."""
    video_id: str
    token: int
    res: ResolveResult
    entry_id: Optional[int] = None    # el ``playlist_entry_id`` que le dio mpv
    acked: bool = False               # mpv confirmó que la añadió


class PlaybackController(QObject):
    current_changed = Signal(object)          # Track | None
    state_changed = Signal(str)
    position_changed = Signal(float, float)   # posición, duración
    notice = Signal(str)
    blocked = Signal(float)                   # bloqueo anti-bot hasta este timestamp
    track_flagged = Signal(str)               # cambió la disponibilidad de un videoId
    modes_changed = Signal(bool, str)         # aleatorio, repetir
    queue_changed = Signal()
    halted_after_track = Signal()             # se paró al acabar la pista (temporizador)
    _job_done = Signal(object, object)
    _radio_done = Signal(int, object, object)  # token, pistas, error

    def __init__(
        self,
        db: Database,
        extractor: ExtractorManager,
        mpv,
        parent: Optional[QObject] = None,
        radio_fetcher=radio_tracks,
        local_resolver=None,
    ):
        super().__init__(parent)
        self.db = db
        self.extractor = extractor
        self.mpv = mpv
        self._radio_fetcher = radio_fetcher
        # Ruta en disco de una pista ``local:`` (None si el archivo ya no está).
        self._local_path = local_resolver or (lambda video_id: local_media.resolve_path(db, video_id))
        # Radio automática al terminar la cola (idea de JustAnotherMusicClient).
        self.autoplay = db.get_bool("autoplay_radio", False)
        self._radio_token = 0
        self._radio_pending = False
        # Al acabar la pista que suena, parar en vez de seguir con la cola.
        self._stop_after_current = False
        # Cómo se pasa de una canción a otra. Salvo "off", la siguiente se
        # encadena en mpv para que suene sin hueco.
        mode = db.get_setting(TRANSITION_KEY, TRANSITION_GAPLESS)
        self.transition = mode if mode in TRANSITIONS else TRANSITION_GAPLESS
        self.crossfade_s = self._clamp_crossfade(db.get_int(CROSSFADE_KEY, CROSSFADE_DEFAULT_S))
        self._fade_in = False                     # la actual entró con fundido y aún sube
        self._pos_at = 0.0                        # cuándo llegó ``_last_pos`` (monotónico)
        self._chain: Optional[_Chain] = None
        # mpv puede tener algo tras la pista actual (se encadenó y aún no se
        # cargó otra con ``replace`` ni se paró): al acabar fuera de lo previsto,
        # hay que pararlo para que no suene por su cuenta.
        self._mpv_extra = False
        # La pista actual llegó encadenada y aún no ha abierto.
        self._from_chain = False
        self._token_seq = 0                       # fichas de carga, también las encadenadas
        self.state = STOPPED
        self.shuffle = False
        self.repeat = REPEAT_OFF
        self._queue: list[Track] = []
        self._order: list[int] = []
        # Orden lógico de la cola (el de la lista, con lo añadido donde se
        # pidió). Sin aleatorio coincide con ``_order``; con él, es adonde
        # vuelve todo al quitarlo.
        self._base: list[int] = []
        self._pos = -1
        self._gen = 0
        self._job: Optional[_Job] = None          # resolución de la pista actual
        self._pre_job: Optional[_Job] = None      # resolución por adelantado
        self._current_res: Optional[ResolveResult] = None
        self._cache: OrderedDict[str, ResolveResult] = OrderedDict()
        self._prefetch_tried: dict[str, float] = {}    # videoId -> último intento
        self._clicked_at = 0.0                    # para medir clic -> sonido en el log
        self._entry_id: Optional[int] = None
        self._awaiting_start = False
        # Ficha de la última carga enviada a mpv y lo que respondió a ella.
        self._load_token = 0
        self._load_acked = False
        self._expected_entry: Optional[int] = None
        self._pending_seek = 0.0
        self._resume_paused = False
        self._last_pos = 0.0
        self._duration = 0.0
        self._reresolves = 0
        # Racha de pistas que no llegan a sonar. Solo la corta una pista que
        # suena de verdad unos segundos (``_healthy_s``), no que yt-dlp
        # resuelva: si mpv no puede reproducir nada, resolver bien no basta.
        self._consecutive_failures = 0
        self._healthy_s = 0.0
        self._streak_marked: list[str] = []
        self._soft_cap_warned = False
        self._rng = random.Random()

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(config.DEBOUNCE_MS)
        self._debounce.timeout.connect(self._begin_resolve)

        self._prefetch_timer = QTimer(self)
        self._prefetch_timer.setSingleShot(True)
        self._prefetch_timer.timeout.connect(self._plan_prefetch)

        # Fundido: mientras dura se recalcula a menudo (la posición de mpv
        # llega cada cuarto de segundo, que daría escalones audibles).
        self._fade_timer = QTimer(self)
        self._fade_timer.setInterval(FADE_TICK_MS)
        self._fade_timer.timeout.connect(self._update_fade)

        # Reintento de carga de la MISMA URL (403 pasajero tras resolver).
        self._loaded_ok = False
        self._load_retries = 0
        self._load_retry_gen = -1
        self._load_retry = QTimer(self)
        self._load_retry.setSingleShot(True)
        self._load_retry.timeout.connect(self._retry_load)

        self._job_done.connect(self._on_job_done)
        self._radio_done.connect(self._on_radio_done)
        mpv.media_key.connect(self._on_media_key)
        mpv.ready.connect(self._on_mpv_ready)
        mpv.crashed.connect(self.notice)
        mpv.gave_up.connect(self._on_mpv_gave_up)
        mpv.position_changed.connect(self._on_position)
        mpv.duration_changed.connect(self._on_duration)
        mpv.pause_changed.connect(self._on_pause_changed)
        mpv.file_started.connect(self._on_file_started)
        mpv.load_acked.connect(self._on_load_acked)
        mpv.file_loaded.connect(self._on_file_loaded)
        mpv.file_ended.connect(self._on_file_ended)

    # -- consulta -----------------------------------------------------------
    def current_track(self) -> Optional[Track]:
        if 0 <= self._pos < len(self._order):
            return self._queue[self._order[self._pos]]
        return None

    def upcoming(self, limit: int = 50) -> list[Track]:
        return [self._queue[i] for i in self._order[self._pos + 1 : self._pos + 1 + limit]]

    # -- sesión guardada ----------------------------------------------------
    def session_queue(self) -> tuple[list[Track], list[int]]:
        """La cola tal como se añadió y el orden en que suena."""
        return list(self._queue), list(self._order)

    def session_state(self) -> dict:
        """Lo que cambia mientras suena: por dónde va y los modos."""
        track = self.current_track()
        return {
            "pos": self._pos,
            "video_id": track.video_id if track else "",
            "position": round(self._last_pos, 1),
            "shuffle": self.shuffle,
            "repeat": self.repeat,
        }

    def restore(
        self,
        tracks: list[Track],
        order: Optional[list[int]] = None,
        pos: int = 0,
        position: float = 0.0,
        shuffle: bool = False,
        repeat: str = REPEAT_OFF,
    ) -> bool:
        """Deja la cola como estaba al cerrar, sin sonar: «Reproducir» sigue
        desde ``position``. No hace nada si ya hay una cola."""
        if not tracks or self._queue or self.state != STOPPED:
            return False
        n = len(tracks)
        if order is None or sorted(order) != list(range(n)):
            order, shuffle = list(range(n)), False     # orden ilegible: el de la lista
        self._queue = list(tracks)
        self._order = list(order)
        self._pos = max(0, min(int(pos), n - 1))
        self.shuffle = bool(shuffle)
        # La sesión guarda el orden en que suena, no el lógico: sin aleatorio
        # son el mismo; con él, el lógico es el de la lista.
        self._base = list(range(n)) if self.shuffle else list(self._order)
        self.repeat = repeat if repeat in (REPEAT_OFF, REPEAT_ALL, REPEAT_ONE) else REPEAT_OFF
        track = self.current_track()
        self._duration = float(track.duration_s or 0)
        position = float(position)
        if not math.isfinite(position):
            position = 0.0                  # NaN o infinito (con duración desconocida no lo para nada más)
        if position < 0 or (self._duration > 0 and position >= self._duration):
            position = 0.0
        self._last_pos = position
        self._entry_id = None
        self.modes_changed.emit(self.shuffle, self.repeat)
        self.current_changed.emit(track)
        self.position_changed.emit(self._last_pos, self._duration)
        self.state_changed.emit(self.state)     # sigue detenido, pero ya hay pista: activa los botones
        self.queue_changed.emit()
        return True

    # -- órdenes de la GUI --------------------------------------------------
    def play_tracks(self, tracks: list[Track], start_index: int = 0) -> None:
        if not tracks:
            return
        self._queue = list(tracks)
        self._base = list(range(len(tracks)))
        start_index = max(0, min(start_index, len(tracks) - 1))
        self._build_order(start_index)
        self._reset_failures()
        self._radio_token += 1            # cancela una radio pendiente de otra cola
        self._radio_pending = False
        self._goto(self._pos)
        self.queue_changed.emit()

    def queue_snapshot(self) -> tuple[list[Track], int]:
        """Pistas en orden de reproducción y posición de la actual."""
        return [self._queue[i] for i in self._order], self._pos

    def enqueue(self, track: Track) -> None:
        """Añade una pista al final de la cola."""
        self.enqueue_many([track])

    def play_next(self, track: Track) -> None:
        """Inserta una pista justo después de la actual."""
        self.play_next_many([track])

    def enqueue_many(self, tracks: list[Track]) -> None:
        """Añade las pistas al final de la cola, en su orden."""
        self._insert(tracks, len(self._order), next_up=False)

    def play_next_many(self, tracks: list[Track]) -> None:
        """Inserta las pistas, en su orden, justo después de la actual."""
        self._insert(tracks, self._pos + 1 if self._pos >= 0 else 0, next_up=True)

    def _insert(self, tracks: list[Track], at: int, next_up: bool) -> None:
        tracks = list(tracks)
        if not tracks:
            return
        if self.current_track() is None:
            self.play_tracks(tracks, 0)
            return
        first = len(self._queue)
        self._queue.extend(tracks)
        indexes = list(range(first, len(self._queue)))
        at = max(0, min(at, len(self._order)))
        self._order[at:at] = indexes
        # En el orden lógico, «a continuación» va tras la que suena y «a la
        # cola», al final: al quitar el aleatorio cada una sigue en su sitio.
        if next_up:
            current = self._order[self._pos]
            after = self._base.index(current) + 1
            self._base[after:after] = indexes
        else:
            self._base.extend(indexes)
        self._replan_prefetch()           # puede haber cambiado la "siguiente"
        self.queue_changed.emit()

    def jump_to(self, order_pos: int) -> None:
        """Salta a una posición de la cola (vista "Cola")."""
        if 0 <= order_pos < len(self._order):
            self._reset_failures()
            self._goto(order_pos)

    # -- edición de la cola -------------------------------------------------
    # Las posiciones son las del orden en que suena (las filas de la vista
    # "Cola"). Ninguna de estas operaciones corta ni recarga lo que suena.
    def remove_from_queue(self, order_pos: int) -> bool:
        """Quita una pista de la cola. La que suena no se quita (devuelve
        False): para dejar de oírla basta con pasar a la siguiente."""
        if not (0 <= order_pos < len(self._order)) or order_pos == self._pos:
            return False
        index = self._order.pop(order_pos)
        if order_pos < self._pos:
            self._pos -= 1
        self._base.remove(index)
        del self._queue[index]
        # Los índices a ``_queue`` que había detrás del quitado bajan uno.
        self._order = [i - 1 if i > index else i for i in self._order]
        self._base = [i - 1 if i > index else i for i in self._base]
        self._replan_prefetch()           # puede haber cambiado la "siguiente"
        self.queue_changed.emit()
        return True

    def move_in_queue(self, from_order_pos: int, to_order_pos: int) -> bool:
        """Lleva la pista de ``from_order_pos`` a ``to_order_pos`` (su
        posición final). La que suena puede moverse y sigue sonando."""
        n = len(self._order)
        if not (0 <= from_order_pos < n and 0 <= to_order_pos < n):
            return False
        if from_order_pos == to_order_pos:
            return True
        pos = self._pos
        if pos == from_order_pos:
            pos = to_order_pos
        elif from_order_pos < pos <= to_order_pos:
            pos -= 1
        elif to_order_pos <= pos < from_order_pos:
            pos += 1
        index = self._order.pop(from_order_pos)
        self._order.insert(to_order_pos, index)
        self._pos = pos
        if not self.shuffle:
            # Sin aleatorio el orden lógico es el que suena. Con él, el lógico
            # no se toca: al quitarlo cada pista vuelve al sitio de la lista.
            self._base = list(self._order)
        self._replan_prefetch()
        self.queue_changed.emit()
        return True

    def clear_queue(self) -> None:
        """Vacía la cola salvo la pista que suena, que sigue sonando."""
        track = self.current_track()
        if track is None:
            self._queue, self._order, self._base, self._pos = [], [], [], -1
        else:
            self._queue, self._order, self._base, self._pos = [track], [0], [0], 0
        # Una radio pedida para la cola anterior ya no debe rellenar esta.
        self._radio_token += 1
        self._radio_pending = False
        self._replan_prefetch()
        self.queue_changed.emit()

    @property
    def stop_after_current(self) -> bool:
        return self._stop_after_current

    @stop_after_current.setter
    def stop_after_current(self, enabled: bool) -> None:
        # El temporizador lo cambia en marcha: lo encadenado ya no debe sonar.
        self._stop_after_current = bool(enabled)
        self._sync_chain()

    def set_transition(self, mode: str) -> None:
        """"gapless", "crossfade" u "off". Se aplica ya: lo encadenado se
        quita o se pone, y un fundido a medias se deshace."""
        if mode not in TRANSITIONS:
            return
        self.transition = mode
        self.db.set_setting(TRANSITION_KEY, mode)
        if mode != TRANSITION_CROSSFADE:
            self._fade_in = False
        self._sync_chain()
        self._update_fade()

    def set_crossfade_seconds(self, seconds: int) -> None:
        self.crossfade_s = self._clamp_crossfade(seconds)
        self.db.set_setting(CROSSFADE_KEY, self.crossfade_s)
        self._update_fade()

    @staticmethod
    def _clamp_crossfade(seconds) -> int:
        try:
            seconds = int(seconds)
        except (TypeError, ValueError):
            return CROSSFADE_DEFAULT_S
        return max(CROSSFADE_MIN_S, min(CROSSFADE_MAX_S, seconds))

    def set_autoplay(self, enabled: bool) -> None:
        self.autoplay = bool(enabled)
        self.db.set_setting("autoplay_radio", "1" if self.autoplay else "0")

    def start_radio(self, track: Track) -> None:
        """Reproduce la pista y completa la cola con pistas relacionadas."""
        self.play_tracks([track], 0)
        self._request_radio(track, then_advance=False)

    def _request_radio(self, seed: Track, then_advance: bool) -> None:
        if local_media.is_local(seed.video_id):
            return                       # YouTube no conoce los archivos del disco
        self._radio_token += 1
        token = self._radio_token
        self._radio_pending = then_advance

        def work() -> None:
            try:
                tracks, error = self._radio_fetcher(seed.video_id), None
            except Exception as exc:  # noqa: BLE001
                tracks, error = [], exc
            self._radio_done.emit(token, tracks, error)

        threading.Thread(target=work, name="radio", daemon=True).start()

    def _on_radio_done(self, token: int, tracks: list, error) -> None:
        if token != self._radio_token:
            return  # el usuario ya puso otra cosa
        advance, self._radio_pending = self._radio_pending, False
        if error is not None:
            self.notice.emit(tr("No se pudo cargar la radio: {error}", error=error))
            return
        known = {t.video_id for t in self._queue}
        fresh = [t for t in tracks if t.video_id not in known]
        if not fresh:
            if advance:
                self.notice.emit(tr("La radio no devolvió pistas nuevas."))
            return
        for track in fresh:
            self.db.upsert_track(track)
            self._queue.append(track)
            self._order.append(len(self._queue) - 1)
            self._base.append(len(self._queue) - 1)
        self._replan_prefetch()
        self.queue_changed.emit()
        if advance and self.state == STOPPED:
            pos = self._next_pos(auto=True)
            if pos is not None:
                self._goto(pos)

    def _on_media_key(self, key: str) -> None:
        if key == "next":
            self.next_track()
        elif key == "prev":
            self.previous_track()

    def toggle_pause(self) -> None:
        if self.state in (PLAYING, PAUSED):
            self.mpv.set_pause(self.state == PLAYING)
        elif self.state == STOPPED and self.current_track() is not None:
            # Reanudar tras una pausa de la cola (sin conexión, bloqueo...).
            self._reset_failures()
            self._goto(self._pos, start_at=self._last_pos)

    def next_track(self) -> None:
        pos = self._next_pos(auto=False)
        if pos is not None:
            self._reset_failures()        # lo pide el usuario: la racha empieza de cero
            self._goto(pos, debounce=True)

    def previous_track(self) -> None:
        if self.state in (PLAYING, PAUSED) and self._last_pos > 3:
            self.seek(0)
            return
        pos = self._prev_pos()
        if pos is not None:
            self._reset_failures()
            self._goto(pos, debounce=True)

    def seek(self, seconds: float) -> None:
        if self.state in (PLAYING, PAUSED):
            self._last_pos = max(0.0, seconds)
            self.mpv.seek(self._last_pos)
        elif self.state == STOPPED and self.current_track() is not None:
            # Detenido con una pista a medias (la cola recuperada al abrir, o
            # en pausa por un fallo): se mueve el punto desde el que seguirá.
            top = self._duration - 1 if self._duration > 1 else seconds
            self._last_pos = max(0.0, min(seconds, top))
            self.position_changed.emit(self._last_pos, self._duration)

    def set_volume(self, volume: int) -> None:
        self.mpv.set_volume(volume)

    def set_shuffle(self, enabled: bool) -> None:
        if enabled == self.shuffle:
            return
        self.shuffle = enabled
        if self._queue:
            current = self._order[self._pos] if 0 <= self._pos < len(self._order) else 0
            self._build_order(current)
        self._replan_prefetch()       # la "siguiente" cambió
        self.modes_changed.emit(self.shuffle, self.repeat)
        self.queue_changed.emit()

    def cycle_repeat(self) -> None:
        self.repeat = {REPEAT_OFF: REPEAT_ALL, REPEAT_ALL: REPEAT_ONE, REPEAT_ONE: REPEAT_OFF}[self.repeat]
        self._replan_prefetch()
        self.modes_changed.emit(self.shuffle, self.repeat)

    def stop(self) -> None:
        self._gen += 1
        self._debounce.stop()
        self._load_retry.stop()
        self._prefetch_timer.stop()
        self._cancel_job()
        if self._pre_job is not None:
            self._pre_job.cancel.set()
            self._pre_job = None
        self._entry_id = None
        self._fade_in = False
        self._forget_chain()              # y el volumen vuelve a ser el elegido
        self.mpv.stop()                   # también vacía la lista de mpv
        self._set_state(STOPPED)

    # -- orden de la cola ---------------------------------------------------
    def _build_order(self, current_index: int) -> None:
        n = len(self._queue)
        if sorted(self._base) != list(range(n)):
            self._base = list(range(n))       # nunca debería pasar: se rehace entero
        if self.shuffle:
            rest = [i for i in self._base if i != current_index]
            self._rng.shuffle(rest)
            self._order = [current_index] + rest
            self._pos = 0
        else:
            # De vuelta al orden lógico, con la que suena en su sitio.
            self._order = list(self._base)
            self._pos = self._order.index(current_index) if 0 <= current_index < n else 0

    def _recently_unavailable(self, track: Track) -> bool:
        row = self.db.get_track(track.video_id)
        since = row.unavailable_since if row else None
        return since is not None and time.time() - since < config.UNAVAILABLE_RECHECK_S

    def _next_pos(self, auto: bool) -> Optional[int]:
        n = len(self._order)
        if n == 0 or self._pos < 0:
            return None
        if auto and self.repeat == REPEAT_ONE:
            return self._pos
        return self._step(self._pos, skip_unavailable=auto)

    def _step(self, pos: int, skip_unavailable: bool) -> Optional[int]:
        """Posición siguiente a ``pos`` en el orden de la cola."""
        n = len(self._order)
        for _ in range(n):
            pos += 1
            if pos >= n:
                if self.repeat != REPEAT_ALL:
                    return None
                pos = 0
            # En avance automático se saltan las marcadas hace poco; un clic
            # manual siempre reintenta.
            if not skip_unavailable or not self._recently_unavailable(self._queue[self._order[pos]]):
                return pos
        return None

    def _lookahead(self, count: int) -> list[Track]:
        """Las próximas ``count`` pistas que sonarían al avanzar."""
        if self.repeat == REPEAT_ONE or self._pos < 0:
            return []   # repetir una reutiliza la URL actual
        tracks: list[Track] = []
        pos: Optional[int] = self._pos
        for _ in range(count):
            pos = self._step(pos, skip_unavailable=True)
            if pos is None or pos == self._pos:
                break
            tracks.append(self._queue[self._order[pos]])
        return tracks

    def _prev_pos(self) -> Optional[int]:
        n = len(self._order)
        if n == 0 or self._pos < 0:
            return None
        if self._pos > 0:
            return self._pos - 1
        return n - 1 if self.repeat == REPEAT_ALL else None

    # -- caché de URLs ------------------------------------------------------
    def _cached(self, video_id: str) -> Optional[ResolveResult]:
        res = self._cache.get(video_id)
        if res is None:
            return None
        if not res.is_fresh():
            del self._cache[video_id]
            return None
        return res

    def _remember(self, res: ResolveResult) -> None:
        self._cache[res.video_id] = res
        self._cache.move_to_end(res.video_id)
        while len(self._cache) > config.URL_CACHE_SIZE:
            self._cache.popitem(last=False)

    # -- transición a una pista --------------------------------------------
    def _set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            self.state_changed.emit(state)
            self._update_fade()           # en pausa el fundido se queda donde iba

    def _goto(self, pos: int, start_at: float = 0.0, debounce: bool = False) -> None:
        self._pos = pos
        track = self.current_track()
        if track is None:
            return
        self._gen += 1
        self._load_retry.stop()
        self._prefetch_timer.stop()
        self._reresolves = 0
        self._healthy_s = 0.0
        self._entry_id = None
        self._awaiting_start = False
        self._pending_seek = start_at
        self._resume_paused = False
        self._last_pos = start_at
        self._duration = float(track.duration_s or 0)
        self._current_res = None
        self._clicked_at = time.monotonic()
        # Un salto (siguiente, anterior, otra fila…) descarta lo encadenado:
        # ``stop`` vacía la lista de mpv y lo que se pida irá con ``replace``.
        self._fade_in = False             # antes de olvidar lo encadenado: así vuelve el volumen entero
        self._forget_chain()
        self.mpv.stop()
        self._set_state(LOADING)
        self.current_changed.emit(track)
        self.position_changed.emit(self._last_pos, self._duration)
        if debounce:
            cached = self._cached(track.video_id) is not None
            self._debounce.start(config.DEBOUNCE_CACHED_MS if cached else config.DEBOUNCE_MS)
        else:
            self._debounce.stop()
            self._begin_resolve()

    def _begin_resolve(self, force_fresh: bool = False) -> None:
        track = self.current_track()
        if track is None:
            return
        if local_media.is_local(track.video_id):
            self._play_local(track)
            return
        if force_fresh:
            self._cache.pop(track.video_id, None)   # esa URL ya falló
        else:
            cached = self._cached(track.video_id)
            if cached is not None:
                self._play_result(cached)
                return
        self._start_play_job(track.video_id)

    def _play_local(self, track: Track) -> None:
        """Archivo del disco: directo a mpv, sin ExtractorManager ni red."""
        res = self._local_result(track)
        if res is None:
            self._handle_failure(ResolveResult(
                video_id=track.video_id, kind=ErrorKind.UNAVAILABLE,
                message=tr("El archivo ya no está en el disco."),
            ))
            return
        self._play_result(res)

    def _local_result(self, track: Track) -> Optional[ResolveResult]:
        """La "URL" de un archivo del disco (``None`` si ya no está): no caduca."""
        path = self._local_path(track.video_id)
        if not path:
            return None
        now = time.time()
        return ResolveResult(
            video_id=track.video_id, url=local_media.file_uri(path), format_desc=tr("archivo local"),
            duration_s=float(track.duration_s) if track.duration_s else None,
            resolved_at=now, expire_ts=now + 365 * 86400, ready=True,
        )

    # -- trabajos de resolución --------------------------------------------
    @staticmethod
    def _running(job: Optional[_Job]) -> bool:
        return job is not None and not job.done and not job.cancel.is_set()

    def _start_play_job(self, video_id: str) -> None:
        pre = self._pre_job
        if self._running(pre) and pre.video_id == video_id:
            # Ya se está resolviendo por adelantado: se adopta como reproducción.
            self._pre_job = None
            self._cancel_job()
            pre.purpose = "play"
            pre.gen = self._gen
            self._job = pre
            return
        job = self._job
        if self._running(job):
            if job.video_id == video_id:
                job.gen = self._gen
                return
            job.cancel.set()   # el usuario cambió de pista; un prefetch en curso sigue
        self._job = self._launch(video_id, "play")

    def _launch(self, video_id: str, purpose: str) -> _Job:
        job = _Job(video_id=video_id, purpose=purpose, gen=self._gen)
        threading.Thread(
            target=self._run_job, args=(job, purpose), name=f"resolve-{video_id}", daemon=True
        ).start()
        return job

    def _run_job(self, job: _Job, purpose: str) -> None:
        try:
            result = self.extractor.resolve(job.video_id, job.cancel, purpose)
            # Solo lo que va a sonar ya espera a que la URL responda; un
            # prefetch tendrá edad de sobra cuando se use.
            waiter = getattr(self.extractor, "await_playable", None)
            if waiter is not None and result.ok and job.purpose == "play" and not job.cancel.is_set():
                waiter(result, job.cancel)
        except Exception as exc:  # noqa: BLE001
            log.exception("resolve lanzó una excepción")
            result = ResolveResult(video_id=job.video_id, kind=ErrorKind.UNKNOWN, message=str(exc))
        finally:
            self.db.close_thread_connection()
        self._job_done.emit(job, result)

    def _cancel_job(self) -> None:
        if self._job is not None:
            self._job.cancel.set()
            self._job = None

    def _on_job_done(self, job: _Job, res: ResolveResult) -> None:
        job.done = True
        if job is self._job:
            self._job = None
        if job is self._pre_job:
            self._pre_job = None
        if res.ok:
            self._remember(res)   # aunque ya no interese ahora: sirve si se vuelve a ella
        if job.purpose == "play" and not job.cancel.is_set() and job.gen == self._gen:
            if res.kind is ErrorKind.LOCAL_RATE_LIMIT:
                # Era un prefetch adoptado y lo frenó el límite local: se
                # relanza como reproducción (el límite no aplica al usuario).
                self._start_play_job(job.video_id)
            elif res.ok:
                self._play_result(res)
            else:
                self._handle_failure(res)
            return
        # prefetch: los fallos son silenciosos; el intento real los tratará.
        self._plan_prefetch()

    # -- resolución por adelantado -----------------------------------------
    def _replan_prefetch(self) -> None:
        """La cola cambió: lo ya resuelto se conserva; se revisa qué falta."""
        self._plan_prefetch()

    def _plan_prefetch(self) -> None:
        # Cada vez que algo pudo cambiar la "siguiente" (la URL llegó, la cola
        # se editó, cambió un modo) se revisa también lo encadenado en mpv.
        self._sync_chain()
        if self.state not in (PLAYING, PAUSED) or self._running(self._pre_job):
            return   # de una en una, y solo cuando la pista actual ya suena
        now = time.time()
        for track in self._lookahead(config.PREFETCH_AHEAD):
            vid = track.video_id
            if local_media.is_local(vid) or self._cached(vid) is not None:
                continue                 # un archivo del disco no se resuelve
            # Un intento por pista cada cierto tiempo, salga bien o mal: un
            # fallo (o una URL que caduca enseguida) no debe repetirse en bucle.
            if now - self._prefetch_tried.get(vid, 0.0) < config.PREFETCH_RETRY_AFTER_S:
                continue
            if self._running(self._job) and self._job.video_id == vid:
                continue
            self._prefetch_tried[vid] = now
            self._pre_job = self._launch(vid, "prefetch")
            return

    # -- sin pausas: la siguiente, encadenada en mpv ----------------------------
    def _new_token(self) -> int:
        self._token_seq += 1
        return self._token_seq

    def _chain_target(self) -> Optional[tuple[Track, ResolveResult]]:
        """Lo que debería estar encadenado tras la que suena, o ``None``.

        Solo con la actual ya sonando, y solo si la URL de la siguiente está a
        mano (no se resuelve nada para esto). Con «repetir una» se encadena la
        misma pista: vuelve a empezar sin hueco y sin otra resolución."""
        if (self.transition == TRANSITION_OFF or self._stop_after_current or not self._loaded_ok
                or self.state not in (PLAYING, PAUSED) or getattr(self.mpv, "append", None) is None):
            return None
        pos = self._next_pos(auto=True)          # la que vendría al acabar (repetir incluido)
        if pos is None:
            return None
        track = self._queue[self._order[pos]]
        if pos == self._pos:
            res = self._current_res
        elif local_media.is_local(track.video_id):
            res = self._local_result(track)      # un archivo del disco no caduca
        else:
            res = self._cached(track.video_id)
        if res is None or not res.ok or not res.is_fresh():
            return None
        return track, res

    def _sync_chain(self) -> None:
        """Deja encadenado en mpv lo que toca ahora: nada, lo mismo u otra."""
        target = self._chain_target()
        chain = self._chain
        if chain is not None:
            if target is not None and chain.video_id == target[0].video_id and chain.res.url == target[1].url:
                return
            # Ya no es la siguiente (cola editada, otro modo, «parar tras esta»).
            self._chain = None
            self.mpv.playlist_clear()             # la que suena se queda
        if target is None:
            return
        track, res = target
        self._chain = _Chain(video_id=track.video_id, token=self._new_token(), res=res)
        self._mpv_extra = True
        self.mpv.append(res.url, res.user_agent, self._chain.token)
        log.debug("encadenada %s tras la actual", track.video_id)

    def _forget_chain(self) -> None:
        """Lo encadenado ya no está en mpv (``stop``, ``replace``, relanzado)."""
        self._chain = None
        self._mpv_extra = False
        self._update_fade()               # sin siguiente, nada que fundir al final

    # -- fundido entre canciones ------------------------------------------------
    def _fade_length(self) -> float:
        """Segundos del fundido; en una pista corta, como mucho un tercio de ella."""
        seconds = float(self.crossfade_s)
        return min(seconds, self._duration / 3) if self._duration > 0 else seconds

    def _position_now(self) -> float:
        """La posición estimada entre dos avisos de mpv (llegan cada 0,25 s)."""
        if self.state != PLAYING or self._entry_id is None or not self._pos_at:
            return self._last_pos
        return self._last_pos + min(0.5, time.monotonic() - self._pos_at)

    def fade_level(self) -> float:
        """1 = volumen elegido; menos, mientras una pista entra o sale con fundido."""
        if self.transition != TRANSITION_CROSSFADE or self.state == STOPPED:
            return 1.0
        if self._entry_id is None:
            # Entre el final de una y el principio de la siguiente: en silencio
            # si la que viene entra con fundido.
            return 0.0 if self._fade_in else 1.0
        length = self._fade_length()
        if length <= 0:
            return 1.0
        pos = self._position_now()
        level = 1.0
        if self._fade_in:
            if pos >= length:
                self._fade_in = False
            else:
                level = min(level, pos / length)
        chain = self._chain
        if chain is not None and chain.acked and self._duration > 0:
            # Solo baja si de verdad viene otra: al final de la cola acaba entera.
            level = min(level, (self._duration - pos) / length)
        return max(0.0, min(1.0, level))

    def _update_fade(self) -> None:
        if not hasattr(self, "_fade_timer"):
            return                        # aún en ``__init__``
        level = self.fade_level()
        set_fade = getattr(self.mpv, "set_fade", None)
        if set_fade is not None:
            set_fade(level)
        near_end = (self._chain is not None and self._duration > 0
                    and self._duration - self._last_pos <= self._fade_length() + 1.0)
        busy = (self.transition == TRANSITION_CROSSFADE and self.state == PLAYING
                and (self._fade_in or near_end))
        if busy and not self._fade_timer.isActive():
            self._fade_timer.start()
        elif not busy and self._fade_timer.isActive():
            self._fade_timer.stop()

    def _release_mpv_extra(self) -> None:
        """Para mpv si pudo quedarse con algo encadenado que ya no toca: al
        acabar una pista empieza solo con lo siguiente de su lista."""
        self._chain = None
        if self._mpv_extra:
            self._mpv_extra = False
            self.mpv.stop()

    def _adopt_chain(self) -> bool:
        """La actual acabó y mpv ya empezó la encadenada: la cola avanza con
        ella sin pararse ni recargar. ``False`` si no hay nada que adoptar (o
        ya no es la que toca) y hay que seguir como siempre."""
        chain = self._chain
        if chain is None or not chain.acked or self._stop_after_current:
            return False
        pos = self._next_pos(auto=True)
        if pos is None:
            return False
        track = self._queue[self._order[pos]]
        if track.video_id != chain.video_id or not chain.res.is_fresh():
            return False
        self._chain = None
        self._mpv_extra = False                   # lo encadenado es ahora lo que suena
        self._pos = pos
        self._gen += 1
        self._debounce.stop()
        self._load_retry.stop()
        self._prefetch_timer.stop()
        self._reresolves = 0
        self._load_retries = 0
        self._healthy_s = 0.0
        # Sus eventos llegan con la ficha y la entrada que mpv dio al encadenarla.
        self._entry_id = None
        self._awaiting_start = True
        self._load_token = chain.token
        self._load_acked = True
        self._expected_entry = chain.entry_id
        self._loaded_ok = False
        self._from_chain = True
        self._pending_seek = 0.0
        self._resume_paused = False               # acabó sonando: la siguiente sigue sonando
        self._last_pos = 0.0
        self._duration = float(track.duration_s or chain.res.duration_s or 0)
        self._current_res = chain.res
        self._clicked_at = 0.0
        # Con fundido, la nueva empieza en silencio y sube en sus primeros segundos.
        self._fade_in = self.transition == TRANSITION_CROSSFADE
        self._update_fade()
        self.mpv.set_media_title(f"{track.title} — {track.artists}" if track.artists else track.title)
        log.info("%s sigue sin pausa a la anterior", track.video_id)
        self.current_changed.emit(track)
        self.position_changed.emit(0.0, self._duration)
        return True

    def _play_result(self, res: ResolveResult) -> None:
        self._current_res = res
        self._remember(res)
        row = self.db.get_track(res.video_id)
        if row is not None and row.unavailable_since is not None:
            self.db.clear_unavailable(res.video_id)
            self.track_flagged.emit(res.video_id)
        if res.duration_s and self._duration <= 0:
            self._duration = float(res.duration_s)
        self._load_retries = 0
        # Las URL del modo por defecto dan 403 durante unos segundos. Si la URL
        # ya está lista (cliente rápido, o la sonda recibió 206) o tiene edad
        # de sobra (caché, prefetch), se carga ya. Si no se pudo sondear, se
        # espera a que "madure" antes de gastar intentos rechazados.
        age = time.time() - res.resolved_at
        if res.ready or age >= config.URL_SETTLED_AGE_S:
            wait_ms = 0
        else:
            wait_ms = int(max(0.0, config.FIRST_LOAD_MIN_URL_AGE_S - age) * 1000)
        if wait_ms > 0:
            self._load_retry_gen = self._gen
            self._load_retry.start(wait_ms)
        else:
            self._send_load(res)
        self._warn_soft_cap()

    def _send_load(self, res: ResolveResult) -> None:
        self._load_retry.stop()
        self._loaded_ok = False
        self._from_chain = False
        self._entry_id = None
        self._awaiting_start = True
        self._fade_in = False             # una carga nueva suena entera desde el principio
        self._forget_chain()              # ``replace`` vacía la lista de mpv (y deja el volumen entero)
        self._load_token = self._new_token()
        self._load_acked = False
        self._expected_entry = None
        track = self.current_track()
        if track is not None:
            self.mpv.set_media_title(f"{track.title} — {track.artists}" if track.artists else track.title)
        self.mpv.load(res.url, res.user_agent, self._load_token)
        self.mpv.set_pause(self._resume_paused)

    def _retry_load(self) -> None:
        res = self._current_res
        if self._load_retry_gen != self._gen or res is None or self.state != LOADING:
            return  # el usuario ya cambió de pista
        self._send_load(res)

    def _warn_soft_cap(self) -> None:
        if self._soft_cap_warned:
            return
        if self.db.resolves_since(time.time() - 86400) > config.DAILY_RESOLVE_SOFT_CAP:
            self._soft_cap_warned = True
            self.notice.emit(
                tr("Más de {DAILY_RESOLVE_SOFT_CAP} resoluciones en 24 h: considera bajar el ritmo para no "
                   "exponer la cuenta.", DAILY_RESOLVE_SOFT_CAP=config.DAILY_RESOLVE_SOFT_CAP)
            )

    # -- fallos de resolución ----------------------------------------------
    def _handle_failure(self, res: ResolveResult) -> None:
        kind = res.kind
        track = self.current_track()
        title = track.title if track else res.video_id
        if kind in (ErrorKind.CANCELLED, ErrorKind.PREEMPTED):
            return
        if kind is ErrorKind.BOT_BLOCK:
            self._set_state(STOPPED)
            self.blocked.emit(res.blocked_until)
            self.notice.emit(res.message)
            return
        if kind is ErrorKind.NETWORK:
            self._set_state(STOPPED)
            self.notice.emit(tr("Sin conexión. La cola quedó en pausa."))
            return
        if kind in (ErrorKind.RUNTIME_MISSING, ErrorKind.BINARY_MISSING):
            self._set_state(STOPPED)
            self.notice.emit(res.message)
            return

        if self._count_failure():
            return

        if kind in (ErrorKind.UNAVAILABLE, ErrorKind.NEEDS_LOGIN):
            row = self.db.get_track(res.video_id)
            if row is not None and row.unavailable_since is not None:
                self.db.refresh_unavailable(res.video_id, res.message)
            else:
                self.db.mark_unavailable(res.video_id, res.message)
                self._streak_marked.append(res.video_id)
            self.track_flagged.emit(res.video_id)
            self.notice.emit(tr("No disponible: {title}", title=title))
        else:
            self.notice.emit(tr("No se pudo reproducir «{title}»: {message}", title=title, message=res.message))
        self._advance_auto()

    def _reset_failures(self) -> None:
        self._consecutive_failures = 0
        self._streak_marked = []

    def _count_failure(self) -> bool:
        """Suma un fallo a la racha. Si llega al límite, para la cola (sin
        seguir pidiendo URL a YouTube) y devuelve True."""
        self._consecutive_failures += 1
        if self._consecutive_failures < config.MAX_CONSECUTIVE_FAILURES:
            return False
        log.warning("%d pistas seguidas sin sonar: la cola se detiene", self._consecutive_failures)
        # Varias seguidas no son pistas borradas: se deshacen las marcas de
        # esta racha y se pausa la cola en lugar de seguir "quemando" pistas.
        for vid in self._streak_marked:
            self.db.clear_unavailable(vid)
            self.track_flagged.emit(vid)
        self._streak_marked = []
        self._gen += 1                    # descarta reintentos y resoluciones en vuelo
        self._debounce.stop()
        self._load_retry.stop()
        self._prefetch_timer.stop()
        self._cancel_job()
        self._entry_id = None
        self._release_mpv_extra()         # nada encadenado debe sonar con la cola en pausa
        self._set_state(STOPPED)
        self.notice.emit(
            tr("Varias pistas seguidas fallaron: puede ser un bloqueo temporal. La cola quedó en pausa.")
        )
        return True

    def _advance_auto(self) -> None:
        if self.stop_after_current:
            # Como al final de la cola: "Reproducir" vuelve a empezar esta pista.
            self.stop_after_current = False
            self._entry_id = None
            self._last_pos = 0.0
            self._set_state(STOPPED)
            self.halted_after_track.emit()
            return
        pos = self._next_pos(auto=True)
        if pos is None:
            # Fin de la cola: "Reproducir" vuelve a empezar la última pista.
            self._entry_id = None
            self._last_pos = 0.0
            self._set_state(STOPPED)
            seed = self.current_track()
            if self.autoplay and seed is not None and not self._radio_pending:
                self._request_radio(seed, then_advance=True)
            return
        if pos == self._pos and self._current_res is not None and self._current_res.is_fresh():
            # Repetir una: se reutiliza la URL vigente, sin otra resolución.
            res = self._current_res
            self._gen += 1
            self._last_pos = 0.0
            self._pending_seek = 0.0
            self._reresolves = 0
            self._set_state(LOADING)
            self._play_result(res)
            return
        self._goto(pos)

    # -- eventos de mpv -----------------------------------------------------
    def _on_load_acked(self, token: int, entry_id: int, accepted: bool) -> None:
        chain = self._chain
        if chain is not None and token == chain.token:
            if accepted:
                chain.acked = True
                chain.entry_id = entry_id or None
            else:
                log.info("mpv no aceptó encadenar %s: sonará como siempre", chain.video_id)
                self._chain = None
            return
        if token != self._load_token or not self._awaiting_start:
            return  # respuesta a una carga que ya no interesa
        if not accepted:
            self._awaiting_start = False
            self._recover_stream(tr("mpv rechazó la orden de carga"))
            return
        self._load_acked = True
        self._expected_entry = entry_id or None

    def _on_file_started(self, entry_id: int) -> None:
        # mpv responde a ``loadfile`` antes de empezar ese archivo: un inicio
        # que llega antes de la respuesta es de una carga anterior.
        if not self._awaiting_start or not self._load_acked:
            return
        if self._expected_entry is not None and entry_id != self._expected_entry:
            return
        self._awaiting_start = False
        self._entry_id = entry_id

    def _on_file_loaded(self) -> None:
        if self._entry_id is None:
            return
        self._loaded_ok = True
        self._from_chain = False
        if self._pending_seek > 1.0:
            self.mpv.seek(self._pending_seek)
        self._pending_seek = 0.0
        if self._clicked_at:
            res = self._current_res
            log.info(
                "%s suena %.2f s después del clic", res.video_id if res else "?",
                time.monotonic() - self._clicked_at,
            )
            self._clicked_at = 0.0
        self._set_state(PAUSED if self._resume_paused else PLAYING)
        # Con la pista ya sonando se resuelven las siguientes (tras una pausa
        # corta: si el usuario va saltando no se gasta una resolución por salto).
        self._prefetch_timer.start(config.PREFETCH_START_DELAY_MS)

    def _on_file_ended(self, entry_id: int, reason: str, file_error: str) -> None:
        if self._entry_id is None or entry_id != self._entry_id:
            return  # evento de una pista anterior
        self._entry_id = None
        duration = self.mpv.duration or self._duration
        last = max(self._last_pos, getattr(self.mpv, "last_time_pos", 0.0))
        premature = (
            reason == "eof" and duration > 0 and last < duration - config.PREMATURE_EOF_MARGIN_S
        )
        res = self._current_res
        if reason == "error" and self._from_chain and not self._loaded_ok:
            # Lo encadenado no abrió (URL caducada, red): en vez de reintentar
            # esa misma URL, se resuelve de nuevo y se carga con ``replace``.
            log.info("la pista encadenada %s no abrió (%s): se resuelve de nuevo",
                     res.video_id if res else "?", file_error)
            self._release_mpv_extra()
            self._recover_stream(file_error or "error")
            return
        if reason != "error" and not premature:
            if self._loaded_ok:
                # Sonó entera aunque durase menos que HEALTHY_PLAY_S (una intro,
                # un interludio): la racha de fallos ya no es seguida.
                self._reset_failures()
            if self._adopt_chain():
                return                    # mpv ya está con la siguiente, sin hueco
        # Fuera de lo previsto, lo que mpv tenga encadenado no debe sonar solo
        # mientras se recupera esta pista o se decide qué viene.
        self._release_mpv_extra()
        delays = config.LOAD_RETRY_DELAYS_MS
        if (
            reason == "error"
            and not self._loaded_ok              # falló al abrir, no a mitad de pista
            and res is not None
            and res.is_fresh()
            and time.time() - res.resolved_at < config.LOAD_RETRY_MAX_URL_AGE_S
            and self._load_retries < len(delays)
        ):
            # Una URL recién resuelta puede dar 403 durante un par de segundos:
            # se reintenta la misma URL, sin otra resolución de yt-dlp.
            delay = delays[self._load_retries]
            self._load_retries += 1
            self._load_retry_gen = self._gen
            log.info("reintentando carga de %s en %d ms (%s)", res.video_id, delay, file_error)
            self._load_retry.start(delay)
            return
        if reason == "error" or premature:
            self._recover_stream(file_error or (tr("fin prematuro") if premature else "error"))
        else:
            self._advance_auto()

    def _recover_stream(self, detail: str) -> None:
        """Caso 2: URL caducada o flujo roto. URL fresca y reanudar donde iba."""
        track = self.current_track()
        if track is None:
            return
        if self._reresolves >= config.MAX_RERESOLVE_PER_TRACK:
            self.notice.emit(tr("No se pudo reproducir «{title}» ({detail}).", title=track.title, detail=detail))
            if self._count_failure():
                return
            self._advance_auto()
            return
        self._reresolves += 1
        log.info("re-resolviendo %s en %.0fs (%s)", track.video_id, self._last_pos, detail)
        self._gen += 1
        self._pending_seek = self._last_pos
        # Una pista en pausa se recupera en pausa; si ya se estaba recuperando
        # (LOADING), se mantiene lo que se decidió entonces.
        if self.state == PAUSED:
            self._resume_paused = True
        elif self.state == PLAYING:
            self._resume_paused = False
        self._current_res = None
        self._set_state(LOADING)
        self._begin_resolve(force_fresh=True)

    def _on_position(self, pos: float) -> None:
        if self._entry_id is None:
            return
        step = pos - self._last_pos
        self._last_pos = pos
        self._pos_at = time.monotonic()
        if 0.0 < step < config.HEALTHY_PLAY_MAX_STEP_S:   # avance normal, no un salto
            self._healthy_s += step
            if self._consecutive_failures and self._healthy_s >= config.HEALTHY_PLAY_S:
                log.info("la pista suena bien: se olvida la racha de %d fallos",
                         self._consecutive_failures)
                self._reset_failures()
        self.position_changed.emit(pos, self._duration)
        if self.transition == TRANSITION_CROSSFADE:
            self._update_fade()           # p. ej. empieza a bajar al acercarse el final

    def _on_duration(self, duration: float) -> None:
        if self._entry_id is not None and duration > 0:
            self._duration = duration
            self.position_changed.emit(self._last_pos, self._duration)

    def _on_pause_changed(self, paused: bool) -> None:
        if self.state in (PLAYING, PAUSED):
            self._set_state(PAUSED if paused else PLAYING)

    # -- recuperación de mpv ------------------------------------------------
    def _on_mpv_ready(self) -> None:
        self._forget_chain()              # un mpv recién lanzado no tiene nada encadenado
        track = self.current_track()
        if track is None or self.state == STOPPED:
            return
        # mpv se relanzó: restaurar pista, posición y pausa.
        self._resume_paused = self.state == PAUSED
        self._pending_seek = self._last_pos
        self._gen += 1
        self._entry_id = None
        res = self._current_res
        self._set_state(LOADING)
        if res is not None and res.is_fresh():
            self._play_result(res)
        else:
            self._begin_resolve(force_fresh=True)

    def _on_mpv_gave_up(self, message: str) -> None:
        self._entry_id = None
        self._forget_chain()
        self._set_state(STOPPED)
        self.notice.emit(message)
