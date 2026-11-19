"""Scrobbling opcional a Last.fm (desactivado por defecto).

* Autenticación de aplicación de escritorio: se pide un *token*
  (``auth.getToken``), el usuario lo autoriza en la web de Last.fm y la app lo
  cambia por una clave de sesión (``auth.getSession``) que no caduca.
* La clave de sesión **nunca** se guarda en claro: va en
  ``credenciales/lastfm.bin``, cifrada con DPAPI (``core/crypto.py``) para el
  usuario de Windows. En la base de datos solo queda si la opción está activa.
* Al empezar a sonar una canción se anuncia (``track.updateNowPlaying``) y,
  cuando ha sonado la mitad o 4 minutos (lo que llegue antes), se registra
  (``track.scrobble``). Las pistas de 30 s o menos no cuentan, como pide
  Last.fm.
* Cada escucha pasa antes por una cola en SQLite: sin red, o con Last.fm
  caído, las escuchas esperan y se envían en tandas de hasta 50 cuando vuelve
  el servicio. Las de más de 14 días se descartan (Last.fm ya no las acepta).

La red siempre va en un hilo aparte: la ventana nunca espera a Last.fm.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlencode

from PySide6.QtCore import QObject, QTimer, Signal

from ytm_native.core import config, crypto, paths
from ytm_native.core.db import Database, Track
from ytm_native.core.i18n import tr

log = logging.getLogger("ytmn.lastfm")

ENABLED_KEY = "lastfm_scrobble_enabled"
SESSION_FILE = "lastfm.bin"
# Dato extra del cifrado: un blob de otro uso (las cookies) no sirve aquí.
_ENTROPY = b"ytmn-lastfm-v1"
LOCAL_TRACK_PREFIX = "local:"

# Códigos de error de la API de Last.fm.
_AUTH_CODES = {4, 9, 14, 15}           # token o sesión no válidos: hay que volver a conectar
_APP_CODES = {10, 13, 26}              # clave o firma de la aplicación no válidas
# Last.fm caído un rato o pidiendo calma: no es culpa de las escuchas.
_TRANSIENT_CODES = {8, 11, 16, 29}
AUTH, APP, RETRY = "auth", "app", "retry"

# ``runner(fn, done)``: ejecuta ``fn`` fuera del hilo de la ventana y llama a
# ``done(resultado, error)`` en él.
Runner = Callable[[Callable, Callable], None]
# ``http(method, params, timeout)`` -> respuesta JSON ya decodificada.
Http = Callable[[str, dict, float], dict]


class LastfmError(Exception):
    def __init__(self, message: str, code: int = 0, network: bool = False):
        super().__init__(message)
        self.code = code
        self.network = network

    @property
    def kind(self) -> str:
        if self.code in _AUTH_CODES:
            return AUTH
        if self.code in _APP_CODES:
            return APP
        return RETRY

    @property
    def transient(self) -> bool:
        """Sin red o el servicio no disponible un rato (no un rechazo)."""
        return self.network or self.code in _TRANSIENT_CODES


def sign(params: dict, secret: str) -> str:
    """Firma de la API: md5 de claves y valores ordenados más el secreto
    (sin ``format`` ni ``callback``)."""
    raw = "".join(f"{key}{params[key]}" for key in sorted(params) if key not in ("format", "callback"))
    return hashlib.md5((raw + secret).encode("utf-8")).hexdigest()  # noqa: S324 - lo exige la API


def _requests_http(method: str, params: dict, timeout: float) -> dict:
    import requests

    headers = {"User-Agent": config.LRCLIB_USER_AGENT}
    try:
        if method == "POST":
            resp = requests.post(config.LASTFM_API_URL, data=params, timeout=timeout, headers=headers)
        else:
            resp = requests.get(config.LASTFM_API_URL, params=params, timeout=timeout, headers=headers)
    except requests.RequestException as exc:
        raise LastfmError(tr("sin conexión con Last.fm ({name__})", name__=type(exc).__name__), network=True) from exc
    try:
        data = resp.json()
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise LastfmError(tr("respuesta inesperada de Last.fm (HTTP {status_code})", status_code=resp.status_code),
                          network=resp.status_code >= 500)
    return data


# ---------------------------------------------------------------------------
# Escuchas
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Scrobble:
    artist: str
    track: str
    album: str
    duration_s: Optional[int]
    timestamp: int                       # cuándo empezó a sonar (UTC, segundos)
    row_id: int = 0


def scrobble_threshold(duration: float) -> Optional[float]:
    """Segundos que tiene que sonar una pista para registrarse, o ``None`` si
    es demasiado corta para Last.fm. Sin duración conocida, los 4 minutos."""
    if duration and duration > 0:
        if duration <= config.LASTFM_MIN_TRACK_S:
            return None
        return min(float(config.LASTFM_SCROBBLE_MAX_S), duration / 2)
    return float(config.LASTFM_SCROBBLE_MAX_S)


def main_artist(artists: str) -> str:
    from ytm_native.services.discover import split_artists

    names = split_artists(artists)
    return names[0] if names else (artists or "").strip()


def scrobble_from(track: Track, started_at: float, duration: float = 0.0) -> Optional[Scrobble]:
    """La escucha que se enviaría de ``track``, o ``None`` si no se envía
    (pista local, sin título o sin artista)."""
    if not track.video_id or track.video_id.startswith(LOCAL_TRACK_PREFIX):
        return None
    title = (track.title or "").strip()
    artist = main_artist(track.artists)
    if not title or title == "(sin título)" or not artist:
        return None
    seconds = duration if duration and duration > 0 else (track.duration_s or 0)
    return Scrobble(
        artist=artist,
        track=title,
        album=(track.album or "").strip(),
        duration_s=int(round(seconds)) if seconds else None,
        timestamp=int(started_at),
    )


# ---------------------------------------------------------------------------
# Cliente de la API
# ---------------------------------------------------------------------------
class LastfmClient:
    def __init__(self, api_key: str, api_secret: str, session_key: str = "", http: Optional[Http] = None):
        self.api_key = api_key
        self.api_secret = api_secret
        self.session_key = session_key
        self._http = http or _requests_http

    def _call(self, method: str, params: Optional[dict] = None, post: bool = False,
              with_session: bool = False) -> dict:
        if not self.api_key or not self.api_secret:
            raise LastfmError(tr("faltan la clave y el secreto de la aplicación de Last.fm"), code=10)
        query = {key: str(value) for key, value in (params or {}).items() if value not in (None, "")}
        query["method"] = method
        query["api_key"] = self.api_key
        if with_session:
            if not self.session_key:
                raise LastfmError(tr("no hay sesión de Last.fm"), code=9)
            query["sk"] = self.session_key
        query["api_sig"] = sign(query, self.api_secret)
        query["format"] = "json"
        data = self._http("POST" if post else "GET", query, config.LASTFM_TIMEOUT_S)
        if "error" in data:
            try:
                code = int(data.get("error") or 0)
            except (TypeError, ValueError):
                code = 0
            raise LastfmError(str(data.get("message") or tr("error {code} de Last.fm", code=code)), code=code)
        return data

    def get_token(self) -> str:
        token = self._call("auth.getToken").get("token")
        if not isinstance(token, str) or not token:
            raise LastfmError(tr("Last.fm no devolvió un token"))
        return token

    def auth_url(self, token: str) -> str:
        return f"{config.LASTFM_AUTH_URL}?{urlencode({'api_key': self.api_key, 'token': token})}"

    def get_session(self, token: str) -> tuple[str, str]:
        """(usuario, clave de sesión) de un token ya autorizado."""
        session = self._call("auth.getSession", {"token": token}).get("session") or {}
        name, key = session.get("name"), session.get("key")
        if not isinstance(key, str) or not key:
            raise LastfmError(tr("Last.fm no devolvió la sesión"))
        return (name if isinstance(name, str) else ""), key

    def update_now_playing(self, entry: Scrobble) -> None:
        self._call("track.updateNowPlaying", {
            "artist": entry.artist, "track": entry.track, "album": entry.album,
            "duration": entry.duration_s,
        }, post=True, with_session=True)

    def scrobble(self, entries: list[Scrobble]) -> tuple[int, int]:
        """Envía hasta 50 escuchas de una vez. Devuelve (aceptadas, ignoradas):
        una ignorada (p. ej. con fecha imposible) no se aceptará al repetirla."""
        params: dict = {}
        for i, entry in enumerate(entries[: config.LASTFM_BATCH]):
            params[f"artist[{i}]"] = entry.artist
            params[f"track[{i}]"] = entry.track
            params[f"timestamp[{i}]"] = entry.timestamp
            params[f"album[{i}]"] = entry.album
            params[f"duration[{i}]"] = entry.duration_s
        attrs = (self._call("track.scrobble", params, post=True, with_session=True)
                 .get("scrobbles", {}).get("@attr", {}))
        try:
            return int(attrs.get("accepted", 0)), int(attrs.get("ignored", 0))
        except (TypeError, ValueError):
            return 0, 0


# ---------------------------------------------------------------------------
# Cola local de reintentos
# ---------------------------------------------------------------------------
_QUEUE_SCHEMA = """
CREATE TABLE IF NOT EXISTS lastfm_queue (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    artist     TEXT NOT NULL,
    track      TEXT NOT NULL,
    album      TEXT NOT NULL DEFAULT '',
    duration_s INTEGER,
    played_at  INTEGER NOT NULL,
    attempts   INTEGER NOT NULL DEFAULT 0,   -- rechazos de Last.fm (la falta de red no cuenta)
    last_error TEXT,
    UNIQUE (played_at, artist, track)
);
"""


class ScrobbleQueue:
    def __init__(self, db: Database):
        self.db = db
        with db.transaction() as c:
            c.executescript(_QUEUE_SCHEMA)

    def add(self, entry: Scrobble) -> None:
        with self.db.transaction() as c:
            c.execute(
                "INSERT OR IGNORE INTO lastfm_queue (artist, track, album, duration_s, played_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (entry.artist, entry.track, entry.album, entry.duration_s, entry.timestamp),
            )
            # Un tope por si Last.fm no vuelve nunca: se pierden las más viejas.
            c.execute(
                "DELETE FROM lastfm_queue WHERE id NOT IN "
                "(SELECT id FROM lastfm_queue ORDER BY played_at DESC LIMIT ?)",
                (config.LASTFM_QUEUE_MAX,),
            )

    def pending(self, limit: int = config.LASTFM_BATCH, now: Optional[float] = None) -> list[Scrobble]:
        """Las más antiguas primero. Antes se tiran las que Last.fm ya no
        aceptaría (demasiado viejas) o que rechazó demasiadas veces."""
        oldest = int((time.time() if now is None else now) - config.LASTFM_MAX_AGE_S)
        with self.db.transaction() as c:
            dropped = c.execute(
                "DELETE FROM lastfm_queue WHERE played_at < ? OR attempts >= ?",
                (oldest, config.LASTFM_MAX_ATTEMPTS),
            ).rowcount
            rows = c.execute(
                "SELECT * FROM lastfm_queue ORDER BY played_at, id LIMIT ?", (limit,)
            ).fetchall()
        if dropped:
            log.info("se descartan %d escuchas que Last.fm ya no aceptaría", dropped)
        return [
            Scrobble(r["artist"], r["track"], r["album"], r["duration_s"], r["played_at"], r["id"])
            for r in rows
        ]

    def remove(self, ids: list[int]) -> None:
        with self.db.transaction() as c:
            c.executemany("DELETE FROM lastfm_queue WHERE id=?", [(i,) for i in ids])

    def note_failure(self, ids: list[int], error: str, rejected: bool) -> None:
        with self.db.transaction() as c:
            c.executemany(
                "UPDATE lastfm_queue SET attempts = attempts + ?, last_error=? WHERE id=?",
                [(1 if rejected else 0, error[:300], i) for i in ids],
            )

    def count(self) -> int:
        with self.db.transaction() as c:
            return c.execute("SELECT COUNT(*) FROM lastfm_queue").fetchone()[0]

    def clear(self) -> None:
        with self.db.transaction() as c:
            c.execute("DELETE FROM lastfm_queue")


# ---------------------------------------------------------------------------
# Credenciales cifradas
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class LastfmCredentials:
    api_key: str = ""
    api_secret: str = ""
    user: str = ""
    session_key: str = ""

    @property
    def has_app(self) -> bool:
        return bool(self.api_key and self.api_secret)

    @property
    def connected(self) -> bool:
        return self.has_app and bool(self.session_key)


def _default_app() -> tuple[str, str]:
    """Clave y secreto de la aplicación fijados al construir (o por entorno)."""
    key = os.environ.get("YTMN_LASTFM_API_KEY") or config.LASTFM_API_KEY
    secret = os.environ.get("YTMN_LASTFM_API_SECRET") or config.LASTFM_API_SECRET
    return key, secret


class SessionStore:
    """``credenciales/lastfm.bin``: clave y secreto de la aplicación (si los
    puso el usuario), usuario y clave de sesión, todo cifrado con DPAPI."""

    def __init__(self, path: Optional[Path] = None):
        self.path = path or paths.cred_dir() / SESSION_FILE

    def load(self) -> LastfmCredentials:
        key, secret = _default_app()
        base = LastfmCredentials(api_key=key, api_secret=secret)
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return base
        except OSError:
            log.warning("no se pudo leer la sesión de Last.fm", exc_info=True)
            return base
        try:
            data = json.loads(crypto.unseal(raw, _ENTROPY).decode("utf-8"))
        except (crypto.CryptoError, ValueError, UnicodeDecodeError) as exc:
            # De otro usuario de Windows, de otro equipo o dañado: como si no
            # hubiera sesión (se pide conectar otra vez). Nunca se anotan los datos.
            log.warning("la sesión de Last.fm guardada no se puede descifrar (%s): se ignora",
                        type(exc).__name__)
            return base
        if not isinstance(data, dict):
            return base

        def text(name: str) -> str:
            value = data.get(name)
            return value if isinstance(value, str) else ""

        return LastfmCredentials(
            api_key=text("api_key") or key,
            api_secret=text("api_secret") or secret,
            user=text("user"),
            session_key=text("session_key"),
        )

    def save(self, creds: LastfmCredentials) -> None:
        default_key, default_secret = _default_app()
        data = {
            # Lo que viene fijado en la app no se copia: si cambia, manda el nuevo.
            "api_key": "" if creds.api_key == default_key else creds.api_key,
            "api_secret": "" if creds.api_secret == default_secret else creds.api_secret,
            "user": creds.user,
            "session_key": creds.session_key,
        }
        blob = crypto.seal(json.dumps(data).encode("utf-8"), _ENTROPY)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            with open(tmp, "wb") as fh:
                fh.write(blob)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        finally:
            tmp.unlink(missing_ok=True)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Servicio
# ---------------------------------------------------------------------------
def _default_runner(fn: Callable, done: Callable) -> None:
    from ytm_native.ui.widgets import run_async

    run_async(fn, done)


class LastfmScrobbler(QObject):
    """Lo que usa la app: anuncia lo que suena, encola y envía escuchas y
    lleva la conexión con la cuenta. Todo es inerte mientras la opción esté
    apagada o no haya cuenta conectada: ni cola ni peticiones."""

    changed = Signal()                   # conexión, opción o cola
    session_lost = Signal(str)           # aviso para el usuario
    halted = Signal(str)                 # la clave de la aplicación no vale

    def __init__(self, db: Database, store: Optional[SessionStore] = None,
                 http: Optional[Http] = None, runner: Optional[Runner] = None,
                 parent: Optional[QObject] = None):
        super().__init__(parent)
        self.db = db
        self.store = store or SessionStore()
        self.queue = ScrobbleQueue(db)
        self._http = http
        self._run = runner or _default_runner
        self._creds = self.store.load()
        self._enabled = db.get_bool(ENABLED_KEY, False)
        self._token = ""
        self._flushing = False
        self._halted = False             # clave de aplicación rechazada: no se insiste
        self._retry_step = 0
        self._retry = QTimer(self)
        self._retry.setSingleShot(True)
        self._retry.timeout.connect(self.flush)

    # -- estado -------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def connected(self) -> bool:
        return self._creds.connected

    @property
    def active(self) -> bool:
        return self._enabled and self._creds.connected

    @property
    def user(self) -> str:
        return self._creds.user

    @property
    def has_app_credentials(self) -> bool:
        return self._creds.has_app

    def _client(self) -> LastfmClient:
        creds = self._creds
        return LastfmClient(creds.api_key, creds.api_secret, creds.session_key, self._http)

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        self.db.set_setting(ENABLED_KEY, "1" if self._enabled else "0")
        if self._enabled:
            self.flush()
        else:
            self._retry.stop()
        self.changed.emit()

    def set_app_credentials(self, api_key: str, api_secret: str) -> None:
        self._creds = LastfmCredentials(api_key.strip(), api_secret.strip())
        self._halted = False
        self.store.save(self._creds)
        self.changed.emit()

    # -- conexión -----------------------------------------------------------
    def request_authorization(self, done: Callable[[Optional[str], Optional[Exception]], None]) -> None:
        """Pide un token y devuelve (en ``done``) la página donde autorizarlo."""
        client = self._client()

        def finished(token, error) -> None:
            if error is None:
                self._token = token
                done(client.auth_url(token), None)
            else:
                done(None, error)

        self._run(client.get_token, finished)

    def complete_authorization(self, done: Callable[[Optional[str], Optional[Exception]], None]) -> None:
        """Cambia el token autorizado por la sesión, la guarda cifrada y activa
        el envío. ``done(usuario, error)``."""
        token, self._token = self._token, ""
        if not token:
            done(None, LastfmError(tr("no hay ninguna autorización pendiente"), code=14))
            return
        client = self._client()

        def finished(result, error) -> None:
            if error is not None:
                done(None, error)
                return
            user, key = result
            creds = replace(self._creds, user=user, session_key=key)
            try:
                self.store.save(creds)
            except (OSError, crypto.CryptoError) as exc:
                log.warning("no se pudo guardar la sesión de Last.fm (%s)", type(exc).__name__)
                done(None, exc)
                return
            self._creds = creds
            self._halted = False
            self.set_enabled(True)
            done(user, None)

        self._run(lambda: client.get_session(token), finished)

    def disconnect(self) -> None:
        """Olvida la sesión (la clave de la aplicación se queda). Lo que
        esperaba en la cola se descarta: era de esa cuenta."""
        self._retry.stop()
        self._creds = replace(self._creds, user="", session_key="")
        if self._creds.api_key or self._creds.api_secret:
            self.store.save(self._creds)
        else:
            self.store.clear()
        self.queue.clear()
        self.set_enabled(False)

    def _lose_session(self, error: Exception) -> None:
        log.warning("Last.fm rechazó la sesión (%s): hay que volver a conectar", error)
        self._retry.stop()
        self._creds = replace(self._creds, session_key="")
        try:
            self.store.save(self._creds)
        except (OSError, crypto.CryptoError):
            log.warning("no se pudo actualizar la sesión de Last.fm guardada")
        self.changed.emit()
        self.session_lost.emit(tr("Last.fm ya no acepta la sesión: vuelve a conectar la cuenta. "
                               "Tus escuchas esperan en la cola."))

    # -- escuchas -----------------------------------------------------------
    def now_playing(self, track: Track, duration: float = 0.0) -> None:
        if not self.active or self._halted:
            return
        entry = scrobble_from(track, time.time(), duration)
        if entry is None:
            return
        client = self._client()

        def finished(_result, error) -> None:
            if isinstance(error, LastfmError) and error.kind == AUTH:
                self._lose_session(error)
            elif error is not None:
                log.debug("no se pudo anunciar la canción en Last.fm: %s", error)

        self._run(lambda: client.update_now_playing(entry), finished)

    def submit(self, track: Track, started_at: float, duration: float = 0.0) -> bool:
        """Encola la escucha y la envía (con lo que esperase). ``True`` si se encoló."""
        if not self.active:
            return False
        entry = scrobble_from(track, started_at, duration)
        if entry is None:
            return False
        self.queue.add(entry)
        self.flush()
        return True

    def flush(self) -> None:
        """Envía una tanda de la cola; al terminar sigue con la siguiente."""
        if self._flushing or not self.active or self._halted:
            return
        batch = self.queue.pending()
        if not batch:
            self._retry_step = 0
            return
        self._flushing = True
        self._retry.stop()
        client = self._client()
        self._run(lambda: client.scrobble(batch), lambda result, error: self._flushed(batch, result, error))

    def _flushed(self, batch: list[Scrobble], result, error) -> None:
        self._flushing = False
        ids = [entry.row_id for entry in batch]
        if error is None:
            accepted, ignored = result if isinstance(result, tuple) else (len(batch), 0)
            if ignored:
                log.info("Last.fm ignoró %d de %d escuchas", ignored, len(batch))
            self.queue.remove(ids)
            self._retry_step = 0
            self.changed.emit()
            if self.queue.count():
                QTimer.singleShot(0, self.flush)
            return
        kind = error.kind if isinstance(error, LastfmError) else RETRY
        transient = not isinstance(error, LastfmError) or error.transient
        # Sin red las escuchas pueden esperar días: solo cuentan los rechazos.
        self.queue.note_failure(ids, str(error), rejected=kind == RETRY and not transient)
        if kind == AUTH:
            self._lose_session(error)
            return
        if kind == APP:
            self._halted = True
            log.warning("Last.fm rechazó la clave de la aplicación: %s", error)
            self.halted.emit(tr("Last.fm rechazó la clave de la aplicación: revisa la clave y el secreto."))
            return
        delays = config.LASTFM_RETRY_DELAYS_S
        delay = delays[min(self._retry_step, len(delays) - 1)]
        self._retry_step += 1
        log.info("no se pudieron enviar %d escuchas a Last.fm (%s); reintento en %d s",
                 len(batch), error, delay)
        self._retry.start(int(delay * 1000))

    def stop(self) -> None:
        self._retry.stop()
