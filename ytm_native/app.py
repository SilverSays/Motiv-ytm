"""Punto de entrada: crea los componentes y lanza la ventana."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from PySide6.QtWidgets import QApplication

from ytm_native.core import paths
from ytm_native.core.db import Database
from ytm_native.playback.extractor import ExtractorManager, probe_url
from ytm_native.playback.mpv_ipc import MpvPlayer
from ytm_native.playback.player import PlaybackController
from ytm_native.services.sync import LibrarySync
from ytm_native.ui import chrome
from ytm_native.ui.main_window import Bridge, MainWindow


def setup_logging() -> None:
    paths.ensure_dirs()
    handler = RotatingFileHandler(
        paths.log_dir() / "ytmn.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def build(app: QApplication) -> MainWindow:
    db = Database(paths.db_path())
    bridge = Bridge()
    extractor = ExtractorManager(
        db,
        on_state=lambda state: bridge.extractor_state.emit(state.value),
        on_notice=bridge.notice.emit,
        prewarm=True,
        url_probe=probe_url,
    )
    mpv = MpvPlayer(volume=db.get_int("volume", 80))
    player = PlaybackController(db, extractor, mpv)
    sync = LibrarySync(db)
    window = MainWindow(db, extractor, mpv, player, sync, bridge)
    # Referencias vivas mientras exista la ventana.
    window._keep = (db, bridge, extractor, mpv, player, sync)  # noqa: SLF001
    return window


def main() -> int:
    setup_logging()
    app = QApplication(sys.argv)
    app.setApplicationName("YT Music Nativo")
    chrome.install(app)
    window = build(app)
    window.show()
    window.start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
