"""Aviso de versión nueva de la aplicación.

Una vez al día como mucho (``APP_UPDATE_CHECK_INTERVAL_S``) se pregunta a
GitHub cuál es la última versión publicada; si es más nueva que
``__version__``, se avisa con un enlace a su página.

Política estricta: la app **nunca** descarga, sustituye ni ejecuta nada por su
cuenta. Lo único que hace el aviso es abrir la página de la versión en el
navegador cuando el usuario lo pulsa. La comprobación se puede apagar
(``check_app_updates``): apagada, no sale ninguna petición.

La última respuesta se guarda en la base: dentro de las 24 h el aviso se puede
repetir al abrir la app sin volver a preguntar.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Callable, Optional

from PySide6.QtCore import QObject, Signal

from ytm_native import __version__
from ytm_native.core import config
from ytm_native.core.db import Database

log = logging.getLogger("ytmn.updater")

ENABLED_KEY = "check_app_updates"
LAST_CHECK_KEY = "last_app_update_check"
LATEST_TAG_KEY = "app_update_latest_tag"
LATEST_URL_KEY = "app_update_latest_url"

_VERSION = re.compile(r"^\s*v?(\d+(?:\.\d+){0,3})\s*$", re.IGNORECASE)

# ``fetch(url, timeout)`` -> (código HTTP, JSON decodificado o ``None``).
Fetch = Callable[[str, float], tuple[int, object]]
Runner = Callable[[Callable, Callable], None]


@dataclass(frozen=True)
class Release:
    version: str        # «0.2.0», sin la «v»
    url: str            # página de la versión en GitHub


def parse_version(text: str) -> Optional[tuple[int, ...]]:
    """``"v1.2.3"`` -> ``(1, 2, 3)``. Lo que no sea una versión numérica
    (``"1.2.0-beta"``, ``"nightly"``) da ``None`` y no cuenta como nueva."""
    match = _VERSION.match(text or "")
    if not match:
        return None
    parts = [int(p) for p in match.group(1).split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()                     # 1.2 == 1.2.0
    return tuple(parts)


def is_newer(candidate: str, current: str = __version__) -> bool:
    new, now = parse_version(candidate), parse_version(current)
    return new is not None and now is not None and new > now


def safe_release_url(url: object, tag: str = "") -> str:
    """El enlace solo puede llevar a las versiones del proyecto en GitHub;
    si la respuesta trae otra cosa, se usa la página de versiones."""
    base = config.APP_RELEASES_PAGE.rstrip("/")
    if isinstance(url, str) and (url == base or url.startswith(base + "/")) and " " not in url:
        return url
    return f"{base}/tag/{tag}" if tag and re.fullmatch(r"[\w.\-]+", tag) else base


def _requests_fetch(url: str, timeout: float) -> tuple[int, object]:
    import requests

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"ytmn-desktop/{__version__}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    resp = requests.get(url, headers=headers, timeout=timeout)
    try:
        data = resp.json()
    except ValueError:
        data = None
    return resp.status_code, data


def check(db: Database, fetch: Optional[Fetch] = None, now: Optional[float] = None,
          force: bool = False, current: str = __version__) -> Optional[Release]:
    """La versión publicada más nueva que ``current``, o ``None``.

    Sin red si la comprobación está apagada o si se preguntó hace menos de
    24 h (salvo ``force``); en ese caso responde con lo que se guardó. Un fallo
    de red no gasta el día: se reintenta en la próxima ocasión."""
    if not db.get_bool(ENABLED_KEY, True) and not force:
        return None
    now = time.time() if now is None else now
    last = db.get_float(LAST_CHECK_KEY, 0.0)
    if not force and 0 <= now - last < config.APP_UPDATE_CHECK_INTERVAL_S:
        return _cached(db, current)
    status, data = (fetch or _requests_fetch)(config.APP_RELEASES_API, config.APP_UPDATE_TIMEOUT_S)
    if status == 404:
        # Aún no hay ninguna versión publicada: respuesta válida.
        _remember(db, now, "", "")
        return None
    if status != 200 or not isinstance(data, dict):
        raise OSError(f"GitHub respondió {status} al preguntar por versiones nuevas")
    tag = data.get("tag_name")
    if not isinstance(tag, str) or data.get("draft") or data.get("prerelease"):
        _remember(db, now, "", "")
        return None
    url = safe_release_url(data.get("html_url"), tag)
    _remember(db, now, tag, url)
    return _cached(db, current)


def _remember(db: Database, now: float, tag: str, url: str) -> None:
    with db.transaction() as c:
        c.executemany(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            [(LAST_CHECK_KEY, repr(now)), (LATEST_TAG_KEY, tag), (LATEST_URL_KEY, url)],
        )


def _cached(db: Database, current: str) -> Optional[Release]:
    tag = db.get_setting(LATEST_TAG_KEY, "") or ""
    if not is_newer(tag, current):
        return None
    version = tag.strip().lstrip("vV")
    return Release(version, safe_release_url(db.get_setting(LATEST_URL_KEY, ""), tag.strip()))


def _default_runner(fn: Callable, done: Callable) -> None:
    from ytm_native.ui.widgets import run_async

    run_async(fn, done)


class AppUpdateChecker(QObject):
    """Comprueba en segundo plano y avisa (``available``) si hay versión nueva."""

    available = Signal(str, str)        # versión, enlace
    checked = Signal(bool)              # al terminar una comprobación pedida a mano: ¿hay nueva?
    failed = Signal(str)                # solo en las pedidas a mano

    def __init__(self, db: Database, fetch: Optional[Fetch] = None, runner: Optional[Runner] = None,
                 parent: Optional[QObject] = None):
        super().__init__(parent)
        self.db = db
        self._fetch = fetch
        self._run = runner or _default_runner
        self._busy = False
        self.release: Optional[Release] = None

    @property
    def enabled(self) -> bool:
        return self.db.get_bool(ENABLED_KEY, True)

    def set_enabled(self, enabled: bool) -> None:
        self.db.set_setting(ENABLED_KEY, "1" if enabled else "0")

    def check(self, manual: bool = False) -> None:
        if self._busy or (not manual and not self.enabled):
            return
        self._busy = True
        db, fetch = self.db, self._fetch

        def work():
            try:
                return check(db, fetch, force=manual)
            finally:
                db.close_thread_connection()

        self._run(work, lambda release, error: self._done(release, error, manual))

    def _done(self, release: Optional[Release], error, manual: bool) -> None:
        self._busy = False
        if error is not None:
            log.info("no se pudo comprobar si hay versión nueva: %s", error)
            if manual:
                self.failed.emit("No se pudo comprobar si hay una versión nueva.")
            return
        if release is not None:
            self.release = release
            self.available.emit(release.version, release.url)
        if manual:
            self.checked.emit(release is not None)
