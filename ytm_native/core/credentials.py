"""Credenciales de la cuenta cifradas en reposo (DPAPI).

``browser.json`` (cabeceras de ytmusicapi) y ``cookies.txt`` (cookies para
yt-dlp) se guardan siempre selladas con ``core/crypto.py``: solo el usuario de
Windows que las guardó puede leerlas, en este equipo.

* ``read_*`` / ``write_*``: acceso de toda la app a esos archivos. Lo que se
  escribe va siempre cifrado y de forma atómica (temporal, ``fsync`` y
  ``os.replace``). Un archivo antiguo aún en claro se sigue leyendo hasta que
  la migración lo cifre.
* ``migrate_plaintext``: al arrancar, cifra en su sitio lo que siga en claro.
* ``EphemeralCookies``: yt-dlp solo entiende un ``cookies.txt`` legible. Para
  cada ejecución se descifra en memoria y se escribe un archivo temporal de
  nombre único que existe mientras dura ese proceso y se borra siempre al
  terminar. Lo que yt-dlp renueva en él se vuelve a guardar cifrado.

Un archivo que no se puede descifrar (dañado, de otro usuario de Windows o
copiado de otro equipo) eleva ``CredentialError``: quien lo lee lo trata como
una sesión caducada que hay que renovar, nunca como un fallo de la app.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import threading
import uuid
from pathlib import Path
from typing import Iterable, Optional

from ytm_native.core import crypto
from ytm_native.core.i18n import tr

log = logging.getLogger("ytmn.credentials")

# Dato extra del cifrado por archivo: un blob de un uso no sirve para otro.
_ENTROPY = {
    "browser.json": b"ytmn-browser-v1",
    "cookies.txt": b"ytmn-cookies-v1",
}
_DEFAULT_ENTROPY = b"ytmn-credential-v1"

EPHEMERAL_PREFIX = ".cookies_ephemeral_"
_EPHEMERAL = re.compile(r"^\.cookies_ephemeral_(\d+)_[0-9a-f]{32}\.tmp$")
_write_lock = threading.Lock()


class CredentialError(Exception):
    """Hay credenciales guardadas pero no se pueden leer (nunca lleva los datos)."""


def _entropy(path: Path) -> bytes:
    return _ENTROPY.get(path.name, _DEFAULT_ENTROPY)


# ---------------------------------------------------------------------------
# Lectura y escritura
# ---------------------------------------------------------------------------
def is_sealed_file(path: Path) -> bool:
    try:
        with open(path, "rb") as fh:
            return crypto.is_sealed(fh.read(len(crypto.MAGIC)))
    except OSError:
        return False


def read_bytes(path: Path) -> bytes:
    """El contenido en claro. ``FileNotFoundError`` si no existe;
    ``CredentialError`` si está cifrado y no se puede descifrar."""
    raw = Path(path).read_bytes()
    if not crypto.is_sealed(raw):
        return raw                       # en claro: anterior a la migración
    try:
        return crypto.unseal(raw, _entropy(Path(path)))
    except crypto.CryptoError as exc:
        log.warning("no se pueden descifrar las credenciales de %s (%s)", Path(path).name, exc)
        raise CredentialError(
            tr("{name} no se puede descifrar: se guardó con otro usuario de Windows, en otro equipo o está "
               "dañado", name=Path(path).name)
        ) from exc


def read_text(path: Path) -> str:
    return read_bytes(path).decode("utf-8", errors="replace")


def read_text_or_empty(path: Path) -> str:
    """Para comprobaciones que no deben fallar nunca (avisos, diagnósticos)."""
    try:
        return read_text(path)
    except (OSError, CredentialError):
        return ""


def readable(path: Path) -> bool:
    try:
        read_bytes(path)
        return True
    except (OSError, CredentialError):
        return False


def write_bytes(path: Path, data: bytes) -> None:
    """Guarda ``data`` cifrado, de forma atómica. Si algo falla, el archivo
    anterior queda intacto y no queda ningún temporal."""
    path = Path(path)
    blob = crypto.seal(bytes(data), _entropy(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with open(tmp, "wb") as fh:
            fh.write(blob)
            fh.flush()
            os.fsync(fh.fileno())        # en disco antes de sustituir: un apagón no deja un archivo vacío
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def write_text(path: Path, text: str) -> None:
    write_bytes(path, text.encode("utf-8"))


# ---------------------------------------------------------------------------
# Migración de lo que quedó en claro
# ---------------------------------------------------------------------------
def migrate_plaintext(files: Iterable[Path]) -> list[Path]:
    """Cifra en su sitio los archivos que sigan en claro. El original en claro
    desaparece al sustituirlo de forma atómica; los temporales en claro de
    versiones anteriores (``browser.json.tmp``) se borran. Nunca falla: lo que
    no se pueda migrar se anota y se reintenta en el próximo arranque."""
    migrated: list[Path] = []
    for path in map(Path, files):
        leftover = path.with_name(path.name + ".tmp")
        try:
            leftover.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            continue
        except OSError as exc:
            log.warning("no se pudo leer %s para cifrarlo: %s", path.name, exc)
            continue
        if crypto.is_sealed(raw):
            continue
        try:
            write_bytes(path, raw)
        except (OSError, crypto.CryptoError) as exc:
            log.warning("no se pudo cifrar %s: %s", path.name, exc)
            continue
        log.info("%s estaba en claro: ahora se guarda cifrado", path.name)
        migrated.append(path)
    return migrated


# ---------------------------------------------------------------------------
# Cookies efímeras para yt-dlp
# ---------------------------------------------------------------------------
def _pid_alive(pid: int) -> bool:
    if pid == os.getpid():
        return True
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    handle = kernel32.OpenProcess(0x1000, False, pid)    # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == 259                            # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def cleanup_ephemeral(folder: Path) -> int:
    """Borra las cookies efímeras que dejó un cierre brusco (un apagón, la app
    matada). Las de un proceso de la app que siga vivo no se tocan."""
    removed = 0
    try:
        entries = list(Path(folder).iterdir())
    except OSError:
        return 0
    for entry in entries:
        match = _EPHEMERAL.match(entry.name)
        if not match or _pid_alive(int(match.group(1))):
            continue
        try:
            entry.unlink()
            removed += 1
        except OSError as exc:
            log.warning("no se pudo borrar un archivo de cookies temporal: %s", exc)
    if removed:
        log.info("se borraron %d archivos de cookies temporales huérfanos", removed)
    return removed


def _looks_like_cookie_jar(text: str) -> bool:
    """Formato Netscape: alguna línea de 7 campos separados por tabuladores
    (las ``#HttpOnly_`` cuentan; los comentarios ``# `` no)."""
    return any(line.count("\t") >= 6 for line in text.splitlines() if not line.startswith("# "))


class EphemeralCookies:
    """El ``cookies.txt`` legible de una ejecución de yt-dlp.

    ``open()`` lo crea (nombre único, solo para este proceso) y devuelve su
    ruta; ``close()`` guarda cifrado lo que yt-dlp renovó y lo borra, pase lo
    que pase. Usar siempre en ``try/finally`` (o como contexto).
    """

    def __init__(self, source: Path):
        self.source = Path(source)
        self.path: Optional[Path] = None
        self._original = b""

    def open(self) -> Path:
        self._original = read_bytes(self.source)
        folder = self.source.parent
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{EPHEMERAL_PREFIX}{os.getpid()}_{uuid.uuid4().hex}.tmp"
        # O_EXCL: nunca se reutiliza ni se sigue un archivo que ya existiera.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        self.path = path
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(self._original)
            _mark_temporary(path)
        except BaseException:
            self.close()
            raise
        return path

    def close(self) -> None:
        path, self.path = self.path, None
        if path is None:
            return
        try:
            self._keep_rotated(path)
        finally:
            for _ in range(3):
                try:
                    path.unlink(missing_ok=True)
                    break
                except OSError:
                    # yt-dlp (o un antivirus) aún lo tiene abierto un instante.
                    threading.Event().wait(0.2)
            else:
                log.warning("no se pudo borrar un archivo de cookies temporal; se borrará al arrancar")

    def _keep_rotated(self, path: Path) -> None:
        """yt-dlp reescribe el archivo al salir con las cookies que YouTube
        renovó: se guardan cifradas si siguen siendo una sesión válida y
        nadie importó otras credenciales mientras tanto."""
        try:
            updated = path.read_bytes()
        except OSError:
            return
        if not updated or updated == self._original:
            return
        text = updated.decode("utf-8", errors="replace")
        if not _looks_like_cookie_jar(text):
            return
        if b"SAPISID" in self._original and "SAPISID" not in text:
            return                       # nunca se guarda un tarro que perdió la sesión
        with _write_lock:
            try:
                if read_bytes(self.source) != self._original:
                    log.info("cookies.txt cambió durante la ejecución de yt-dlp: se descartan sus renovaciones")
                    return
                write_bytes(self.source, updated)
            except (OSError, CredentialError, crypto.CryptoError) as exc:
                log.warning("no se pudieron guardar las cookies renovadas por yt-dlp: %s", exc)

    def __enter__(self) -> Path:
        return self.open()

    def __exit__(self, *_exc) -> None:
        self.close()


def _mark_temporary(path: Path) -> None:
    """Atributo «temporal»: Windows intenta tenerlo solo en memoria. No se
    marca oculto: yt-dlp no podría reescribirlo al salir. Es un detalle: si
    falla, no pasa nada."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x100)  # FILE_ATTRIBUTE_TEMPORARY
    except Exception:  # noqa: BLE001
        pass
