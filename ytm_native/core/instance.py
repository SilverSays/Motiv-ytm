"""Una sola instancia de la app por carpeta de datos.

Dos ventanas a la vez escribirían la misma base SQLite y la misma sesión, y
cada una lanzaría su propio ``mpv``. La segunda que se abre no arranca: avisa
a la primera para que se ponga delante y sale.

* El cerrojo es un ``QLockFile`` en la carpeta de datos: lo suelta el sistema
  si la app muere, y Qt reconoce un cerrojo huérfano por el PID que guarda.
* El aviso es una conexión a un ``QLocalServer`` (tubería con nombre en
  Windows) propio de esa carpeta de datos.

Va ligado a la carpeta de datos (``YTMN_HOME``), no al usuario: las pruebas
y una copia de desarrollo con su propia carpeta no chocan con la app real.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QLockFile, QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from ytm_native.core import paths

log = logging.getLogger("ytmn.instance")

def server_name(home: Path) -> str:
    digest = hashlib.sha1(str(Path(home).resolve()).lower().encode("utf-8")).hexdigest()[:16]
    return f"ytmn-instance-{digest}"


class SingleInstance(QObject):
    activated = Signal()           # otra instancia pidió que esta se ponga delante

    def __init__(self, home: Optional[Path] = None, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._home = Path(home) if home is not None else paths.app_home()
        self._name = server_name(self._home)
        self._lock: Optional[QLockFile] = None
        self._server: Optional[QLocalServer] = None

    def acquire(self) -> bool:
        """``True`` si esta es la única instancia. Si no, avisa a la que ya
        está abierta y devuelve ``False``."""
        self._home.mkdir(parents=True, exist_ok=True)
        lock = QLockFile(str(self._home / "ytmn.lock"))
        lock.setStaleLockTime(0)      # huérfano solo si su proceso ya no existe
        if not lock.tryLock(0):
            if lock.error() == QLockFile.LockError.LockFailedError:
                self._notify_running()
                return False
            # Sin permiso o error de disco: mejor abrir que no abrir nunca.
            log.warning("no se pudo comprobar si hay otra instancia (%s)", lock.error())
            return True
        self._lock = lock
        server = QLocalServer(self)
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        QLocalServer.removeServer(self._name)     # restos de un cierre brusco (Unix)
        if server.listen(self._name):
            server.newConnection.connect(self._on_connection)
            self._server = server
        else:
            log.warning("no se pudo abrir el aviso entre instancias: %s", server.errorString())
        return True

    def release(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server = None
        if self._lock is not None:
            self._lock.unlock()
            self._lock = None

    def _notify_running(self) -> None:
        if paths.IS_WINDOWS:
            # Deja que la otra instancia pase al frente (Windows lo impide si
            # no lo permite el proceso que tiene el foco, que es este).
            try:
                import ctypes

                ctypes.windll.user32.AllowSetForegroundWindow(-1)    # ASFW_ANY
            except (OSError, AttributeError):
                pass
        sock = QLocalSocket()
        sock.connectToServer(self._name)
        if sock.waitForConnected(1000):
            sock.disconnectFromServer()       # la llamada misma es el aviso
        else:
            log.info("otra instancia tiene el cerrojo pero no contesta: %s", sock.errorString())

    def _on_connection(self) -> None:
        # Basta con que llegue la conexión: solo otra instancia de la app
        # conoce este nombre. (Lo que escribe puede no llegar a leerse si ya
        # cerró la tubería cuando esta lo atiende.)
        server = self._server
        knocked = False
        while server is not None and server.hasPendingConnections():
            sock = server.nextPendingConnection()
            sock.abort()
            sock.deleteLater()
            knocked = True
        if knocked:
            self.activated.emit()
