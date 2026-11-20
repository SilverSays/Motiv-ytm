"""La cola de reproducción: lo que se ve de ella, vaciarla, quitar y mover
pistas, y poner pistas en ella desde cualquier vista.

Es una mezcla de ``MainWindow``: sus métodos usan los controles y el estado
de la ventana (``self.model``, ``self.table``, ``self.band``, ``self.player``…).
"""

from __future__ import annotations

from ytm_native.playback import bootstrap
from ytm_native.core.db import Track, is_local_playlist
from ..art import track_art_url
from ytm_native.core.i18n import plural, tr
from .common import QUEUE_KEY


class QueueView:
    """Lo de la cola, como parte de ``MainWindow``."""

    # ------------------------------------------------------------ cola y menú
    def _queue_contents(self) -> tuple[list[Track], list[str]]:
        tracks, pos = self.player.queue_snapshot()
        ahead = max(0, len(tracks) - pos - 1) if pos >= 0 else 0
        facts = [plural(len(tracks), "{n} pista", "{n} pistas"), tr("{ahead} por delante", ahead=ahead)] if tracks else []
        return tracks, facts

    def _refresh_queue(self) -> None:
        """La cola a la vista cuando avanza o cambia: se tocan solo las filas
        que cambian, la marca de lo que suena y las notas de la banda. Nada se
        reconstruye ni vuelve arriba: quien esté desplazándose por una cola
        larga sigue donde estaba."""
        tracks, facts = self._queue_contents()
        current = self.player.current_track()
        self.model.set_tracks(tracks)
        self.model.set_current(current.video_id if current else None)
        art = track_art_url(current.video_id, current.thumb_url, 400) if current else ""
        if art != self._band_art:
            self._band_art = art
            self.band.cover.set_url(art)
            self._apply_band_tint()
        self.band.set_facts(facts)
        self.band.extra_btn.setEnabled(len(tracks) > 1)
        self._set_empty("" if tracks else tr("La cola está vacía"), tr("Reproduce una lista o una búsqueda."),
                        glyph="queue")
        self._update_buttons()

    def _on_queue_changed(self) -> None:
        if self._view_key == QUEUE_KEY:
            self._refresh_queue()

    def _clear_queue(self) -> None:
        """«Vaciar la cola»: queda solo lo que suena, y sigue sonando."""
        tracks, pos = self.player.queue_snapshot()
        others = len(tracks) - 1 if pos >= 0 else len(tracks)
        if others <= 0 or not self._confirm(
            tr("Vaciar la cola"),
            tr("Se quitarán {plural} de la cola. Lo que suena sigue sonando.",
               plural=plural(others, "{n} pista", "{n} pistas")),
            tr("Vaciar"),
        ):
            return
        self.player.clear_queue()

    def _remove_from_queue(self, row: int, track: Track) -> None:
        """«Quitar de la cola» de la fila ``row``, si sigue siendo esa pista
        (la cola puede haber cambiado con el menú abierto)."""
        tracks, _pos = self.player.queue_snapshot()
        if 0 <= row < len(tracks) and tracks[row].video_id == track.video_id:
            self.player.remove_from_queue(row)

    def _on_row_moved(self, from_row: int, to_row: int) -> None:
        # Solo se arrastra con la lista tal cual es (``_sync_list_tools``); aun
        # así, las filas de la tabla se traducen a las del modelo.
        if not self.proxy.is_natural():
            return
        source_from, source_to = self.proxy.source_row(from_row), self.proxy.source_row(to_row)
        key = self._view_key
        if key == QUEUE_KEY:
            moved = self.player.move_in_queue(source_from, source_to)
        elif is_local_playlist(key):
            moved = self.db.move_in_local_playlist(key, source_from, source_to)
            if moved:
                self._after_local_change(key)     # solo cambian esas filas (y quizá la carátula)
        else:
            moved = False
        if moved:
            self.table.selectRow(self.proxy.view_row(source_to))    # la selección sigue a la fila movida

    def _play_single(self, track: Track) -> None:
        if self._missing_binaries_notice():
            return
        self.db.upsert_track(track)
        self.player.play_tracks([track], 0)

    def _queue_track(self, track: Track, next_up: bool) -> None:
        if bootstrap.missing_binaries():
            self._notice(tr("Faltan binarios: abre Opciones (Ctrl+,) › «Mantenimiento» › «Binarios que faltan»."))
            return
        self.db.upsert_track(track)
        if next_up:
            self.player.play_next(track)
        else:
            self.player.enqueue(track)
        self._notice(tr("«{title}» añadida a la cola.", title=track.title))

    def _queue_tracks(self, tracks: list[Track], next_up: bool) -> None:
        """Varias a la cola de una vez, en el orden en que están en la tabla."""
        if not tracks:
            return
        if bootstrap.missing_binaries():
            self._notice(tr("Faltan binarios: abre Opciones (Ctrl+,) › «Mantenimiento» › «Binarios que faltan»."))
            return
        for track in tracks:
            self.db.upsert_track(track)
        if next_up:
            self.player.play_next_many(tracks)
        else:
            self.player.enqueue_many(tracks)
        self._notice(plural(len(tracks), "{n} canción añadida a la cola.", "{n} canciones añadidas a la cola."))

    def _start_radio(self, track: Track) -> None:
        if bootstrap.missing_binaries():
            self._notice(tr("Faltan binarios: abre Opciones (Ctrl+,) › «Mantenimiento» › «Binarios que faltan»."))
            return
        self.db.upsert_track(track)
        self.player.start_radio(track)
        self._notice(tr("Cargando radio…"))
