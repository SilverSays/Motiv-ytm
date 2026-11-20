"""Punto de entrada: crea los componentes y lanza la ventana."""

from __future__ import annotations

import atexit
import logging
import os
import subprocess
import sys
import threading
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication, QMessageBox

from ytm_native.core import i18n, paths
from ytm_native.core.db import Database
from ytm_native.core.instance import SingleInstance
from ytm_native.playback.extractor import ExtractorManager, probe_url
from ytm_native.playback.media_controls import MediaControls
from ytm_native.playback.mpv_ipc import MpvPlayer
from ytm_native.playback.player import PlaybackController, saved_volume
from ytm_native.services.sync import LibrarySync
from ytm_native.ui import chrome
from ytm_native.ui.art import track_art_url
from ytm_native.ui.main_window import Bridge, MainWindow
from ytm_native.ui.tray import MINIMIZED_ARG
from ytm_native.core.i18n import tr


log = logging.getLogger("ytmn.app")

_CRASH_TITLE = "YT Music Nativo"
_crash_dialogs = 0


def _write_fallback_log(text: str) -> None:
    """Si el registro aún no está montado (falló al arrancar), el error se
    escribe a mano en el mismo archivo."""
    try:
        folder = paths.log_dir()
        folder.mkdir(parents=True, exist_ok=True)
        with open(folder / "ytmn.log", "a", encoding="utf-8") as fh:
            fh.write(text)
    except OSError:
        pass


def _show_crash_dialog(message: str) -> None:
    app = QApplication.instance()
    if app is not None and QThread.currentThread() is app.thread():
        QMessageBox.critical(None, _CRASH_TITLE, message)
    elif paths.IS_WINDOWS:
        # Sin Qt (falló antes de crearlo) o desde otro hilo: el cuadro de Windows.
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, _CRASH_TITLE, 0x10)   # MB_ICONERROR


def report_crash(exc_type, exc, tb) -> None:
    """``sys.excepthook``: con ``pythonw`` no hay consola, así que un error sin
    atrapar se anota en ``ytmn.log`` y se enseña en un cuadro, en lugar de
    cerrar la app sin decir nada."""
    global _crash_dialogs
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc, tb)
        return
    if logging.getLogger().handlers:
        log.critical("error sin atrapar", exc_info=(exc_type, exc, tb))
    else:
        _write_fallback_log(
            "CRITICAL error sin atrapar\n" + "".join(traceback.format_exception(exc_type, exc, tb))
        )
    if sys.stderr is not None:
        sys.__excepthook__(exc_type, exc, tb)
    # Un fallo que se repite en cada evento no debe llenar la pantalla de cuadros.
    if _crash_dialogs >= 3:
        return
    _crash_dialogs += 1
    try:
        _show_crash_dialog(
            tr("Ocurrió un error inesperado:\n"
               "\n"
               "{name__}: {exc}\n"
               "\n"
               "Los detalles están en {value}", name__=exc_type.__name__, exc=exc, value=paths.log_dir() / 'ytmn.log')
        )
    except Exception:  # noqa: BLE001 - el aviso no puede provocar otro fallo
        log.exception("no se pudo enseñar el aviso del error")


def _report_thread_crash(args: threading.ExceptHookArgs) -> None:
    if args.exc_type is SystemExit:
        return
    name = args.thread.name if args.thread is not None else "?"
    log.error("error sin atrapar en el hilo %s", name,
              exc_info=(args.exc_type, args.exc_value, args.exc_traceback))


def install_crash_hooks() -> None:
    sys.excepthook = report_crash
    threading.excepthook = _report_thread_crash


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
    mpv = MpvPlayer(volume=saved_volume(db))
    player = PlaybackController(db, extractor, mpv)
    sync = LibrarySync(db)
    window = MainWindow(db, extractor, mpv, player, sync, bridge)
    # La sesión multimedia de Windows la publica la app (con artista y
    # carátula); si no se puede, queda la básica de mpv.
    controls = MediaControls(player, window, art_url=_media_art_url)
    mpv.set_media_controls(not controls.active)
    app.aboutToQuit.connect(controls.close)
    # La ventana ya los cierra al cerrarse; esto cubre las salidas que no pasan
    # por ella (un ``quit`` de Qt, un error) y, al final, la salida de Python.
    stop = _child_stopper(mpv, extractor, db)
    # Salir sin pasar por la ventana (cerrar sesión en Windows con la app en
    # la bandeja) también guarda la sesión y cierra lo demás en orden.
    app.aboutToQuit.connect(window.shutdown)
    app.aboutToQuit.connect(stop)
    atexit.register(stop)
    # Referencias vivas mientras exista la ventana.
    window._keep = (db, bridge, extractor, mpv, player, sync, controls)  # noqa: SLF001
    return window


def _media_art_url(track) -> str:
    """Carátula para los controles de Windows: solo direcciones web (la de un
    archivo del disco no la acepta)."""
    url = track_art_url(track.video_id, track.thumb_url, 300)
    return "" if url.startswith("file:") else url


def _child_stopper(mpv: MpvPlayer, extractor: ExtractorManager, db: Database):
    def stop() -> None:
        # La base, la última: lo que guardan los demás al cerrarse queda dentro
        # y el WAL se vuelca al archivo principal.
        for name, step in (("mpv", mpv.shutdown), ("yt-dlp", extractor.shutdown), ("la base de datos", db.close)):
            try:
                step()
            except Exception:  # noqa: BLE001 - se intenta cerrar todo lo demás
                log.exception("no se pudo cerrar %s", name)

    return stop


def show_initial(window: MainWindow, argv: list[str]) -> None:
    """Con ``--minimized`` (el inicio con Windows) la app arranca en la
    bandeja; si no hay bandeja, minimizada, para que se pueda encontrar."""
    if MINIMIZED_ARG not in argv[1:]:
        window.show()
    elif not window.tray_ready():
        window.showMinimized()


def relaunch_command(argv: Optional[list[str]] = None) -> list[str]:
    """La orden que abre otra vez esta misma instalación con los mismos
    argumentos que esta (``argv``, por defecto los de ``sys.argv``), salvo
    ``--minimized``: quien reinicia está mirando la ventana."""
    args = [arg for arg in (sys.argv if argv is None else argv)[1:] if arg != MINIMIZED_ARG]
    exe = Path(sys.executable)
    if getattr(sys, "frozen", False):
        return [str(exe), *args]
    windowed = exe.with_name("pythonw.exe")
    if exe.name.lower() == "python.exe" and windowed.is_file():
        exe = windowed
    return [str(exe), str(Path(__file__).resolve().parent / "__main__.py"), *args]


def relaunch_env() -> dict[str, str]:
    """El entorno de la copia nueva: el de esta sin ``YTMN_LANG``. Esa
    variable manda sobre la preferencia guardada; heredada, la app volvería
    a abrirse en el idioma de antes, justo el que se quería cambiar."""
    env = os.environ.copy()
    env.pop(i18n.ENV_KEY, None)
    return env


def relaunch() -> None:
    """Abre la app de nuevo tras salir (Opciones > Idioma > Reiniciar ahora).
    Se llama cuando esta copia ya soltó el cerrojo de instancia: si no, la
    nueva creería que ya hay una abierta y se cerraría."""
    try:
        subprocess.Popen(relaunch_command(), close_fds=True, env=relaunch_env(),  # noqa: S603 - la propia app
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    except OSError:
        log.exception("no se pudo volver a abrir la app")


def main() -> int:
    install_crash_hooks()
    setup_logging()
    # El idioma se decide aquí, en el hilo principal y antes de que haya
    # ningún hilo de trabajo: así nadie lo decide a medias en paralelo.
    log.info("idioma de la interfaz: %s", i18n.active())
    app = QApplication(sys.argv)
    app.setApplicationName("YT Music Nativo")
    instance = SingleInstance()
    if not instance.acquire():
        log.info("la app ya estaba abierta: se le pasa el foco y esta sale")
        return 0
    try:
        # Ya como única instancia: nadie más usa las credenciales ni el perfil
        # de Firefox del inicio de sesión mientras se migran o se borran.
        try:
            paths.secure_credentials()
        except Exception:  # noqa: BLE001 - se reintenta en el próximo arranque
            log.exception("no se pudieron asegurar las credenciales al arrancar")
        chrome.install(app)
        window = build(app)
        # Abrir la app otra vez (con esta en la bandeja o en el mini
        # reproductor) devuelve la ventana principal al frente.
        instance.activated.connect(window.bring_to_front)
        show_initial(window, sys.argv)
        window.start()
        return app.exec()
    finally:
        instance.release()
        if app.property("ytmn_restart"):
            relaunch()


if __name__ == "__main__":
    sys.exit(main())
