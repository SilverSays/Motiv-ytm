"""Controles multimedia de Windows: lo que suena, con su carátula, y sus botones.

Windows enseña la sesión multimedia de cada app en el panel de volumen, en la
pantalla de bloqueo y en los auriculares o teclados con teclas de reproducción
(«System Media Transport Controls», SMTC). ``mpv`` ya publica una por su
cuenta, pero solo con el título, sin artista ni carátula, y desaparece entre
pista y pista. Aquí la publica la propia app:

* título, artista, álbum y carátula de la pista;
* reproducir, pausar, siguiente y anterior, también con la cola detenida;
* la barra de progreso, que además deja saltar a otro punto.

Hace falta el paquete ``winrt`` (ver ``requirements.txt``). Si no está, o si
Windows no deja crear la sesión, no pasa nada: ``active`` queda en ``False`` y
siguen funcionando los controles básicos de ``mpv``.

Los avisos de Windows llegan en un hilo suyo: se pasan al de la GUI con señales.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import timedelta
from typing import Callable, Optional

from PySide6.QtCore import QObject, Signal

from ytm_native.core import config
from ytm_native.core.db import Track
from ytm_native.playback.player import LOADING, PAUSED, PLAYING, STOPPED, PlaybackController

log = logging.getLogger("ytmn.media")

# Estados que entiende la sesión (los nombres de ``MediaPlaybackStatus``).
CLOSED, CHANGING = "closed", "changing"
_STATUS = {PLAYING: "playing", PAUSED: "paused", LOADING: CHANGING, STOPPED: "stopped"}


class WindowsSession:
    """La sesión multimedia de este proceso. Solo se puede crear en Windows."""

    def __init__(self, on_button: Callable[[str], None], on_seek: Callable[[float], None]):
        from winrt.windows.foundation import Uri
        from winrt.windows.media import (
            MediaPlaybackStatus,
            MediaPlaybackType,
            SystemMediaTransportControlsButton as Button,
            SystemMediaTransportControlsTimelineProperties,
        )
        from winrt.windows.media.playback import MediaPlayer
        from winrt.windows.storage.streams import RandomAccessStreamReference

        self._uri, self._stream_ref = Uri, RandomAccessStreamReference
        self._music = MediaPlaybackType.MUSIC
        self._timeline = SystemMediaTransportControlsTimelineProperties
        self._statuses = {
            "playing": MediaPlaybackStatus.PLAYING, "paused": MediaPlaybackStatus.PAUSED,
            "stopped": MediaPlaybackStatus.STOPPED, CHANGING: MediaPlaybackStatus.CHANGING,
            CLOSED: MediaPlaybackStatus.CLOSED,
        }
        names = {Button.PLAY: "play", Button.PAUSE: "pause", Button.STOP: "stop",
                 Button.NEXT: "next", Button.PREVIOUS: "previous"}

        # Una app de escritorio no tiene sesión propia: se toma la de un
        # ``MediaPlayer`` que no reproduce nada, con sus órdenes automáticas
        # apagadas para que los botones lleguen aquí.
        self._player = MediaPlayer()
        self._player.command_manager.is_enabled = False
        smtc = self._smtc = self._player.system_media_transport_controls
        smtc.is_enabled = False              # sin pista no se enseña nada
        smtc.is_play_enabled = True
        smtc.is_pause_enabled = True
        smtc.is_next_enabled = True
        smtc.is_previous_enabled = True

        def pressed(_sender, args) -> None:
            name = names.get(args.button)
            if name:
                on_button(name)

        def seek_requested(_sender, args) -> None:
            on_seek(args.requested_playback_position.total_seconds())

        self._button_token = smtc.add_button_pressed(pressed)
        self._seek_token = smtc.add_playback_position_change_requested(seek_requested)

    def set_track(self, title: str, artist: str, album: str, art_url: str) -> None:
        updater = self._smtc.display_updater
        updater.clear_all()
        updater.type = self._music
        music = updater.music_properties
        music.title, music.artist, music.album_title = title, artist, album
        if art_url:
            # Windows descarga la carátula por su cuenta, del mismo CDN público.
            updater.thumbnail = self._stream_ref.create_from_uri(self._uri(art_url))
        updater.update()
        self._smtc.is_enabled = True

    def set_status(self, status: str) -> None:
        if status == CLOSED:
            self._smtc.display_updater.clear_all()
            self._smtc.display_updater.update()
            self._smtc.is_enabled = False
        self._smtc.playback_status = self._statuses[status]

    def set_timeline(self, position: float, duration: float) -> None:
        timeline = self._timeline()
        end = timedelta(seconds=max(0.0, duration))
        timeline.start_time = timedelta(0)
        timeline.min_seek_time = timedelta(0)
        timeline.position = timedelta(seconds=max(0.0, min(position, duration) if duration > 0 else position))
        timeline.max_seek_time = end
        timeline.end_time = end
        self._smtc.update_timeline_properties(timeline)

    def close(self) -> None:
        self._smtc.remove_button_pressed(self._button_token)
        self._smtc.remove_playback_position_change_requested(self._seek_token)
        self._smtc.is_enabled = False
        self._player.close()


def open_windows_session(on_button: Callable[[str], None],
                         on_seek: Callable[[float], None]) -> Optional[WindowsSession]:
    """La sesión de Windows, o ``None`` si aquí no se puede (y por qué, al registro)."""
    if sys.platform != "win32" or os.environ.get("YTMN_NO_MEDIA_CONTROLS"):
        return None
    try:
        return WindowsSession(on_button, on_seek)
    except ImportError as exc:
        log.info("controles multimedia propios desactivados: falta el paquete winrt (%s). "
                 "Se instala con instalar.bat.", exc)
    except Exception:  # noqa: BLE001 - p. ej. un Windows sin las funciones multimedia
        log.exception("no se pudo crear la sesión multimedia de Windows")
    return None


def default_art_url(track: Track) -> str:
    return track.thumb_url or f"https://i.ytimg.com/vi/{track.video_id}/mqdefault.jpg"


class MediaControls(QObject):
    """Une el reproductor con la sesión multimedia del sistema."""

    _button = Signal(str)       # del hilo de Windows al de la GUI
    _seek = Signal(float)

    def __init__(
        self,
        player: PlaybackController,
        parent: Optional[QObject] = None,
        session_factory: Callable = open_windows_session,
        art_url: Callable[[Track], str] = default_art_url,
    ):
        super().__init__(parent)
        self.player = player
        self._art_url = art_url
        self._sent_pos = 0.0
        self._sent_dur = -1.0
        self._sent_at = 0.0
        self._button.connect(self._on_button)
        self._seek.connect(self._on_seek)
        self._session = session_factory(self._button.emit, self._seek.emit)
        if self._session is None:
            return
        player.current_changed.connect(self._on_current)
        player.state_changed.connect(self._on_state)
        player.position_changed.connect(self._on_position)
        # Por si ya hay una pista (la cola recuperada al abrir).
        track = player.current_track()
        if track is not None:
            self._on_current(track)
            self._on_state(player.state)

    @property
    def active(self) -> bool:
        return self._session is not None

    def close(self) -> None:
        session, self._session = self._session, None
        if session is not None:
            try:
                session.close()
            except Exception:  # noqa: BLE001 - al salir no hay nada más que hacer
                log.exception("al cerrar la sesión multimedia")

    def _guarded(self, call: Callable[[], None]) -> None:
        """Un fallo de Windows no debe parar la música: se anota y se sigue."""
        if self._session is None:
            return
        try:
            call()
        except Exception:  # noqa: BLE001
            log.exception("la sesión multimedia de Windows falló")

    # -- del reproductor a Windows -----------------------------------------
    def _on_current(self, track: Optional[Track]) -> None:
        session = self._session
        if session is None:
            return
        if track is None:
            self._guarded(lambda: session.set_status(CLOSED))
            return
        self._sent_dur = -1.0            # la pista nueva manda su línea de tiempo entera
        self._guarded(lambda: session.set_track(track.title, track.artists, track.album,
                                                self._art_url(track)))

    def _on_state(self, state: str) -> None:
        session = self._session
        if session is None:
            return
        status = _STATUS.get(state, "stopped")
        if state == STOPPED and self.player.current_track() is None:
            status = CLOSED
        self._guarded(lambda: session.set_status(status))

    def _on_position(self, position: float, duration: float) -> None:
        session = self._session
        if session is None:
            return
        now = time.monotonic()
        elapsed = now - self._sent_at
        expected = self._sent_pos + (elapsed if self.player.state == PLAYING else 0.0)
        # Windows no necesita cada aviso: basta uno cada pocos segundos, salvo
        # que la posición salte (se movió la barra) o cambie la duración.
        if (duration == self._sent_dur and abs(position - expected) < 1.5
                and elapsed < config.MEDIA_TIMELINE_INTERVAL_S):
            return
        self._sent_pos, self._sent_dur, self._sent_at = position, duration, now
        self._guarded(lambda: session.set_timeline(position, duration))

    # -- de Windows al reproductor -----------------------------------------
    def _on_button(self, name: str) -> None:
        player = self.player
        if name == "next":
            player.next_track()
        elif name == "previous":
            player.previous_track()
        elif name == "play":
            if player.state in (PAUSED, STOPPED):
                player.toggle_pause()
        elif name in ("pause", "stop"):
            if player.state == PLAYING:
                player.toggle_pause()

    def _on_seek(self, seconds: float) -> None:
        self.player.seek(seconds)
