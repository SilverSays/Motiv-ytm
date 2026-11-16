"""Sugerencias, gustos y recomendaciones del inicio (sin red: clientes falsos)."""

import random

import pytest

from ytm_native.core import config
from ytm_native.services import discover
from ytm_native.core.db import Track
from ytm_native.services.discover import PlaylistCard, Shelf


def playlist_item(n: int, author: str = "Alguien") -> dict:
    return {
        "resultType": "playlist",
        "title": f"Lista {n}",
        "author": author,
        "browseId": f"VLPL{n:04d}",
        "thumbnails": [{"url": f"https://img/{n}=w60-h60", "width": 60},
                       {"url": f"https://img/{n}=w576-h576", "width": 576}],
    }


class FakeClient:
    def __init__(self, fail: bool = False, related: bool = True):
        self.fail = fail
        self.related = related
        self.calls: list[tuple] = []

    def _maybe_fail(self):
        if self.fail:
            raise ConnectionError("sin red")

    def get_search_suggestions(self, query):
        self.calls.append(("suggest", query))
        return ["daft punk", "daft punk get lucky", "Daft Punk", "  daft   punk  one more time ", ""]

    def search(self, query, filter=None, limit=20):
        self.calls.append(("search", query, filter))
        self._maybe_fail()
        base = sum(map(ord, query)) % 500 * 10
        items = [playlist_item(base + i, author=f"Fan de {query}") for i in range(8)]
        titles = [query, f"{query} mix", f"{query}: lo mejor", f"{query} (lista)",
                  f"{query} Supply", f"On {query}", f"{query}bag", f"{query} grandes éxitos"]
        return [dict(item, title=title) for item, title in zip(items, titles)]

    def get_watch_playlist(self, videoId=None, radio=False, limit=25):
        self.calls.append(("watch", videoId))
        self._maybe_fail()
        return {"related": f"MPTR{videoId}"}

    def get_song_related(self, browse_id):
        self.calls.append(("related", browse_id))
        if not self.related:
            return [{"title": "You might also like", "contents": [{"title": "Canción", "videoId": "x"}]}]
        return [
            {"title": "You might also like", "contents": [{"title": "Canción", "videoId": "x"}]},
            {"title": "Recommended playlists", "contents": [
                {"title": f"Relacionada {i}", "playlistId": f"PLrel{browse_id}{i}",
                 "description": f"Playlist • Autora {i} • 20K views", "thumbnails": []}
                for i in range(5)
            ]},
            {"title": "About the artist", "contents": "texto"},
        ]

    def get_home(self, limit=4):
        self.calls.append(("home",))
        return [
            {"title": "Selección rápida", "contents": [{"title": "Canción", "videoId": "v"}] * 4},
            {"title": "Clásicos", "contents": [
                {"title": f"Clásicos {i}", "playlistId": f"RDCLAK{i}", "description": "A, B, C"}
                for i in range(4)
            ]},
        ]

    def get_playlist(self, playlist_id, limit=300):
        return {
            "title": "Lista ajena", "author": {"name": "Fan"}, "year": "2024",
            "description": None, "duration_seconds": 400,
            "thumbnails": [{"url": "https://img/p=w576-h576", "width": 576}],
            "tracks": [
                {"videoId": "a1", "title": "Uno", "artists": [{"name": "X"}], "duration_seconds": 200},
                {"videoId": None, "title": "Borrada"},
                {"videoId": "a2", "title": "Dos", "artists": [{"name": "Y"}], "duration": "3:20"},
            ],
        }


def fill_library(db) -> None:
    """Dos listas: en la primera manda Daft Punk; en la segunda, Air."""
    for position, (pid, title, artists) in enumerate((
        ("PLuno", "Para trabajar",
         ["Daft Punk", "Daft Punk", "Daft Punk, Pharrell Williams", "Daft Punk", "Air"]),
        ("PLdos", "Domingo", ["Air", "Air", "Various Artists"]),
    )):
        db.upsert_playlist_meta(pid, title, position)
        tracks = [(Track(f"{pid}-{i}", f"Canción {i}", a), None) for i, a in enumerate(artists)]
        db.replace_playlist_tracks(pid, tracks, len(tracks))


def test_sugerencias_sin_repetidos_ni_lo_ya_escrito():
    client = FakeClient()
    out = discover.search_suggestions(" daft punk ", client_factory=lambda: client)
    assert out == ["daft punk get lucky", "daft punk one more time"]
    assert client.calls == [("suggest", "daft punk")]
    assert discover.search_suggestions("   ", client_factory=lambda: client) == []


def test_busquedas_recientes_sin_duplicados_y_con_tope(db):
    for query in ("air", "Daft Punk", "AIR", "  ", "moon   safari"):
        discover.remember_search(db, query)
    assert discover.recent_searches(db) == ["moon safari", "AIR", "Daft Punk"]
    for i in range(20):
        discover.remember_search(db, f"búsqueda {i}")
    recent = discover.recent_searches(db)
    assert len(recent) == config.RECENT_SEARCHES and recent[0] == "búsqueda 19"


def test_artistas_se_separan_y_se_filtra_lo_que_no_es_artista():
    assert discover.split_artists("Daft Punk, Pharrell Williams") == ["Daft Punk", "Pharrell Williams"]
    assert discover.split_artists("Various Artists, 12M views, Canal - Topic") == ["Canal"]
    assert discover.split_artists("") == []


def test_gustos_pesan_por_canciones_y_por_listas_reproducidas(db):
    fill_library(db)
    assert discover.taste(db).artists[0] == ("Daft Punk", 4)
    # La lista de Air se reproduce mucho desde la app: Air pasa delante.
    for _ in range(8):
        db.note_playlist_played("PLdos")
    assert discover.taste(db).artists[0] == ("Air", 3)
    assert [p.playlist_id for p in db.main_playlists()] == ["PLdos", "PLuno"]


def test_tarjeta_saca_el_id_y_la_nota_de_cada_forma_de_respuesta():
    card = discover.card_from_item(playlist_item(7, "Jonathan"))
    assert card == PlaylistCard("PL0007", "Lista 7", "De Jonathan", "https://img/7=w576-h576")
    related = discover.card_from_item(
        {"title": "Happy", "playlistId": "PLx", "description": "Playlist • Ricardo • 11K views"}
    )
    assert (related.playlist_id, related.note) == ("PLx", "De Ricardo")
    assert discover.card_from_item({"title": "Sin id"}) is None
    assert discover.card_from_item({"playlistId": "PLx", "title": " "}) is None


def test_recomendaciones_salen_de_las_listas_y_de_los_artistas(db):
    fill_library(db)
    db.upsert_playlist_meta("PLrelMPTRPLuno-00", "Ya la tengo", 5)   # no debe recomendarse
    client = FakeClient()
    arrived: list[Shelf] = []
    kind, shelves = discover.build_recommendations(
        db, client_factory=lambda: client, on_shelf=arrived.append, rng=random.Random(4)
    )
    assert kind == "gustos" and arrived == shelves
    titles = [s.title for s in shelves]
    assert titles[:2] == ["Parecidas a «Para trabajar»", "Parecidas a «Domingo»"]
    assert shelves[0].note.startswith("Elegidas a partir de «Canción ")
    assert shelves[0].cards[0].note.startswith("De Autora ")
    # Cada artista favorito tiene su fila, venga de lo relacionado o de la búsqueda.
    by_artist = next(s for s in shelves if s.title.endswith("Daft Punk"))
    assert by_artist.title in ("Porque escuchas a Daft Punk", "Listas de la comunidad con Daft Punk")
    assert by_artist.note == "Está en 4 canciones de tus listas"
    assert any(s.title.endswith(" Air") for s in shelves)
    # Ninguna lista se repite entre filas ni es de la biblioteca.
    ids = [c.playlist_id for s in shelves for c in s.cards]
    assert len(ids) == len(set(ids))
    assert not set(ids) & {p.playlist_id for p in db.playlists()}
    assert ("home",) not in client.calls


def test_artista_sin_relacionadas_cae_en_la_busqueda_por_titulo(db):
    fill_library(db)
    client = FakeClient(related=False)
    kind, shelves = discover.build_recommendations(db, client_factory=lambda: client, rng=random.Random(1))
    assert kind == "gustos"
    assert [s.title for s in shelves] == [
        "Listas de la comunidad con Daft Punk", "Listas de la comunidad con Air",
        "Listas de la comunidad con Pharrell Williams",
    ]
    # "Air Supply", "On Air" y "Airbag" no son listas de Air.
    assert [c.title for c in shelves[1].cards] == [
        "Air", "Air mix", "Air: lo mejor", "Air (lista)", "Air grandes éxitos",
    ]
    assert shelves[0].cards[0].note == "De Fan de Daft Punk"


def test_semilla_de_artista_prefiere_donde_es_el_principal(db):
    fill_library(db)
    db.replace_playlist_tracks("PLdos", [
        (Track("solo", "Suya", "Pharrell Williams"), None),
        (Track("otro", "No es suya", "Pharrell Williamson"), None),
    ], 2)
    for seed in range(5):
        assert discover._artist_seed(db, "Pharrell Williams", random.Random(seed)).video_id == "solo"
    assert discover._artist_seed(db, "Nadie", random.Random(0)) is None


def test_sin_biblioteca_se_ensena_la_portada_general(db):
    client = FakeClient()
    kind, shelves = discover.build_recommendations(
        db, client_factory=lambda: client, generic_factory=lambda: client
    )
    assert kind == "general"
    assert [s.title for s in shelves] == ["Clásicos"]       # la fila de canciones no es de listas
    assert shelves[0].cards[0].note == "A, B, C"


def test_sin_red_el_error_llega_a_quien_llama(db):
    fill_library(db)
    with pytest.raises(ConnectionError):
        discover.build_recommendations(db, client_factory=lambda: FakeClient(fail=True))


def test_cache_de_recomendaciones_y_cuando_caduca(db):
    assert discover.load_shelves(db) is None and discover.shelves_stale(db, None)
    shelves = [Shelf("Fila", "nota", (PlaylistCard("PLa", "A", "De X", "u"),))]
    discover.save_shelves(db, "general", shelves, now=1000.0)
    cached = discover.load_shelves(db)
    assert cached == (1000.0, "general", shelves)
    assert not discover.shelves_stale(db, cached, now=1000.0 + 60)
    assert discover.shelves_stale(db, cached, now=1000.0 + config.HOME_RECS_TTL_S)
    # Guardada la portada general, en cuanto hay biblioteca toca personalizar.
    fill_library(db)
    assert discover.shelves_stale(db, cached, now=1000.0 + 60)
    discover.save_shelves(db, "gustos", shelves, now=1000.0)
    assert not discover.shelves_stale(db, discover.load_shelves(db), now=1000.0 + 60)
    db.set_discover_entry(discover.HOME_CACHE_KEY, "{roto")
    assert discover.load_shelves(db) is None


def test_lista_ajena_se_descarga_sin_las_pistas_borradas():
    data = discover.fetch_playlist("PLzzz", client_factory=FakeClient)
    assert (data.title, data.author, data.year, data.duration_s) == ("Lista ajena", "Fan", "2024", 400)
    assert [t.video_id for t in data.tracks] == ["a1", "a2"]
    assert data.tracks[1].duration_s == 200 and data.description == ""


# ---------------------------------------------------------------------------
# Fase 4
# ---------------------------------------------------------------------------
def test_los_nombres_con_coma_no_se_parten():
    assert discover.split_artists("Tyler, The Creator, Kali Uchis") == ["Tyler, The Creator", "Kali Uchis"]
    assert discover.split_artists("Earth, Wind & Fire") == ["Earth, Wind & Fire"]
    assert discover.split_artists("crosby, stills, nash & young") == ["crosby, stills, nash & young"]
    # Sin un nombre conocido, la coma separa: "The Weeknd" es otro artista.
    assert discover.split_artists("Daft Punk, The Weeknd") == ["Daft Punk", "The Weeknd"]


def test_un_artista_de_una_sola_letra_cuenta():
    assert discover.split_artists("X, Ana") == ["X", "Ana"]
    assert discover.split_artists("7, Ø") == ["7", "Ø"]
    assert discover.split_artists("&, -, Ana") == ["Ana"]


def test_una_fila_no_repite_la_misma_lista():
    items = [playlist_item(1), playlist_item(2), playlist_item(1), playlist_item(3), playlist_item(2)]
    # Quitando la ya enseñada quedan dos distintas (no cuatro): no llegan al mínimo.
    assert discover._take_cards(items, {"PL0003"}) == ()
    cards = discover._take_cards(items + [playlist_item(4)], set())
    assert [c.playlist_id for c in cards] == ["PL0001", "PL0002", "PL0003", "PL0004"]


class NowClient:
    def __init__(self, artist_error=None):
        self.artist_error = artist_error

    def get_watch_playlist(self, videoId=None, radio=False, limit=None):
        return {"tracks": [
            {"videoId": videoId, "title": "Suena", "artists": [{"name": "Ana", "id": "UCana"}]},
            {"videoId": "p1", "title": "Parecida", "artists": [{"name": "Vera"}]},
        ]}

    def get_artist(self, channel_id):
        if self.artist_error is not None:
            raise self.artist_error
        return {"name": "Ana", "description": "Biografía", "thumbnails": []}


def test_sin_red_para_el_artista_no_se_guarda_una_semana(db):
    track = Track("np1", "Suena", "Ana")
    info = discover.now_playing_info(track, client_factory=lambda: NowClient(OSError("timed out")))
    assert info.partial and info.artist == "Ana" and [t.video_id for t in info.similar] == ["p1"]
    discover.save_now_info(db, info)
    assert discover.load_now_info(db, "np1") is None          # se volverá a pedir
    server = Exception("Server returned HTTP 503: Service Unavailable.")
    assert discover.now_playing_info(track, client_factory=lambda: NowClient(server)).partial


def test_un_canal_sin_pagina_de_artista_si_se_guarda(db):
    track = Track("np2", "Suena", "Ana")
    info = discover.now_playing_info(track, client_factory=lambda: NowClient(KeyError("header")))
    assert not info.partial
    discover.save_now_info(db, info)
    assert discover.load_now_info(db, "np2") is not None
    complete = discover.now_playing_info(Track("np3", "Suena", "Ana"), client_factory=NowClient)
    assert not complete.partial and complete.bio == "Biografía"


def test_lista_ajena_sin_las_pistas_no_disponibles():
    class Client:
        def get_playlist(self, playlist_id, limit=None):
            return {"title": "Lista", "tracks": [
                {"videoId": "a1", "title": "Sí", "isAvailable": True},
                {"videoId": "a2", "title": "No", "isAvailable": False},
                {"videoId": "a3", "title": "Sin dato"},
                "basura",
            ]}

    data = discover.fetch_playlist("PLx", client_factory=Client)
    assert [t.video_id for t in data.tracks] == ["a1", "a3"]
