"""La música del disco: la carpeta elegida, leerla y lo que se ve de ella.

Es una mezcla de ``MainWindow``: sus métodos usan los controles y el estado
de la ventana (``self.model``, ``self.table``, ``self.band``, ``self.player``…).
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from PySide6.QtWidgets import QFileDialog

from ytm_native.core import config
from ytm_native.services import local_media
from ..widgets import run_async
from ytm_native.core.i18n import plural, tr
from .common import LOCAL_KEY, format_total

log = logging.getLogger("ytmn.ui")


class LocalView:
    """Lo de la música del disco, como parte de ``MainWindow``."""

    # ------------------------------------------------- música del disco (local)
    def _music_folder(self) -> str:
        return self.db.get_setting(config.LOCAL_MUSIC_DIR_KEY, "") or ""

    def _fill_local(self) -> None:
        tracks = self.db.local_tracks()
        self.model.set_tracks(tracks)
        folder = self._music_folder()
        facts = [plural(len(tracks), "{n} canción", "{n} canciones")]
        total = format_total(sum(t.duration_s or 0 for t in tracks))
        if total:
            facts.append(total)
        if self._scanning:
            facts.append(tr("Leyendo la carpeta…"))
        cover = next((t.thumb_url for t in tracks if t.thumb_url), "")
        self._band_art = cover
        self.band.set_content(
            tr("En tu equipo"), folder, facts, cover, show_sync=False, extra=(tr("Opciones"), "more"),
        )
        if self._scanning and not tracks:
            self._set_empty(tr("Leyendo la carpeta…"), tr("Las canciones aparecen al terminar."), glyph="folder")
        else:
            self._set_empty("" if tracks else tr("No hay canciones en esta carpeta"),
                            tr("Formatos que se leen: MP3, FLAC, M4A, AAC, OGG, Opus y WAV."), glyph="folder")

    def _sync_local_actions(self) -> None:
        has_folder = bool(self._music_folder())
        self.rescan_action.setEnabled(has_folder and not self._scanning)
        self.forget_folder_action.setEnabled(has_folder)

    def _choose_music_folder(self) -> None:
        start = self._music_folder() or str(Path.home() / "Music")
        folder = QFileDialog.getExistingDirectory(self, tr("Carpeta de música"), start)
        if not folder:
            return
        folder = os.path.abspath(folder)
        self.db.set_setting(config.LOCAL_MUSIC_DIR_KEY, folder)
        self.db.forget_local_folder(local_media.normalized_path(folder))
        self._reload_sidebar()
        self._scan_local(manual=True)
        self._navigate(LOCAL_KEY)

    def _forget_music_folder(self) -> None:
        if not self._music_folder() or not self._confirm(
            tr("Dejar de usar la carpeta"),
            tr("Las canciones de la carpeta dejan de aparecer en la app. Los archivos no se tocan."),
            tr("Dejar de usarla"),
        ):
            return
        if self._scan_cancel is not None:
            self._scan_cancel.set()
        self.db.set_setting(config.LOCAL_MUSIC_DIR_KEY, "")
        self.db.forget_local_folder()
        if self._view_key == LOCAL_KEY:
            self._view_key = None
        self._reload_sidebar()

    def _scan_local(self, manual: bool = False) -> None:
        """Lee la carpeta en segundo plano: solo los archivos nuevos o cambiados."""
        folder = self._music_folder()
        if not folder or self._scanning:
            return
        if not os.path.isdir(folder):
            if manual:
                self._notice(tr("No se encuentra la carpeta de música: {folder}", folder=folder), "error")
            return
        self._scanning = True
        cancel = self._scan_cancel = threading.Event()
        if self._view_key == LOCAL_KEY:
            self._fill_local()
        run_async(lambda: local_media.scan_in_thread(self.db, folder, cancel),
                  lambda report, error: self._on_scan_done(report, error, manual))

    def _on_scan_done(self, report, error, manual: bool) -> None:
        self._scanning = False
        self._scan_cancel = None
        if error is not None:
            log.warning("no se pudo leer la carpeta de música: %s", error)
            self._notice(tr("No se pudo leer la carpeta de música."), "error")
        elif manual and report is not None and not report.cancelled:
            self._notice(tr("Carpeta leída: {plural}.", plural=plural(report.total, "{n} canción", "{n} canciones")))
        if self._view_key == LOCAL_KEY:
            scroll = self.table.verticalScrollBar().value()
            self._fill_local()
            self.table.verticalScrollBar().setValue(scroll)
            current = self.player.current_track()
            self.model.set_current(current.video_id if current else None)
            self._update_buttons()
