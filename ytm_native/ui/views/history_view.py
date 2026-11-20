"""El historial de escucha: lo reciente, lo más escuchado y sus cuentas, y
la fila «Escuchado hace poco» del inicio.

Es una mezcla de ``MainWindow``: sus métodos usan los controles y el estado
de la ventana (``self.model``, ``self.table``, ``self.band``, ``self.player``…).
"""

from __future__ import annotations

import time

from ytm_native.core import config
from ytm_native.core.db import Track
from ..art import track_art_url
from ..components import take_launch
from ..widgets import format_ago
from ytm_native.core.i18n import plural, tr
from .common import HOME_KEY, HISTORY_KEY, HEARD_RECENT, HEARD_MONTH


class HistoryView:
    """Lo del historial de escucha, como parte de ``MainWindow``."""

    # --------------------------------------------------------------- historial
    def _refresh_recent(self) -> None:
        """«Escuchado hace poco» del inicio: lo último del historial, cada canción una vez."""
        self._recent_tracks = [row[0] for row in self.db.recent_plays(config.HISTORY_HOME_CARDS)]
        self.home.set_recent([
            (t.title, t.artists, track_art_url(t.video_id, t.thumb_url, 400), t)
            for t in self._recent_tracks
        ])

    def _play_recent(self, track: Track) -> None:
        take_launch()                 # la tarjeta no abre una vista: no hay carátula que vuele
        if self._missing_binaries_notice():
            return
        ids = [t.video_id for t in self._recent_tracks]
        if track.video_id in ids:
            self.player.play_tracks(self._recent_tracks, ids.index(track.video_id))
        else:
            self.player.play_tracks([track], 0)

    def _fill_history(self) -> None:
        """Vista «Historial»: la tabla con una nota por pista en lugar del álbum."""
        scope, limit = self._heard_scope, config.HISTORY_VIEW_LIMIT
        month_ago = time.time() - 30 * 86400
        if scope == HEARD_RECENT:
            rows = self.db.recent_plays(limit)
            notes = {t.video_id: format_ago(at).capitalize() for t, at, _n in rows}
            header = tr("Última vez")
        else:
            rows = self.db.top_plays(month_ago if scope == HEARD_MONTH else 0.0, limit)
            notes = {t.video_id: plural(n, "{n} vez", "{n} veces") for t, _at, n in rows}
            header = tr("Escuchas")
        tracks = [row[0] for row in rows]
        self.model.set_tracks(tracks, notes, header)
        total, songs = self.db.play_stats()
        plays, heard = self.db.play_stats(month_ago) if scope == HEARD_MONTH else (total, songs)
        facts = []
        if plays:
            # El plazo va en la primera nota: si la banda es estrecha, las
            # que no caben se ocultan empezando por el final.
            first = (plural(plays, "{n} escucha en 30 días", "{n} escuchas en 30 días")
                     if scope == HEARD_MONTH else plural(plays, "{n} escucha", "{n} escuchas"))
            facts = [first,
                     plural(heard, "{n} canción distinta", "{n} canciones distintas")]
        latest = self.db.recent_plays(1)
        self._band_art = (track_art_url(latest[0][0].video_id, latest[0][0].thumb_url, 400)
                          if latest else "")
        self.band.set_content(
            tr("Historial"),
            tr("Lo que has escuchado en esta app. Se guarda solo en este equipo."),
            facts, self._band_art, show_sync=False, show_cover=bool(latest),
            extra=(tr("Borrar historial"), "close") if total else None,
        )
        self.heard_bar.setVisible(total > 0)
        if tracks:
            self._set_empty("")
        elif total:
            self._set_empty(tr("Nada escuchado en los últimos 30 días"),
                            tr("Lo anterior sigue en las otras dos pestañas."), glyph="history")
        elif self.tracker.enabled:
            self._set_empty(tr("Aún no hay nada en el historial"),
                            tr("Aquí aparecerá cada canción que escuches al menos medio minuto."),
                            glyph="history")
        else:
            self._set_empty(tr("El historial está desactivado"),
                            tr("Actívalo en Opciones (Ctrl+,): «Guardar el historial de escucha»."),
                            glyph="history")

    def _set_heard_scope(self, scope: str) -> None:
        if scope == self._heard_scope:
            return
        self._heard_scope = scope
        self.heard_bar.set_scope(scope)
        if self._view_key == HISTORY_KEY:
            self._show_view(HISTORY_KEY, force=True)

    def _on_play_logged(self, _video_id: str) -> None:
        if self._view_key == HISTORY_KEY:
            # La tabla se rehace con la escucha nueva sin perder por dónde se iba leyendo.
            bar = self.table.verticalScrollBar()
            where = bar.value()
            self._show_view(HISTORY_KEY, force=True)
            bar.setValue(where)
        elif self._view_key == HOME_KEY:
            self._refresh_recent()

    def _set_history_enabled(self, enabled: bool) -> None:
        self.tracker.set_enabled(enabled)
        if self._view_key == HISTORY_KEY:
            self._show_view(HISTORY_KEY, force=True)

    def _clear_history(self) -> None:
        plays, _songs = self.db.play_stats()
        if not plays or not self._confirm(
            tr("Borrar historial"),
            tr("Se borrarán {plural} de este equipo. Tus listas no cambian. No se puede deshacer.", plural=plural(plays, "{n} escucha", "{n} escuchas")),
            tr("Borrar"),
        ):
            return
        self.db.clear_history()
        self._refresh_recent()
        if self._view_key == HISTORY_KEY:
            self._show_view(HISTORY_KEY, force=True)
        self._notice(tr("Historial borrado."))
