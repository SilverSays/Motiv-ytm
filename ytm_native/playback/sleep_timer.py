"""Temporizador de apagado: parar la música dentro de un rato o al acabar la
canción que suena.

En los últimos segundos el volumen baja poco a poco hasta el silencio; al
parar, vuelve a su nivel (con la reproducción ya detenida), así la próxima vez
no arranca muda. El volumen del fundido no es una preferencia del usuario: no
toca la barra de volumen ni se guarda.
"""

from __future__ import annotations

import math
import time
from typing import Callable, Optional

from PySide6.QtCore import QObject, QTimer, Signal

from ytm_native.playback.player import PLAYING

TIME, TRACK = "time", "track"
FADE_S = 12.0           # el volumen baja durante los últimos 12 segundos
TICK_MS = 250
FADE_TICK_MS = 100
PRESETS_MIN = (15, 30, 45, 60)


class SleepTimer(QObject):
    changed = Signal()                 # se programó, se pospuso, se canceló o terminó
    remaining_changed = Signal(int)    # segundos enteros que faltan (solo por tiempo)
    expired = Signal()                 # la reproducción se paró por el temporizador

    def __init__(self, player, parent: Optional[QObject] = None,
                 clock: Callable[[], float] = time.monotonic, fade_s: float = FADE_S):
        super().__init__(parent)
        self.player = player
        self._clock = clock
        self._fade_s = fade_s
        self._mode = ""
        self._deadline = 0.0
        self._total = 0.0
        self._base: Optional[int] = None      # volumen de antes del fundido
        self._applied: Optional[int] = None
        self._last_second = -1
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        player.position_changed.connect(self._on_position)
        player.halted_after_track.connect(self._on_halted)
        player.current_changed.connect(self._on_track_changed)

    # -- estado ---------------------------------------------------------------
    @property
    def mode(self) -> str:
        return self._mode

    @property
    def active(self) -> bool:
        return bool(self._mode)

    @property
    def fading(self) -> bool:
        return self._base is not None

    def remaining(self) -> Optional[float]:
        if self._mode != TIME:
            return None
        return max(0.0, self._deadline - self._clock())

    def remaining_seconds(self) -> Optional[int]:
        """Segundos enteros que faltan, redondeados hacia arriba (lo que se
        enseña: "30:00" recién puesto, "0:01" en el último segundo). El margen
        absorbe el error de coma flotante de restar dos lecturas del reloj."""
        left = self.remaining()
        return None if left is None else max(0, math.ceil(left - 1e-6))

    # -- órdenes --------------------------------------------------------------
    def start(self, seconds: float) -> None:
        """Parar dentro de ``seconds`` segundos."""
        self._restore_volume()
        self.player.stop_after_current = False
        self._mode = TIME
        self._total = max(0.0, float(seconds))
        self._deadline = self._clock() + self._total
        self._last_second = -1
        self._timer.start(TICK_MS)
        self.changed.emit()
        self._tick()

    def start_minutes(self, minutes: float) -> None:
        self.start(minutes * 60)

    def stop_after_track(self) -> None:
        """Parar cuando acabe la canción que suena (la siguiente no empieza)."""
        self._restore_volume()
        self._timer.stop()
        self._mode = TRACK
        self.player.stop_after_current = True
        self.changed.emit()

    def postpone(self, minutes: float = 10) -> None:
        """Más tiempo. Si ya estaba bajando el volumen, vuelve a su nivel."""
        if self._mode != TIME:
            return
        self._restore_volume()
        self._deadline = max(self._deadline, self._clock()) + minutes * 60
        self._total = self._deadline - self._clock()
        self._timer.start(TICK_MS)
        self.changed.emit()
        self._tick()

    def cancel(self) -> None:
        if not self._mode:
            return
        self._timer.stop()
        self._restore_volume()
        self.player.stop_after_current = False
        self._mode = ""
        self.changed.emit()

    def note_volume(self, volume: int) -> None:
        """El usuario movió el volumen durante el fundido: ese es el nivel al
        que se volverá, y el fundido sigue desde él."""
        if self._base is not None:
            self._base = int(volume)
            self._applied = None

    # -- interno --------------------------------------------------------------
    def _tick(self) -> None:
        left = self.remaining()
        if left is None:
            self._timer.stop()
            return
        if left <= 0:
            self._expire()
            return
        second = self.remaining_seconds()
        if second != self._last_second:
            self._last_second = second
            self.remaining_changed.emit(second)
        fade = min(self._fade_s, self._total) if self._total > 0 else self._fade_s
        if left <= fade:
            self._fade_to(left / fade)
            if self._timer.interval() != FADE_TICK_MS:
                self._timer.setInterval(FADE_TICK_MS)
        elif self._timer.interval() != TICK_MS:
            self._timer.setInterval(TICK_MS)

    def _fade_to(self, fraction: float) -> None:
        if self._base is None:
            self._base = int(getattr(self.player.mpv, "volume", 100))
        level = max(0, min(self._base, round(self._base * fraction)))
        if level != self._applied:
            self._applied = level
            self.player.set_volume(level)

    def _restore_volume(self) -> None:
        base, self._base = self._base, None
        self._applied = None
        if base is not None:
            self.player.set_volume(base)

    def _expire(self) -> None:
        self._timer.stop()
        self._mode = ""
        self.player.stop_after_current = False
        self.player.stop()
        self._restore_volume()        # ya parado: la próxima vez no arranca en silencio
        self.changed.emit()
        self.expired.emit()

    def _on_halted(self) -> None:
        if self._mode != TRACK:
            return
        self._mode = ""
        self._restore_volume()
        self.changed.emit()
        self.expired.emit()

    def _on_position(self, pos: float, duration: float) -> None:
        if self._mode != TRACK or duration <= 0 or self.player.state != PLAYING:
            return
        left = duration - pos
        if left <= self._fade_s:
            self._fade_to(max(0.0, left / self._fade_s))

    def _on_track_changed(self, _track) -> None:
        # Se saltó de canción a mitad del fundido: la nueva empieza con su volumen.
        if self._mode == TRACK and self._base is not None:
            self._restore_volume()
