"""Historial de escucha: anota cada canción que se oye de verdad.

Una canción cuenta cuando ha *sonado* ``HISTORY_MIN_LISTEN_S`` segundos (o la
mitad, si es más corta). Se mide el tiempo que suena, no la posición: adelantar
con la barra no suma, y una pista que se salta a los pocos segundos no deja
rastro. Volver al principio y oírla otra vez (repetir una, «Anterior») cuenta
como otra escucha.

Todo queda en la base local: el historial no se envía a ningún sitio.
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import QObject, Signal

from ytm_native.core import config
from ytm_native.core.db import Database, Track
from ytm_native.playback.player import PLAYING, PlaybackController

log = logging.getLogger("ytmn.history")

ENABLED_KEY = "history_enabled"
# Entre dos avisos de posición seguidos pasan ~0,25 s; un salto mayor que esto
# es que se movió la barra, no que sonara.
_MAX_STEP_S = 2.0


def needed_seconds(duration: float) -> float:
    """Cuánto tiene que sonar una pista de esa duración para contar."""
    full = float(config.HISTORY_MIN_LISTEN_S)
    return min(full, duration / 2) if duration > 0 else full


class PlayTracker(QObject):
    logged = Signal(str)             # videoId de la escucha que acaba de anotarse

    def __init__(self, db: Database, player: PlaybackController, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.db = db
        self.player = player
        self.enabled = db.get_bool(ENABLED_KEY, True)
        self._track: Optional[Track] = None
        self._listened = 0.0
        self._last_pos: Optional[float] = None
        self._counted = False
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

    def _on_position(self, pos: float, duration: float) -> None:
        track = self._track
        if track is None:
            return
        last, self._last_pos = self._last_pos, pos
        if self.player.state != PLAYING or last is None:
            return
        step = pos - last
        if self._counted:
            # Ya contada: si vuelve al principio empieza otra escucha.
            if step < 0 and pos < _MAX_STEP_S:
                self._listened = 0.0
                self._counted = False
            return
        if 0 < step <= _MAX_STEP_S:
            self._listened += step
        if not self.enabled or self._listened < needed_seconds(duration):
            return
        self._counted = True
        try:
            self.db.upsert_track(track)      # el historial enseña la pista aunque no esté en ninguna lista
            self.db.log_play(track.video_id)
        except Exception:  # noqa: BLE001 - un fallo al anotar no debe parar la música
            log.exception("no se pudo anotar la escucha de %s", track.video_id)
            return
        self.logged.emit(track.video_id)
