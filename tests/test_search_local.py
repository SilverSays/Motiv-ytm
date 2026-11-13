"""Búsqueda combinada, listas locales y datos de "Ahora suena" (sin red)."""

import pytest

from ytm_native.core import config
from ytm_native.services import discover
from ytm_native.core.db import Track, is_local_playlist
from ytm_native.services.sync import LibrarySync, SyncState, match_level, search_tracks


def song(vid, title, artist, duration=None, **extra):
    item = {"resultType": "song", "videoId": vid, "title": title, "artists": [{"name": artist, "id": "UC" + vid}],
            "album": {"name": "Disco"}, "videoType": "MUSIC_VIDEO_TYPE_ATV"}
    if duration:
        item["duration_seconds"] = duration
    item.update(extra)
    return item


def video(vid, title, channel, duration=None, **extra):
    item = {"resultType": "video", "videoId": vid, "title": title, "artists": [{"name": channel, "id": "UC" + vid}],
            "videoType": "MUSIC_VIDEO_TYPE_UGC"}
    if duration:
        item["duration_seconds"] = duration
    item.update(extra)
    return item


class SearchClient:
    """Imita la respuesta real a "ska cha cha sonic rush": lo bueno son vídeos."""

    def __init__(self, fail=()):
        self.fail = set(fail)
        self.calls = []

    def search(self, query, filter=None, limit=20):
        self.calls.append(filter)
        if filter in self.fail:
            raise ConnectionError("sin red")
        if filter is None:
            return [
                video("top", "Ska Cha Cha - Sonic Rush [OST]", "DeoxysPrime", 173, category="Top result"),
                video("v2", "Sonic Rush (DS) - Ska Cha Cha / Night Carnival", "Rizer"),
                song("s1", "O'Zanana ( This Is It )", "Skah Shah #1"),
                song("s2", "The Ska Cha Cha", "Sour Roy"),
                {"resultType": "playlist", "browseId": "VLPLx", "title": "Sonic Rush OST"},
                {"resultType": "episode", "videoId": "ep", "title": "Podcast de Sonic",
                 "videoType": "MUSIC_VIDEO_TYPE_PODCAST_EPISODE"},
                {"resultType": "artist", "browseId": "UCx", "artist": "Wipomatic"},
            ]
        if filter == "songs":
            return [
                song("s3", "T' Cha - T' Cha", "Boogie Down Productions", 275),
                song("s2", "The Ska Cha Cha", "Sour Roy", 201),
                song("s4", "Sonic Rush", "Cosmo & Dibs", 180),
            ]
        return [
            video("top", "Ska Cha Cha - Sonic Rush [OST]", "DeoxysPrime", 173),
            video("v2", "Sonic Rush (DS) - Ska Cha Cha / Night Carnival", "Rizer", 190),
            video("v3", "Sonic Rush Original Soundtrack: Ska Cha Cha", "Otro canal", 172),
            video("pod", "Episodio", "Canal", 900, videoType="MUSIC_VIDEO_TYPE_PODCAST_EPISODE"),
        ]


def test_busqueda_pone_primero_el_resultado_principal_aunque_sea_un_video():
    client = SearchClient()
    tracks = search_tracks("ska cha cha sonic rush", client_factory=lambda: client)
    ids = [t.video_id for t in tracks]
    assert sorted(client.calls, key=str) == [None, "songs", "videos"]
    assert ids[0] == "top"                                  # el que enseña la web arriba del todo
    # Después, lo que contiene todo lo buscado; las canciones que no vienen a cuento, al final.
    assert ids[1:3] == ["v2", "v3"]
    assert ids.index("s2") < ids.index("s3") and ids.index("s4") < ids.index("s1")
    assert "ep" not in ids and "pod" not in ids and len(ids) == len(set(ids))
    by_id = {t.video_id: t for t in tracks}
    assert by_id["v2"].duration_s == 190                    # la duración llega de la lista filtrada
    assert by_id["top"].artists == "DeoxysPrime" and by_id["s2"].album == "Disco"
    assert search_tracks("ska cha cha sonic rush", limit=2, client_factory=lambda: client)[1].video_id == "v2"


def test_busqueda_aguanta_que_falle_una_de_las_tres_peticiones():
    tracks = search_tracks("sonic rush", client_factory=lambda: SearchClient(fail=["videos"]))
    assert tracks[0].video_id == "top" and any(t.video_id == "s4" for t in tracks)
    with pytest.raises(ConnectionError):
        search_tracks("sonic rush", client_factory=lambda: SearchClient(fail=[None, "songs", "videos"]))


def test_nivel_de_coincidencia_ignora_acentos_y_admite_la_palabra_a_medias():
    track = Track("x", "Canción del Mariachi", "Los Lobos", "Desperado")
    assert match_level("cancion mariachi", track) == 0
    assert match_level("los lobos maria", track) == 0        # "maria" va a medias, al escribir
    assert match_level("mariachi lobos zeppelin", track) == 1
    assert match_level("lobos zeppelin", track) == 2
    assert match_level("led zeppelin", track) == 3
    assert match_level("   ", track) == 0


# ---------------------------------------------------------------------------
# Listas locales
# ---------------------------------------------------------------------------
def test_lista_local_se_crea_se_llena_y_se_vacia(db):
    a, b, c = (Track(v, f"Canción {v}", "Artista", thumb_url=f"https://img/{v}=w60-h60") for v in "abc")
    pid = db.create_local_playlist("  Para correr ")
    assert is_local_playlist(pid) and db.get_playlist(pid).title == "Para correr"
    assert db.get_playlist(pid).local and db.playlist_cover(db.get_playlist(pid)) == ""
    assert db.add_to_local_playlist(pid, a) and db.add_to_local_playlist(pid, b)
    assert not db.add_to_local_playlist(pid, a)             # no se repite
    assert db.add_to_local_playlist(pid, c)
    assert [t.video_id for t in db.playlist_tracks(pid)] == ["a", "b", "c"]
    assert db.playlist_cover(db.get_playlist(pid)) == "https://img/a=w60-h60"
    assert db.remove_from_local_playlist(pid, "b") and not db.remove_from_local_playlist(pid, "zz")
    assert [t.video_id for t in db.playlist_tracks(pid)] == ["a", "c"]
    assert db.add_to_local_playlist(pid, b)                 # las posiciones siguen siendo correlativas
    assert [t.video_id for t in db.playlist_tracks(pid)] == ["a", "c", "b"]
    db.rename_playlist(pid, "Para caminar")
    assert db.get_playlist(pid).title == "Para caminar"
    assert db.delete_local_playlist(pid) and db.get_playlist(pid) is None
    assert db.get_track("a") is not None                    # borrar la lista no borra las canciones


def test_listas_locales_van_delante_y_las_de_la_cuenta_no_se_tocan_desde_aqui(db):
    db.upsert_playlist_meta("PLcuenta", "De la cuenta", 0)
    first = db.create_local_playlist("Primera", [Track("x", "X")])
    second = db.create_local_playlist("Segunda")
    assert [p.playlist_id for p in db.playlists()] == [first, second, "PLcuenta"]
    assert db.get_playlist(first).stored_tracks == 1
    assert not db.delete_local_playlist("PLcuenta") and db.get_playlist("PLcuenta") is not None
    assert not db.add_to_local_playlist("PLcuenta", Track("y", "Y"))


def test_sincronizar_no_borra_ni_confunde_las_listas_locales(db):
    local = db.create_local_playlist("Solo mía", [Track("x", "X")])
    db.upsert_playlist_meta("PLvieja", "Ya no existe", 0)
    assert db.delete_playlists_not_in(["PLotra"]) == 1
    assert [p.playlist_id for p in db.playlists()] == [local]

    class Client:
        def get_library_playlists(self, limit=None):
            return [{"playlistId": "PLnueva", "title": "Nueva", "count": 1}]

        def get_playlist(self, pid, limit=None):
            return {"tracks": [{"videoId": "n1", "title": "N1", "artists": [{"name": "A"}]}], "trackCount": 1}

    report = LibrarySync(db, Client, credentials_present=lambda: True, pause=lambda _s: None).run()
    assert report.state is SyncState.IDLE and report.playlists_removed == 0
    assert {p.playlist_id for p in db.playlists()} == {local, "PLnueva"}
    assert [t.video_id for t in db.playlist_tracks(local)] == ["x"]


# ---------------------------------------------------------------------------
# Ahora suena
# ---------------------------------------------------------------------------
class NowClient:
    def __init__(self, artist_fails=False):
        self.artist_fails = artist_fails

    def get_watch_playlist(self, videoId=None, radio=False, limit=25):
        head = {"videoId": videoId, "title": "Get Lucky", "length": "4:09", "views": "5,4 M",
                "videoType": "MUSIC_VIDEO_TYPE_ATV", "year": "2013", "album": {"name": "Random Access Memories"},
                "artists": [{"name": "Daft Punk", "id": "UCdaft"}]}
        others = [{"videoId": f"sim{i}", "title": f"Parecida {i}", "length": "3:00",
                   "artists": [{"name": "Otro"}], "thumbnail": [{"url": f"https://img/{i}", "width": 400}]}
                  for i in range(20)]
        return {"tracks": [head, dict(others[0])] + others, "related": "MPTRx"}

    def get_artist(self, channel_id):
        if self.artist_fails:
            raise KeyError("musicImmersiveHeaderRenderer")
        return {"name": "Daft Punk", "description": "Dúo francés formado en 1993 en París.\nMás historia.",
                "subscribers": "7,21 M", "monthlyListeners": "66,4 M usuarios mensuales",
                "thumbnails": [{"url": "https://img/artista=w540-h540", "width": 540}]}


def test_ahora_suena_reune_parecidas_artista_y_datos_de_la_cancion():
    track = Track("gl", "Get Lucky", "Daft Punk, Pharrell Williams")
    info = discover.now_playing_info(track, client_factory=NowClient)
    assert len(info.similar) == config.NOW_SIMILAR_TRACKS
    assert [t.video_id for t in info.similar[:2]] == ["sim0", "sim1"]      # sin la que suena ni repetidas
    assert info.similar[0].duration_s == 180 and info.similar[0].thumb_url == "https://img/0"
    assert info.artist == "Daft Punk" and info.bio.startswith("Dúo francés formado en 1993")
    assert info.artist_stats == [["Oyentes mensuales", "66,4 M"], ["Suscriptores", "7,21 M"]]
    assert info.facts == [["Álbum", "Random Access Memories"], ["Año", "2013"],
                          ["Reproducciones", "5,4 M"], ["Tipo", "Audio oficial"]]


def test_ahora_suena_sin_pagina_de_artista_se_queda_con_el_nombre():
    info = discover.now_playing_info(Track("v", "Vídeo", "Canal"), client_factory=lambda: NowClient(artist_fails=True))
    assert info.artist == "Daft Punk" and info.bio == "" and info.artist_stats == []
    assert info.similar                                     # lo demás sigue llegando


def test_ahora_suena_se_guarda_una_semana(db):
    info = discover.now_playing_info(Track("gl", "Get Lucky", "Daft Punk"), client_factory=NowClient)
    discover.save_now_info(db, info, now=1000.0)
    cached = discover.load_now_info(db, "gl", now=1000.0 + 3600)
    assert cached == info and isinstance(cached.similar[0], Track)
    assert discover.load_now_info(db, "gl", now=1000.0 + config.NOW_INFO_TTL_S) is None
    assert discover.load_now_info(db, "otra") is None
    # Una entrada guardada con otra forma no rompe el panel: se vuelve a pedir.
    db.set_discover_entry("suena:vieja", '{"video_id": "vieja", "artist_stats": ["66 M oyentes"]}')
    assert discover.load_now_info(db, "vieja") is None


def test_a_igual_coincidencia_la_cancion_va_antes_que_la_subida_de_un_usuario():
    class Client:
        def search(self, query, filter=None, limit=20):
            if filter is None:
                return [
                    song("oficial", "Get Lucky", "Daft Punk", 369, category="Top result"),
                    video("fan", "Daft Punk - Get Lucky (10 min loop)", "nTOURIST", 600),
                    video("clip", "Daft Punk - Get Lucky (Official Video)", "Daft Punk", 250,
                          videoType="MUSIC_VIDEO_TYPE_OMV"),
                    song("radio", "Get Lucky (Radio Edit)", "Daft Punk", 248),
                ]
            return []

    ids = [t.video_id for t in search_tracks("daft punk get lucky", client_factory=Client)]
    assert ids == ["oficial", "clip", "radio", "fan"]
