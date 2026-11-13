import pytest

from ytm_native.services.lyrics import (
    Lyrics,
    build_queries,
    clean_lookup_text,
    current_line_index,
    fetch_lyrics,
    parse_lrc,
)
from ytm_native.services.sync import CookieKeeper, merge_set_cookies, radio_tracks, sanitize_browser_headers

LRC = "[00:12.50]Primera línea\n[00:20.00][01:05.00]Estribillo\n[00:31.2]Tercera\n\n[ar:Alguien]"


def test_parse_lrc_sorts_and_repeats_lines():
    lines = parse_lrc(LRC)
    assert [(ln.start_s, ln.text) for ln in lines] == [
        (12.5, "Primera línea"), (20.0, "Estribillo"), (31.2, "Tercera"), (65.0, "Estribillo"),
    ]


def test_current_line_index():
    lines = parse_lrc(LRC)
    assert current_line_index(lines, 0) == -1
    assert current_line_index(lines, 12.5) == 0
    assert current_line_index(lines, 25) == 1
    assert current_line_index(lines, 999) == 3


def test_clean_lookup_text_strips_video_noise():
    assert clean_lookup_text("Canción (Official Video)") == "Canción"
    assert clean_lookup_text("Canción [Lyrics]") == "Canción"
    assert clean_lookup_text("Canción (feat. Otro Artista)") == "Canción"
    assert clean_lookup_text("Canción - Official Audio") == "Canción"
    assert clean_lookup_text("Canción (Remastered 2011)") == "Canción"
    assert clean_lookup_text("Amor (de verdad)") == "Amor (de verdad)"     # no toca paréntesis normales


def test_build_queries_tries_clean_title_and_single_artists():
    queries = build_queries("Tema (Official Video)", "Ana Robles, Vera Sol", "Disco")
    assert {"title": "Tema", "artist": "Ana Robles", "album": "Disco"} in queries
    assert queries[0] == {"title": "Tema (Official Video)", "artist": "Ana Robles, Vera Sol", "album": "Disco"}
    assert len(queries) <= 6


class FakeLrclib:
    def __init__(self, exact=None, search=None, fail=False):
        self.exact, self.search, self.fail = exact, search or [], fail
        self.urls = []

    def __call__(self, url, timeout):
        self.urls.append(url)
        if self.fail:
            raise OSError("sin red")
        return self.exact if "/get?" in url else self.search


def test_fetch_prefers_exact_synced_match():
    api = FakeLrclib(exact={"duration": 200, "syncedLyrics": LRC, "plainLyrics": "texto"})
    lyrics = fetch_lyrics("Tema", "Artista", "Disco", 200.4, http_json=api)
    assert lyrics.synced and len(lyrics.lines) == 4
    assert len(api.urls) == 1 and "duration=200" in api.urls[0] and "album_name=Disco" in api.urls[0]


def test_fetch_rejects_wrong_duration_and_falls_back_to_search():
    api = FakeLrclib(
        exact=None,
        search=[
            {"duration": 260, "syncedLyrics": "[00:01.00]otra versión"},
            {"duration": 201, "syncedLyrics": "[00:01.00]la buena"},
        ],
    )
    lyrics = fetch_lyrics("Tema", "Artista", "", 200, http_json=api)
    assert [ln.text for ln in lyrics.lines] == ["la buena"]


def test_fetch_returns_plain_lyrics_when_nothing_is_synced():
    api = FakeLrclib(exact={"duration": 200, "syncedLyrics": None, "plainLyrics": "solo texto"})
    lyrics = fetch_lyrics("Tema", "Artista", "", 200, http_json=api)
    assert not lyrics.synced and lyrics.plain == "solo texto"


def test_fetch_none_when_not_found_and_raises_without_network():
    assert fetch_lyrics("Tema", "Artista", "", 200, http_json=FakeLrclib()) is None
    with pytest.raises(OSError):
        fetch_lyrics("Tema", "Artista", "", 200, http_json=FakeLrclib(fail=True))


def test_lyrics_json_roundtrip_and_cache(db):
    lyrics = Lyrics(lines=parse_lrc(LRC), plain="texto")
    assert Lyrics.from_json(lyrics.to_json()) == lyrics
    assert db.get_lyrics_entry("v1") is None
    db.set_lyrics_entry("v1", lyrics.to_json())
    db.set_lyrics_entry("v2", None)                       # "se buscó y no había"
    assert Lyrics.from_json(db.get_lyrics_entry("v1")[1]).synced
    assert db.get_lyrics_entry("v2")[1] is None


def test_radio_tracks_parses_watch_playlist_and_skips_seed():
    class Client:
        def get_watch_playlist(self, videoId=None, radio=False, limit=25):
            assert radio is True and videoId == "seed"
            return {"tracks": [
                {"videoId": "seed", "title": "Semilla", "artists": [{"name": "A"}], "length": "3:20"},
                {"videoId": "r1", "title": "Uno", "artists": [{"name": "A"}], "length": "3:45"},
                {"videoId": "r1", "title": "Uno repetido", "artists": [], "length": "3:45"},
                {"title": "sin id"},
                {"videoId": "r2", "title": "Dos", "artists": [{"name": "B"}], "album": {"name": "X"}},
            ]}

    tracks = radio_tracks("seed", client_factory=Client)
    assert [t.video_id for t in tracks] == ["r1", "r2"]
    assert tracks[0].duration_s == 225 and tracks[1].album == "X"


def test_sanitize_browser_headers_drops_what_chrome_adds():
    raw = {
        "music.youtube.com": "POST",                      # pseudo-cabecera :authority mal pegada
        "/youtubei/v1/browse?ctoken=abc": "https",        # :path
        "content-encoding": "gzip",
        "accept-encoding": "gzip, deflate",
        "sec-fetch-mode": "cors",
        "priority": "u=1, i",
        "cookie": "a=b",
        "authorization": "SAPISIDHASH x",
        "x-goog-authuser": "0",
    }
    assert sanitize_browser_headers(raw) == {
        "cookie": "a=b", "authorization": "SAPISIDHASH x", "x-goog-authuser": "0",
    }


def test_merge_set_cookies_updates_adds_and_removes():
    header = "SAPISID=abc; SIDCC=old; YSC=x"
    merged, changed = merge_set_cookies(header, [
        "SIDCC=new; expires=Thu, 11-Nov-2027 00:00:00 GMT; path=/; domain=.youtube.com",
        "__Secure-1PSIDTS=ts1; path=/; Secure; HttpOnly",
        "YSC=deleted; expires=Thu, 01-Jan-1970 00:00:00 GMT",
        "SAPISID=abc; path=/",                                   # sin cambio
        "basura sin igual",
    ])
    assert merged == "SAPISID=abc; SIDCC=new; __Secure-1PSIDTS=ts1"
    assert changed == ["SIDCC", "__Secure-1PSIDTS", "YSC"]
    assert merge_set_cookies(header, []) == (header, [])


class _FakeResponse:
    def __init__(self, url, set_cookies):
        self.url = url

        class _Headers:
            def getlist(self, name):
                return list(set_cookies) if name == "Set-Cookie" else []

        self.raw = type("Raw", (), {"headers": _Headers()})()


def test_cookie_keeper_persists_only_youtube_cookies_and_never_a_dead_session(tmp_path):
    import json

    path = tmp_path / "browser.json"
    path.write_text(json.dumps({"Cookie": "SAPISID=abc; SIDCC=old", "x-goog-authuser": "0"}), encoding="utf-8")
    keeper = CookieKeeper(path)
    keeper.hook(_FakeResponse("https://music.youtube.com/youtubei/v1/browse", ["SIDCC=new; path=/"]))
    keeper.hook(_FakeResponse("https://otro-sitio.example/x", ["SAPISID=robada; path=/"]))   # se ignora
    assert keeper.persist() == ["SIDCC"]
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved == {"Cookie": "SAPISID=abc; SIDCC=new", "x-goog-authuser": "0"}
    # Si el servidor borra la sesión, no se pisa el archivo con una cabecera sin SAPISID.
    keeper.hook(_FakeResponse("https://music.youtube.com/x", ["SAPISID=deleted; path=/"]))
    assert keeper.persist() == []
    assert "SAPISID=abc" in path.read_text(encoding="utf-8")


def test_device_bound_sessions_are_recognised():
    from ytm_native.services.sync import looks_device_bound

    chrome = "cookie: SID=a; __Secure-1PSIDTS=b; __Secure-1PSIDRTS=c; SAPISID=d"
    firefox = "cookie: SID=a; __Secure-1PSIDTS=b; SAPISID=d"
    assert looks_device_bound(chrome)
    assert not looks_device_bound(firefox)
    assert not looks_device_bound("")
