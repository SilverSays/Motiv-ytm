import pytest

from ytm_native.services.lyrics import (
    Lyrics,
    build_queries,
    clean_lookup_text,
    current_line_index,
    fetch_lyrics,
    parse_lrc,
)
from ytm_native.core import credentials
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
    saved = json.loads(credentials.read_text(path))       # se guarda cifrado
    assert saved == {"Cookie": "SAPISID=abc; SIDCC=new", "x-goog-authuser": "0"}
    assert credentials.is_sealed_file(path)
    # Si el servidor borra la sesión, no se pisa el archivo con una cabecera sin SAPISID.
    keeper.hook(_FakeResponse("https://music.youtube.com/x", ["SAPISID=deleted; path=/"]))
    assert keeper.persist() == []
    assert "SAPISID=abc" in credentials.read_text(path)


def test_device_bound_sessions_are_recognised():
    from ytm_native.services.sync import looks_device_bound

    chrome = "cookie: SID=a; __Secure-1PSIDTS=b; __Secure-1PSIDRTS=c; SAPISID=d"
    firefox = "cookie: SID=a; __Secure-1PSIDTS=b; SAPISID=d"
    assert looks_device_bound(chrome)
    assert not looks_device_bound(firefox)
    assert not looks_device_bound("")


# ---------------------------------------------------------------------------
# Fase 4: LRC por palabra, desfase, caché dañada y presupuesto de tiempo
# ---------------------------------------------------------------------------
def test_word_marks_give_one_clean_line_each():
    lrc = ("[00:12.00]<00:12.00>Hola <00:12.50>mun<00:12.70>do\n"
           "[00:15.00]Otra [00:15.80]frase [00:16.40]aquí\n"
           "<00:20.00>Solo <00:20.40>marcas")
    lines = parse_lrc(lrc)
    assert [(ln.start_s, ln.text) for ln in lines] == [
        (12.0, "Hola mundo"), (15.0, "Otra frase aquí"), (20.0, "Solo marcas"),
    ]


def test_offset_moves_the_whole_lyrics():
    lines = parse_lrc("[offset:+500]\n[00:10.00]Uno\n[00:00.20]Antes")
    assert [(ln.start_s, ln.text) for ln in lines] == [(0.0, "Antes"), (9.5, "Uno")]
    lines = parse_lrc("[00:10.00]Uno\n[offset:-250]")
    assert lines[0].start_s == 10.25


def test_repeated_lines_are_not_duplicated():
    lines = parse_lrc("[00:10.00]Uno\n[00:10.00]Uno\n[00:10.00] [00:30.00]Uno")
    assert [(ln.start_s, ln.text) for ln in lines] == [(10.0, "Uno"), (30.0, "Uno")]


@pytest.mark.parametrize("payload", [
    "", "no es json", "[]", "null", "42", '{"lines": "texto"}',
    '{"lines": [[1, 2], ["a", "b"], [true, "x"], [-1, "x"], [1]], "plain": 3, "source": null}',
    None, b"\xff",
])
def test_corrupt_cached_lyrics_become_empty_instead_of_failing(payload):
    lyrics = Lyrics.from_json(payload)
    assert lyrics.empty and lyrics.source == "LRCLIB"


def test_partly_damaged_cached_lyrics_keep_the_good_lines():
    lyrics = Lyrics.from_json('{"lines": [[3, "tres"], ["x", "mal"], [1.5, "uno"]], "plain": "p"}')
    assert [(ln.start_s, ln.text) for ln in lyrics.lines] == [(1.5, "uno"), (3.0, "tres")]
    assert lyrics.plain == "p"


class SlowLrclib(FakeLrclib):
    """Cada consulta "tarda" ``cost`` segundos en un reloj simulado."""

    def __init__(self, clock, cost, **kwargs):
        super().__init__(**kwargs)
        self.clock, self.cost, self.timeouts = clock, cost, []

    def __call__(self, url, timeout):
        self.timeouts.append(timeout)
        self.clock[0] += self.cost
        return super().__call__(url, timeout)


def test_the_search_stops_when_the_time_budget_runs_out():
    now = [0.0]
    api = SlowLrclib(now, cost=4.0)
    with pytest.raises(TimeoutError):
        fetch_lyrics("Tema (Official Video)", "Ana, Vera", "", 200, http_json=api,
                     budget_s=10.0, clock=lambda: now[0])
    assert len(api.urls) == 3 and all(t <= 10.0 for t in api.timeouts)
    assert api.timeouts[-1] == pytest.approx(2.0)          # la última, con lo que quedaba


def test_a_plain_lyric_found_before_the_budget_ends_is_kept():
    now = [0.0]
    api = SlowLrclib(now, cost=4.0, exact={"duration": 200, "syncedLyrics": None, "plainLyrics": "texto"})
    lyrics = fetch_lyrics("Tema", "Ana, Vera", "", 200, http_json=api, budget_s=10.0, clock=lambda: now[0])
    assert lyrics is not None and lyrics.plain == "texto"


def test_a_later_network_failure_keeps_what_was_found():
    calls = []

    def api(url, timeout):
        calls.append(url)
        if len(calls) == 1:
            return {"duration": 200, "syncedLyrics": None, "plainLyrics": "texto"}
        raise OSError("se cayó la red")

    lyrics = fetch_lyrics("Tema", "Ana, Vera", "", 200, http_json=api)
    assert lyrics.plain == "texto" and len(calls) == 2      # no sigue insistiendo sin red


def test_an_answer_that_is_not_json_counts_as_not_found():
    def api(url, timeout):
        if "/search?" in url:
            return [{"duration": 200, "syncedLyrics": "[00:01.00]buena"}]
        raise ValueError("Expecting value")

    lyrics = fetch_lyrics("Tema", "Artista", "", 200, http_json=api)
    assert [ln.text for ln in lyrics.lines] == ["buena"]


# ---------------------------------------------------------------------------
# Fase 8.3: YouTube Music como segunda fuente
# ---------------------------------------------------------------------------
from ytm_native.services import lyrics as lyrics_mod  # noqa: E402
from ytm_native.services.lyrics import fetch_ytmusic_lyrics, lyrics_from_ytmusic  # noqa: E402


class _TimedLine:
    """Como ``ytmusicapi.models.lyrics.LyricLine`` (tiempos en milisegundos)."""

    def __init__(self, text, start_time):
        self.text, self.start_time, self.end_time, self.id = text, start_time, start_time + 900, 1


class FakeYTMusic:
    def __init__(self, browse_id="MPLYt_abc", lyrics=None):
        self.browse_id, self.lyrics, self.calls = browse_id, lyrics, []

    def get_watch_playlist(self, videoId=None, limit=25, **_kw):
        self.calls.append(("watch", videoId, limit))
        return {"tracks": [], "lyrics": self.browse_id}

    def get_lyrics(self, browseId, timestamps=False):
        self.calls.append(("lyrics", browseId, timestamps))
        return self.lyrics


TIMED = {"lyrics": [_TimedLine("Segunda", 4200), _TimedLine("Primera", 1500), _TimedLine("  ", 9000)],
         "source": "Source: LyricFind", "hasTimestamps": True}


def test_ytmusic_timed_lyrics_become_synced_lines_in_seconds():
    client = FakeYTMusic(lyrics=TIMED)
    found = fetch_ytmusic_lyrics("vid1", client_factory=lambda: client)
    assert found.synced and found.source == "YouTube Music"
    assert [(ln.start_s, ln.text) for ln in found.lines] == [(1.5, "Primera"), (4.2, "Segunda"), (9.0, "")]
    assert client.calls == [("watch", "vid1", 1), ("lyrics", "MPLYt_abc", True)]


def test_ytmusic_plain_lyrics_and_missing_browse_id():
    plain = FakeYTMusic(lyrics={"lyrics": "Una\nDos\n", "source": "x", "hasTimestamps": False})
    found = fetch_ytmusic_lyrics("vid1", client_factory=lambda: plain)
    assert not found.synced and found.plain == "Una\nDos" and found.source == "YouTube Music"
    without = FakeYTMusic(browse_id=None)
    assert fetch_ytmusic_lyrics("vid1", client_factory=lambda: without) is None
    assert without.calls == [("watch", "vid1", 1)]           # sin letra: no se pide nada más


def test_ytmusic_is_never_asked_about_files_from_disk():
    client = FakeYTMusic(lyrics=TIMED)
    assert fetch_ytmusic_lyrics("local:abc", client_factory=lambda: client) is None
    assert client.calls == []


@pytest.mark.parametrize("data", [None, [], {"lyrics": None}, {"lyrics": ""}, {"lyrics": [{"text": 3}]},
                                  {"lyrics": [{"text": "x", "start_time": -5}]}])
def test_odd_ytmusic_answers_count_as_no_lyrics(data):
    assert lyrics_from_ytmusic(data) is None


def test_ytmusic_lines_given_as_dicts_also_work():
    found = lyrics_from_ytmusic({"lyrics": [{"text": "Hola", "start_time": 2000}], "hasTimestamps": True})
    assert [(ln.start_s, ln.text) for ln in found.lines] == [(2.0, "Hola")]


class FakeYT:
    """Doble de ``fetch_ytmusic_lyrics`` para el motor de búsqueda."""

    def __init__(self, result=None, error=None, clock=None, cost=0.0):
        self.result, self.error, self.clock, self.cost = result, error, clock, cost
        self.calls = []

    def __call__(self, video_id, timeout):
        self.calls.append((video_id, timeout))
        if self.clock is not None:
            self.clock[0] += self.cost
        if self.error is not None:
            raise self.error
        return self.result


YT_SYNCED = Lyrics(lines=parse_lrc("[00:01.00]de youtube"), plain="de youtube", source="YouTube Music")
YT_PLAIN = Lyrics(plain="texto de youtube", source="YouTube Music")


def test_ytmusic_synced_wins_when_lrclib_has_no_synced_lyrics():
    api = FakeLrclib(exact={"duration": 200, "syncedLyrics": None, "plainLyrics": "texto lrclib"},
                     search=[{"duration": 200, "syncedLyrics": None, "plainLyrics": "otro"}])
    yt = FakeYT(result=YT_SYNCED)
    found = fetch_lyrics("Tema", "Artista", "", 200, http_json=api, video_id="vid1", ytmusic=yt)
    assert found == YT_SYNCED and found.source == "YouTube Music"
    assert yt.calls and yt.calls[0][0] == "vid1"
    # Prioridad 2 antes que la 3: con la de YouTube Music ya no se busca en LRCLIB.
    assert not any("/search?" in url for url in api.urls)


def test_exact_lrclib_synced_lyrics_do_not_ask_youtube():
    api = FakeLrclib(exact={"duration": 200, "syncedLyrics": LRC})
    yt = FakeYT(result=YT_SYNCED)
    found = fetch_lyrics("Tema", "Artista", "", 200, http_json=api, video_id="vid1", ytmusic=yt)
    assert found.source == "LRCLIB" and yt.calls == []


def test_lrclib_search_synced_beats_plain_ytmusic():
    api = FakeLrclib(exact=None, search=[{"duration": 200, "syncedLyrics": "[00:01.00]buscada"}])
    found = fetch_lyrics("Tema", "Artista", "", 200, http_json=api, video_id="vid1", ytmusic=FakeYT(YT_PLAIN))
    assert [ln.text for ln in found.lines] == ["buscada"] and found.source == "LRCLIB"


def test_plain_lyrics_prefer_lrclib_then_ytmusic():
    lrclib_plain = FakeLrclib(exact={"duration": 200, "syncedLyrics": None, "plainLyrics": "de lrclib"})
    found = fetch_lyrics("Tema", "A", "", 200, http_json=lrclib_plain, video_id="v", ytmusic=FakeYT(YT_PLAIN))
    assert found.plain == "de lrclib" and found.source == "LRCLIB"
    found = fetch_lyrics("Tema", "A", "", 200, http_json=FakeLrclib(), video_id="v", ytmusic=FakeYT(YT_PLAIN))
    assert found == YT_PLAIN


def test_without_video_id_or_for_local_files_youtube_is_not_asked():
    yt = FakeYT(result=YT_SYNCED)
    assert fetch_lyrics("Tema", "A", "", 200, http_json=FakeLrclib(), ytmusic=yt) is None
    assert fetch_lyrics("Tema", "A", "", 200, http_json=FakeLrclib(), video_id="local:1", ytmusic=yt) is None
    assert yt.calls == []


def test_ytmusic_network_failure_is_a_failure_and_odd_errors_are_not():
    with pytest.raises(OSError):
        fetch_lyrics("Tema", "A", "", 200, http_json=FakeLrclib(), video_id="v",
                     ytmusic=FakeYT(error=ConnectionError("sin red")))
    # Un fallo que no es de red (respuesta rara) no tumba la búsqueda en LRCLIB.
    api = FakeLrclib(search=[{"duration": 200, "syncedLyrics": "[00:01.00]sigue"}])
    found = fetch_lyrics("Tema", "A", "", 200, http_json=api, video_id="v", ytmusic=FakeYT(error=KeyError("x")))
    assert [ln.text for ln in found.lines] == ["sigue"]


def test_ytmusic_respects_the_global_time_budget():
    now = [0.0]
    api = SlowLrclib(now, cost=4.0)
    yt = FakeYT(result=None, clock=now, cost=20.0)          # sin red: agota el plazo que le den
    with pytest.raises(TimeoutError):
        fetch_lyrics("Tema", "Artista", "", 200, http_json=api, budget_s=10.0,
                     clock=lambda: now[0], video_id="vid1", ytmusic=yt)
    (_vid, timeout), = yt.calls
    assert timeout == pytest.approx(6.0)                      # lo que quedaba tras LRCLIB
    assert len(api.urls) == 1                                  # y después ya no se pregunta más
    # Con tiempo de sobra, YouTube Music no pasa de su propio tope.
    now[0] = 0.0
    yt = FakeYT(result=None)
    fetch_lyrics("Tema", "Artista", "", 200, http_json=FakeLrclib(), budget_s=60.0,
                 clock=lambda: now[0], video_id="vid1", ytmusic=yt)
    assert yt.calls[0][1] == lyrics_mod.config.LYRICS_YTMUSIC_TIMEOUT_S


def test_ytmusic_lyrics_keep_their_source_in_the_cache(db):
    db.set_lyrics_entry("v1", YT_SYNCED.to_json())
    cached = Lyrics.from_json(db.get_lyrics_entry("v1")[1])
    assert cached == YT_SYNCED and cached.source == "YouTube Music"


def test_the_lyrics_panel_credits_the_real_source(qapp):
    from ytm_native.ui.components import LyricsPanel

    panel = LyricsPanel()
    panel.show_lines(["de youtube"], True, f"de {YT_SYNCED.source}")
    assert panel.source.text() == "de YouTube Music"
    panel.show_lines(["texto"], False, f"de {YT_PLAIN.source}, sin sincronizar")
    assert panel.source.text() == "de YouTube Music, sin sincronizar"


def test_the_window_asks_youtube_with_the_track_id(monkeypatch, db, qapp):
    """La ventana pasa el videoId a la búsqueda y el crédito sale de la letra."""
    from test_home_search import wait_until
    from ytm_native.core.db import Track
    from ytm_native.ui import main_window as mw

    calls = []

    def fake_fetch(title, artists, album, duration, video_id=""):
        calls.append(video_id)
        return YT_SYNCED

    monkeypatch.setattr(mw, "fetch_lyrics", fake_fetch)
    shown = []

    class Host:
        db = None
        _lyrics_for = None

        def __init__(self):
            self.lyrics = type("L", (), {"count": lambda s: 0,
                                         "show_message": lambda s, t: None,
                                         "show_lines": lambda s, lines, synced, source: shown.append(source)})()
            self.db = db

    host = Host()
    host._lyrics_message = lambda text: mw.MainWindow._lyrics_message(host, text)
    host._show_lyrics = lambda lyrics: mw.MainWindow._show_lyrics(host, lyrics)
    mw.MainWindow._load_lyrics(host, Track("vid9", "Tema", "Artista", "", 200))
    assert wait_until(lambda: shown, 3000)
    assert calls == ["vid9"] and shown == ["de YouTube Music"]


def test_shared_lyrics_client_waits_its_turn(monkeypatch):
    """El cliente propio de las letras se usa de uno en uno; si otro lo
    retiene todo el plazo, esta consulta se rinde sin letra (no se cuelga)."""
    shared = lyrics_mod._lyrics_client
    assert shared.lock.acquire()
    try:
        assert fetch_ytmusic_lyrics("vid1", timeout=0.05) is None
    finally:
        shared.lock.release()
    client = FakeYTMusic(lyrics=TIMED)
    monkeypatch.setattr(shared, "get", lambda: client)
    assert fetch_ytmusic_lyrics("vid1", timeout=2.0).synced
    assert not shared.lock.locked()
