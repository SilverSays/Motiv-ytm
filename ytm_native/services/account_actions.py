"""Cambios opcionales en la cuenta de YouTube Music.

Todo lo de este módulo ESCRIBE en la cuenta (me gusta, biblioteca, listas,
historial), así que va detrás de un conmutador maestro que viene apagado
(``config.ACCOUNT_MUTATIONS_KEY``). Con él apagado, cualquier llamada se
corta aquí mismo con ``MutationsDisabled``: no se crea el cliente ni sale
ninguna petición.

Dos capas:

* ``AccountClient``: las operaciones bloqueantes (para un hilo de trabajo).
  Cada una comprueba el conmutador, espera su turno en el limitador (un
  cambio cada ``ACCOUNT_MIN_INTERVAL_S`` como mucho: una ráfaga de clics no
  sale como ráfaga) y usa el cliente autenticado de la sincronización.
* ``AccountActions``: lo que usa la ventana. Aplica el cambio en la interfaz
  al momento (interfaz optimista), lo envía en segundo plano y, si falla,
  devuelve el estado anterior y avisa. Si el usuario cambia de idea mientras
  una petición viaja, al volver solo se envía el último estado pedido.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional

from PySide6.QtCore import QObject, Signal

from ytm_native.core import config, paths
from ytm_native.core.db import Database, Track
from ytm_native.core.i18n import tr

log = logging.getLogger("ytmn.account")

LIKE, DISLIKE, INDIFFERENT = "LIKE", "DISLIKE", "INDIFFERENT"
RATINGS = (LIKE, DISLIKE, INDIFFERENT)
_LOCAL_PREFIX = "local:"


class MutationsDisabled(Exception):
    """El conmutador de cambios en la cuenta está apagado."""


class AccountActionError(Exception):
    """YouTube Music no aceptó el cambio (o no se puede hacer)."""


def mutations_enabled(db: Database) -> bool:
    return db.get_bool(config.ACCOUNT_MUTATIONS_KEY, False)


def history_reporting_enabled(db: Database) -> bool:
    """El historial de YouTube solo se alimenta con las dos opciones activas."""
    return mutations_enabled(db) and db.get_bool(config.ACCOUNT_HISTORY_KEY, False)


def is_local_track(video_id: str) -> bool:
    return bool(video_id) and video_id.startswith(_LOCAL_PREFIX)


def describe_error(exc: BaseException) -> str:
    """Texto corto para el aviso de un cambio que no salió."""
    from ytm_native.services.sync import SessionExpired, http_status, is_auth_error

    if isinstance(exc, MutationsDisabled):
        return tr("Los cambios en la cuenta están desactivados.")
    if isinstance(exc, AccountActionError):
        return str(exc)
    if isinstance(exc, SessionExpired) or is_auth_error(exc):
        return tr("La sesión de YouTube Music caducó: vuelve a iniciar sesión.")
    if http_status(exc) == 429:
        return tr("YouTube Music pidió esperar un poco antes de más cambios.")
    if isinstance(exc, OSError):
        return tr("Sin conexión.")
    return tr("YouTube Music no aceptó el cambio.")


class RateLimiter:
    """Un cambio cada ``min_interval`` segundos como mucho, de uno en uno."""

    def __init__(self, min_interval: float = config.ACCOUNT_MIN_INTERVAL_S,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.min_interval = float(min_interval)
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._last: Optional[float] = None

    def run(self, fn: Callable[[], Any]) -> Any:
        with self._lock:
            if self._last is not None:
                wait = self._last + self.min_interval - self._clock()
                if wait > 0:
                    self._sleep(wait)
            try:
                return fn()
            finally:
                self._last = self._clock()


@dataclass(frozen=True)
class TrackStatus:
    """Lo que la cuenta dice de una pista: valoración y si está en la biblioteca."""

    rating: Optional[str] = None
    in_library: Optional[bool] = None
    add_token: str = ""
    remove_token: str = ""


def _default_factory() -> Any:
    from ytm_native.services.sync import default_client_factory

    return default_client_factory()


class AccountClient:
    """Operaciones de escritura, bloqueantes. Llamar desde un hilo de trabajo."""

    def __init__(self, db: Database, client_factory: Callable[[], Any] = _default_factory,
                 limiter: Optional[RateLimiter] = None,
                 credentials_present: Callable[[], bool] = lambda: paths.browser_json().is_file()):
        self.db = db
        self._factory = client_factory
        self.limiter = limiter or RateLimiter()
        self._credentials_present = credentials_present

    # -- guardas -------------------------------------------------------------
    def _guard(self, video_id: str = "") -> None:
        if not mutations_enabled(self.db):
            raise MutationsDisabled(tr("los cambios en la cuenta están desactivados"))
        if video_id and is_local_track(video_id):
            raise AccountActionError(tr("Las canciones del disco no están en YouTube Music."))
        if not self._credentials_present():
            raise AccountActionError(tr("Inicia sesión en YouTube Music para cambiar tu cuenta."))

    def _call(self, work: Callable[[Any], Any]) -> Any:
        """Una petición con su turno en el limitador y el cliente de la cuenta."""

        def run() -> Any:
            client = self._factory()
            try:
                return work(client)
            finally:
                keeper = getattr(client, "_ytmn_cookie_keeper", None)
                if keeper is not None:
                    keeper.persist()          # cookies que YouTube renovó por el camino

        return self.limiter.run(run)

    # -- canciones -----------------------------------------------------------
    def rate_song(self, video_id: str, rating: str) -> None:
        if rating not in RATINGS:
            raise ValueError(tr("valoración desconocida: {rating!r}", rating=rating))
        self._guard(video_id)
        self._call(lambda client: client.rate_song(video_id, rating))

    def track_status(self, video_id: str) -> TrackStatus:
        """Valoración y biblioteca de la pista según la cuenta (con los tokens
        que hacen falta para guardarla o quitarla)."""
        self._guard(video_id)
        data = self._call(lambda client: client.get_watch_playlist(videoId=video_id, limit=1)) or {}
        tracks = data.get("tracks") if isinstance(data, dict) else None
        item = next((t for t in tracks or [] if isinstance(t, dict) and t.get("videoId") == video_id), None)
        if item is None:
            return TrackStatus()
        rating = item.get("likeStatus")
        tokens = item.get("feedbackTokens") if isinstance(item.get("feedbackTokens"), dict) else {}
        in_library = item.get("inLibrary")
        return TrackStatus(
            rating=rating if rating in RATINGS else None,
            in_library=in_library if isinstance(in_library, bool) else None,
            add_token=str(tokens.get("add") or ""),
            remove_token=str(tokens.get("remove") or ""),
        )

    def set_in_library(self, video_id: str, add: bool) -> None:
        """Guarda la canción en la biblioteca de la cuenta o la quita.

        YouTube Music pide un token de cada canción para esto; se obtiene en
        la misma consulta que da su estado.
        """
        self._guard(video_id)
        status = self.track_status(video_id)
        token = status.add_token if add else status.remove_token
        if not token:
            raise AccountActionError(
                tr("YouTube Music no permite guardar esta canción en la biblioteca.")
                if add else tr("YouTube Music no permite quitar esta canción de la biblioteca.")
            )
        result = self._call(lambda client: client.edit_song_library_status([token]))
        if isinstance(result, dict) and result.get("feedbackResponses"):
            if not all(isinstance(r, dict) and r.get("isProcessed", True)
                       for r in result["feedbackResponses"]):
                raise AccountActionError(tr("YouTube Music no procesó el cambio en la biblioteca."))

    def report_history(self, video_id: str) -> None:
        """Anota la escucha en el historial de YouTube (sub-opción aparte)."""
        if not history_reporting_enabled(self.db):
            raise MutationsDisabled(tr("el registro de escuchas en YouTube está desactivado"))
        self._guard(video_id)

        def work(client: Any) -> Any:
            song = client.get_song(video_id)
            return client.add_history_item(song)

        self._call(work)

    # -- listas --------------------------------------------------------------
    def create_playlist(self, title: str, video_ids: Iterable[str], description: str = "") -> str:
        self._guard()
        title = (title or "").strip()
        if not title:
            raise AccountActionError(tr("La lista necesita un nombre."))
        if "<" in title or ">" in title:
            raise AccountActionError(tr("El nombre de la lista no puede llevar < ni >."))
        ids = [v for v in dict.fromkeys(video_ids) if v and not is_local_track(v)]
        result = self._call(lambda client: client.create_playlist(
            title, description, privacy_status="PRIVATE", video_ids=ids or None))
        if not isinstance(result, str) or not result:
            raise AccountActionError(tr("YouTube Music no creó la lista."))
        return result

    def add_playlist_items(self, playlist_id: str, video_ids: Iterable[str]) -> None:
        self._guard()
        ids = [v for v in dict.fromkeys(video_ids) if v and not is_local_track(v)]
        if not ids:
            return
        result = self._call(lambda client: client.add_playlist_items(playlist_id, ids, duplicates=False))
        _expect_succeeded(result, tr("YouTube Music no añadió las canciones a la lista."))

    def remove_playlist_items(self, playlist_id: str, items: Iterable[tuple[str, str]]) -> None:
        """``items``: pares (videoId, setVideoId) tal como los guarda la sincronización."""
        self._guard()
        videos = [{"videoId": vid, "setVideoId": set_vid} for vid, set_vid in items if vid and set_vid]
        if not videos:
            raise AccountActionError(tr("No se sabe qué copia de la canción quitar: sincroniza la lista."))
        result = self._call(lambda client: client.remove_playlist_items(playlist_id, videos))
        _expect_succeeded(result, tr("YouTube Music no quitó la canción de la lista."))

    def rate_playlist(self, playlist_id: str, rating: str) -> None:
        """Guarda (``LIKE``) o quita (``INDIFFERENT``) una lista o álbum de la biblioteca."""
        if rating not in RATINGS:
            raise ValueError(tr("valoración desconocida: {rating!r}", rating=rating))
        self._guard()
        self._call(lambda client: client.rate_playlist(playlist_id, rating))


def _expect_succeeded(result: Any, message: str) -> None:
    # ytmusicapi devuelve "STATUS_SUCCEEDED" o, si algo falla, la respuesta entera.
    status = result.get("status") if isinstance(result, dict) else result
    if status != "STATUS_SUCCEEDED":
        raise AccountActionError(message)


# ---------------------------------------------------------------------------
# Lado de la interfaz
# ---------------------------------------------------------------------------
Runner = Callable[[Callable[[], Any], Callable[[Any, Optional[BaseException]], None]], None]


class AccountActions(QObject):
    """Cambios en la cuenta con interfaz optimista y vuelta atrás si fallan."""

    status_changed = Signal(str)            # videoId cuyo estado (me gusta, biblioteca) cambió
    notice = Signal(str, str)               # texto, tipo ("info" o "error")
    playlist_created = Signal(str, str)     # id de la lista nueva, título
    playlist_item_removed = Signal(str, str)  # id de la lista, videoId

    def __init__(self, db: Database, runner: Runner, client: Optional[AccountClient] = None,
                 parent: Optional[QObject] = None):
        super().__init__(parent)
        self.db = db
        self.client = client or AccountClient(db)
        self._run = runner
        self._shown: dict[tuple[str, str], Any] = {}      # lo que enseña la interfaz
        self._wanted: dict[tuple[str, str], Any] = {}     # último estado pedido aún sin enviar
        self._busy: set[tuple[str, str]] = set()          # peticiones en vuelo
        self._asked: set[str] = set()                     # estado ya preguntado en esta sesión

    # -- ajustes ---------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return mutations_enabled(self.db)

    def set_enabled(self, enabled: bool) -> None:
        self.db.set_setting(config.ACCOUNT_MUTATIONS_KEY, "1" if enabled else "0")

    @property
    def history_enabled(self) -> bool:
        return self.db.get_bool(config.ACCOUNT_HISTORY_KEY, False)

    def set_history_enabled(self, enabled: bool) -> None:
        self.db.set_setting(config.ACCOUNT_HISTORY_KEY, "1" if enabled else "0")

    def can_change(self, track: Optional[Track]) -> bool:
        return self.enabled and track is not None and not is_local_track(track.video_id)

    # -- estado que se enseña ----------------------------------------------------
    def rating(self, video_id: str) -> Optional[str]:
        key = ("rate", video_id)
        if key in self._shown:
            return self._shown[key]
        return self.db.get_account_mark(video_id)[0]

    def liked(self, video_id: str) -> bool:
        return self.rating(video_id) == LIKE

    def in_library(self, video_id: str) -> Optional[bool]:
        key = ("library", video_id)
        if key in self._shown:
            return self._shown[key]
        return self.db.get_account_mark(video_id)[1]

    def _confirmed(self, key: tuple[str, str]) -> Any:
        rating, in_library = self.db.get_account_mark(key[1])
        return rating if key[0] == "rate" else in_library

    # -- me gusta y biblioteca ---------------------------------------------------
    def set_liked(self, track: Track, liked: bool) -> bool:
        return self.rate(track, LIKE if liked else INDIFFERENT)

    def rate(self, track: Track, rating: str) -> bool:
        """``False`` si no se puede (conmutador apagado o pista del disco):
        entonces no sale nada hacia la red."""
        if rating not in RATINGS or not self._allowed(track):
            return False
        self._request(("rate", track.video_id), rating, track.title)
        return True

    def set_in_library(self, track: Track, add: bool) -> bool:
        if not self._allowed(track):
            return False
        self._request(("library", track.video_id), bool(add), track.title)
        return True

    def _allowed(self, track: Optional[Track]) -> bool:
        if track is None or is_local_track(track.video_id):
            return False
        if not self.enabled:
            self.notice.emit(tr("Activa «Permitir cambios en mi cuenta» para cambiar tu cuenta de YouTube Music."),
                             "info")
            return False
        return True

    def _request(self, key: tuple[str, str], value: Any, title: str) -> None:
        current = self.rating(key[1]) if key[0] == "rate" else self.in_library(key[1])
        if value == current and key not in self._busy:
            return
        # Interfaz optimista: el cambio se ve ya.
        self._shown[key] = value
        self._wanted[key] = value
        self.status_changed.emit(key[1])
        if key not in self._busy:
            self._send(key, title)

    def _send(self, key: tuple[str, str], title: str) -> None:
        target = self._wanted.pop(key)
        if target == self._confirmed(key):
            self._shown.pop(key, None)
            self.status_changed.emit(key[1])
            return                                        # la cuenta ya está así
        self._busy.add(key)
        kind, video_id = key
        if kind == "rate":
            work = lambda: self.client.rate_song(video_id, target)          # noqa: E731
        else:
            work = lambda: self.client.set_in_library(video_id, target)     # noqa: E731
        self._run(work, lambda _result, error: self._sent(key, target, title, error))

    def _sent(self, key: tuple[str, str], target: Any, title: str, error: Optional[BaseException]) -> None:
        self._busy.discard(key)
        kind, video_id = key
        if error is not None:
            # Vuelta atrás: la interfaz enseña otra vez lo que la cuenta tiene.
            self._wanted.pop(key, None)
            self._shown.pop(key, None)
            self.status_changed.emit(video_id)
            what = tr("el me gusta de") if kind == "rate" else tr("la biblioteca con")
            log.warning("no se pudo cambiar %s %s: %s", what, video_id, type(error).__name__)
            self.notice.emit(tr("No se pudo actualizar {what} «{title}». {describe_error}", what=what, title=title, describe_error=describe_error(error)), "error")
            return
        try:
            if kind == "rate":
                self.db.set_account_rating(video_id, target)
            else:
                self.db.set_account_library(video_id, target)
        except Exception:  # noqa: BLE001 - la cuenta ya cambió: solo se pierde la copia local
            log.exception("no se pudo guardar el estado de %s", video_id)
        if key in self._wanted:
            self._send(key, title)                         # cambió de idea mientras viajaba
        elif self._shown.get(key) == target:
            self._shown.pop(key, None)                     # ya lo dice la base
            self.status_changed.emit(video_id)

    # -- estado de la cuenta -------------------------------------------------------
    def refresh_status(self, track: Optional[Track], force: bool = False) -> bool:
        """Pregunta a la cuenta si la pista tiene me gusta y si está en la
        biblioteca (una petición, una vez por sesión y pista)."""
        if not self.can_change(track) or (track.video_id in self._asked and not force):
            return False
        video_id = track.video_id
        self._asked.add(video_id)

        def done(status: Optional[TrackStatus], error: Optional[BaseException]) -> None:
            if error is not None or status is None:
                self._asked.discard(video_id)
                log.info("estado de %s en la cuenta: %s", video_id, type(error).__name__ if error else "vacío")
                return
            try:
                if status.rating is not None and ("rate", video_id) not in self._busy:
                    self.db.set_account_rating(video_id, status.rating)
                if status.in_library is not None and ("library", video_id) not in self._busy:
                    self.db.set_account_library(video_id, status.in_library)
            except Exception:  # noqa: BLE001
                log.exception("no se pudo guardar el estado de %s", video_id)
            self.status_changed.emit(video_id)

        self._run(lambda: self.client.track_status(video_id), done)
        return True

    # -- listas --------------------------------------------------------------------
    def create_playlist(self, title: str, tracks: Iterable[Track]) -> bool:
        if not self.enabled:
            self._allowed(Track("", ""))
            return False
        ids = [t.video_id for t in tracks if not is_local_track(t.video_id)]

        def done(playlist_id: Optional[str], error: Optional[BaseException]) -> None:
            if error is not None:
                self.notice.emit(tr("No se pudo crear la lista «{title}». {describe_error}", title=title, describe_error=describe_error(error)), "error")
                return
            self.notice.emit(tr("Lista «{title}» creada en tu cuenta de YouTube Music.", title=title), "info")
            self.playlist_created.emit(playlist_id, title)

        self._run(lambda: self.client.create_playlist(title, ids), done)
        return True

    def remove_from_playlist(self, playlist_id: str, track: Track, set_video_id: str) -> bool:
        """Quita la canción de una lista de la cuenta. Es destructivo: quien
        llama ya pidió confirmación. La lista local cambia al confirmarlo la cuenta."""
        if not self._allowed(track):
            return False

        def done(_result, error: Optional[BaseException]) -> None:
            if error is not None:
                self.notice.emit(tr("No se pudo quitar «{title}» de la lista. {describe_error}", title=track.title, describe_error=describe_error(error)),
                                 "error")
                return
            self.playlist_item_removed.emit(playlist_id, track.video_id)

        self._run(lambda: self.client.remove_playlist_items(playlist_id, [(track.video_id, set_video_id)]),
                  done)
        return True

    # -- historial -----------------------------------------------------------------
    def report_history(self, track: Track) -> bool:
        """Anota la escucha en el historial de YouTube si la sub-opción está
        activa. Un fallo solo queda en el registro: no merece un aviso."""
        if track is None or is_local_track(track.video_id) or not history_reporting_enabled(self.db):
            return False

        def done(_result, error: Optional[BaseException]) -> None:
            if error is not None:
                log.info("no se pudo anotar %s en el historial de YouTube: %s",
                         track.video_id, type(error).__name__)

        self._run(lambda: self.client.report_history(track.video_id), done)
        return True
