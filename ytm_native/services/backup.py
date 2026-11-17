"""Copia de seguridad de lo que solo vive en esta app: listas locales,
historial de escucha y preferencias.

* Las listas de la cuenta no se copian: vuelven solas al sincronizar.
* Nunca se copian credenciales, cookies ni estados internos (bloqueos,
  actualizaciones, la cola de la última sesión): solo una lista cerrada de
  preferencias del usuario (``PREFERENCE_KEYS``).
* Importar valida el archivo entero antes de tocar nada y escribe todo en una
  sola transacción: o entra la copia completa o no cambia nada.
* Dos maneras de importar: ``MERGE`` (fusionar) añade lo que falta sin borrar
  nada ni duplicar canciones, escuchas o listas; ``REPLACE`` (sobrescribir)
  deja las listas locales, el historial y las preferencias como en la copia.
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ytm_native.core.db import (
    LOCAL_PREFIX,
    Database,
    Track,
    _UPSERT_TRACK_SQL,
    _track_params,
)

FORMAT = "ytmn-backup"
VERSION = 1
MERGE, REPLACE = "merge", "replace"
MAX_BYTES = 200 * 1024 * 1024
FILLER_TITLE = "(sin título)"       # la base no deja que pise un título conocido

# Lo que el usuario eligió en el menú, nada más.
PREFERENCE_KEYS = (
    "volume",
    "playback_mode",
    "autoplay_radio",
    "history_enabled",
    "resume_session",
    "sidebar_collapsed",
    "recent_searches",
    "equalize_volume",
    "tray_close_to_tray",
    "tray_minimize_to_tray",
    "sleep_quit_app",
)

_VIDEO_ID = re.compile(r"^[\w\-.]{1,64}$", re.ASCII)
_LOCAL_ID = re.compile(r"^" + re.escape(LOCAL_PREFIX) + r"[\w\-]{1,64}$", re.ASCII)


class BackupError(ValueError):
    """El archivo no es una copia válida (el mensaje se enseña tal cual)."""


@dataclass
class BackupPlaylist:
    playlist_id: str
    title: str
    description: str = ""
    position: int = 0
    created_at: Optional[float] = None
    play_count: int = 0
    last_played: Optional[float] = None
    tracks: list[str] = field(default_factory=list)


@dataclass
class Backup:
    exported_at: float
    tracks: dict[str, Track]
    playlists: list[BackupPlaylist]
    history: list[tuple[str, float]]
    settings: dict[str, Optional[str]]

    def summary(self) -> str:
        songs = sum(len(p.tracks) for p in self.playlists)
        return (f"{_count(len(self.playlists), 'lista', 'listas')} con "
                f"{_count(songs, 'canción', 'canciones')}, "
                f"{_count(len(self.history), 'escucha', 'escuchas')} y "
                f"{_count(len(self.settings), 'preferencia', 'preferencias')}")


@dataclass
class ImportReport:
    playlists_added: int = 0
    playlists_merged: int = 0
    tracks_added: int = 0
    plays_added: int = 0
    settings_applied: int = 0

    def summary(self) -> str:
        parts = [_count(self.playlists_added, "lista nueva", "listas nuevas")]
        if self.playlists_merged:
            parts.append(_count(self.playlists_merged, "lista completada", "listas completadas"))
        parts.append(_count(self.tracks_added, "canción añadida", "canciones añadidas"))
        parts.append(_count(self.plays_added, "escucha", "escuchas"))
        return ", ".join(parts)


def _count(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


# ---------------------------------------------------------------------------
# Exportar
# ---------------------------------------------------------------------------
def build_backup(db: Database, now: Optional[float] = None) -> dict:
    with db.transaction() as c:
        playlists = c.execute(
            "SELECT * FROM playlists WHERE substr(playlist_id, 1, ?) = ? ORDER BY position, title",
            (len(LOCAL_PREFIX), LOCAL_PREFIX),
        ).fetchall()
        entries = []
        for row in playlists:
            tracks = [r["video_id"] for r in c.execute(
                "SELECT video_id FROM playlist_tracks WHERE playlist_id=? ORDER BY position",
                (row["playlist_id"],),
            )]
            entries.append({
                "id": row["playlist_id"],
                "title": row["title"],
                "description": row["description"] or "",
                "position": row["position"],
                "created_at": row["last_full_sync"],
                "play_count": row["play_count"] or 0,
                "last_played": row["last_played"],
                "tracks": tracks,
            })
        history = [[r["video_id"], r["played_at"]] for r in c.execute(
            "SELECT video_id, played_at FROM plays ORDER BY played_at, id"
        )]
        rows = c.execute(
            "SELECT * FROM tracks WHERE video_id IN ("
            " SELECT video_id FROM playlist_tracks WHERE substr(playlist_id, 1, ?) = ?"
            " UNION SELECT video_id FROM plays)",
            (len(LOCAL_PREFIX), LOCAL_PREFIX),
        ).fetchall()
        tracks = {
            r["video_id"]: {
                "title": r["title"],
                "artists": r["artists"] or "",
                "album": r["album"] or "",
                "duration_s": r["duration_s"],
                "thumb_url": r["thumb_url"] or "",
            }
            for r in rows
        }
        marks = ",".join("?" * len(PREFERENCE_KEYS))
        settings = {
            r["key"]: r["value"]
            for r in c.execute(f"SELECT key, value FROM settings WHERE key IN ({marks})", PREFERENCE_KEYS)
            if r["value"] is not None
        }
    return {
        "format": FORMAT,
        "version": VERSION,
        "exported_at": time.time() if now is None else now,
        "tracks": tracks,
        "playlists": entries,
        "history": history,
        "settings": settings,
    }


def export_backup(db: Database, path: Path | str) -> Backup:
    """Escribe la copia en ``path`` (de golpe: o queda entera o no queda)."""
    data = build_backup(db)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".ytmn-", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return parse_backup(data)


# ---------------------------------------------------------------------------
# Leer y validar
# ---------------------------------------------------------------------------
def read_backup(path: Path | str) -> Backup:
    source = Path(path)
    try:
        size = source.stat().st_size
    except OSError as exc:
        raise BackupError(f"No se pudo abrir el archivo: {exc.strerror or exc}") from exc
    if size > MAX_BYTES:
        raise BackupError("El archivo es demasiado grande para ser una copia de esta app.")
    try:
        with open(source, encoding="utf-8") as fh:
            data = json.load(fh)
    except UnicodeDecodeError as exc:
        raise BackupError("El archivo no es texto JSON.") from exc
    except json.JSONDecodeError as exc:
        raise BackupError(f"El archivo no es JSON válido (línea {exc.lineno}).") from exc
    except OSError as exc:
        raise BackupError(f"No se pudo leer el archivo: {exc.strerror or exc}") from exc
    return parse_backup(data)


def _text(value, where: str, limit: int = 2000, optional: bool = False) -> str:
    if value is None and optional:
        return ""
    if not isinstance(value, str):
        raise BackupError(f"{where}: se esperaba texto.")
    return value[:limit]


def _number(value, where: str, optional: bool = True) -> Optional[float]:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise BackupError(f"{where}: se esperaba un número.")
    return float(value)


def _video_id(value, where: str) -> str:
    if not isinstance(value, str) or not _VIDEO_ID.match(value):
        raise BackupError(f"{where}: identificador de canción no válido.")
    return value


def parse_backup(data) -> Backup:
    """Comprueba la estructura entera y devuelve la copia normalizada.

    Nada se escribe con una copia a medias: cualquier error sale aquí, con un
    mensaje que dice dónde."""
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise BackupError("El archivo no es una copia de seguridad de YT Music Nativo.")
    version = data.get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise BackupError("La copia no dice su versión.")
    if version > VERSION:
        raise BackupError("La copia es de una versión más nueva de la app: actualízala para importarla.")
    exported_at = _number(data.get("exported_at"), "Fecha de la copia") or 0.0

    raw_tracks = data.get("tracks", {})
    if not isinstance(raw_tracks, dict):
        raise BackupError("Canciones: se esperaba un objeto.")
    tracks: dict[str, Track] = {}
    for video_id, info in raw_tracks.items():
        where = f"Canción {video_id!r}"
        _video_id(video_id, where)
        if not isinstance(info, dict):
            raise BackupError(f"{where}: se esperaba un objeto.")
        duration = _number(info.get("duration_s"), f"{where} (duración)")
        tracks[video_id] = Track(
            video_id,
            _text(info.get("title"), f"{where} (título)", 500, optional=True) or FILLER_TITLE,
            _text(info.get("artists"), f"{where} (artistas)", 500, optional=True),
            _text(info.get("album"), f"{where} (álbum)", 500, optional=True),
            int(duration) if duration is not None and duration >= 0 else None,
            thumb_url=_text(info.get("thumb_url"), f"{where} (carátula)", 2000, optional=True),
        )

    raw_playlists = data.get("playlists", [])
    if not isinstance(raw_playlists, list):
        raise BackupError("Listas: se esperaba una lista.")
    playlists: list[BackupPlaylist] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw_playlists, 1):
        where = f"Lista {index}"
        if not isinstance(entry, dict):
            raise BackupError(f"{where}: se esperaba un objeto.")
        playlist_id = entry.get("id")
        if not isinstance(playlist_id, str) or not _LOCAL_ID.match(playlist_id):
            raise BackupError(f"{where}: identificador de lista local no válido.")
        if playlist_id in seen:
            raise BackupError(f"{where}: el identificador {playlist_id!r} aparece dos veces.")
        seen.add(playlist_id)
        raw_ids = entry.get("tracks", [])
        if not isinstance(raw_ids, list):
            raise BackupError(f"{where}: sus canciones deben ser una lista.")
        ids: list[str] = []
        present: set[str] = set()
        for position, video_id in enumerate(raw_ids, 1):
            _video_id(video_id, f"{where}, canción {position}")
            if video_id not in present:           # una canción, una vez por lista
                present.add(video_id)
                ids.append(video_id)
        position = _number(entry.get("position"), f"{where} (posición)")
        play_count = _number(entry.get("play_count"), f"{where} (reproducciones)")
        playlists.append(BackupPlaylist(
            playlist_id,
            _text(entry.get("title"), f"{where} (título)", 300).strip() or "Lista sin nombre",
            _text(entry.get("description"), f"{where} (descripción)", 5000, optional=True),
            int(position) if position is not None else 0,
            _number(entry.get("created_at"), f"{where} (creación)"),
            max(0, int(play_count or 0)),
            _number(entry.get("last_played"), f"{where} (última escucha)"),
            ids,
        ))

    raw_history = data.get("history", [])
    if not isinstance(raw_history, list):
        raise BackupError("Historial: se esperaba una lista.")
    history: list[tuple[str, float]] = []
    for index, item in enumerate(raw_history, 1):
        where = f"Historial, escucha {index}"
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise BackupError(f"{where}: se esperaba [canción, fecha].")
        played_at = _number(item[1], where, optional=False)
        if played_at <= 0:
            raise BackupError(f"{where}: fecha no válida.")
        history.append((_video_id(item[0], where), played_at))

    raw_settings = data.get("settings", {})
    if not isinstance(raw_settings, dict):
        raise BackupError("Preferencias: se esperaba un objeto.")
    settings: dict[str, Optional[str]] = {}
    for key, value in raw_settings.items():
        if key not in PREFERENCE_KEYS:
            continue                    # nada que no sea una preferencia conocida
        if value is not None and not isinstance(value, (str, int, float)):
            raise BackupError(f"Preferencia {key!r}: valor no válido.")
        settings[key] = None if value is None else str(value)[:20000]
    return Backup(exported_at, tracks, playlists, history, settings)


# ---------------------------------------------------------------------------
# Importar
# ---------------------------------------------------------------------------
def _track_for(backup: Backup, video_id: str) -> Track:
    return backup.tracks.get(video_id) or Track(video_id, FILLER_TITLE)


def import_backup(db: Database, backup: Backup, strategy: str = MERGE) -> ImportReport:
    if strategy not in (MERGE, REPLACE):
        raise ValueError(f"estrategia desconocida: {strategy}")
    report = ImportReport()
    referenced = {vid for p in backup.playlists for vid in p.tracks} | {vid for vid, _ in backup.history}
    prefix = (len(LOCAL_PREFIX), LOCAL_PREFIX)
    with db.transaction() as c:
        known = {r["video_id"] for r in c.execute("SELECT video_id FROM tracks")}
        missing = sorted(referenced - known)
        c.executemany(_UPSERT_TRACK_SQL, [_track_params(_track_for(backup, vid)) for vid in sorted(referenced)])
        report.tracks_added = len(missing)

        if strategy == REPLACE:
            c.execute("DELETE FROM playlist_tracks WHERE substr(playlist_id, 1, ?) = ?", prefix)
            c.execute("DELETE FROM playlists WHERE substr(playlist_id, 1, ?) = ?", prefix)
            c.execute("DELETE FROM plays")
        existing = {
            r["playlist_id"]
            for r in c.execute("SELECT playlist_id FROM playlists WHERE substr(playlist_id, 1, ?) = ?", prefix)
        }
        next_position = c.execute(
            "SELECT COALESCE(MAX(position), -1000000) AS p FROM playlists WHERE substr(playlist_id, 1, ?) = ?",
            prefix,
        ).fetchone()["p"] + 1
        for playlist in backup.playlists:
            tracks = [_track_for(backup, vid) for vid in playlist.tracks]
            if playlist.playlist_id in existing:
                # Fusionar: la lista que ya hay conserva su nombre y su orden;
                # se le añaden al final las canciones que no tenía.
                Database._append_tracks(c, playlist.playlist_id, tracks)
                report.playlists_merged += 1
                continue
            if strategy == REPLACE:
                position = playlist.position
            else:
                position, next_position = next_position, next_position + 1
            c.execute(
                "INSERT INTO playlists (playlist_id, title, position, last_full_sync, description, "
                "author, year, thumb_url, play_count, last_played) VALUES (?, ?, ?, ?, ?, '', '', '', ?, ?)",
                (playlist.playlist_id, playlist.title, position, playlist.created_at or time.time(),
                 playlist.description, playlist.play_count, playlist.last_played),
            )
            Database._append_tracks(c, playlist.playlist_id, tracks)
            existing.add(playlist.playlist_id)
            report.playlists_added += 1

        # Una escucha es una canción en un instante: la misma no entra dos veces.
        present = set() if strategy == REPLACE else {
            (r["video_id"], round(r["played_at"], 3)) for r in c.execute("SELECT video_id, played_at FROM plays")
        }
        fresh = []
        for video_id, played_at in sorted(backup.history, key=lambda item: item[1]):
            mark = (video_id, round(played_at, 3))
            if mark not in present:
                present.add(mark)
                fresh.append((video_id, played_at))
        c.executemany("INSERT INTO plays (video_id, played_at) VALUES (?, ?)", fresh)
        report.plays_added = len(fresh)

        current = {r["key"] for r in c.execute("SELECT key FROM settings WHERE value IS NOT NULL")}
        for key, value in backup.settings.items():
            if strategy == MERGE and key in current:
                continue                # fusionar no cambia lo que ya se eligió aquí
            c.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            report.settings_applied += 1
    return report

