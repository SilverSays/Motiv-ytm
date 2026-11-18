"""Sincronización de la biblioteca (``ytmusicapi`` -> SQLite).

Decisiones tomadas a partir de las observaciones del plan:

* Se compara el ``count`` nuevo con el **último ``count`` guardado**
  (``playlists.remote_count``), no con el número de filas almacenadas.
* ``count`` puede llegar como entero, cadena (``"1,234"``) o no llegar.
* El conteo no detecta reordenamientos ni +1/-1: cada lista se re-descarga
  completa cada 7-14 días (el día exacto depende del id, para repartirlas) y
  hay sincronización forzada manual.
* Cadencia: intervalo mínimo entre sincronizaciones automáticas, retraso al
  arrancar (lo pone la GUI) y pausa aleatoria entre descargas de listas.
* Sesión caducada: estado ``AUTH_EXPIRED`` persistente, sin reintentos en bucle.
* Una biblioteca remota vacía cuando la local no lo está se trata como
  sospechosa (posible sesión caducada) y **no** borra nada.
* Una biblioteca a la que de golpe le faltan la mayoría de las listas, o una
  lista que llega con muchas menos canciones de las que dice tener, tampoco
  borra nada la primera vez; solo si la siguiente respuesta es igual.
* Un 401/403 al pedir una lista concreta solo caduca la sesión si la cuenta
  ya no responde; si no, se anota en esa lista y se sigue con las demás.
"""

from __future__ import annotations

import logging
import random
import re
import threading
import time
import zlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

from ytm_native.core import config, credentials, paths
from ytm_native.core.crypto import CryptoError
from ytm_native.core.db import Database, Playlist, Track

log = logging.getLogger("ytmn.sync")

# Ajustes donde se recuerda una respuesta sospechosa hasta ver si se repite.
SHRINK_KEY = "sync_shrunk_library"
SUSPECT_KEY = "sync_suspect:"


class SyncState(str, Enum):
    IDLE = "IDLE"
    SYNCING = "SYNCING"
    AUTH_EXPIRED = "AUTH_EXPIRED"
    NO_CREDENTIALS = "NO_CREDENTIALS"
    ERROR = "ERROR"


@dataclass
class SyncReport:
    state: SyncState = SyncState.IDLE
    playlists_seen: int = 0
    playlists_downloaded: int = 0
    playlists_removed: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    message: str = ""


def parse_count(value: Any) -> Optional[int]:
    """``count`` de ytmusicapi -> entero o ``None`` (int, "5", "1,234", None...)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    digits = re.sub(r"[^\d]", "", str(value))
    return int(digits) if digits else None


class SessionExpired(Exception):
    """La cuenta ya no tiene sesión (la respuesta no lo dice con un código HTTP)."""


_AUTH_STATUSES = (401, 403)
# Solo el código de una respuesta HTTP, nunca un número suelto: "403" aparece
# también en puertos, direcciones IP o identificadores dentro del mensaje.
_HTTP_STATUS = re.compile(r"\bhttp(?:\s+error)?\s+(\d{3})\b|\bstatus(?:\s+code)?[\s:=]+(\d{3})\b")
_AUTH_PHRASES = re.compile(
    r"\bunauthori[sz]ed\b|\bforbidden\b|please provide authentication|\bsigned out\b|\blogged out\b"
)


def http_status(exc: BaseException) -> Optional[int]:
    """Código HTTP de la respuesta que provocó el error, si se sabe."""
    for source in (getattr(exc, "response", None), exc):
        for attr in ("status_code", "status", "code"):
            value = getattr(source, attr, None) if source is not None else None
            if isinstance(value, int) and not isinstance(value, bool) and 100 <= value <= 599:
                return value
    match = _HTTP_STATUS.search(str(exc).lower())
    if match:
        return int(match.group(1) or match.group(2))
    return None


def is_auth_error(exc: BaseException) -> bool:
    """¿El error dice que la cuenta no tiene permiso o sesión?

    Se mira el código de la respuesta (``requests``, ``urllib``) o el que
    ytmusicapi pone en su mensaje ("Server returned HTTP 401: ..."), y frases
    concretas de sesión cerrada.
    """
    seen: set[int] = set()
    current: Optional[BaseException] = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, SessionExpired):
            return True
        status = http_status(current)
        if status is not None:
            if status in _AUTH_STATUSES:
                return True
        elif _AUTH_PHRASES.search(str(current).lower()):
            return True
        current = current.__cause__ or current.__context__
    return False


def full_sync_interval_s(playlist_id: str) -> float:
    """Entre 7 y 14 días, estable por lista, para no re-descargar todas el mismo día."""
    span = config.FULL_SYNC_MAX_DAYS - config.FULL_SYNC_MIN_DAYS + 1
    days = config.FULL_SYNC_MIN_DAYS + zlib.crc32(playlist_id.encode("utf-8")) % span
    return days * 86400.0


def best_thumbnail(thumbnails: Any) -> str:
    """URL de la miniatura más grande de una lista de ytmusicapi."""
    if not isinstance(thumbnails, list):
        return ""
    candidates = [t for t in thumbnails if isinstance(t, dict) and t.get("url")]
    if not candidates:
        return ""
    return str(max(candidates, key=lambda t: t.get("width") or 0)["url"])


def track_from_item(item: dict) -> Optional[tuple[Track, Optional[str]]]:
    video_id = item.get("videoId")
    if not video_id:
        return None  # pista eliminada / sin id: no es reproducible
    artists = ", ".join(a.get("name", "") for a in (item.get("artists") or []) if a and a.get("name"))
    album = (item.get("album") or {}).get("name") or ""
    duration = item.get("duration_seconds")
    if duration is None and (item.get("duration") or item.get("length")):
        duration = _parse_duration(str(item.get("duration") or item.get("length")))
    track = Track(
        video_id=video_id,
        title=item.get("title") or "(sin título)",
        artists=artists,
        album=album,
        duration_s=int(duration) if duration is not None else None,
        thumb_url=best_thumbnail(item.get("thumbnails") or item.get("thumbnail")),
    )
    return track, item.get("setVideoId")


def _parse_duration(text: str) -> Optional[int]:
    parts = text.split(":")
    try:
        total = 0
        for p in parts:
            total = total * 60 + int(p)
        return total
    except ValueError:
        return None


# Cookies de vida corta (unos 10 minutos) que solo el navegador que creó la
# sesión puede renovar: Chrome en Windows liga la sesión al dispositivo (Device
# Bound Session Credentials). Una sesión copiada de ahí deja de valer en horas.
DEVICE_BOUND_COOKIES = ("__Secure-1PSIDRTS", "__Secure-3PSIDRTS")

DEVICE_BOUND_WARNING = (
    "Esta sesión viene de un navegador que la liga al dispositivo (Chrome en Windows). "
    "Fuera de ese navegador deja de valer en pocas horas. Para una sesión duradera, "
    "repite el proceso en una ventana privada de Firefox."
)


def looks_device_bound(text: str) -> bool:
    """¿Las cabeceras o el cookies.txt traen cookies ligadas al dispositivo?"""
    return any(name in (text or "") for name in DEVICE_BOUND_COOKIES)


_HEADER_TOKEN = re.compile(r"^[A-Za-z0-9!#$%&'*+^_`|~-]+$")
_HEADER_DROP = {"accept-encoding", "content-encoding", "content-length", "host", "connection", "priority"}


def sanitize_browser_headers(raw: dict) -> dict:
    """Quita lo que Chrome copia y no debe reenviarse.

    Con ``content-encoding: gzip`` el servidor espera un cuerpo comprimido y
    responde algo que no es JSON; las pseudo-cabeceras (``:path``,
    ``:authority``) acaban como claves sin sentido al pegarlas.
    """
    return {
        k: v
        for k, v in raw.items()
        if _HEADER_TOKEN.match(k) and k.lower() not in _HEADER_DROP and not k.lower().startswith("sec-")
    }


def merge_set_cookies(cookie_header: str, set_cookies: list[str]) -> tuple[str, list[str]]:
    """Aplica cabeceras ``Set-Cookie`` a una cabecera ``Cookie``.

    Devuelve la cabecera resultante y los nombres que cambiaron. YouTube rota
    varias cookies de sesión con cada respuesta; si no se guardan, la sesión
    copiada del navegador deja de valer a las pocas horas. Adaptado de
    ``refresh_youtube_cookie_jar`` de JustAnotherMusicClient (Apache-2.0).
    """
    pairs: list[list[str]] = []
    for part in (cookie_header or "").split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and name:
            pairs.append([name, value])
    changed: list[str] = []
    for raw in set_cookies:
        name, sep, value = raw.split(";", 1)[0].strip().partition("=")
        if not sep or not name:
            continue
        value = value.strip()
        existing = next((p for p in pairs if p[0] == name), None)
        if value in ("", "EXPIRED", "deleted"):
            if existing is not None:
                pairs.remove(existing)
                changed.append(name)
        elif existing is None:
            pairs.append([name, value])
            changed.append(name)
        elif existing[1] != value:
            existing[1] = value
            changed.append(name)
    return "; ".join(f"{n}={v}" for n, v in pairs), changed


class CookieKeeper:
    """Recoge las cookies que rota el servidor y las guarda en ``browser.json``."""

    def __init__(self, path, baseline: Optional[str] = None):
        self.path = path
        self.set_cookies: list[str] = []
        # Cabecera Cookie con la que se creó el cliente. Si el archivo ya trae
        # otra, el usuario importó credenciales nuevas mientras esta
        # sincronización seguía en marcha: las rotaciones de la vieja sobran.
        self.baseline = baseline

    def hook(self, response, *_args, **_kwargs):
        from urllib.parse import urlparse

        host = urlparse(getattr(response, "url", "") or "").hostname or ""
        if host == "youtube.com" or host.endswith(".youtube.com"):   # nunca de otros dominios
            try:
                self.set_cookies.extend(response.raw.headers.getlist("Set-Cookie"))
            except Exception:  # noqa: BLE001 - sin acceso a las cabeceras crudas
                pass
        return response

    def persist(self) -> list[str]:
        """Escribe las cookies rotadas, siempre cifradas (DPAPI) y de forma
        atómica. Devuelve los nombres actualizados."""
        import json

        if not self.set_cookies:
            return []
        try:
            data = json.loads(credentials.read_text(self.path))
            if not isinstance(data, dict):
                return []
            key = next((k for k in data if k.lower() == "cookie"), None)
            if key is None:
                return []
            pending, self.set_cookies = self.set_cookies, []
            if self.baseline is not None and data[key] != self.baseline:
                log.info("credenciales cambiadas durante la sincronización: se descartan sus cookies renovadas")
                return []
            merged, changed = merge_set_cookies(data[key], pending)
            if not changed or "SAPISID=" not in merged:
                return []      # nunca se guarda una cabecera que perdió la sesión
            data[key] = merged
            # Temporal, fsync y os.replace: un apagón no deja un archivo vacío,
            # y un fallo deja el anterior intacto y sin temporales.
            credentials.write_text(self.path, json.dumps(data, ensure_ascii=False, indent=1))
            return changed
        except (OSError, ValueError, credentials.CredentialError, CryptoError) as exc:
            log.warning("no se pudieron guardar las cookies renovadas: %s", type(exc).__name__)
            return []


def default_client_factory():
    """Cliente autenticado con las cabeceras (saneadas) de la cuenta secundaria."""
    import json

    import requests
    from ytmusicapi import YTMusic  # import diferido: arranque más rápido

    path = paths.browser_json()
    try:
        raw = json.loads(credentials.read_text(path))
    except credentials.CredentialError as exc:
        # De otro usuario de Windows, de otro equipo o dañado: para la app es
        # una sesión caducada que hay que renovar, no un fallo.
        raise SessionExpired("las credenciales guardadas no se pueden descifrar") from exc
    if not isinstance(raw, dict):
        raise SessionExpired("las credenciales guardadas no tienen el formato esperado")
    keeper = CookieKeeper(path, baseline=next((v for k, v in raw.items() if k.lower() == "cookie"), None))
    session = requests.Session()
    session.request = _with_timeout(session.request)
    session.hooks["response"].append(keeper.hook)
    client = YTMusic(auth=sanitize_browser_headers(raw), requests_session=session)
    client._ytmn_cookie_keeper = keeper   # noqa: SLF001 - lo recoge LibrarySync al terminar
    return client


def _with_timeout(request, seconds: float = 30.0):
    def wrapped(*args, **kwargs):
        kwargs.setdefault("timeout", seconds)
        return request(*args, **kwargs)

    return wrapped


class LibrarySync:
    def __init__(
        self,
        db: Database,
        client_factory: Callable[[], Any] = default_client_factory,
        *,
        credentials_present: Callable[[], bool] = lambda: paths.browser_json().is_file(),
        clock: Callable[[], float] = time.time,
        pause: Optional[Callable[[float], None]] = None,
        rng: Optional[random.Random] = None,
    ):
        self.db = db
        self._client_factory = client_factory
        self._credentials_present = credentials_present
        self._clock = clock
        self._pause = pause or time.sleep
        self._rng = rng or random.Random()
        self._lock = threading.Lock()

    # -- estado persistente -------------------------------------------------
    def state(self) -> SyncState:
        raw = self.db.get_setting("sync_state", SyncState.IDLE.value)
        try:
            return SyncState(raw)
        except ValueError:
            return SyncState.IDLE

    def _set_state(self, state: SyncState) -> None:
        self.db.set_setting("sync_state", state.value)

    def credentials_renewed(self) -> None:
        """Llamar cuando el usuario importa cabeceras nuevas."""
        self._set_state(SyncState.IDLE)

    def due(self) -> bool:
        """¿Toca una sincronización automática?"""
        if not self._credentials_present():
            return False
        if self.state() is SyncState.AUTH_EXPIRED:
            return False  # sin reintentos en bucle: espera a credenciales nuevas
        last = self.db.get_float("last_sync_check")
        return self._clock() - last >= config.SYNC_MIN_INTERVAL_S

    # -- ejecución ----------------------------------------------------------
    def run(
        self,
        force_all: bool = False,
        only_playlist: Optional[str] = None,
        cancel: Optional[threading.Event] = None,
    ) -> SyncReport:
        """Sincroniza (bloqueante; llamar desde un hilo de trabajo)."""
        if not self._lock.acquire(blocking=False):
            return SyncReport(state=SyncState.SYNCING, message="Ya hay una sincronización en curso.")
        try:
            return self._run(force_all, only_playlist, cancel or threading.Event())
        finally:
            self._lock.release()
            self.db.close_thread_connection()

    def _run(self, force_all: bool, only_playlist: Optional[str], cancel: threading.Event) -> SyncReport:
        report = SyncReport()
        if not self._credentials_present():
            report.state = SyncState.NO_CREDENTIALS
            report.message = "Faltan las cabeceras de ytmusicapi (browser.json)."
            self._set_state(report.state)
            return report

        self._set_state(SyncState.SYNCING)
        try:
            client = self._client_factory()
            remote = client.get_library_playlists(limit=None) or []
        except Exception as exc:  # noqa: BLE001
            return self._fail(report, exc)

        remote = [p for p in remote if p and p.get("playlistId")]
        report.playlists_seen = len(remote)
        # Las listas creadas en la app no cuentan: no vienen de la cuenta.
        local = {p.playlist_id: p for p in self.db.playlists() if not p.local}

        if not remote and self._looks_logged_out(client):
            # ytmusicapi no lanza error con la sesión cerrada: devuelve una
            # biblioteca vacía. Una cuenta con sesión siempre trae alguna lista.
            return self._fail(report, SessionExpired("la sesión está cerrada (logged out)"))

        if not remote and local:
            report.state = SyncState.ERROR
            report.message = (
                "La biblioteca remota llegó vacía; no se borró nada. "
                "Puede que la sesión haya caducado: revisa las credenciales."
            )
            self._set_state(report.state)
            return report

        now = self._clock()
        downloaded_any = False
        for position, item in enumerate(remote):
            if cancel.is_set():
                break
            pid = item["playlistId"]
            if only_playlist and pid != only_playlist:
                continue
            count = parse_count(item.get("count"))
            title = item.get("title") or "(sin título)"
            self.db.upsert_playlist_meta(pid, title, position)
            known = local.get(pid)
            if not (force_all or only_playlist or self._needs_download(known, count, now)):
                continue

            if downloaded_any:
                lo, hi = config.SYNC_PAUSE_BETWEEN_LISTS_S
                self._pause(self._rng.uniform(lo, hi))
            downloaded_any = True
            try:
                if self._sync_playlist(client, item, known, count, title, report):
                    report.playlists_downloaded += 1
            except SessionExpired as exc:
                return self._fail(report, exc)

        if not only_playlist and not cancel.is_set():
            self._remove_missing(remote, local, report)
            self.db.set_setting("last_sync_check", self._clock())

        report.state = SyncState.IDLE
        report.message = " ".join(
            [f"{report.playlists_downloaded} lista(s) actualizadas de {report.playlists_seen}."]
            + ([f"{len(report.errors)} con errores."] if report.errors else [])
            + report.warnings
        )
        self._set_state(SyncState.IDLE)
        log.info("sincronización correcta: %s", report.message)
        keeper = getattr(client, "_ytmn_cookie_keeper", None)
        if keeper is not None:
            rotated = keeper.persist()
            # Solo nombres, nunca valores: sirve para saber si el servidor rota la sesión.
            log.info("cookies renovadas y guardadas: %s", ", ".join(sorted(set(rotated))) or "ninguna")
        return report

    def _sync_playlist(
        self, client: Any, item: dict, known: Optional[Playlist], count: Optional[int],
        title: str, report: SyncReport,
    ) -> bool:
        """Descarga una lista y la guarda. Devuelve si se guardó.

        Lo que falle en esta lista se anota en ella y la sincronización sigue
        con las demás. Solo una sesión perdida (``SessionExpired``) la corta:
        un 401/403 de una lista (privada, de otra cuenta) no dice nada de la
        sesión mientras la cuenta siga respondiendo.
        """
        pid = item["playlistId"]
        try:
            data = client.get_playlist(pid, limit=None)
            if not isinstance(data, dict):
                raise ValueError("la respuesta no trae la lista")
            raw_tracks = data.get("tracks") or []
            items = [t for t in (track_from_item(i) for i in raw_tracks if isinstance(i, dict)) if t]
        except Exception as exc:  # noqa: BLE001 - una lista rota no debe frenar al resto
            if is_auth_error(exc) and self._looks_logged_out(client):
                raise SessionExpired(str(exc)) from exc
            log.warning("no se pudo descargar la lista %s: %s", pid, exc)
            report.errors.append(f"{title}: {exc}")
            self.db.set_playlist_error(pid, str(exc)[:300])
            return False

        # Si la biblioteca no informó conteo, se usa el de la propia lista.
        stored_count = count if count is not None else parse_count(data.get("trackCount"))
        suspicion = self._truncation(known, len(raw_tracks), len(items), stored_count)
        if suspicion:
            key = SUSPECT_KEY + pid
            signature = f"{len(raw_tracks)}/{stored_count}"
            if self.db.get_setting(key) != signature:
                # Primera vez: se guarda lo que había y se reintenta en la próxima.
                self.db.set_setting(key, signature)
                log.warning("lista %s sospechosa de llegar truncada: %s", pid, suspicion)
                report.errors.append(f"{title}: {suspicion}")
                self.db.set_playlist_error(pid, suspicion)
                return False
            log.warning("lista %s: la misma respuesta dos veces seguidas, se acepta (%s)", pid, suspicion)
        if self.db.get_setting(SUSPECT_KEY + pid) is not None:
            self.db.set_setting(SUSPECT_KEY + pid, None)

        self.db.replace_playlist_tracks(pid, items, stored_count, synced_at=self._clock())
        author = data.get("author")
        self.db.set_playlist_details(
            pid,
            description=str(data.get("description") or ""),
            author=str(author.get("name") or "") if isinstance(author, dict) else str(author or ""),
            year=str(data.get("year") or ""),
            duration_s=parse_count(data.get("duration_seconds")),
            thumb_url=best_thumbnail(data.get("thumbnails")) or best_thumbnail(item.get("thumbnails")),
        )
        return True

    @staticmethod
    def _truncation(known: Optional[Playlist], received: int, playable: int,
                    expected: Optional[int]) -> str:
        """Motivo para no fiarse de la lista recibida, o ``""``.

        Solo preocupa si se perderían canciones guardadas. Si el propio
        servidor dice que la lista mengua (su conteo baja), es que se borraron
        de verdad; si no da conteo, solo una lista vacía es sospechosa.
        """
        had = known.stored_tracks if known is not None else 0
        if had == 0 or playable >= had:
            return ""
        if expected is not None:
            if expected == 0 or received >= expected * config.SYNC_TRUNCATED_RATIO:
                return ""
            return (f"llegaron {received} de las {expected} canciones que dice tener; "
                    "no se borró nada y se reintentará")
        if playable > 0:
            return ""
        return f"llegó vacía y tenía {had} canciones; no se borró nada y se reintentará"

    def _remove_missing(self, remote: list[dict], local: dict[str, Playlist], report: SyncReport) -> None:
        """Borra las listas que ya no están en la cuenta, salvo que falten
        tantas de golpe que parezca una respuesta truncada: entonces se espera
        a que la siguiente sincronización traiga la misma biblioteca."""
        remote_ids = {p["playlistId"] for p in remote}
        gone = sorted(set(local) - remote_ids)
        if (gone and len(local) > config.SYNC_MASS_DELETE_MIN_LOCAL
                and len(remote_ids) < len(local) * config.SYNC_MASS_DELETE_RATIO):
            signature = f"{len(gone)}:{zlib.crc32(','.join(gone).encode('utf-8'))}"
            if self.db.get_setting(SHRINK_KEY) != signature:
                self.db.set_setting(SHRINK_KEY, signature)
                warning = (
                    f"La cuenta devolvió {len(remote_ids)} listas de las {len(local)} guardadas; "
                    "no se borró ninguna. Si las quitaste tú, desaparecerán en la próxima sincronización."
                )
                log.warning("biblioteca remota sospechosamente corta: %s", warning)
                report.warnings.append(warning)
                return
            log.warning("la biblioteca llegó corta dos veces seguidas: se borran %d listas", len(gone))
        if self.db.get_setting(SHRINK_KEY) is not None:
            self.db.set_setting(SHRINK_KEY, None)
        report.playlists_removed = self.db.delete_playlists_not_in(remote_ids)

    @staticmethod
    def _looks_logged_out(client: Any) -> bool:
        try:
            return not (client.get_account_info() or {}).get("accountName")
        except Exception:  # noqa: BLE001 - sin sesión la respuesta no trae la cuenta
            return True

    @staticmethod
    def _needs_download(known, count: Optional[int], now: float) -> bool:
        if known is None or known.last_full_sync is None:
            return True
        if known.last_error:
            return True  # falló o llegó sospechosa la última vez: se reintenta
        if known.thumb_url is None:
            return True  # sincronizada antes de guardar carátulas: una descarga para completarla
        age = now - known.last_full_sync
        if age >= full_sync_interval_s(known.playlist_id):
            return True  # compensa el punto ciego del conteo
        if count is None:
            return age >= config.NO_COUNT_RESYNC_S
        return count != known.remote_count

    def _fail(self, report: SyncReport, exc: BaseException) -> SyncReport:
        if is_auth_error(exc):
            report.state = SyncState.AUTH_EXPIRED
            report.message = "La sesión caducó: renueva las credenciales de la cuenta secundaria."
            if looks_device_bound(credentials.read_text_or_empty(paths.browser_json())):
                report.message += " " + DEVICE_BOUND_WARNING
        else:
            report.state = SyncState.ERROR
            report.message = f"Error de sincronización: {exc}"
        log.warning("sincronización falló (%s): %s", report.state.value, exc)
        self._set_state(report.state)
        return report


_anon_client: Any = None
_anon_lock = threading.Lock()


def anon_client() -> Any:
    """Cliente SIN autenticar, compartido por toda la app.

    Crear uno nuevo en cada búsqueda obliga a pedir otra vez el identificador
    de visitante (casi un segundo): con la búsqueda en vivo eso se notaría en
    cada pausa al escribir. Nunca lleva cabeceras ni cookies de la cuenta.
    """
    global _anon_client
    with _anon_lock:
        if _anon_client is None:
            import requests
            from ytmusicapi import YTMusic

            session = requests.Session()
            session.request = _with_timeout(session.request, 15.0)
            _anon_client = YTMusic(requests_session=session)
        return _anon_client


def search_songs(query: str, limit: int = 30, client_factory: Optional[Callable[[], Any]] = None) -> list[Track]:
    """Búsqueda de música nueva con un cliente SIN autenticar."""
    client = (client_factory or anon_client)()
    results = client.search(query, filter="songs", limit=limit) or []
    tracks = []
    for item in results:
        parsed = track_from_item(item)
        if parsed:
            tracks.append(parsed[0])
    return tracks


def _fold(text: str) -> list[str]:
    """Palabras en minúsculas y sin acentos, para comparar búsqueda y resultado."""
    import unicodedata

    plain = "".join(c for c in unicodedata.normalize("NFKD", text or "") if not unicodedata.combining(c))
    return re.findall(r"\w+", plain.casefold())


def match_level(query: str, track: Track) -> int:
    """0 = el resultado contiene todas las palabras buscadas; 1 = la mayoría;
    2 = alrededor de la mitad; 3 = casi ninguna.

    Las palabras se buscan en título, artistas y álbum. Un resultado vale
    también si empieza por la palabra (al escribir, la última va a medias).
    """
    wanted = list(dict.fromkeys(_fold(query)))
    if not wanted:
        return 0
    have = _fold(f"{track.title} {track.artists} {track.album}")
    found = sum(1 for word in wanted if any(h == word or h.startswith(word) for h in have))
    coverage = found / len(wanted)
    return 0 if coverage >= 0.999 else 1 if coverage >= 0.6 else 2 if coverage >= 0.34 else 3


def search_tracks(
    query: str,
    limit: int = 40,
    client_factory: Optional[Callable[[], Any]] = None,
    top: Optional[dict] = None,
) -> list[Track]:
    """Búsqueda como la de la web de YouTube Music: canciones y vídeos.

    Buscar solo "canciones" deja fuera lo que no está publicado como pista de
    álbum (bandas sonoras de videojuegos, directos, versiones de usuarios), y
    ahí la web sí acierta porque enseña primero su "resultado principal", sea
    canción o vídeo. Se hacen tres peticiones a la vez (sin filtro, canciones
    y vídeos) y se ordena así:

    1. el resultado principal de YouTube Music, si se puede reproducir;
    2. lo que contiene todas las palabras buscadas, luego la mayoría, la mitad
       y el resto; dentro de cada grupo, canciones y vídeos oficiales antes
       que las subidas de usuarios, y por lo demás el orden de YouTube Music.

    Si el resultado principal es un artista y se pasa ``top``, su respuesta
    queda en ``top["artist"]``: la búsqueda iba de él y merece su propia fila.
    """
    from concurrent.futures import ThreadPoolExecutor

    client = (client_factory or anon_client)()

    def fetch(filter_name: Optional[str]) -> list[dict]:
        if filter_name is None:
            return client.search(query) or []
        return client.search(query, filter=filter_name, limit=20) or []

    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="busqueda") as pool:
        futures = [pool.submit(fetch, name) for name in (None, "songs", "videos")]
    batches, errors = [], []
    for future in futures:
        try:
            batches.append(future.result())
        except Exception as exc:  # noqa: BLE001 - una de las tres puede fallar sin arruinar el resto
            errors.append(exc)
            batches.append([])
    if len(errors) == len(futures):
        raise errors[0]

    found: dict[str, Track] = {}
    order: list[str] = []
    amateur: set[str] = set()      # vídeos subidos por usuarios (ni canción ni vídeo oficial)
    top_id = ""
    for batch in batches:
        for item in batch:
            if (top is not None and isinstance(item, dict) and item.get("resultType") == "artist"
                    and item.get("category") == "Top result" and "artist" not in top):
                top["artist"] = item
            if not isinstance(item, dict) or item.get("resultType") not in ("song", "video"):
                continue
            if "PODCAST" in str(item.get("videoType") or ""):
                continue
            parsed = track_from_item(item)
            if parsed is None:
                continue
            track = parsed[0]
            known = found.get(track.video_id)
            if known is None:
                found[track.video_id] = track
                order.append(track.video_id)
                if item.get("resultType") == "video" and "UGC" in str(item.get("videoType") or "UGC"):
                    amateur.add(track.video_id)
                if item.get("category") == "Top result" and not top_id:
                    top_id = track.video_id
            elif known.duration_s is None and track.duration_s is not None:
                # La lista sin filtro no trae la duración: se completa con la filtrada.
                found[track.video_id] = Track(
                    known.video_id, known.title, known.artists, known.album or track.album,
                    track.duration_s, thumb_url=known.thumb_url or track.thumb_url,
                )
    # A igual coincidencia, la canción o el vídeo oficial van antes que la
    # subida de un usuario: suelen sonar mejor y traen álbum y duración.
    ranked = sorted(
        order,
        key=lambda vid: (0 if vid == top_id else 1, match_level(query, found[vid]),
                         1 if vid in amateur else 0, order.index(vid)),
    )
    return [found[vid] for vid in ranked[:limit]]


def radio_tracks(
    video_id: str, limit: int = config.RADIO_LIMIT, client_factory: Optional[Callable[[], Any]] = None
) -> list[Track]:
    """Pistas relacionadas ("radio") para una pista semilla, con un cliente SIN autenticar.

    Equivale al "Up next" que usa JustAnotherMusicClient para el autoplay.
    """
    data = (client_factory or anon_client)().get_watch_playlist(videoId=video_id, radio=True, limit=limit) or {}
    tracks, seen = [], {video_id}
    for item in data.get("tracks") or []:
        parsed = track_from_item(item)
        if parsed and parsed[0].video_id not in seen:
            seen.add(parsed[0].video_id)
            tracks.append(parsed[0])
    return tracks
