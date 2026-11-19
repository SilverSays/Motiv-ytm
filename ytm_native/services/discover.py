"""Descubrir: sugerencias de búsqueda y recomendaciones para la pantalla de inicio.

Todo se pide con el cliente SIN autenticar. Los "gustos" no salen de la cuenta
sino de la biblioteca ya guardada en SQLite: qué artistas se repiten en las
listas y qué listas se reproducen más desde esta app. Con eso se buscan listas
de la comunidad, de dos maneras:

* por cada lista principal, las listas que YouTube Music relaciona con una de
  sus canciones (la pestaña "Relacionado" de la pista);
* por cada artista favorito, lo mismo a partir de una canción suya y, si no
  hay nada relacionado, las listas de la comunidad que lo nombran en el título.

También cuenta lo que se ha escuchado hace poco en la app (el historial
local), esté o no en una lista: así hay recomendaciones propias sin cuenta.

Sin biblioteca ni historial se enseñan las listas generales de la portada.

No depende de Qt: se prueba con clientes falsos.
"""

from __future__ import annotations

import json
import logging
import random
import re
import threading
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterable, Optional

from ytm_native.core import config
from ytm_native.core.db import Database, Track
from ytm_native.services.sync import (
    _fold,
    _with_timeout,
    anon_client,
    best_thumbnail,
    http_status,
    parse_count,
    track_from_item,
)
from ytm_native.core import i18n
from ytm_native.core.i18n import tr

log = logging.getLogger("ytmn.discover")

HOME_CACHE_KEY = "inicio"
NOW_CACHE_PREFIX = "suena:"
ARTIST_CACHE_PREFIX = "artista:"
CACHE_PURGED_KEY = "discover_cache_purged_at"
CACHE_LANG_KEY = "discover_cache_lang"         # idioma de la última purga por idioma
RECENT_KEY = "recent_searches"


@dataclass(frozen=True)
class PlaylistCard:
    """Una lista ajena a la biblioteca, tal como se enseña en una tarjeta."""

    playlist_id: str
    title: str
    note: str = ""
    thumb_url: str = ""


@dataclass(frozen=True)
class Shelf:
    """Una fila de recomendaciones con su porqué."""

    title: str
    note: str
    cards: tuple[PlaylistCard, ...]


@dataclass
class RemotePlaylist:
    playlist_id: str
    title: str
    author: str = ""
    description: str = ""
    year: str = ""
    duration_s: Optional[int] = None
    thumb_url: str = ""
    tracks: list[Track] = field(default_factory=list)


@dataclass(frozen=True)
class Taste:
    """Qué artistas pesan más: (nombre, nº de canciones suyas en la biblioteca).

    Un artista que solo sale del historial de escucha lleva 0 canciones."""

    artists: tuple[tuple[str, int], ...] = ()


# ---------------------------------------------------------------------------
# Sugerencias y búsquedas recientes
# ---------------------------------------------------------------------------
def search_suggestions(
    query: str, limit: int = 8, client_factory: Optional[Callable[[], Any]] = None
) -> list[str]:
    """Lo que YouTube Music propone para completar ``query`` (sin cuenta)."""
    query = (query or "").strip()
    if not query:
        return []
    raw = (client_factory or anon_client)().get_search_suggestions(query) or []
    out: list[str] = []
    seen = {query.casefold()}       # repetir lo que ya está escrito no ayuda
    for item in raw:
        text = item.get("text") if isinstance(item, dict) else item
        text = " ".join(str(text or "").split())
        if text and text.casefold() not in seen:
            seen.add(text.casefold())
            out.append(text)
        if len(out) >= limit:
            break
    return out


def recent_searches(db: Database) -> list[str]:
    try:
        data = json.loads(db.get_setting(RECENT_KEY) or "[]")
    except ValueError:
        return []
    return [str(x) for x in data if isinstance(x, str) and x.strip()][: config.RECENT_SEARCHES]


def remember_search(db: Database, query: str) -> list[str]:
    """Pone ``query`` al frente de las búsquedas recientes (solo en este equipo)."""
    query = " ".join((query or "").split())
    if not query:
        return recent_searches(db)
    kept = [q for q in recent_searches(db) if q.casefold() != query.casefold()]
    recent = ([query] + kept)[: config.RECENT_SEARCHES]
    db.set_setting(RECENT_KEY, json.dumps(recent, ensure_ascii=False))
    return recent


# ---------------------------------------------------------------------------
# Gustos
# ---------------------------------------------------------------------------
_NOT_AN_ARTIST = re.compile(
    r"^(various artists|varios artistas|unknown artist|artista desconocido)$"
    r"|\b(views|vistas|visualizaciones|reproducciones)$",
    re.IGNORECASE,
)


# Nombres oficiales que llevan coma: unidos con ", " a otros artistas serían
# indistinguibles de dos artistas, así que se reconocen por su nombre.
_COMMA_NAMES = (
    "Tyler, The Creator",
    "Earth, Wind & Fire",
    "Crosby, Stills & Nash",
    "Crosby, Stills, Nash & Young",
    "Emerson, Lake & Palmer",
    "Blood, Sweat & Tears",
    "Peter, Paul and Mary",
    "Peter, Paul & Mary",
    "Now, Now",
    "Hey, Ocean!",
)
_COMMA_PARTS = sorted(
    ([part.strip().casefold() for part in name.split(",")] for name in _COMMA_NAMES),
    key=len, reverse=True,
)


def _join_comma_names(pieces: list[str]) -> list[str]:
    """Vuelve a unir los trozos que forman un nombre con coma de ``_COMMA_NAMES``."""
    folded = [p.casefold() for p in pieces]
    names: list[str] = []
    i = 0
    while i < len(pieces):
        for parts in _COMMA_PARTS:
            if folded[i:i + len(parts)] == parts:
                names.append(", ".join(pieces[i:i + len(parts)]))
                i += len(parts)
                break
        else:
            names.append(pieces[i])
            i += 1
    return names


def split_artists(artists: str) -> list[str]:
    """``"A, B"`` -> ``["A", "B"]``, sin entradas que no son artistas.

    Un nombre que lleva coma ("Tyler, The Creator") no se parte, y uno de una
    sola letra o cifra ("X", "7") es un artista como cualquier otro."""
    pieces = [p.strip() for p in (artists or "").split(",")]
    names = []
    for raw in _join_comma_names([p for p in pieces if p]):
        name = re.sub(r"\s+-\s+Topic$", "", raw)
        # Lo que no tiene ni una letra ni una cifra ("&", "-") no es un nombre.
        if any(c.isalnum() for c in name) and not _NOT_AN_ARTIST.search(name):
            names.append(name)
    return names


def taste(db: Database, limit: int = 8, now: Optional[float] = None) -> Taste:
    """Artistas que más pesan: cuántas canciones tienen y en qué listas están.

    El primer artista de cada pista pesa entero y los invitados la mitad; las
    listas que se reproducen más desde la app multiplican el peso de lo suyo.
    Lo escuchado en los últimos meses suma por su cuenta: media canción por
    cada escucha (con tope), aunque el artista no esté en ninguna lista.
    """
    weight: Counter = Counter()
    songs: Counter = Counter()
    label: dict[str, str] = {}
    for artists, plays in db.library_artist_rows():
        boost = 1.0 + min(plays, 12) * 0.25
        for position, name in enumerate(split_artists(artists)):
            key = name.casefold()
            label.setdefault(key, name)
            weight[key] += boost * (1.0 if position == 0 else 0.5)
            songs[key] += 1
    since = (time.time() if now is None else now) - config.HISTORY_TASTE_DAYS * 86400
    for artists, times in db.played_artist_rows(since):
        heard = min(times, 12) * 0.5
        for position, name in enumerate(split_artists(artists)):
            key = name.casefold()
            label.setdefault(key, name)
            weight[key] += heard * (1.0 if position == 0 else 0.5)
    ranked = sorted(weight, key=lambda k: (-weight[k], label[k]))
    return Taste(tuple((label[k], songs[k]) for k in ranked[:limit]))


# ---------------------------------------------------------------------------
# De la respuesta de ytmusicapi a tarjetas
# ---------------------------------------------------------------------------
def _playlist_id(item: dict) -> str:
    pid = str(item.get("playlistId") or "")
    if not pid:
        browse = str(item.get("browseId") or "")
        pid = browse[2:] if browse.startswith("VL") else ""
    return pid


def _card_note(item: dict) -> str:
    author = item.get("author")
    if isinstance(author, list):          # algunas respuestas traen [{name, id}]
        author = ", ".join(a.get("name", "") for a in author if isinstance(a, dict))
    elif isinstance(author, dict):
        author = author.get("name")
    if author:
        return tr("De {author}", author=author)
    description = str(item.get("description") or "")
    parts = [p.strip() for p in description.split("•")]
    if len(parts) >= 2:                   # "Playlist • Autor • 20K views"
        return tr("De {value}", value=parts[1]) if parts[1] else ""
    return description                    # portada: "Artista, artista, artista"


def card_from_item(item: Any) -> Optional[PlaylistCard]:
    if not isinstance(item, dict):
        return None
    pid = _playlist_id(item)
    title = str(item.get("title") or "").strip()
    if not pid or not title:
        return None
    return PlaylistCard(pid, title, _card_note(item), best_thumbnail(item.get("thumbnails")))


def _take_cards(items: Iterable[Any], seen: set[str]) -> tuple[PlaylistCard, ...]:
    """Tarjetas nuevas: ni de la biblioteca, ni ya enseñadas en otra fila, ni
    repetidas dentro de esta (la misma lista puede llegar dos veces)."""
    cards = []
    taken: set[str] = set()
    for item in items or []:
        card = card_from_item(item)
        if card is None or card.playlist_id in seen or card.playlist_id in taken:
            continue
        taken.add(card.playlist_id)
        cards.append(card)
        if len(cards) >= config.HOME_SHELF_MAX_CARDS:
            break
    if len(cards) < config.HOME_SHELF_MIN_CARDS:
        return ()
    seen.update(c.playlist_id for c in cards)
    return tuple(cards)


def related_playlists(client: Any, video_id: str) -> list[dict]:
    """Listas que YouTube Music relaciona con una canción (dos peticiones)."""
    watch = client.get_watch_playlist(videoId=video_id, radio=True, limit=1) or {}
    browse_id = watch.get("related")
    if not browse_id:
        return []
    try:
        sections = client.get_song_related(browse_id) or []
    except (KeyError, IndexError, TypeError):
        # "No hay contenido adicional para esta canción": la respuesta llega
        # sin secciones y ytmusicapi no la sabe leer. No es un fallo de red.
        return []
    found: list[dict] = []
    for section in sections:
        contents = section.get("contents") if isinstance(section, dict) else None
        if isinstance(contents, list):
            # Por forma y no por título: el título cambia con el idioma.
            found.extend(c for c in contents if isinstance(c, dict) and c.get("playlistId"))
    return found


def community_playlists(client: Any, artist: str) -> list[dict]:
    return client.search(artist, filter="community_playlists", limit=20) or []


# ---------------------------------------------------------------------------
# Recomendaciones del inicio
# ---------------------------------------------------------------------------
_generic_client: Any = None
_generic_lock = threading.Lock()


def generic_client() -> Any:
    """Cliente anónimo en español: solo para la portada general, cuyos títulos
    de sección se enseñan tal cual llegan."""
    global _generic_client
    with _generic_lock:
        if _generic_client is None:
            import requests
            from ytmusicapi import YTMusic

            session = requests.Session()
            session.request = _with_timeout(session.request, 15.0)
            _generic_client = YTMusic(requests_session=session, language=i18n.active())
        return _generic_client


def _seed_track(tracks: list[Track], rng: random.Random) -> Optional[Track]:
    usable = [t for t in tracks[:40] if t.unavailable_since is None]
    return rng.choice(usable) if usable else None


def _artist_seed(db: Database, name: str, rng: random.Random) -> Optional[Track]:
    """Una canción de ese artista, mejor si es el principal: de la biblioteca
    y, si allí no hay ninguna, de lo escuchado."""
    key = name.casefold()
    for source in (db.library_tracks_mentioning, db.played_tracks_mentioning):
        lead, featured = [], []
        for track in source(name):
            names = [n.casefold() for n in split_artists(track.artists)]
            if names[:1] == [key]:
                lead.append(track)
            elif key in names:
                featured.append(track)
        pool = lead or featured
        if pool:
            return rng.choice(pool)
    return None


_AFTER_NAME = re.compile(
    r"\s+(mix|playlist|lista|best|greatest|grandes|éxitos|exitos|hits|songs|canciones|lo mejor|top|"
    r"essentials|radio)\b"
)


def _titled_like(items: Iterable[Any], artist: str) -> list[Any]:
    """Solo las listas cuyo título es el nombre del artista o empieza por él
    seguido de algo que no forma otro nombre ("Air: lo mejor", no "Air Supply")."""
    key = artist.casefold()
    kept = []
    for item in items or []:
        title = str(item.get("title") or "").strip().casefold() if isinstance(item, dict) else ""
        if not title.startswith(key):
            continue
        rest = title[len(key):]
        # Vale el nombre a secas, seguido de puntuación o de una palabra de lista.
        if not rest or _AFTER_NAME.match(rest) or not re.match(r"\s*\w", rest):
            kept.append(item)
    return kept


def _pick_artists(artists: tuple[tuple[str, int], ...], count: int, rng: random.Random):
    """El favorito siempre; el resto, al azar entre los siguientes, para que
    dos renovaciones seguidas no enseñen lo mismo."""
    if len(artists) <= count:
        return list(artists)
    rest = list(artists[1:])
    rng.shuffle(rest)
    return [artists[0]] + rest[: count - 1]


def build_recommendations(
    db: Database,
    *,
    client_factory: Optional[Callable[[], Any]] = None,
    generic_factory: Optional[Callable[[], Any]] = None,
    on_shelf: Optional[Callable[[Shelf], None]] = None,
    rng: Optional[random.Random] = None,
) -> tuple[str, list[Shelf]]:
    """Arma las filas de recomendaciones (bloqueante: llamar desde un hilo).

    Devuelve ``("gustos" | "general", filas)``. ``on_shelf`` recibe cada fila
    en cuanto está lista. Si todas las peticiones fallan, propaga el último
    error: sin red no hay nada que enseñar y la GUI debe poder decirlo.
    """
    rng = rng or random.Random()
    client = (client_factory or anon_client)()
    playlists = [p for p in db.main_playlists() if p.stored_tracks]
    seen = {p.playlist_id for p in db.playlists()}
    shelves: list[Shelf] = []
    last_error: Optional[Exception] = None

    def add(title: str, note: str, items: Iterable[Any]) -> None:
        cards = _take_cards(items, seen)
        if cards:
            shelf = Shelf(title, note, cards)
            shelves.append(shelf)
            if on_shelf is not None:
                on_shelf(shelf)

    for playlist in playlists[: config.HOME_SEED_PLAYLISTS]:
        tracks = db.playlist_tracks(playlist.playlist_id)
        before = len(shelves)
        # No toda canción tiene listas relacionadas: si la primera no da nada,
        # se prueba con otra de la misma lista.
        for _attempt in range(2):
            seed = _seed_track(tracks, rng)
            if seed is None or len(shelves) > before:
                break
            tracks = [t for t in tracks if t.video_id != seed.video_id]
            try:
                items = related_playlists(client, seed.video_id)
            except Exception as exc:  # noqa: BLE001 - una fila rota no frena a las demás
                log.warning("relacionadas de %s: %s", seed.video_id, exc)
                last_error = exc
                break
            by = tr(", de {artists}", artists=seed.artists) if seed.artists else ""
            add(tr("Parecidas a «{title}»", title=playlist.title), tr("Elegidas a partir de «{title}»{by}", title=seed.title, by=by), items)

    for name, songs in _pick_artists(taste(db).artists, config.HOME_SEED_ARTISTS, rng):
        count = tr("una canción") if songs == 1 else tr("{songs} canciones", songs=songs)
        note = tr("Está en {count} de tus listas", count=count) if songs else tr("Lo has escuchado hace poco")
        before = len(shelves)
        try:
            # Primero lo relacionado con una canción suya: sirve para descubrir
            # y no depende de cómo se llame el artista.
            seed = _artist_seed(db, name, rng)
            if seed is not None:
                add(tr("Porque escuchas a {name}", name=name), note, related_playlists(client, seed.video_id))
            if len(shelves) == before:
                # Sin relacionadas: listas que lo nombran en el título. Buscar
                # "Air" a secas trae también "Air Supply" o "On Air".
                add(tr("Listas de la comunidad con {name}", name=name), note,
                    _titled_like(community_playlists(client, name), name))
        except Exception as exc:  # noqa: BLE001
            log.warning("listas de la comunidad de %s: %s", name, exc)
            last_error = exc

    if shelves:
        return "gustos", shelves
    if last_error is not None:
        raise last_error

    # Sin biblioteca (o sin nada parecido): la portada general, sin cuenta.
    for section in (generic_factory or generic_client)().get_home(limit=4) or []:
        if isinstance(section, dict) and isinstance(section.get("contents"), list):
            add(str(section.get("title") or tr("Para empezar")), "", section["contents"])
    return "general", shelves


def _with_language(data: dict) -> dict:
    """Lo que se guarda en la caché lleva el idioma en que se pidió: sus
    títulos, notas y datos (y lo que devuelve YouTube Music) van en él."""
    data["lang"] = i18n.active()
    return data


def _same_language(data: dict) -> bool:
    """¿Se guardó en el idioma de ahora? Lo de antes del inglés no lo dice:
    era español. Al cambiar de idioma, lo guardado se vuelve a pedir."""
    return data.pop("lang", i18n.SPANISH) == i18n.active()


def save_shelves(db: Database, kind: str, shelves: list[Shelf], now: Optional[float] = None) -> None:
    payload = _with_language({"kind": kind, "shelves": [asdict(s) for s in shelves]})
    db.set_discover_entry(HOME_CACHE_KEY, json.dumps(payload, ensure_ascii=False), now)


def load_shelves(db: Database) -> Optional[tuple[float, str, list[Shelf]]]:
    """``(fecha, tipo, filas)`` de la última vez, o ``None``."""
    entry = db.get_discover_entry(HOME_CACHE_KEY)
    if entry is None:
        return None
    fetched_at, raw = entry
    try:
        data = json.loads(raw)
        if not _same_language(data):
            return None
        shelves = [
            Shelf(s["title"], s.get("note", ""), tuple(PlaylistCard(**c) for c in s["cards"]))
            for s in data["shelves"]
        ]
        return fetched_at, str(data.get("kind") or "gustos"), shelves
    except (ValueError, KeyError, TypeError):
        return None


def shelves_stale(
    db: Database, cached: Optional[tuple[float, str, list[Shelf]]], now: Optional[float] = None
) -> bool:
    """¿Toca renovar las recomendaciones guardadas?"""
    if cached is None:
        return True
    fetched_at, kind, shelves = cached
    now = time.time() if now is None else now
    if now - fetched_at >= config.HOME_RECS_TTL_S:
        return True
    if kind != "general":
        return False
    # Se guardó la portada general y ya hay biblioteca: toca personalizar.
    if any(p.stored_tracks for p in db.playlists()):
        return True
    # Sin biblioteca pero ya con algo escuchado: también (como mucho cada hora,
    # por si de ese historial no sale ninguna fila).
    return now - fetched_at >= 3600 and db.play_stats()[0] >= 5


# ---------------------------------------------------------------------------
# Ahora suena: música parecida y quién la hace
# ---------------------------------------------------------------------------
@dataclass
class NowInfo:
    """Lo que el panel "Ahora suena" enseña además de la carátula."""

    video_id: str
    similar: list[Track] = field(default_factory=list)
    artist: str = ""
    artist_id: str = ""                                     # canal del artista (para abrir su página)
    artist_thumb: str = ""
    artist_stats: list[list[str]] = field(default_factory=list)  # [etiqueta, valor] del artista
    bio: str = ""                                           # historia del artista o grupo
    facts: list[list[str]] = field(default_factory=list)    # [etiqueta, valor] de la canción
    # Faltan los datos del artista por un fallo pasajero (red, servidor): se
    # enseña lo que hay, pero no se guarda, para pedirlo entero la próxima vez.
    partial: bool = False


_VIDEO_KINDS = {
    "MUSIC_VIDEO_TYPE_ATV": "Audio oficial",
    "MUSIC_VIDEO_TYPE_OMV": "Vídeo musical oficial",
    "MUSIC_VIDEO_TYPE_UGC": "Subido por un usuario",
    "MUSIC_VIDEO_TYPE_OFFICIAL_SOURCE_MUSIC": "Publicado por el artista",
}


def tolerant(client: Any) -> Any:
    """El cliente, con la lectura de la página del artista a prueba de carruseles
    sin título (vídeos destacados): ytmusicapi 1.12 falla entera por uno de ellos
    y se perdía todo el artista (pasa, por ejemplo, con Vetusta Morla)."""
    parser = getattr(client, "parser", None)
    original = getattr(parser, "parse_channel_contents", None)
    if original is None or getattr(parser, "_ytmn_tolerant", False):
        return client

    def parse(results):
        kept = [
            r for r in results or []
            if not (isinstance(r, dict) and "musicCarouselShelfRenderer" in r)
            or "header" in r["musicCarouselShelfRenderer"]
        ]
        return original(kept)

    try:
        parser.parse_channel_contents = parse
        parser._ytmn_tolerant = True
    except AttributeError:
        pass
    return client


def artist_stats(data: dict) -> list[list[str]]:
    """Cifras del artista como pares [etiqueta, valor], sin la coletilla de la unidad."""
    stats: list[list[str]] = []
    monthly = str(data.get("monthlyListeners") or "").strip()
    if monthly:
        # "66,4 M usuarios mensuales" -> "66,4 M"
        number = re.sub(r"\s*(usuarios mensuales|monthly (audience|listeners))\s*$", "", monthly, flags=re.I)
        stats.append(["Oyentes mensuales", number])
    subscribers = str(data.get("subscribers") or "").strip()
    if subscribers:
        stats.append(["Suscriptores", subscribers])
    views = re.sub(r"\s*(visualizaciones|views)\s*$", "", str(data.get("views") or "").strip(), flags=re.I)
    if views:
        stats.append(["Reproducciones en total", views])
    return stats


def _transient(exc: BaseException) -> bool:
    """¿Un fallo que puede no repetirse (red, servidor saturado)?"""
    if isinstance(exc, OSError):          # también los de requests y urllib
        return True
    status = http_status(exc)
    return status is not None and (status == 429 or status >= 500)


def now_playing_info(track: Track, client_factory: Optional[Callable[[], Any]] = None) -> NowInfo:
    """Música parecida, datos de la canción y del artista (bloqueante, sin cuenta).

    La biografía llega en español cuando YouTube Music la tiene. Un canal de
    usuario (quien subió un vídeo) no es un artista: se queda solo el nombre.
    """
    client = (client_factory or generic_client)()
    watch = client.get_watch_playlist(videoId=track.video_id, radio=True, limit=config.NOW_SIMILAR_TRACKS + 2) or {}
    items = [i for i in (watch.get("tracks") or []) if isinstance(i, dict)]
    head = next((i for i in items if i.get("videoId") == track.video_id), {})
    info = NowInfo(track.video_id)

    seen = {track.video_id}
    for item in items:
        parsed = track_from_item(item)
        if parsed and parsed[0].video_id not in seen:
            seen.add(parsed[0].video_id)
            info.similar.append(parsed[0])
    del info.similar[config.NOW_SIMILAR_TRACKS:]

    album = (head.get("album") or {}).get("name") or track.album
    for label, value in (
        ("Álbum", album),
        ("Año", head.get("year")),
        ("Reproducciones", head.get("views")),
        # El tipo es texto de la app, no un dato: se guarda ya traducido
        # (la caché va por idioma). Las etiquetas, en español: las traduce
        # quien las enseña.
        ("Tipo", tr(_VIDEO_KINDS.get(str(head.get("videoType") or ""), ""))),
    ):
        if value:
            info.facts.append([label, str(value)])

    artists = [a for a in (head.get("artists") or []) if isinstance(a, dict) and a.get("name")]
    info.artist = artists[0]["name"] if artists else (split_artists(track.artists) or [track.artists])[0]
    artist_id = artists[0].get("id") if artists else None
    if artist_id:
        try:
            data = tolerant(client).get_artist(artist_id) or {}
        except Exception as exc:  # noqa: BLE001 - un canal de usuario no tiene página de artista
            if _transient(exc):
                log.info("datos del artista %s pendientes (fallo pasajero): %s", artist_id, exc)
                info.partial = True
            else:
                log.info("sin página de artista para %s: %s", artist_id, type(exc).__name__)
            data = {}
        if data:
            info.artist_id = str(artist_id)
        info.artist = str(data.get("name") or info.artist)
        info.artist_thumb = best_thumbnail(data.get("thumbnails"))
        info.bio = str(data.get("description") or "").strip()
        info.artist_stats = artist_stats(data)
    return info


def purge_stale_cache(db: Database, now: Optional[float] = None) -> int:
    """Quita de la caché lo que ya no se usaría (más viejo que su plazo), como
    mucho una vez cada ``DISCOVER_PURGE_INTERVAL_S``: cada canción y cada
    artista que se abren dejan una entrada, y sin esto la base solo crece.
    Las recomendaciones del inicio no se tocan: se enseñan aunque sean viejas.

    La primera vez tras cambiar de idioma, además, se va al momento lo
    guardado en el anterior: ya no se enseñaría (se vuelve a pedir) y, si
    no, se quedaría en la base hasta caducar."""
    now = time.time() if now is None else now
    removed = purge_other_language(db)
    if 0 <= now - db.get_float(CACHE_PURGED_KEY) < config.DISCOVER_PURGE_INTERVAL_S:
        return removed
    db.set_setting(CACHE_PURGED_KEY, now)
    expired = db.purge_discover_cache(
        {NOW_CACHE_PREFIX: config.NOW_INFO_TTL_S, ARTIST_CACHE_PREFIX: config.ARTIST_PAGE_TTL_S}, now
    )
    if expired:
        log.info("caché de recomendaciones: %d entradas caducadas borradas", expired)
    return removed + expired


def purge_other_language(db: Database) -> int:
    """Borra las canciones y artistas guardados en otro idioma, una vez por
    cambio de idioma (``CACHE_LANG_KEY`` recuerda el último purgado)."""
    language = i18n.active()
    if db.get_setting(CACHE_LANG_KEY) == language:
        return 0
    removed = db.purge_discover_language((NOW_CACHE_PREFIX, ARTIST_CACHE_PREFIX), language, i18n.SPANISH)
    db.set_setting(CACHE_LANG_KEY, language)
    if removed:
        log.info("caché de recomendaciones: %d entradas en otro idioma borradas", removed)
    return removed


def save_now_info(db: Database, info: NowInfo, now: Optional[float] = None) -> None:
    """Guarda la información una semana; la incompleta por un fallo pasajero no."""
    if info.partial:
        return
    db.set_discover_entry(
        f"{NOW_CACHE_PREFIX}{info.video_id}", json.dumps(_with_language(asdict(info)), ensure_ascii=False), now
    )
    purge_stale_cache(db, now)


def load_now_info(db: Database, video_id: str, now: Optional[float] = None) -> Optional[NowInfo]:
    """Lo guardado para esa canción, si no tiene más de una semana."""
    entry = db.get_discover_entry(f"{NOW_CACHE_PREFIX}{video_id}")
    if entry is None:
        return None
    fetched_at, raw = entry
    if (time.time() if now is None else now) - fetched_at >= config.NOW_INFO_TTL_S:
        return None
    try:
        data = json.loads(raw)
        if not _same_language(data):
            return None
        data["similar"] = [Track(**t) for t in data.get("similar") or []]
        info = NowInfo(**data)
        # Una entrada guardada con otra forma se descarta y se vuelve a pedir.
        if not all(isinstance(pair, list) and len(pair) == 2 for pair in info.artist_stats + info.facts):
            return None
        return info
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Abrir una lista ajena
# ---------------------------------------------------------------------------
def fetch_playlist(
    playlist_id: str, limit: int = 300, client_factory: Optional[Callable[[], Any]] = None
) -> RemotePlaylist:
    """Descarga una lista pública (sin cuenta) para verla y reproducirla."""
    if playlist_id.startswith("MPRE"):
        # Un álbum sin lista de audio conocida: se pide su página.
        return fetch_album(playlist_id)
    data = (client_factory or anon_client)().get_playlist(playlist_id, limit=limit) or {}
    author = data.get("author")
    author = str(author.get("name") or "") if isinstance(author, dict) else str(author or "")
    # Como en los álbumes: lo que YouTube marca como no disponible no se enseña.
    items = [i for i in (data.get("tracks") or []) if isinstance(i, dict) and i.get("isAvailable") is not False]
    tracks = [t[0] for t in (track_from_item(i) for i in items) if t]
    if not author and playlist_id.startswith("OLAK5uy") and tracks:
        author = split_artists(tracks[0].artists)[:1][0] if split_artists(tracks[0].artists) else ""
    return RemotePlaylist(
        playlist_id=playlist_id,
        title=str(data.get("title") or tr("Lista")),
        author=author,
        description=str(data.get("description") or ""),
        year=str(data.get("year") or ""),
        duration_s=parse_count(data.get("duration_seconds")),
        thumb_url=best_thumbnail(data.get("thumbnails")),
        tracks=tracks,
    )


def _album_track(item: Any, album: str, thumb: str) -> Optional[Track]:
    """Pista de ``get_album``: allí el álbum llega como texto y sin carátula propia."""
    if not isinstance(item, dict) or not item.get("videoId"):
        return None
    if item.get("isAvailable") is False:
        return None
    fixed = dict(item)
    if not isinstance(fixed.get("album"), dict):
        fixed["album"] = {"name": album}
    parsed = track_from_item(fixed)
    if parsed is None:
        return None
    track = parsed[0]
    return track if track.thumb_url else Track(
        track.video_id, track.title, track.artists, track.album, track.duration_s, thumb_url=thumb
    )


def fetch_album(browse_id: str, client_factory: Optional[Callable[[], Any]] = None) -> RemotePlaylist:
    """Un álbum o sencillo (``MPREb_…``) como lista que se puede ver y reproducir."""
    data = (client_factory or generic_client)().get_album(browse_id) or {}
    title = str(data.get("title") or tr("Álbum"))
    thumb = best_thumbnail(data.get("thumbnails"))
    artists = ", ".join(
        a.get("name", "") for a in (data.get("artists") or []) if isinstance(a, dict) and a.get("name")
    )
    tracks = [t for t in (_album_track(i, title, thumb) for i in (data.get("tracks") or [])) if t]
    return RemotePlaylist(
        playlist_id=browse_id,
        title=title,
        author=artists,
        description=str(data.get("description") or ""),
        year=str(data.get("year") or ""),
        duration_s=parse_count(data.get("duration_seconds")),
        thumb_url=thumb,
        tracks=tracks,
    )


# ---------------------------------------------------------------------------
# Búsqueda: listas de la comunidad y artistas
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ArtistCard:
    """Un artista tal como se enseña en una tarjeta redonda."""

    channel_id: str
    name: str
    note: str = ""
    thumb_url: str = ""


@dataclass
class SearchExtras:
    artists: list[ArtistCard] = field(default_factory=list)
    playlists: list[PlaylistCard] = field(default_factory=list)


def artist_matches(query: str, name: str) -> bool:
    """¿El artista es lo que se busca? Vale si todas las palabras de su nombre
    están en la búsqueda ("daft punk get lucky" -> Daft Punk) o si la búsqueda
    es el principio de su nombre ("vetusta" -> Vetusta Morla). Así buscar una
    canción no llena la fila de artistas que no vienen a cuento."""
    wanted, have = _fold(query), _fold(name)
    if not wanted or not have:
        return False
    if all(word in wanted for word in have):
        return True
    return all(any(h == w or h.startswith(w) for h in have) for w in wanted)


def artist_card_from_item(item: Any) -> Optional[ArtistCard]:
    if not isinstance(item, dict):
        return None
    channel = str(item.get("browseId") or "")
    name = str(item.get("artist") or item.get("title") or "").strip()
    if not channel and isinstance(item.get("artists"), list) and item["artists"]:
        first = item["artists"][0] if isinstance(item["artists"][0], dict) else {}
        channel, name = str(first.get("id") or ""), name or str(first.get("name") or "")
    if not channel.startswith("UC") or not name:
        return None
    subscribers = str(item.get("subscribers") or "").strip()
    return ArtistCard(channel, name, tr("{subscribers} suscriptores", subscribers=subscribers) if subscribers else tr("Artista"),
                      best_thumbnail(item.get("thumbnails")))


def search_extras(query: str, client_factory: Optional[Callable[[], Any]] = None) -> SearchExtras:
    """Listas de la comunidad y artistas de una búsqueda: dos peticiones a la
    vez, sin cuenta. El cliente en inglés es a propósito: en español YouTube
    Music devuelve las listas de la comunidad con otra forma y no se leen."""
    from concurrent.futures import ThreadPoolExecutor

    query = (query or "").strip()
    if not query:
        return SearchExtras()
    client = (client_factory or anon_client)()
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="busqueda-extra") as pool:
        playlists_job = pool.submit(client.search, query, filter="community_playlists",
                                    limit=config.SEARCH_PLAYLISTS)
        artists_job = pool.submit(client.search, query, filter="artists", limit=config.SEARCH_ARTISTS)
    out, errors = SearchExtras(), []
    try:
        seen: set[str] = set()
        for item in playlists_job.result() or []:
            card = card_from_item(item)
            if card is not None and card.playlist_id not in seen:
                seen.add(card.playlist_id)
                out.playlists.append(card)
    except Exception as exc:  # noqa: BLE001 - una de las dos puede fallar sola
        errors.append(exc)
    try:
        seen = set()
        for item in artists_job.result() or []:
            card = artist_card_from_item(item)
            if card is not None and card.channel_id not in seen and artist_matches(query, card.name):
                seen.add(card.channel_id)
                out.artists.append(card)
    except Exception as exc:  # noqa: BLE001
        errors.append(exc)
    if len(errors) == 2:
        raise errors[0]
    return out


def find_artist(name: str, client_factory: Optional[Callable[[], Any]] = None) -> Optional[ArtistCard]:
    """El canal de un artista a partir de su nombre: las pistas de la biblioteca
    no guardan su id. Mejor el que se llama exactamente así."""
    name = (name or "").strip()
    if not name:
        return None
    items = (client_factory or anon_client)().search(name, filter="artists", limit=5) or []
    cards = [c for c in (artist_card_from_item(i) for i in items) if c is not None]
    for card in cards:
        if card.name.casefold() == name.casefold():
            return card
    matching = [c for c in cards if artist_matches(name, c.name)]
    return matching[0] if matching else None


# ---------------------------------------------------------------------------
# Página del artista
# ---------------------------------------------------------------------------
@dataclass
class ArtistPage:
    channel_id: str
    name: str
    thumb_url: str = ""
    bio: str = ""
    stats: list[list[str]] = field(default_factory=list)
    top_songs: list[Track] = field(default_factory=list)
    songs_playlist: str = ""          # lista con todas sus canciones (para «Todas las canciones»)
    albums: list[PlaylistCard] = field(default_factory=list)
    singles: list[PlaylistCard] = field(default_factory=list)
    playlists: list[PlaylistCard] = field(default_factory=list)
    similar: list[Track] = field(default_factory=list)
    related: list[ArtistCard] = field(default_factory=list)
    radio_id: str = ""
    shuffle_id: str = ""


def _section(data: dict, key: str) -> tuple[list, str]:
    section = data.get(key)
    if not isinstance(section, dict):
        return [], ""
    results = [r for r in (section.get("results") or []) if isinstance(r, dict)]
    return results, str(section.get("browseId") or "")


def _release_card(item: dict, single: bool) -> Optional[PlaylistCard]:
    """Álbum o sencillo. Se abre por su lista de audio si la trae (la misma
    petición que cualquier lista) y si no, por la página del álbum."""
    browse = str(item.get("browseId") or "")
    pid = str(item.get("audioPlaylistId") or "") or browse
    title = str(item.get("title") or "").strip()
    if not pid or not title:
        return None
    year = str(item.get("year") or "").strip()
    kind = str(item.get("type") or "").strip()
    note = tr("{kind} de {year}", kind=kind, year=year) if single and kind and year else (year or kind)
    return PlaylistCard(pid, title, note, best_thumbnail(item.get("thumbnails")))


def _same_artist(track: Track, name: str) -> bool:
    key = name.casefold()
    return any(n.casefold() == key for n in split_artists(track.artists))


def _tracks_of(watch: dict, keep: Callable[[Track], bool]) -> list[Track]:
    out, ids = [], set()
    for item in watch.get("tracks") or []:
        parsed = track_from_item(item) if isinstance(item, dict) else None
        if parsed and parsed[0].video_id not in ids and keep(parsed[0]):
            ids.add(parsed[0].video_id)
            out.append(parsed[0])
    return out


def artist_page(
    channel_id: str,
    client_factory: Optional[Callable[[], Any]] = None,
    search_factory: Optional[Callable[[], Any]] = None,
) -> ArtistPage:
    """Todo lo de un artista (bloqueante, sin cuenta): su página, la música
    parecida (su radio, sin sus propias canciones) y las listas de la comunidad
    que llevan su nombre. Si solo falla lo secundario, la página sale igual."""
    from concurrent.futures import ThreadPoolExecutor

    client = tolerant((client_factory or generic_client)())
    data = client.get_artist(channel_id) or {}
    name = str(data.get("name") or "").strip() or tr("Artista")
    page = ArtistPage(
        channel_id=channel_id,
        name=name,
        thumb_url=best_thumbnail(data.get("thumbnails")),
        bio=str(data.get("description") or "").strip(),
        stats=artist_stats(data),
        radio_id=str(data.get("radioId") or ""),
        shuffle_id=str(data.get("shuffleId") or ""),
    )

    songs, songs_browse = _section(data, "songs")
    seen: set[str] = set()
    for item in songs:
        parsed = track_from_item(item)
        if parsed and parsed[0].video_id not in seen:
            seen.add(parsed[0].video_id)
            page.top_songs.append(parsed[0])
    if songs_browse.startswith("VL"):
        page.songs_playlist = songs_browse[2:]

    albums, _ = _section(data, "albums")
    page.albums = [c for c in (_release_card(i, False) for i in albums) if c][: config.ARTIST_MAX_CARDS]
    singles, _ = _section(data, "singles")
    page.singles = [c for c in (_release_card(i, True) for i in singles) if c][: config.ARTIST_MAX_CARDS]
    own_lists, _ = _section(data, "playlists")
    page.playlists = [c for c in (card_from_item(i) for i in own_lists) if c]
    related, _ = _section(data, "related")
    page.related = [c for c in (artist_card_from_item(i) for i in related) if c][: config.ARTIST_MAX_CARDS]

    searcher = (search_factory or anon_client)()

    def top_songs_fallback() -> list[Track]:
        # A veces la página llega sin «Canciones»: se buscan las suyas.
        found = []
        for item in searcher.search(name, filter="songs", limit=20) or []:
            parsed = track_from_item(item) if isinstance(item, dict) else None
            if parsed and _same_artist(parsed[0], name) and parsed[0].video_id not in seen:
                seen.add(parsed[0].video_id)
                found.append(parsed[0])
        return found

    def similar() -> list[Track]:
        if page.radio_id:
            watch = client.get_watch_playlist(playlistId=page.radio_id, limit=40) or {}
        elif page.top_songs:
            watch = client.get_watch_playlist(videoId=page.top_songs[0].video_id, radio=True, limit=40) or {}
        else:
            return []
        # Lo parecido es lo de otros: sus propias canciones ya están arriba.
        return _tracks_of(watch, lambda t: not _same_artist(t, name))

    def community() -> list[PlaylistCard]:
        items = _titled_like(community_playlists(searcher, name), name)
        return [c for c in (card_from_item(i) for i in items) if c]

    jobs: dict[str, Callable[[], list]] = {"similar": similar, "community": community}
    if not page.top_songs:
        jobs["songs"] = top_songs_fallback
    with ThreadPoolExecutor(max_workers=len(jobs), thread_name_prefix="artista") as pool:
        futures = {key: pool.submit(job) for key, job in jobs.items()}
    results: dict[str, list] = {}
    for key, future in futures.items():
        try:
            results[key] = future.result()
        except Exception as exc:  # noqa: BLE001 - lo secundario no tumba la página
            log.warning("página de %s, %s: %s", name, key, exc)
            results[key] = []

    page.top_songs.extend(results.get("songs", []))
    del page.top_songs[config.ARTIST_TOP_SONGS:]
    page.similar = results["similar"][: config.ARTIST_SIMILAR_TRACKS]
    known = {c.playlist_id for c in page.playlists}
    for card in results["community"]:
        if card.playlist_id not in known:
            known.add(card.playlist_id)
            page.playlists.append(card)
    del page.playlists[config.ARTIST_MAX_CARDS:]
    return page


def save_artist_page(db: Database, page: ArtistPage, now: Optional[float] = None) -> None:
    db.set_discover_entry(
        f"{ARTIST_CACHE_PREFIX}{page.channel_id}", json.dumps(_with_language(asdict(page)), ensure_ascii=False), now
    )
    purge_stale_cache(db, now)


def load_artist_page(db: Database, channel_id: str, now: Optional[float] = None) -> Optional[ArtistPage]:
    """Lo guardado de ese artista, si no tiene más de un día."""
    entry = db.get_discover_entry(f"{ARTIST_CACHE_PREFIX}{channel_id}")
    if entry is None:
        return None
    fetched_at, raw = entry
    if (time.time() if now is None else now) - fetched_at >= config.ARTIST_PAGE_TTL_S:
        return None
    try:
        data = json.loads(raw)
        if not _same_language(data):
            return None
        for key in ("top_songs", "similar"):
            data[key] = [Track(**t) for t in data.get(key) or []]
        for key in ("albums", "singles", "playlists"):
            data[key] = [PlaylistCard(**c) for c in data.get(key) or []]
        data["related"] = [ArtistCard(**c) for c in data.get("related") or []]
        page = ArtistPage(**data)
        if not all(isinstance(pair, list) and len(pair) == 2 for pair in page.stats):
            return None
        return page
    except (ValueError, TypeError):
        return None


def artist_mix(page: ArtistPage, shuffle: bool = False,
               client_factory: Optional[Callable[[], Any]] = None) -> list[Track]:
    """La radio del artista (o su mezcla aleatoria) como lista de pistas."""
    playlist_id = page.shuffle_id if shuffle and page.shuffle_id else page.radio_id
    if not playlist_id:
        return list(page.top_songs)
    watch = (client_factory or generic_client)().get_watch_playlist(playlistId=playlist_id, limit=50) or {}
    return _tracks_of(watch, lambda _t: True)
