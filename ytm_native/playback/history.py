"""Historial de escucha: anota cada canción que se oye de verdad.

Una canción cuenta cuando ha *sonado* ``HISTORY_MIN_LISTEN_S`` segundos (o la
mitad, si es más corta). Se mide el tiempo que suena, no la posición: adelantar
con la barra no suma, y una pista que se salta a los pocos segundos no deja
rastro. Volver al principio y oírla otra vez (repetir una, «Anterior») cuenta
como otra escucha.

El historial queda en la base local y no se envía a ningún sitio. Aparte,
y solo si el usuario conecta Last.fm y activa el envío, la misma medida del
tiempo que suena decide cuándo anunciar la canción y cuándo registrarla allí
(la mitad o 4 minutos, ver ``services/lastfm.py``).

Lo mismo con el historial de YouTube: solo con los cambios en la cuenta
permitidos y la sub-opción «Registrar escuchas en el historial de YouTube»
activa, al sonar la mitad o 4 minutos se anota la escucha en la cuenta
(``services/account_actions.py``). Las pistas del disco nunca se envían.
"""

from __future__ import annotations

import logging
import time
from typing import Optional, Protocol

from PySide6.QtCore import QObject, Signal

from ytm_native.core import config
from ytm_native.core.db import Database, Track
from ytm_native.playback.player import PLAYING, PlaybackController

log = logging.getLogger("ytmn.history")

ENABLED_KEY = "history_enabled"
# Entre dos avisos de posición seguidos pasan ~0,25 s; un salto mayor que esto
# es que se movió la barra, no que sonara.
_MAX_STEP_S = 2.0


class Scrobbler(Protocol):
    """Lo que el contador necesita de ``services.lastfm.LastfmScrobbler``."""

    @property
    def active(self) -> bool: ...

    def now_playing(self, track: Track, duration: float = 0.0) -> None: ...

    def submit(self, track: Track, started_at: float, duration: float = 0.0) -> bool: ...


class HistoryReporter(Protocol):
    """Lo que el contador necesita de ``services.account_actions.AccountActions``."""

    def report_history(self, track: Track) -> bool: ...


# Umbral del historial de YouTube: la mitad de la pista o 4 minutos.
YOUTUBE_HISTORY_MAX_S = 240.0


def youtube_history_seconds(duration: float) -> float:
    return min(YOUTUBE_HISTORY_MAX_S, duration / 2) if duration > 0 else YOUTUBE_HISTORY_MAX_S


def needed_seconds(duration: float) -> float:
    """Cuánto tiene que sonar una pista de esa duración para contar."""
    full = float(config.HISTORY_MIN_LISTEN_S)
    return min(full, duration / 2) if duration > 0 else full


class PlayTracker(QObject):
    logged = Signal(str)             # videoId de la escucha que acaba de anotarse

    def __init__(self, db: Database, player: PlaybackController, parent: Optional[QObject] = None,
                 scrobbler: Optional[Scrobbler] = None, clock=time.time,
                 reporter: Optional[HistoryReporter] = None):
        super().__init__(parent)
        self.db = db
        self.player = player
        self.scrobbler = scrobbler
        self.reporter = reporter
        self._clock = clock
        self.enabled = db.get_bool(ENABLED_KEY, True)
        self._track: Optional[Track] = None
        self._listened = 0.0
        self._last_pos: Optional[float] = None
        self._counted = False
        self._write_failures = 0         # intentos fallidos de anotar la escucha actual
        # Last.fm: lo sonado de esta escucha, cuándo empezó y qué se envió ya.
        self._heard = 0.0
        self._started_at: Optional[float] = None
        self._announced = False
        self._scrobbled = False          # ya decidido: enviada o demasiado corta
        self._submitted = False          # enviada a Last.fm
        # Historial de YouTube: lo sonado de esta escucha y si ya se decidió.
        self._yt_heard = 0.0
        self._yt_done = False
        player.current_changed.connect(self._on_current)
        player.position_changed.connect(self._on_position)

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        self.db.set_setting(ENABLED_KEY, "1" if self.enabled else "0")
        self._listened = 0.0             # lo oído con el historial apagado no cuenta después

    def _on_current(self, track: Optional[Track]) -> None:
        self._track = track
        self._listened = 0.0
        self._last_pos = None
        self._counted = False
        self._write_failures = 0
        self._restart_listen()

    def _restart_listen(self) -> None:
        self._heard = 0.0
        self._started_at = None
        self._announced = False
        self._scrobbled = False
        self._submitted = False
        self._yt_heard = 0.0
        self._yt_done = False

    def _on_position(self, pos: float, duration: float) -> None:
        track = self._track
        if track is None:
            return
        last, self._last_pos = self._last_pos, pos
        if self.player.state != PLAYING or last is None:
            return
        step = pos - last
        if step < 0 and pos < _MAX_STEP_S and (self._counted or self._submitted or self._yt_done):
            # Ya contada: si vuelve al principio empieza otra escucha.
            self._listened = 0.0
            self._counted = False
            self._restart_listen()
            return
        played = 0 < step <= _MAX_STEP_S
        self._follow_scrobble(track, pos, duration, step if played else 0.0)
        self._follow_youtube(track, duration, step if played else 0.0)
        if self._counted:
            return
        if played:
            self._listened += step
        if not self.enabled or self._listened < needed_seconds(duration):
            return
        try:
            self.db.upsert_track(track)      # el historial enseña la pista aunque no esté en ninguna lista
            self.db.log_play(track.video_id)
        except Exception:  # noqa: BLE001 - un fallo al anotar no debe parar la música
            # Sin marcarla como contada: el siguiente aviso de posición lo
            # vuelve a intentar (la base pudo estar ocupada un momento).
            self._write_failures += 1
            if self._write_failures == 1:
                log.exception("no se pudo anotar la escucha de %s; se reintentará", track.video_id)
            return
        self._counted = True
        self._write_failures = 0
        self.logged.emit(track.video_id)

    def _follow_youtube(self, track: Track, duration: float, step: float) -> None:
        """Historial de YouTube: al llegar al umbral se pide anotar la escucha
        una vez. Si la opción está apagada, ``report_history`` no envía nada."""
        reporter = self.reporter
        if reporter is None or self._yt_done:
            return
        self._yt_heard += step
        known = duration if duration > 0 else float(track.duration_s or 0)
        if self._yt_heard >= youtube_history_seconds(known):
            self._yt_done = True
            try:
                reporter.report_history(track)
            except Exception:  # noqa: BLE001 - nunca debe parar la música
                log.exception("no se pudo pedir el registro de %s en YouTube", track.video_id)

    def _follow_scrobble(self, track: Track, pos: float, duration: float, step: float) -> None:
        """Last.fm: anuncia la canción al empezar a sonar y la registra al
        llegar al umbral. Con el envío apagado no hace nada."""
        scrobbler = self.scrobbler
        if scrobbler is None or self._scrobbled or not scrobbler.active:
            return
        from ytm_native.services.lastfm import scrobble_threshold

        known = duration if duration > 0 else float(track.duration_s or 0)
        if self._started_at is None:
            self._started_at = self._clock() - max(0.0, pos)
        if not self._announced:
            self._announced = True
            scrobbler.now_playing(track, known)
        self._heard += step
        needed = scrobble_threshold(known)
        if needed is None:
            self._scrobbled = True           # demasiado corta para Last.fm
            return
        if self._heard >= needed:
            self._scrobbled = self._submitted = True
            scrobbler.submit(track, self._started_at, known)
