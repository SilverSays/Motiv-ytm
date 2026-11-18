"""Caché local en SQLite. Única fuente de lectura de la biblioteca para la GUI.

Hilos: las conexiones de SQLite no se comparten entre hilos. ``Database``
entrega una conexión *por hilo* (``threading.local``) en modo WAL, de modo que
el hilo de sincronización escribe mientras la GUI lee sin bloquearse. La GUI
solo necesita refrescar sus modelos al recibir la señal de fin de sincronización.
"""

from __future__ import annotations

import logging
import math
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

log = logging.getLogger("ytmn.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS playlists (
    playlist_id    TEXT PRIMARY KEY,
    title          TEXT NOT NULL,
    position       INTEGER NOT NULL DEFAULT 0,
    -- Último ``count`` recibido de get_library_playlists (NO el número de filas
    -- guardadas: con pistas eliminadas ambos números pueden no coincidir nunca).
    remote_count   INTEGER,
    last_full_sync REAL,
    last_error     TEXT,
    description    TEXT,
    author         TEXT,
    year           TEXT,
    duration_s     INTEGER,
    -- NULL = aún no se ha pedido; '' = la lista no tiene carátula.
    thumb_url      TEXT
);
CREATE TABLE IF NOT EXISTS tracks (
    video_id           TEXT PRIMARY KEY,
    title              TEXT NOT NULL,
    artists            TEXT NOT NULL DEFAULT '',
    album              TEXT NOT NULL DEFAULT '',
    duration_s         INTEGER,
    -- Un "no disponible" puede ser transitorio: se guarda desde cuándo, no un
    -- booleano permanente.
    unavailable_since  REAL,
    unavailable_reason TEXT,
    thumb_url          TEXT
);
CREATE TABLE IF NOT EXISTS playlist_tracks (
    playlist_id  TEXT NOT NULL,
    position     INTEGER NOT NULL,
    video_id     TEXT NOT NULL,
    set_video_id TEXT,
    PRIMARY KEY (playlist_id, position)
);
CREATE TABLE IF NOT EXISTS bad_versions (
    version      TEXT PRIMARY KEY,
    discarded_at REAL NOT NULL,
    reason       TEXT
);
CREATE TABLE IF NOT EXISTS resolve_log (
    ts        REAL NOT NULL,
    video_id  TEXT NOT NULL,
    mode      TEXT NOT NULL,
    outcome   TEXT NOT NULL,
    elapsed_s REAL,
    format    TEXT
);
CREATE INDEX IF NOT EXISTS idx_resolve_log_ts ON resolve_log (ts);
CREATE TABLE IF NOT EXISTS lyrics (
    video_id   TEXT PRIMARY KEY,
    fetched_at REAL NOT NULL,
    payload    TEXT            -- NULL = se buscó y no había letra
);
-- Recomendaciones de la pantalla de inicio: se guardan para pintarlas al
-- arrancar sin esperar a la red.
CREATE TABLE IF NOT EXISTS discover_cache (
    key        TEXT PRIMARY KEY,
    fetched_at REAL NOT NULL,
    payload    TEXT NOT NULL
);
-- Historial de escucha: una fila por cada vez que una canción se oyó de
-- verdad (no las que se saltan a los pocos segundos). Es local: no se envía.
CREATE TABLE IF NOT EXISTS plays (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id  TEXT NOT NULL,
    played_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_plays_at ON plays (played_at);
CREATE INDEX IF NOT EXISTS idx_plays_video ON plays (video_id);
-- Lo que la cuenta de YouTube Music dice de cada canción (me gusta y
-- biblioteca), solo de las que la app cambió o consultó. NULL = no se sabe.
CREATE TABLE IF NOT EXISTS account_marks (
    video_id   TEXT PRIMARY KEY,
    rating     TEXT,
    in_library INTEGER,
    updated_at REAL NOT NULL
);
-- Archivos de música del disco. La pista vive en ``tracks`` con el id
-- ``local:<sha1 de la ruta>``; aquí, dónde está y cómo era al leerla (si
-- tamaño y fecha no cambian, no se vuelve a leer).
CREATE TABLE IF NOT EXISTS local_files (
    video_id   TEXT PRIMARY KEY,
    path       TEXT NOT NULL,
    folder     TEXT NOT NULL,
    size       INTEGER NOT NULL,
    mtime      REAL NOT NULL,
    scanned_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_local_files_folder ON local_files (folder);
"""


@dataclass(frozen=True)
class Track:
    video_id: str
    title: str
    artists: str = ""
    album: str = ""
    duration_s: Optional[int] = None
    unavailable_since: Optional[float] = None
    unavailable_reason: Optional[str] = None
    thumb_url: str = ""


# Columnas añadidas después de la primera versión: se crean en bases existentes.
_MIGRATIONS = (
    ("playlists", "description", "TEXT"),
    ("playlists", "author", "TEXT"),
    ("playlists", "year", "TEXT"),
    ("playlists", "duration_s", "INTEGER"),
    ("playlists", "thumb_url", "TEXT"),
    ("tracks", "thumb_url", "TEXT"),
    # Cuántas veces se reprodujo la lista desde esta app: ordena "tus listas
    # principales" en la pantalla de inicio. Es local, no se envía a YouTube.
    ("playlists", "play_count", "INTEGER"),
    ("playlists", "last_played", "REAL"),
)


@dataclass(frozen=True)
class Playlist:
    playlist_id: str
    title: str
    position: int
    remote_count: Optional[int]
    last_full_sync: Optional[float]
    last_error: Optional[str]
    stored_tracks: int = 0
    description: str = ""
    author: str = ""
    year: str = ""
    duration_s: Optional[int] = None
    thumb_url: Optional[str] = None
    play_count: int = 0
    last_played: Optional[float] = None

    @property
    def local(self) -> bool:
        return is_local_playlist(self.playlist_id)


# Alta o enriquecimiento de una pista. Un dato vacío (o el título de relleno
# de la sincronización) nunca pisa uno que ya se conocía; la carátula que ya
# había se conserva. No toca ``unavailable_since``: eso lo decide el extractor.
_UPSERT_TRACK_SQL = (
    "INSERT INTO tracks (video_id, title, artists, album, duration_s, thumb_url) "
    "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(video_id) DO UPDATE SET "
    "title=CASE WHEN TRIM(excluded.title) IN ('', '(sin título)') THEN tracks.title "
    "ELSE excluded.title END, "
    "artists=COALESCE(NULLIF(excluded.artists, ''), tracks.artists), "
    "album=COALESCE(NULLIF(excluded.album, ''), tracks.album), "
    "duration_s=COALESCE(excluded.duration_s, tracks.duration_s), "
    "thumb_url=COALESCE(NULLIF(tracks.thumb_url, ''), excluded.thumb_url)"
)


def _track_params(track: Track) -> tuple:
    return (track.video_id, track.title, track.artists, track.album, track.duration_s, track.thumb_url)


def _like_prefix(prefix: str) -> str:
    """Patrón LIKE (con barra invertida como escape) para las claves que empiezan por ``prefix``."""
    return prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


# Las listas creadas en la app viven solo en esta base: su id lleva este
# prefijo y la sincronización con la cuenta nunca las toca.
LOCAL_PREFIX = "local:"


def is_local_playlist(playlist_id: Optional[str]) -> bool:
    return bool(playlist_id) and playlist_id.startswith(LOCAL_PREFIX)


class DatabaseLockTimeout(sqlite3.OperationalError):
    """Otra operación retuvo la base más tiempo del razonable.

    Hereda de ``OperationalError`` para que quien ya trata los errores de
    SQLite (base bloqueada, disco lleno) trate también este igual.
    """


class Database:
    # Espera máxima por el candado de la conexión antes de rendirse con un
    # error claro, en lugar de dejar el hilo (a veces el de la ventana) parado.
    LOCK_TIMEOUT_S = 10.0

    def __init__(self, path: Path | str):
        self._path = str(path)
        self._local = threading.local()
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        # ":memory:" es por conexión; para tests se comparte una sola conexión.
        self._shared: Optional[sqlite3.Connection] = None
        self._shared_lock = threading.RLock()
        if self._path == ":memory:":
            self._shared = self._open()
        with self._tx() as c:
            c.executescript(SCHEMA)
            for table, column, kind in _MIGRATIONS:
                existing = {row["name"] for row in c.execute(f"PRAGMA table_info({table})")}
                if column not in existing:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")

    # -- conexiones ---------------------------------------------------------
    def _open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=15, check_same_thread=False)
        try:
            conn.row_factory = sqlite3.Row
            if self._path != ":memory:":
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=15000")
        except BaseException:
            conn.close()
            raise
        return conn

    @property
    def conn(self) -> sqlite3.Connection:
        if self._shared is not None:
            return self._shared
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._open()
            self._local.conn = conn
        return conn

    def _tx(self) -> "_Tx":
        return _Tx(self)

    def transaction(self) -> "_Tx":
        """Varias escrituras juntas o ninguna (``with db.transaction() as c``)."""
        return _Tx(self)

    def close_thread_connection(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # -- settings -----------------------------------------------------------
    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self._tx() as c:
            row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row is not None and row["value"] is not None else default

    def set_setting(self, key: str, value: object) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, None if value is None else str(value)),
            )

    def get_float(self, key: str, default: float = 0.0) -> float:
        """El ajuste como número finito; ``nan``, ``inf`` o basura dan ``default``."""
        raw = self.get_setting(key)
        if raw is None:
            return default
        try:
            value = float(raw)
        except (ValueError, OverflowError):
            return default
        return value if math.isfinite(value) else default

    def get_int(self, key: str, default: int = 0) -> int:
        """El ajuste como entero. Un entero escrito tal cual se lee exacto
        (sin pasar por ``float``, que redondea a partir de 2**53)."""
        raw = self.get_setting(key)
        if raw is None:
            return default
        try:
            return int(raw.strip())
        except ValueError:
            pass
        try:
            value = float(raw)
        except (ValueError, OverflowError):
            return default
        return int(value) if math.isfinite(value) else default

    def get_bool(self, key: str, default: bool = False) -> bool:
        raw = self.get_setting(key)
        return default if raw is None else raw in ("1", "true", "True")

    # -- biblioteca ---------------------------------------------------------
    def playlists(self) -> list[Playlist]:
        with self._tx() as c:
            rows = c.execute(
                "SELECT p.*, (SELECT COUNT(*) FROM playlist_tracks pt "
                "             WHERE pt.playlist_id = p.playlist_id) AS stored "
                "FROM playlists p ORDER BY p.position, p.title"
            ).fetchall()
        return [self._playlist(r) for r in rows]

    def get_playlist(self, playlist_id: str) -> Optional[Playlist]:
        with self._tx() as c:
            row = c.execute(
                "SELECT p.*, (SELECT COUNT(*) FROM playlist_tracks pt "
                "             WHERE pt.playlist_id = p.playlist_id) AS stored "
                "FROM playlists p WHERE p.playlist_id=?",
                (playlist_id,),
            ).fetchone()
        return self._playlist(row) if row else None

    @staticmethod
    def _playlist(r: sqlite3.Row) -> Playlist:
        return Playlist(
            playlist_id=r["playlist_id"],
            title=r["title"],
            position=r["position"],
            remote_count=r["remote_count"],
            last_full_sync=r["last_full_sync"],
            last_error=r["last_error"],
            stored_tracks=r["stored"],
            description=r["description"] or "",
            author=r["author"] or "",
            year=r["year"] or "",
            duration_s=r["duration_s"],
            thumb_url=r["thumb_url"],
            play_count=r["play_count"] or 0,
            last_played=r["last_played"],
        )

    def main_playlists(self) -> list[Playlist]:
        """Las listas, primero las que más se reproducen desde esta app.

        Sin reproducciones registradas el orden es el de la biblioteca.
        """
        return sorted(self.playlists(), key=lambda p: (-p.play_count, p.position, p.title))

    def note_playlist_played(self, playlist_id: str, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        with self._tx() as c:
            c.execute(
                "UPDATE playlists SET play_count=COALESCE(play_count, 0) + 1, last_played=? "
                "WHERE playlist_id=?",
                (now, playlist_id),
            )

    def library_track_count(self) -> int:
        with self._tx() as c:
            return c.execute(
                "SELECT COUNT(DISTINCT video_id) AS n FROM playlist_tracks"
            ).fetchone()["n"]

    def library_artist_rows(self) -> list[tuple[str, int]]:
        """(artistas de la pista, reproducciones de su lista) por cada pista de la
        biblioteca. Una pista que está en dos listas cuenta dos veces: pesa más."""
        with self._tx() as c:
            rows = c.execute(
                "SELECT t.artists AS artists, COALESCE(p.play_count, 0) AS plays "
                "FROM playlist_tracks pt "
                "JOIN tracks t ON t.video_id = pt.video_id "
                "JOIN playlists p ON p.playlist_id = pt.playlist_id "
                "WHERE t.artists != ''"
            ).fetchall()
        return [(r["artists"], r["plays"]) for r in rows]

    def library_tracks_mentioning(self, artist: str, limit: int = 60) -> list[Track]:
        """Pistas de la biblioteca cuyo campo de artistas contiene ``artist``
        (quien llama comprueba que sea el artista y no parte de otro nombre)."""
        pattern = "%" + artist.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        with self._tx() as c:
            rows = c.execute(
                "SELECT DISTINCT t.* FROM playlist_tracks pt "
                "JOIN tracks t ON t.video_id = pt.video_id "
                "WHERE t.artists LIKE ? ESCAPE '\\' AND t.unavailable_since IS NULL LIMIT ?",
                (pattern, limit),
            ).fetchall()
        return [self._track(r) for r in rows]

    def upsert_playlist_meta(self, playlist_id: str, title: str, position: int) -> None:
        """Actualiza título y orden sin tocar ``remote_count`` ni ``last_full_sync``."""
        with self._tx() as c:
            c.execute(
                "INSERT INTO playlists (playlist_id, title, position) VALUES (?, ?, ?) "
                "ON CONFLICT(playlist_id) DO UPDATE SET title=excluded.title, "
                "position=excluded.position",
                (playlist_id, title, position),
            )

    def set_playlist_details(
        self,
        playlist_id: str,
        *,
        description: str = "",
        author: str = "",
        year: str = "",
        duration_s: Optional[int] = None,
        thumb_url: str = "",
    ) -> None:
        """Datos de la cabecera de la lista (llegan con la descarga completa)."""
        with self._tx() as c:
            c.execute(
                "UPDATE playlists SET description=?, author=?, year=?, duration_s=?, thumb_url=? "
                "WHERE playlist_id=?",
                (description, author, year, duration_s, thumb_url, playlist_id),
            )

    def set_playlist_error(self, playlist_id: str, error: Optional[str]) -> None:
        with self._tx() as c:
            c.execute("UPDATE playlists SET last_error=? WHERE playlist_id=?", (error, playlist_id))

    def replace_playlist_tracks(
        self,
        playlist_id: str,
        tracks: Iterable[tuple[Track, Optional[str]]],
        remote_count: Optional[int],
        synced_at: Optional[float] = None,
    ) -> int:
        """Reemplaza el contenido de una lista en una sola transacción."""
        synced_at = time.time() if synced_at is None else synced_at
        n = 0
        with self._tx() as c:
            c.execute("DELETE FROM playlist_tracks WHERE playlist_id=?", (playlist_id,))
            for track, set_video_id in tracks:
                # No se pisa unavailable_since: eso lo decide el extractor.
                c.execute(
                    "INSERT INTO tracks (video_id, title, artists, album, duration_s, thumb_url) "
                    "VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(video_id) DO UPDATE SET title=excluded.title, "
                    "artists=excluded.artists, album=excluded.album, "
                    "duration_s=COALESCE(excluded.duration_s, tracks.duration_s), "
                    "thumb_url=COALESCE(NULLIF(excluded.thumb_url, ''), tracks.thumb_url)",
                    (track.video_id, track.title, track.artists, track.album, track.duration_s,
                     track.thumb_url),
                )
                c.execute(
                    "INSERT INTO playlist_tracks (playlist_id, position, video_id, set_video_id) "
                    "VALUES (?, ?, ?, ?)",
                    (playlist_id, n, track.video_id, set_video_id),
                )
                n += 1
            c.execute(
                "UPDATE playlists SET remote_count=?, last_full_sync=?, last_error=NULL "
                "WHERE playlist_id=?",
                (remote_count, synced_at, playlist_id),
            )
        return n

    # -- listas locales ------------------------------------------------------
    def create_local_playlist(self, title: str, tracks: Iterable[Track] = (),
                              now: Optional[float] = None) -> str:
        """Crea una lista que solo existe en esta app. Devuelve su id."""
        import uuid

        now = time.time() if now is None else now
        playlist_id = LOCAL_PREFIX + uuid.uuid4().hex[:12]
        with self._tx() as c:
            # Posición negativa: las locales van antes que las de la cuenta
            # (que la sincronización numera desde 0), en orden de creación.
            first = c.execute(
                "SELECT COALESCE(MAX(position), -1000000) AS p FROM playlists WHERE playlist_id LIKE ?",
                (LOCAL_PREFIX + "%",),
            ).fetchone()["p"]
            c.execute(
                "INSERT INTO playlists (playlist_id, title, position, last_full_sync, description, "
                "author, year, thumb_url) VALUES (?, ?, ?, ?, '', '', '', '')",
                (playlist_id, title.strip() or "Lista sin nombre", first + 1, now),
            )
            # La lista y sus canciones, juntas o nada: un fallo a mitad no
            # deja una lista a medio llenar.
            self._append_tracks(c, playlist_id, tracks)
        return playlist_id

    def rename_playlist(self, playlist_id: str, title: str) -> None:
        with self._tx() as c:
            c.execute("UPDATE playlists SET title=? WHERE playlist_id=?", (title.strip(), playlist_id))

    def delete_local_playlist(self, playlist_id: str) -> bool:
        if not is_local_playlist(playlist_id):
            return False          # las de la cuenta solo desaparecen si se borran en la cuenta
        with self._tx() as c:
            c.execute("DELETE FROM playlist_tracks WHERE playlist_id=?", (playlist_id,))
            c.execute("DELETE FROM playlists WHERE playlist_id=?", (playlist_id,))
        return True

    def add_to_local_playlist(self, playlist_id: str, track: Track) -> bool:
        """Añade la pista al final. ``False`` si ya estaba (o la lista no es local)."""
        return self.add_tracks_to_local_playlist(playlist_id, [track]) == 1

    def add_tracks_to_local_playlist(self, playlist_id: str, tracks: Iterable[Track]) -> int:
        """Añade al final, en una sola transacción, las pistas que aún no
        estén. Devuelve cuántas se añadieron (0 si la lista no es local)."""
        if not is_local_playlist(playlist_id):
            return 0
        with self._tx() as c:
            return self._append_tracks(c, playlist_id, tracks)

    @staticmethod
    def _append_tracks(c: sqlite3.Connection, playlist_id: str, tracks: Iterable[Track]) -> int:
        tracks = list(tracks)
        if not tracks:
            return 0
        c.executemany(_UPSERT_TRACK_SQL, [_track_params(t) for t in tracks])
        present = {
            r["video_id"]
            for r in c.execute("SELECT video_id FROM playlist_tracks WHERE playlist_id=?", (playlist_id,))
        }
        fresh: list[str] = []
        for track in tracks:
            if track.video_id not in present:
                present.add(track.video_id)
                fresh.append(track.video_id)
        if not fresh:
            return 0
        start = c.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 AS p FROM playlist_tracks WHERE playlist_id=?",
            (playlist_id,),
        ).fetchone()["p"]
        c.executemany(
            "INSERT INTO playlist_tracks (playlist_id, position, video_id) VALUES (?, ?, ?)",
            [(playlist_id, start + i, vid) for i, vid in enumerate(fresh)],
        )
        return len(fresh)

    def remove_from_local_playlist(self, playlist_id: str, video_id: str) -> bool:
        if not is_local_playlist(playlist_id):
            return False
        with self._tx() as c:
            rows = [
                r["video_id"]
                for r in c.execute(
                    "SELECT video_id FROM playlist_tracks WHERE playlist_id=? ORDER BY position",
                    (playlist_id,),
                ).fetchall()
            ]
            if video_id not in rows:
                return False
            rows.remove(video_id)
            c.execute("DELETE FROM playlist_tracks WHERE playlist_id=?", (playlist_id,))
            c.executemany(
                "INSERT INTO playlist_tracks (playlist_id, position, video_id) VALUES (?, ?, ?)",
                [(playlist_id, i, vid) for i, vid in enumerate(rows)],
            )
        return True

    def playlist_cover(self, playlist: Playlist) -> str:
        """Carátula de la lista; una local toma la de su primera canción."""
        if playlist.thumb_url or not playlist.local:
            return playlist.thumb_url or ""
        with self._tx() as c:
            row = c.execute(
                "SELECT t.video_id, t.thumb_url FROM playlist_tracks pt "
                "JOIN tracks t ON t.video_id = pt.video_id WHERE pt.playlist_id=? "
                "ORDER BY pt.position LIMIT 1",
                (playlist.playlist_id,),
            ).fetchone()
        if row is None:
            return ""
        if row["thumb_url"] or row["video_id"].startswith(LOCAL_PREFIX):
            return row["thumb_url"] or ""         # un archivo del disco no tiene miniatura en YouTube
        return f"https://i.ytimg.com/vi/{row['video_id']}/mqdefault.jpg"

    def delete_playlists_not_in(self, keep_ids: Iterable[str]) -> int:
        keep = list(keep_ids)
        with self._tx() as c:
            marks = ",".join("?" for _ in keep) or "''"
            gone = [
                r["playlist_id"]
                for r in c.execute(
                    f"SELECT playlist_id FROM playlists WHERE playlist_id NOT IN ({marks}) "
                    "AND playlist_id NOT LIKE ?", keep + [LOCAL_PREFIX + "%"]
                ).fetchall()
            ]
            for pid in gone:
                c.execute("DELETE FROM playlist_tracks WHERE playlist_id=?", (pid,))
                c.execute("DELETE FROM playlists WHERE playlist_id=?", (pid,))
        return len(gone)

    def playlist_tracks(self, playlist_id: str) -> list[Track]:
        with self._tx() as c:
            rows = c.execute(
                "SELECT t.* FROM playlist_tracks pt JOIN tracks t ON t.video_id = pt.video_id "
                "WHERE pt.playlist_id=? ORDER BY pt.position",
                (playlist_id,),
            ).fetchall()
        return [self._track(r) for r in rows]

    def get_track(self, video_id: str) -> Optional[Track]:
        with self._tx() as c:
            row = c.execute("SELECT * FROM tracks WHERE video_id=?", (video_id,)).fetchone()
        return self._track(row) if row else None

    def upsert_track(self, track: Track) -> None:
        """Registra una pista fuera de la biblioteca (p. ej. un resultado de
        búsqueda) o completa la que ya había con los datos que traiga."""
        with self._tx() as c:
            c.execute(_UPSERT_TRACK_SQL, _track_params(track))

    @staticmethod
    def _track(r: sqlite3.Row) -> Track:
        return Track(
            video_id=r["video_id"],
            title=r["title"],
            artists=r["artists"],
            album=r["album"],
            duration_s=r["duration_s"],
            unavailable_since=r["unavailable_since"],
            unavailable_reason=r["unavailable_reason"],
            thumb_url=r["thumb_url"] or "",
        )

    def mark_unavailable(self, video_id: str, reason: str, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        with self._tx() as c:
            # Conserva la fecha original si ya estaba marcada.
            c.execute(
                "UPDATE tracks SET unavailable_since=COALESCE(unavailable_since, ?), "
                "unavailable_reason=? WHERE video_id=?",
                (now, reason, video_id),
            )

    def refresh_unavailable(self, video_id: str, reason: str, now: Optional[float] = None) -> None:
        """Tras un re-chequeo fallido: reinicia el plazo de reintento."""
        now = time.time() if now is None else now
        with self._tx() as c:
            c.execute(
                "UPDATE tracks SET unavailable_since=?, unavailable_reason=? WHERE video_id=?",
                (now, reason, video_id),
            )

    def clear_unavailable(self, video_id: str) -> None:
        with self._tx() as c:
            c.execute(
                "UPDATE tracks SET unavailable_since=NULL, unavailable_reason=NULL "
                "WHERE video_id=? AND unavailable_since IS NOT NULL",
                (video_id,),
            )

    # -- versiones descartadas de yt-dlp -----------------------------------
    def add_bad_version(self, version: str, reason: str) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO bad_versions (version, discarded_at, reason) "
                "VALUES (?, ?, ?)",
                (version, time.time(), reason),
            )

    def is_bad_version(self, version: str) -> bool:
        with self._tx() as c:
            return (
                c.execute("SELECT 1 FROM bad_versions WHERE version=?", (version,)).fetchone()
                is not None
            )

    # -- caché de letras ----------------------------------------------------
    def get_lyrics_entry(self, video_id: str) -> Optional[tuple[float, Optional[str]]]:
        """(fecha, json) si ya se buscó; ``json`` es None cuando no había letra."""
        with self._tx() as c:
            row = c.execute(
                "SELECT fetched_at, payload FROM lyrics WHERE video_id=?", (video_id,)
            ).fetchone()
        return (row["fetched_at"], row["payload"]) if row else None

    def set_lyrics_entry(self, video_id: str, payload: Optional[str]) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO lyrics (video_id, fetched_at, payload) VALUES (?, ?, ?)",
                (video_id, time.time(), payload),
            )

    # -- estado en la cuenta de YouTube Music --------------------------------
    def get_account_mark(self, video_id: str) -> tuple[Optional[str], Optional[bool]]:
        """(valoración, en la biblioteca) que se sabe de la cuenta; None = no se sabe."""
        with self._tx() as c:
            row = c.execute(
                "SELECT rating, in_library FROM account_marks WHERE video_id=?", (video_id,)
            ).fetchone()
        if row is None:
            return None, None
        in_library = row["in_library"]
        return row["rating"], None if in_library is None else bool(in_library)

    def set_account_rating(self, video_id: str, rating: Optional[str], now: Optional[float] = None) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT INTO account_marks (video_id, rating, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(video_id) DO UPDATE SET rating=excluded.rating, updated_at=excluded.updated_at",
                (video_id, rating, time.time() if now is None else now),
            )

    def set_account_library(self, video_id: str, in_library: Optional[bool],
                            now: Optional[float] = None) -> None:
        value = None if in_library is None else int(bool(in_library))
        with self._tx() as c:
            c.execute(
                "INSERT INTO account_marks (video_id, in_library, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(video_id) DO UPDATE SET in_library=excluded.in_library, "
                "updated_at=excluded.updated_at",
                (video_id, value, time.time() if now is None else now),
            )

    def playlist_set_video_id(self, playlist_id: str, video_id: str) -> Optional[str]:
        """El ``setVideoId`` (copia concreta en la lista de la cuenta) de una canción."""
        with self._tx() as c:
            row = c.execute(
                "SELECT set_video_id FROM playlist_tracks WHERE playlist_id=? AND video_id=? "
                "AND set_video_id IS NOT NULL ORDER BY position LIMIT 1",
                (playlist_id, video_id),
            ).fetchone()
        return row["set_video_id"] if row else None

    def remove_playlist_entry(self, playlist_id: str, video_id: str, set_video_id: Optional[str] = None) -> bool:
        """Quita una canción de la copia local de una lista de la cuenta (tras
        quitarla en la cuenta), sin esperar a la próxima sincronización."""
        with self._tx() as c:
            rows = c.execute(
                "SELECT video_id, set_video_id FROM playlist_tracks WHERE playlist_id=? ORDER BY position",
                (playlist_id,),
            ).fetchall()
            keep = [(r["video_id"], r["set_video_id"]) for r in rows]
            for i, (vid, set_vid) in enumerate(keep):
                if vid == video_id and (set_video_id is None or set_vid == set_video_id):
                    del keep[i]
                    break
            else:
                return False
            c.execute("DELETE FROM playlist_tracks WHERE playlist_id=?", (playlist_id,))
            c.executemany(
                "INSERT INTO playlist_tracks (playlist_id, position, video_id, set_video_id) VALUES (?, ?, ?, ?)",
                [(playlist_id, i, vid, set_vid) for i, (vid, set_vid) in enumerate(keep)],
            )
        return True

    # -- archivos de música del disco ---------------------------------------
    def local_file_path(self, video_id: str) -> Optional[str]:
        with self._tx() as c:
            row = c.execute("SELECT path FROM local_files WHERE video_id=?", (video_id,)).fetchone()
        return row["path"] if row else None

    def local_file_index(self, folder: str) -> dict[str, tuple[str, int, float]]:
        """Lo que se leyó de la carpeta: ruta -> (id, tamaño, fecha de modificación)."""
        with self._tx() as c:
            rows = c.execute(
                "SELECT video_id, path, size, mtime FROM local_files WHERE folder=?", (folder,)
            ).fetchall()
        return {r["path"]: (r["video_id"], r["size"], r["mtime"]) for r in rows}

    def save_local_files(self, folder: str, found: Iterable[tuple[Track, str, int, float]],
                         gone: Iterable[str] = (), now: Optional[float] = None) -> None:
        """Guarda lo leído de la carpeta en una sola transacción. Los datos del
        archivo mandan: pisan título, artistas, álbum, duración y carátula."""
        now = time.time() if now is None else now
        with self._tx() as c:
            for track, path, size, mtime in found:
                c.execute(
                    "INSERT INTO tracks (video_id, title, artists, album, duration_s, thumb_url) "
                    "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(video_id) DO UPDATE SET title=excluded.title, "
                    "artists=excluded.artists, album=excluded.album, duration_s=excluded.duration_s, "
                    "thumb_url=excluded.thumb_url, unavailable_since=NULL, unavailable_reason=NULL",
                    _track_params(track),
                )
                c.execute(
                    "INSERT INTO local_files (video_id, path, folder, size, mtime, scanned_at) "
                    "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(video_id) DO UPDATE SET path=excluded.path, "
                    "folder=excluded.folder, size=excluded.size, mtime=excluded.mtime, "
                    "scanned_at=excluded.scanned_at",
                    (track.video_id, path, folder, int(size), float(mtime), now),
                )
            c.executemany("DELETE FROM local_files WHERE video_id=?", [(vid,) for vid in gone])

    def forget_local_folder(self, keep_folder: Optional[str] = None) -> int:
        """Olvida los archivos de otras carpetas (al cambiar de carpeta)."""
        with self._tx() as c:
            if keep_folder is None:
                return c.execute("DELETE FROM local_files").rowcount
            return c.execute("DELETE FROM local_files WHERE folder<>?", (keep_folder,)).rowcount

    def local_tracks(self) -> list[Track]:
        """Las canciones del disco, por artista, álbum y título."""
        with self._tx() as c:
            rows = c.execute(
                "SELECT t.* FROM local_files lf JOIN tracks t ON t.video_id = lf.video_id "
                "ORDER BY t.artists COLLATE NOCASE, t.album COLLATE NOCASE, lf.path COLLATE NOCASE"
            ).fetchall()
        return [self._track(r) for r in rows]

    def local_track_count(self) -> int:
        with self._tx() as c:
            return c.execute("SELECT COUNT(*) AS n FROM local_files").fetchone()["n"]

    # -- historial de escucha ----------------------------------------------
    def log_play(self, video_id: str, now: Optional[float] = None) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT INTO plays (video_id, played_at) VALUES (?, ?)",
                (video_id, time.time() if now is None else now),
            )

    def play_stats(self, since: float = 0.0) -> tuple[int, int]:
        """(escuchas, canciones distintas) desde ``since``."""
        with self._tx() as c:
            row = c.execute(
                "SELECT COUNT(*) AS plays, COUNT(DISTINCT video_id) AS songs "
                "FROM plays WHERE played_at >= ?",
                (since,),
            ).fetchone()
        return row["plays"], row["songs"]

    def recent_plays(self, limit: int = 100) -> list[tuple[Track, float, int]]:
        """Lo último escuchado, cada canción una vez: (pista, última vez, veces)."""
        with self._tx() as c:
            rows = c.execute(
                "SELECT t.*, MAX(p.played_at) AS last_at, COUNT(*) AS times "
                "FROM plays p JOIN tracks t ON t.video_id = p.video_id "
                "GROUP BY p.video_id ORDER BY last_at DESC, MAX(p.id) DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [(self._track(r), r["last_at"], r["times"]) for r in rows]

    def top_plays(self, since: float = 0.0, limit: int = 100) -> list[tuple[Track, float, int]]:
        """Lo más escuchado desde ``since``: (pista, última vez, veces)."""
        with self._tx() as c:
            rows = c.execute(
                "SELECT t.*, MAX(p.played_at) AS last_at, COUNT(*) AS times "
                "FROM plays p JOIN tracks t ON t.video_id = p.video_id "
                "WHERE p.played_at >= ? "
                "GROUP BY p.video_id ORDER BY times DESC, last_at DESC LIMIT ?",
                (since, limit),
            ).fetchall()
        return [(self._track(r), r["last_at"], r["times"]) for r in rows]

    def played_artist_rows(self, since: float = 0.0) -> list[tuple[str, int]]:
        """(artistas de la pista, veces que se escuchó) desde ``since``."""
        with self._tx() as c:
            rows = c.execute(
                "SELECT t.artists AS artists, COUNT(*) AS times "
                "FROM plays p JOIN tracks t ON t.video_id = p.video_id "
                "WHERE p.played_at >= ? AND t.artists != '' GROUP BY p.video_id",
                (since,),
            ).fetchall()
        return [(r["artists"], r["times"]) for r in rows]

    def played_tracks_mentioning(self, artist: str, limit: int = 60) -> list[Track]:
        """Pistas del historial cuyo campo de artistas contiene ``artist``."""
        pattern = "%" + artist.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        with self._tx() as c:
            rows = c.execute(
                "SELECT DISTINCT t.* FROM plays p JOIN tracks t ON t.video_id = p.video_id "
                "WHERE t.artists LIKE ? ESCAPE '\\' AND t.unavailable_since IS NULL LIMIT ?",
                (pattern, limit),
            ).fetchall()
        return [self._track(r) for r in rows]

    def clear_history(self) -> int:
        with self._tx() as c:
            return c.execute("DELETE FROM plays").rowcount

    # -- caché de recomendaciones -------------------------------------------
    def get_discover_entry(self, key: str) -> Optional[tuple[float, str]]:
        with self._tx() as c:
            row = c.execute(
                "SELECT fetched_at, payload FROM discover_cache WHERE key=?", (key,)
            ).fetchone()
        return (row["fetched_at"], row["payload"]) if row else None

    def set_discover_entry(self, key: str, payload: str, now: Optional[float] = None) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO discover_cache (key, fetched_at, payload) VALUES (?, ?, ?)",
                (key, time.time() if now is None else now, payload),
            )

    def purge_discover_cache(self, max_age_by_prefix: dict[str, float], now: Optional[float] = None) -> int:
        """Borra las entradas de cada prefijo más viejas que su plazo. Las
        claves que no empiezan por ninguno (la del inicio) no se tocan."""
        now = time.time() if now is None else now
        removed = 0
        with self._tx() as c:
            for prefix, max_age in max_age_by_prefix.items():
                removed += c.execute(
                    "DELETE FROM discover_cache WHERE key LIKE ? ESCAPE '\\' AND fetched_at < ?",
                    (_like_prefix(prefix), now - max_age),
                ).rowcount
        return removed

    # -- registro de resoluciones (límite de tasa y diagnóstico) ----------
    def log_resolve(
        self, video_id: str, mode: str, outcome: str, elapsed_s: float, fmt: str = ""
    ) -> None:
        now = time.time()
        with self._tx() as c:
            c.execute(
                "INSERT INTO resolve_log (ts, video_id, mode, outcome, elapsed_s, format) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (now, video_id, mode, outcome, elapsed_s, fmt),
            )
            c.execute("DELETE FROM resolve_log WHERE ts < ?", (now - 30 * 86400,))

    def resolves_since(self, since_ts: float) -> int:
        with self._tx() as c:
            return c.execute(
                "SELECT COUNT(*) AS n FROM resolve_log WHERE ts >= ?", (since_ts,)
            ).fetchone()["n"]


class _Tx:
    """Transacción con commit/rollback; serializa el acceso a la conexión compartida.

    El candado solo se toma cuando ya hay conexión: si abrirla falla (archivo
    bloqueado por el antivirus o por OneDrive), la excepción sale sin dejar el
    candado tomado y el resto de hilos siguen pudiendo usar la base.
    """

    def __init__(self, db: Database):
        self._db = db
        self._conn: Optional[sqlite3.Connection] = None

    def __enter__(self) -> sqlite3.Connection:
        conn = self._db.conn
        if not self._db._shared_lock.acquire(timeout=self._db.LOCK_TIMEOUT_S):
            raise DatabaseLockTimeout(
                f"La base de datos lleva más de {self._db.LOCK_TIMEOUT_S:g} s "
                "ocupada por otra operación"
            )
        self._conn = conn
        return conn

    def __exit__(self, exc_type, exc, tb) -> None:
        conn = self._conn
        self._conn = None
        try:
            if exc_type is None:
                try:
                    conn.commit()
                except BaseException:
                    # Un commit fallido deja la transacción abierta: se deshace
                    # para que la conexión no quede envenenada para la siguiente.
                    _safe_rollback(conn)
                    raise
            else:
                _safe_rollback(conn)
        finally:
            self._db._shared_lock.release()


def _safe_rollback(conn: sqlite3.Connection) -> None:
    """Deshace sin tapar la excepción que llevó hasta aquí."""
    try:
        conn.rollback()
    except sqlite3.Error:
        log.warning("No se pudo deshacer la transacción", exc_info=True)
