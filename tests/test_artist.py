"""Página del artista y listas de la comunidad en la búsqueda (sin red)."""

import pytest

from ytm_native.core import config
from ytm_native.services import discover
from ytm_native.core.db import Track
from ytm_native.services.discover import ArtistCard, PlaylistCard
from ytm_native.ui import main_window as mw
from ytm_native.ui.search import ARTISTS, PLAYLISTS, SONGS

# La ventana y la red falsa son las mismas que usan las pruebas del inicio.
from test_home_search import animations_on, net, wait, wait_until, window  # noqa: F401


# ---------------------------------------------------------------------------
# Búsqueda: listas de la comunidad y artistas
# ---------------------------------------------------------------------------
def playlist_item(n, title=None, author="Fan"):
    return {"resultType": "playlist", "title": title or f"Lista {n}", "author": author,
            "browseId": f"VLPL{n:04d}", "thumbnails": [{"url": f"https://img/{n}=w60-h60", "width": 60}]}


def artist_item(name, channel, subscribers=None):
    item = {"resultType": "artist", "artist": name, "browseId": channel,
            "thumbnails": [{"url": f"https://img/{channel}=w120-h120", "width": 120}]}
    if subscribers:
        item["subscribers"] = subscribers
    return item


class ExtrasClient:
    def __init__(self, fail=()):
        self.fail = set(fail)
        self.calls = []

    def search(self, query, filter=None, limit=20):
        self.calls.append(filter)
        if filter in self.fail:
            raise ConnectionError("sin red")
        if filter == "community_playlists":
            return [playlist_item(i) for i in range(4)] + [playlist_item(1)]     # repetida
        if filter == "artists":
            return [artist_item("Daft Punk", "UCdaft", "7,21 M"), artist_item("The Weeknd", "UCweek"),
                    artist_item("Daft Punk Tribute", "UCtrib"), {"resultType": "artist", "artist": "Sin canal"}]
        return []


def test_buscar_trae_listas_de_la_comunidad_y_solo_los_artistas_que_vienen_a_cuento():
    extras = discover.search_extras("daft punk get lucky", client_factory=ExtrasClient)
    assert [c.playlist_id for c in extras.playlists] == ["PL0000", "PL0001", "PL0002", "PL0003"]
    assert extras.playlists[0].note == "De Fan"
    # The Weeknd sale en la respuesta, pero no es lo buscado; un tributo tampoco.
    assert [a.name for a in extras.artists] == ["Daft Punk"]
    assert extras.artists[0].note == "7,21 M suscriptores"


def test_buscar_listas_aguanta_que_falle_una_peticion_pero_no_las_dos():
    extras = discover.search_extras("daft", client_factory=lambda: ExtrasClient(fail={"artists"}))
    assert len(extras.playlists) == 4 and extras.artists == []
    with pytest.raises(ConnectionError):
        discover.search_extras("daft", client_factory=lambda: ExtrasClient(fail={"artists", "community_playlists"}))
    assert discover.search_extras("   ", client_factory=ExtrasClient).playlists == []


def test_coincidencia_de_artista():
    assert discover.artist_matches("vetusta", "Vetusta Morla")              # a medio escribir
    assert discover.artist_matches("daft punk get lucky", "Daft Punk")       # artista más canción
    assert discover.artist_matches("beyonce", "Beyoncé")                     # sin acentos
    assert not discover.artist_matches("daft punk", "The Weeknd")
    assert not discover.artist_matches("air", "Fair Play")                   # solo al principio de palabra


def test_artista_principal_de_la_busqueda_llega_aparte():
    class Client:
        def search(self, query, filter=None, limit=20):
            if filter is None:
                return [{"resultType": "artist", "category": "Top result", "subscribers": "294 K",
                         "artists": [{"name": "Vetusta Morla", "id": "UCvet"}]},
                        {"resultType": "song", "videoId": "c1", "title": "Copenhague",
                         "artists": [{"name": "Vetusta Morla", "id": "UCvet"}]}]
            return []

    from ytm_native.services.sync import search_tracks

    top: dict = {}
    tracks = search_tracks("vetusta morla", client_factory=Client, top=top)
    assert [t.video_id for t in tracks] == ["c1"]
    card = discover.artist_card_from_item(top["artist"])
    assert card == ArtistCard("UCvet", "Vetusta Morla", "294 K suscriptores", "")


# ---------------------------------------------------------------------------
# Página del artista
# ---------------------------------------------------------------------------
class Parser:
    """Imita el lector de ytmusicapi que falla con un carrusel sin título."""

    def parse_channel_contents(self, results):
        for r in results:
            if "musicCarouselShelfRenderer" in r and "header" not in r["musicCarouselShelfRenderer"]:
                raise KeyError("header")
        return {}


class ArtistClient:
    def __init__(self, songs=True):
        self.songs = songs
        self.parser = Parser()
        self.watch_calls = []

    def get_artist(self, channel_id):
        self.parser.parse_channel_contents([{"musicCarouselShelfRenderer": {"contents": []}}])
        data = {
            "name": "Daft Punk", "description": "Dúo francés.\nMás historia.",
            "monthlyListeners": "66,1 M usuarios mensuales", "subscribers": "7,21 M",
            "views": "6.066.806.347 visualizaciones", "radioId": "RDEMdaft", "shuffleId": "RDAOdaft",
            "thumbnails": [{"url": "https://img/foto=w540-h225", "width": 540}],
            "albums": {"results": [
                {"title": "Discovery", "browseId": "MPREdisc", "audioPlaylistId": "OLAKdisc", "year": "2001",
                 "thumbnails": [{"url": "https://img/disc", "width": 544}]},
                {"title": "Homework", "browseId": "MPREhome", "year": "1997"},
            ]},
            "singles": {"results": [{"title": "Get Lucky", "browseId": "MPRElucky", "type": "Single",
                                     "year": "2013"}]},
            "related": {"results": [{"title": "Justice", "browseId": "UCjustice", "subscribers": "2 M"},
                                    {"title": "Sin canal", "browseId": None}]},
        }
        if self.songs:
            data["songs"] = {"browseId": "VLOLAKtodas", "results": [
                {"videoId": "s1", "title": "One More Time", "artists": [{"name": "Daft Punk"}]},
                {"videoId": "s2", "title": "Around the World", "artists": [{"name": "Daft Punk"}]},
            ]}
        return data

    def get_watch_playlist(self, videoId=None, playlistId=None, radio=False, limit=25):
        self.watch_calls.append(playlistId or videoId)
        return {"tracks": [
            {"videoId": "s1", "title": "One More Time", "artists": [{"name": "Daft Punk"}]},
            {"videoId": "p1", "title": "D.A.N.C.E.", "artists": [{"name": "Justice"}]},
            {"videoId": "p2", "title": "Nightcall", "artists": [{"name": "Kavinsky"}]},
            {"videoId": "p1", "title": "D.A.N.C.E.", "artists": [{"name": "Justice"}]},
        ]}


class ArtistSearch:
    def __init__(self, fail_lists=False):
        self.fail_lists = fail_lists

    def search(self, query, filter=None, limit=20):
        if filter == "community_playlists":
            if self.fail_lists:
                raise ConnectionError("sin red")
            return [playlist_item(1, "Daft Punk: lo mejor"), playlist_item(2, "Daft Punk Tribute Band"),
                    playlist_item(3, "Fiesta")]
        if filter == "songs":
            return [{"videoId": "b1", "title": "Digital Love", "artists": [{"name": "Daft Punk"}]},
                    {"videoId": "b2", "title": "Get Lucky (cover)", "artists": [{"name": "Otro"}]}]
        return []


def test_pagina_del_artista_reune_todo_y_aguanta_el_carrusel_sin_titulo():
    client = ArtistClient()
    page = discover.artist_page("UCdaft", client_factory=lambda: client, search_factory=ArtistSearch)
    assert page.name == "Daft Punk" and page.bio.startswith("Dúo francés")
    assert page.stats == [["Oyentes mensuales", "66,1 M"], ["Suscriptores", "7,21 M"],
                          ["Reproducciones en total", "6.066.806.347"]]
    assert [t.video_id for t in page.top_songs] == ["s1", "s2"] and page.songs_playlist == "OLAKtodas"
    # Un álbum se abre por su lista de audio si la trae; si no, por su página.
    assert [(c.playlist_id, c.note) for c in page.albums] == [("OLAKdisc", "2001"), ("MPREhome", "1997")]
    assert page.singles[0].note == "Single de 2013"
    # Lo parecido es lo de otros, sin repetir.
    assert [t.video_id for t in page.similar] == ["p1", "p2"] and client.watch_calls == ["RDEMdaft"]
    assert [c.title for c in page.playlists] == ["Daft Punk: lo mejor"]
    assert page.related == [ArtistCard("UCjustice", "Justice", "2 M suscriptores", "")]


def test_pagina_del_artista_sin_canciones_las_busca_y_sin_listas_sale_igual():
    page = discover.artist_page("UCdaft", client_factory=lambda: ArtistClient(songs=False),
                                search_factory=lambda: ArtistSearch(fail_lists=True))
    assert [t.video_id for t in page.top_songs] == ["b1"]          # solo las suyas
    assert page.playlists == [] and page.similar


def test_pagina_del_artista_se_guarda_un_dia(db):
    page = discover.artist_page("UCdaft", client_factory=ArtistClient, search_factory=ArtistSearch)
    discover.save_artist_page(db, page, now=1000.0)
    cached = discover.load_artist_page(db, "UCdaft", now=1000.0 + 3600)
    assert cached == page and isinstance(cached.top_songs[0], Track)
    assert isinstance(cached.albums[0], PlaylistCard) and isinstance(cached.related[0], ArtistCard)
    assert discover.load_artist_page(db, "UCdaft", now=1000.0 + config.ARTIST_PAGE_TTL_S) is None
    db.set_discover_entry("artista:roto", '{"channel_id": "roto", "name": "x", "stats": ["mal"]}')
    assert discover.load_artist_page(db, "roto") is None


def test_album_sin_lista_de_audio_se_abre_por_su_pagina(monkeypatch):
    class AlbumClient:
        def get_album(self, browse_id):
            return {"title": "Homework", "year": "1997", "artists": [{"name": "Daft Punk"}],
                    "thumbnails": [{"url": "https://img/home", "width": 544}],
                    "tracks": [{"videoId": "h1", "title": "Da Funk", "artists": [{"name": "Daft Punk"}],
                                "album": "Homework", "duration": "5:28"},
                               {"videoId": "h2", "title": "Borrada", "isAvailable": False}]}

    monkeypatch.setattr(discover, "generic_client", AlbumClient)
    album = discover.fetch_playlist("MPREhome")
    assert album.title == "Homework" and album.author == "Daft Punk" and album.year == "1997"
    assert [t.video_id for t in album.tracks] == ["h1"]
    assert album.tracks[0].album == "Homework" and album.tracks[0].thumb_url == "https://img/home"
    assert album.tracks[0].duration_s == 328


# ---------------------------------------------------------------------------
# En la ventana
# ---------------------------------------------------------------------------
def search(window, text):
    window._navigate(mw.SEARCH_KEY)
    window.search_edit.setText(text)
    window._do_search(commit=True)
    assert wait_until(lambda: window._extras_state == "done" and window.model.rowCount() > 0)


def test_busqueda_ensena_listas_de_la_comunidad_y_abre_una(window, net):
    search(window, "daft punk")
    assert window._search_scope == SONGS and not window.table.isHidden() and window.search_cards.isHidden()
    assert window.artist_strip.isHidden()                 # el resultado principal no era un artista
    window.scope_bar.buttons[PLAYLISTS].click()
    assert window.table.isHidden() and not window.search_cards.isHidden()
    assert window.band.search_note.text() == "5 listas de la comunidad para «daft punk»"
    assert window.play_all_btn.isHidden()                 # no hay una lista de canciones que reproducir
    window.search_cards.playlists._cards[2].click()
    assert window._view_key == mw.REMOTE_PREFIX + "PLbus2"
    window._go_back()
    # Al volver se conserva la pestaña y lo encontrado.
    assert window._view_key == mw.SEARCH_KEY and window._search_scope == PLAYLISTS
    assert not window.search_cards.isHidden() and window.search_cards.playlists.count() == 5
    window.scope_bar.buttons[ARTISTS].click()
    assert window.search_cards.artists.count() == 1 and window.search_cards.playlists.isHidden()
    window.scope_bar.buttons[SONGS].click()
    assert not window.table.isHidden() and window.search_cards.isHidden()


def test_buscar_un_artista_ofrece_su_pagina_y_la_abre(window, net, db, monkeypatch):
    search(window, "artista")
    assert not window.artist_strip.isHidden() and window.artist_strip.card.channel_id == "UCartista"
    window.artist_strip.click()
    assert window._view_key == mw.ARTIST_PREFIX + "UCartista"
    assert not window.artist_view.isHidden() and window.body_box.isHidden() and not window.band.isHidden()
    assert wait_until(lambda: window.artist_view.page() is not None)
    view = window.artist_view
    assert window.band._full_title == "Artista" and window.band.cover._round
    assert window.band._facts[:2] == ["1 M oyentes mensuales", "200 K suscriptores"]
    assert not window.band.back_btn.isHidden() and not window.band.extra_btn.isHidden()
    assert view.songs_section.isVisibleTo(view.widget()) and len(view.songs.rows()) == 5
    assert view.albums.grid.count() == 6 and view.related.grid.count() == 4
    assert view.about_section.title.text() == "Sobre Artista"
    # Una pista de la página: suena esa y siguen las de su apartado.
    monkeypatch.setattr(mw.bootstrap, "missing_binaries", lambda: [])
    played = []
    monkeypatch.setattr(window.player, "play_tracks", lambda tracks, start=0: played.append((tracks, start)))
    view.similar.rows()[2].click()
    assert [t.video_id for t in played[-1][0]][:1] == ["par0"] and played[-1][1] == 2
    assert db.get_track("par2") is not None
    # Reproducir: sus populares; Aleatorio y Radio: su mezcla de YouTube Music.
    window.play_all_btn.click()
    assert [t.video_id for t in played[-1][0]][:1] == ["top0"]
    window.shuffle_all_btn.click()
    assert wait_until(lambda: played[-1][0][0].video_id == "mix0")
    # Un álbum se abre como una lista ajena y «Volver» regresa al artista.
    view.albums.grid._cards[0].click()
    assert window._view_key == mw.REMOTE_PREFIX + "OLAKalb0"
    window._go_back()
    assert window._view_key == mw.ARTIST_PREFIX + "UCartista" and net.artist_calls == ["UCartista"]
    # Un artista parecido abre su propia página.
    view.related.grid._cards[1].click()
    assert window._view_key == mw.ARTIST_PREFIX + "UCrel1"


def test_ver_todo_despliega_las_tarjetas_que_no_caben(window, net):
    window._open_artist(ArtistCard("UCartista", "Artista"))
    assert wait_until(lambda: window.artist_view.page() is not None)
    albums = window.artist_view.albums
    assert albums.grid.shown() < 6 and not albums.action.isHidden()
    albums.action.click()
    assert albums.grid.shown() == 6 and albums.action.text() == "Ver menos"


def test_ver_artista_desde_el_panel_cierra_el_panel_y_abre_la_pagina(window, net, monkeypatch):
    window.now_action.setChecked(True)
    track = Track("gl", "Get Lucky", "Artista")
    monkeypatch.setattr(window.player, "current_track", lambda: track)
    window._on_current_changed(track)
    assert wait_until(lambda: window.now_panel.info() is not None)
    assert not window.now_panel.artist_btn.isHidden()
    window.now_panel.artist_btn.click()
    # Sin el canal guardado se busca por el nombre.
    assert wait_until(lambda: window._view_key == mw.ARTIST_PREFIX + "UCartista")
    assert not window.now_action.isChecked()
    assert wait_until(lambda: not window.now_panel.isVisible())
    # El menú de cualquier pista lleva también al artista.
    menu = window._track_menu(Track("x", "Otra", "Artista, Invitado"))
    assert "Ir a Artista" in [a.text() for a in menu.actions()]


def test_artista_que_no_carga_ofrece_reintentar(window, net, monkeypatch):
    def broken(channel_id):
        raise ConnectionError("sin red")

    monkeypatch.setattr(mw.discover, "artist_page", broken)
    window._open_artist(ArtistCard("UCroto", "Roto"))
    assert wait_until(lambda: not window.artist_view.retry_btn.isHidden())
    assert window.band._full_title == "Roto" and window.artist_view.page() is None
    monkeypatch.setattr(mw.discover, "artist_page", net.artist_page)
    window.artist_view.retry_btn.click()
    assert wait_until(lambda: window.artist_view.page() is not None)
