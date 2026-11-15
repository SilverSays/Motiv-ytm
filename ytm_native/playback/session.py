"""Seguir donde se dejó: la cola, la pista y el segundo vuelven al abrir la app.

Se guardan dos entradas en ``settings``:

* ``session_queue``: las pistas y el orden en que suenan. Puede pesar (una
  lista de miles de canciones), así que solo se escribe cuando la cola cambia.
* ``session_state``: por qué pista va, en qué segundo y los modos. Es pequeña:
  se escribe al cambiar de pista, al pausar, cada pocos segundos mientras
  suena y al cerrar.

Las dos se escriben por separado, así que un cierre brusco puede dejarlas
desacompasadas un instante: por eso el estado lleva el ``videoId`` de la pista
y, al recuperar, manda él sobre la posición numérica.

Todo queda en la base local: nada de esto se envía a ningún sitio.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Optional

from PySide6.QtCore import QObject, QTimer

from ytm_native.core import config
from ytm_native.core.db import Database, Track
from ytm_native.playback.player import PLAYING, PlaybackController

log = logging.getLogger("ytmn.session")

QUEUE_KEY = "session_queue"
STATE_KEY = "session_state"
ENABLED_KEY = "resume_session"
_VERSION = 1


def encode_queue(tracks: list[Track], order: list[int]) -> str:
    rows = [[t.video_id, t.title, t.artists, t.album, t.duration_s, t.thumb_url] for t in tracks]
    # El orden natural (sin aleatorio) no hace falta escribirlo.
    plain = order == list(range(len(tracks)))
    return json.dumps({"v": _VERSION, "tracks": rows, "order": None if plain else order},
                      ensure_ascii=False, separators=(",", ":"))


def decode_queue(raw: Optional[str]) -> Optional[tuple[list[Track], list[int]]]:
    """Pistas y orden guardados, o ``None`` si no hay nada que se pueda leer."""
    try:
        data = json.loads(raw) if raw else None
        if not isinstance(data, dict) or data.get("v") != _VERSION:
            return None
        tracks = [
            Track(video_id=str(row[0]), title=str(row[1]), artists=str(row[2] or ""),
                  album=str(row[3] or ""), duration_s=row[4] if isinstance(row[4], int) else None,
                  thumb_url=str(row[5] or ""))
            for row in data["tracks"]
        ]
        order = data.get("order")
    except (ValueError, TypeError, KeyError, IndexError):
        return None
    if not tracks or any(not t.video_id for t in tracks):
        return None
    plain = list(range(len(tracks)))
    if (not isinstance(order, list) or not all(type(i) is int for i in order)
            or sorted(order) != plain):
        order = plain
    return tracks, order


class SessionKeeper(QObject):
    """Escucha al reproductor, guarda la sesión y la recupera al arrancar."""

    def __init__(self, db: Database, player: PlaybackController, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.db = db
        self.player = player
        self.enabled = db.get_bool(ENABLED_KEY, True)
        self._restoring = False
        self._last_state = ""          # último estado escrito: lo mismo no se reescribe

        self._queue_timer = QTimer(self)
        self._queue_timer.setSingleShot(True)
        self._queue_timer.setInterval(config.SESSION_QUEUE_SAVE_DELAY_MS)
        self._queue_timer.timeout.connect(self._save_queue)

        self._tick = QTimer(self)
        self._tick.setInterval(config.SESSION_SAVE_INTERVAL_MS)
        self._tick.timeout.connect(self._save_state)

        player.queue_changed.connect(self._on_queue_changed)
        player.current_changed.connect(lambda _track: self._save_state())
        player.modes_changed.connect(lambda _shuffle, _repeat: self._save_state())
        player.state_changed.connect(self._on_state)

    # -- ajuste -------------------------------------------------------------
    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        self.db.set_setting(ENABLED_KEY, "1" if self.enabled else "0")
        if self.enabled:
            self._save_queue()            # lo que haya en este momento
            self._save_state()
            if self.player.state == PLAYING:
                self._tick.start()
        else:
            # Desactivado no queda rastro de lo que sonaba.
            self._queue_timer.stop()
            self._tick.stop()
            self._last_state = ""
            self.db.set_setting(QUEUE_KEY, None)
            self.db.set_setting(STATE_KEY, None)

    # -- recuperar ----------------------------------------------------------
    def restore(self) -> bool:
        """Pone en el reproductor la cola guardada, detenida en su punto."""
        if not self.enabled:
            return False
        decoded = decode_queue(self.db.get_setting(QUEUE_KEY))
        if decoded is None:
            return False
        tracks, order = decoded
        try:
            state = json.loads(self.db.get_setting(STATE_KEY) or "{}")
            pos = int(state.get("pos", 0))
            position = float(state.get("position", 0.0))
            video_id = str(state.get("video_id") or "")
        except (ValueError, TypeError, AttributeError):
            state, pos, position, video_id = {}, 0, 0.0, ""
        pos = max(0, min(pos, len(order) - 1))
        if video_id and tracks[order[pos]].video_id != video_id:
            # La cola y el estado no casan (cierre brusco entre una escritura
            # y otra): se busca la pista; si no está, se empieza la que haya.
            match = next((i for i, q in enumerate(order) if tracks[q].video_id == video_id), None)
            if match is None:
                position = 0.0
            else:
                pos = match
        self._restoring = True
        try:
            restored = self.player.restore(
                tracks, order, pos, position,
                shuffle=bool(state.get("shuffle")), repeat=str(state.get("repeat") or "off"),
            )
        finally:
            self._restoring = False
        if restored:
            self._last_state = self._state_json()
            log.info("sesión recuperada: %d pistas, pista %d en %.0f s", len(tracks), pos + 1, position)
        return restored

    # -- guardar ------------------------------------------------------------
    def save(self) -> None:
        """Escribe ya lo pendiente (al cerrar la ventana)."""
        if not self.enabled:
            return
        if self._queue_timer.isActive():
            self._queue_timer.stop()
            self._save_queue()
        self._save_state()

    def _on_queue_changed(self) -> None:
        if self.enabled and not self._restoring:
            self._queue_timer.start()     # varias altas seguidas, una sola escritura

    def _on_state(self, state: str) -> None:
        if not self.enabled:
            return
        if state == PLAYING:
            self._tick.start()
        else:
            self._tick.stop()
            self._save_state()            # al pausar o detenerse queda el segundo exacto

    def _save_queue(self) -> None:
        tracks, order = self.player.session_queue()
        if not self.enabled or not tracks:
            return
        started = time.perf_counter()
        self.db.set_setting(QUEUE_KEY, encode_queue(tracks, order))
        elapsed = time.perf_counter() - started
        if elapsed > 0.1:
            log.info("guardar la cola (%d pistas) tardó %.2f s", len(tracks), elapsed)

    def _state_json(self) -> str:
        return json.dumps(self.player.session_state(), separators=(",", ":"))

    def _save_state(self) -> None:
        if not self.enabled or self._restoring or self.player.current_track() is None:
            return
        state = self._state_json()
        if state != self._last_state:
            self._last_state = state
            self.db.set_setting(STATE_KEY, state)
