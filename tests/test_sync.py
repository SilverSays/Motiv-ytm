import json
import os
import urllib.error

import pytest

from ytm_native.core import config
from ytm_native.services import sync as sync_module
from ytm_native.services.sync import (
    CookieKeeper,
    LibrarySync,
    SessionExpired,
    SyncState,
    full_sync_interval_s,
    is_auth_error,
    parse_count,
    search_songs,
)


def item(video_id, title="T"):
    return {
        "videoId": video_id, "title": title, "artists": [{"name": "A"}, {"name": "B"}],
        "album": {"name": "Disco"}, "duration_seconds": 180, "setVideoId": f"s-{video_id}",
    }


class FakeClient:
    def __init__(self):
        self.library = []          # [{playlistId, title, count}]
        self.playlists = {}        # id -> [items]
        self.fetched = []
        self.fail_library = None
        self.fail_playlist = {}
        self.logged_in = True
        self.reply = {}            # id -> respuesta tal cual de get_playlist

    def get_account_info(self):
        if not self.logged_in:
            raise KeyError("Unable to find 'header'")      # así responde ytmusicapi sin sesión
        return {"accountName": "Cuenta de prueba"}

    def get_library_playlists(self, limit=None):
        assert limit is None
        if self.fail_library:
            raise self.fail_library
        return list(self.library)

    def get_playlist(self, playlist_id, limit=None):
        assert limit is None
        self.fetched.append(playlist_id)
        if playlist_id in self.fail_playlist:
            raise self.fail_playlist[playlist_id]
        if playlist_id in self.reply:
            return self.reply[playlist_id]
        tracks = self.playlists.get(playlist_id, [])
        return {"tracks": list(tracks), "trackCount": len(tracks)}

    def search(self, query, filter=None, limit=20):
        return [item("s1", "Resultado"), {"title": "sin id"}]


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now


@pytest.fixture
def env(db):
    client = FakeClient()
    clock = Clock()
    pauses = []
    sync = LibrarySync(
        db, lambda: client, credentials_present=lambda: True, clock=clock, pause=pauses.append
    )
    return type("Env", (), dict(sync=sync, client=client, clock=clock, db=db, pauses=pauses))


def test_parse_count_handles_every_shape():
    assert parse_count(5) == 5
    assert parse_count("5") == 5
    assert parse_count("1,234") == 1234
    assert parse_count("12 canciones") == 12
    assert parse_count(None) is None
    assert parse_count("") is None
    assert parse_count(True) is None


def test_first_sync_downloads_everything_with_pauses_between_lists(env):
    env.client.library = [
        {"playlistId": "PL1", "title": "Uno", "count": "2"},
        {"playlistId": "PL2", "title": "Dos", "count": 1},
    ]
    env.client.playlists = {"PL1": [item("a"), item("b")], "PL2": [item("c")]}
    report = env.sync.run()
    assert report.state is SyncState.IDLE and report.playlists_downloaded == 2
    assert [t.video_id for t in env.db.playlist_tracks("PL1")] == ["a", "b"]
    assert env.db.playlist_tracks("PL1")[0].artists == "A, B"
    assert len(env.pauses) == 1                       # pausa entre listas, no antes de la primera
    lo, hi = config.SYNC_PAUSE_BETWEEN_LISTS_S
    assert lo <= env.pauses[0] <= hi


def test_compares_stored_count_not_row_count(env):
    """Lista con una pista eliminada: count=3 pero solo 2 filas guardables."""
    env.client.library = [{"playlistId": "PL1", "title": "Uno", "count": "3"}]
    env.client.playlists = {"PL1": [item("a"), item("b"), {"videoId": None, "title": "borrada"}]}
    env.sync.run()
    assert env.db.get_playlist("PL1").remote_count == 3
    assert env.db.get_playlist("PL1").stored_tracks == 2
    env.client.fetched.clear()
    env.clock.now += 3600
    env.sync.run()
    assert env.client.fetched == []                   # no se re-descarga en cada sincronización


def test_changed_count_triggers_download(env):
    env.client.library = [{"playlistId": "PL1", "title": "Uno", "count": 1}]
    env.client.playlists = {"PL1": [item("a")]}
    env.sync.run()
    env.client.library[0]["count"] = 2
    env.client.playlists["PL1"].append(item("b"))
    env.client.fetched.clear()
    env.sync.run()
    assert env.client.fetched == ["PL1"]
    assert len(env.db.playlist_tracks("PL1")) == 2


def test_periodic_full_sync_covers_count_blind_spot(env):
    env.client.library = [{"playlistId": "PL1", "title": "Uno", "count": 2}]
    env.client.playlists = {"PL1": [item("a"), item("b")]}
    env.sync.run()
    # Reordenada: mismo conteo.
    env.client.playlists["PL1"] = [item("b"), item("a")]
    env.client.fetched.clear()
    env.clock.now += full_sync_interval_s("PL1") - 60
    env.sync.run()
    assert env.client.fetched == []
    env.clock.now += 120
    env.sync.run()
    assert env.client.fetched == ["PL1"]
    assert [t.video_id for t in env.db.playlist_tracks("PL1")] == ["b", "a"]
    days = full_sync_interval_s("PL1") / 86400
    assert config.FULL_SYNC_MIN_DAYS <= days <= config.FULL_SYNC_MAX_DAYS


def test_forced_sync_of_a_single_list(env):
    env.client.library = [
        {"playlistId": "PL1", "title": "Uno", "count": 1},
        {"playlistId": "PL2", "title": "Dos", "count": 1},
    ]
    env.client.playlists = {"PL1": [item("a")], "PL2": [item("c")]}
    env.sync.run()
    env.client.fetched.clear()
    env.sync.run(only_playlist="PL2")
    assert env.client.fetched == ["PL2"]
    assert len(env.db.playlists()) == 2               # una sincronización parcial no borra nada


def test_missing_count_falls_back_to_time_based_resync(env):
    env.client.library = [{"playlistId": "LM", "title": "Me gusta"}]
    env.client.playlists = {"LM": [item("a")]}
    env.sync.run()
    assert env.db.get_playlist("LM").remote_count == 1    # se usa trackCount de la propia lista
    env.client.fetched.clear()
    env.clock.now += 3600
    env.sync.run()
    assert env.client.fetched == []
    env.clock.now += config.NO_COUNT_RESYNC_S
    env.sync.run()
    assert env.client.fetched == ["LM"]


def test_removed_playlists_are_deleted_but_empty_library_is_suspicious(env):
    env.client.library = [
        {"playlistId": "PL1", "title": "Uno", "count": 1},
        {"playlistId": "PL2", "title": "Dos", "count": 1},
    ]
    env.client.playlists = {"PL1": [item("a")], "PL2": [item("c")]}
    env.sync.run()
    env.client.library = env.client.library[:1]
    report = env.sync.run()
    assert report.playlists_removed == 1
    assert [p.playlist_id for p in env.db.playlists()] == ["PL1"]
    # Biblioteca vacía de golpe: no se borra nada.
    env.client.library = []
    report = env.sync.run()
    assert report.state is SyncState.ERROR
    assert [p.playlist_id for p in env.db.playlists()] == ["PL1"]


def test_logged_out_session_is_detected_even_without_an_http_error(env):
    """Sin sesión, ytmusicapi devuelve una biblioteca vacía en lugar de un 401."""
    env.client.library = [{"playlistId": "PL1", "title": "Uno", "count": 1}]
    env.client.playlists = {"PL1": [item("a")]}
    env.sync.run()
    env.client.library = []
    env.client.logged_in = False
    report = env.sync.run()
    assert report.state is SyncState.AUTH_EXPIRED
    assert [p.playlist_id for p in env.db.playlists()] == ["PL1"]     # no se borra nada
    assert not env.sync.due()


def test_logged_out_on_first_sync_is_not_reported_as_success(env):
    env.client.logged_in = False
    assert env.sync.run().state is SyncState.AUTH_EXPIRED


def test_auth_expiry_stops_automatic_retries(env):
    env.db.set_setting("last_sync_check", 0)
    assert env.sync.due()
    env.client.fail_library = Exception("Server returned HTTP 401: Unauthorized.")
    report = env.sync.run()
    assert report.state is SyncState.AUTH_EXPIRED
    assert env.sync.state() is SyncState.AUTH_EXPIRED
    env.clock.now += 10 * 86400
    assert not env.sync.due()                         # sin reintentos en bucle
    env.sync.credentials_renewed()
    assert env.sync.due()


def test_one_broken_playlist_does_not_stop_the_rest(env):
    env.client.library = [
        {"playlistId": "PL1", "title": "Rota", "count": 1},
        {"playlistId": "PL2", "title": "Dos", "count": 1},
    ]
    env.client.playlists = {"PL2": [item("c")]}
    env.client.fail_playlist = {"PL1": KeyError("contents")}
    report = env.sync.run()
    assert report.state is SyncState.IDLE and len(report.errors) == 1
    assert len(env.db.playlist_tracks("PL2")) == 1
    assert env.db.get_playlist("PL1").last_error


def test_due_respects_minimum_interval(env):
    env.client.library = [{"playlistId": "PL1", "title": "Uno", "count": 0}]
    env.sync.run()
    assert not env.sync.due()
    env.clock.now += config.SYNC_MIN_INTERVAL_S + 1
    assert env.sync.due()


def test_auth_error_detection():
    assert is_auth_error(Exception("Server returned HTTP 403: Forbidden"))
    assert is_auth_error(Exception("Server returned HTTP 401: Unauthorized.\nalgo"))
    assert is_auth_error(SessionExpired("la sesión está cerrada"))
    assert is_auth_error(Exception("Please provide authentication before using this function"))
    assert not is_auth_error(KeyError("contents"))


@pytest.mark.parametrize("message", [
    "Connection failed to 192.168.1.40:4030",
    "HTTPSConnectionPool(host='music.youtube.com', port=4403): Max retries exceeded",
    "<urllib3.connection.HTTPSConnection object at 0x000001F403A1B401>: timed out",
    "https://music.youtube.com/browse/VL401abc403 no responde",
    "Server returned HTTP 500: Internal Server Error. 401 veces",
])
def test_numbers_that_are_not_http_codes_are_not_auth_errors(message):
    assert not is_auth_error(Exception(message))


def test_auth_error_from_the_response_object():
    class Response:
        def __init__(self, code):
            self.status_code = code

    class HTTPError(Exception):
        def __init__(self, code):
            super().__init__(f"error {code} en la petición")
            self.response = Response(code)

    assert is_auth_error(HTTPError(401))
    assert is_auth_error(HTTPError(403))
    assert not is_auth_error(HTTPError(404))
    assert is_auth_error(urllib.error.HTTPError("https://x", 403, "Forbidden", {}, None))
    assert not is_auth_error(urllib.error.HTTPError("https://x", 503, "Unavailable", {}, None))
    # Un error envuelto: cuenta la causa.
    try:
        try:
            raise HTTPError(401)
        except HTTPError as inner:
            raise RuntimeError("falló la petición") from inner
    except RuntimeError as outer:
        assert is_auth_error(outer)


# ---------------------------------------------------------------------------
# Una lista que falla no corta la sincronización ni caduca la sesión
# ---------------------------------------------------------------------------
def test_a_forbidden_playlist_is_isolated_while_the_account_works(env):
    env.client.library = [
        {"playlistId": "PL1", "title": "Privada", "count": 1},
        {"playlistId": "PL2", "title": "Dos", "count": 1},
    ]
    env.client.playlists = {"PL2": [item("c")]}
    env.client.fail_playlist = {"PL1": Exception("Server returned HTTP 403: Forbidden")}
    report = env.sync.run()
    assert report.state is SyncState.IDLE and env.sync.state() is SyncState.IDLE
    assert len(report.errors) == 1 and "Privada" in report.errors[0]
    assert len(env.db.playlist_tracks("PL2")) == 1
    assert env.db.get_playlist("PL1").last_error


def test_an_auth_error_in_a_playlist_with_the_session_gone_expires_it(env):
    env.client.library = [{"playlistId": "PL1", "title": "Uno", "count": 1}]
    env.client.fail_playlist = {"PL1": Exception("Server returned HTTP 401: Unauthorized")}
    env.client.logged_in = False
    report = env.sync.run()
    assert report.state is SyncState.AUTH_EXPIRED


def test_a_garbled_playlist_answer_does_not_stop_the_rest(env):
    env.client.library = [
        {"playlistId": "PL1", "title": "Rara", "count": 1},
        {"playlistId": "PL2", "title": "Dos", "count": 1},
    ]
    env.client.playlists = {"PL2": [item("c")]}
    env.client.reply = {"PL1": None}
    report = env.sync.run()
    assert report.state is SyncState.IDLE and len(report.errors) == 1
    assert len(env.db.playlist_tracks("PL2")) == 1


def test_a_failed_playlist_is_retried_on_the_next_sync(env):
    env.client.library = [{"playlistId": "PL1", "title": "Uno", "count": 1}]
    env.client.playlists = {"PL1": [item("a")]}
    env.sync.run()
    env.client.fail_playlist = {"PL1": KeyError("contents")}
    env.sync.run(force_all=True)
    assert env.db.get_playlist("PL1").last_error
    env.client.fail_playlist = {}
    env.client.fetched.clear()
    env.clock.now += 3600
    env.sync.run()                                    # mismo conteo, pero quedó con error
    assert env.client.fetched == ["PL1"]
    assert env.db.get_playlist("PL1").last_error is None


# ---------------------------------------------------------------------------
# Respuestas truncadas: no se borra lo que había
# ---------------------------------------------------------------------------
def twenty_songs(env):
    env.client.library = [{"playlistId": "PL1", "title": "Grande", "count": 20}]
    env.client.playlists = {"PL1": [item(f"v{i}") for i in range(20)]}
    env.sync.run()
    assert len(env.db.playlist_tracks("PL1")) == 20


def test_a_truncated_playlist_keeps_its_songs_and_is_retried(env):
    twenty_songs(env)
    env.client.reply = {"PL1": {"tracks": [item("v0"), item("v1")], "trackCount": 20}}
    report = env.sync.run(force_all=True)
    assert len(env.db.playlist_tracks("PL1")) == 20
    assert len(report.errors) == 1 and "2 de las 20" in report.errors[0]
    assert env.db.get_playlist("PL1").last_error
    # La siguiente sincronización la vuelve a pedir; esta vez llega entera.
    env.client.reply = {}
    env.client.fetched.clear()
    env.clock.now += 3600
    report = env.sync.run()
    assert env.client.fetched == ["PL1"] and not report.errors
    assert len(env.db.playlist_tracks("PL1")) == 20
    assert env.db.get_setting(sync_module.SUSPECT_KEY + "PL1") is None


def test_an_empty_answer_for_a_full_playlist_deletes_nothing(env):
    twenty_songs(env)
    env.client.reply = {"PL1": {"tracks": [], "trackCount": None}}
    report = env.sync.run(force_all=True)
    assert len(env.db.playlist_tracks("PL1")) == 20 and len(report.errors) == 1


def test_the_same_short_answer_twice_is_accepted(env):
    twenty_songs(env)
    env.client.reply = {"PL1": {"tracks": [item("v0"), item("v1")], "trackCount": 20}}
    env.sync.run(force_all=True)
    assert len(env.db.playlist_tracks("PL1")) == 20
    env.sync.run(force_all=True)                      # no era un corte pasajero
    assert len(env.db.playlist_tracks("PL1")) == 2


def test_a_playlist_that_really_shrank_is_replaced_at_once(env):
    twenty_songs(env)
    env.client.library[0]["count"] = 2
    env.client.playlists["PL1"] = [item("v0"), item("v1")]
    report = env.sync.run()
    assert not report.errors
    assert [t.video_id for t in env.db.playlist_tracks("PL1")] == ["v0", "v1"]


# ---------------------------------------------------------------------------
# Umbral contra borrados masivos de listas
# ---------------------------------------------------------------------------
def test_a_library_that_suddenly_lost_most_lists_deletes_nothing(env):
    env.client.library = [{"playlistId": f"PL{i}", "title": f"L{i}", "count": 1} for i in range(20)]
    env.client.playlists = {f"PL{i}": [item(f"v{i}")] for i in range(20)}
    env.sync.run()
    assert len(env.db.playlists()) == 20
    env.client.library = env.client.library[:1]
    report = env.sync.run()
    assert report.state is SyncState.IDLE and report.playlists_removed == 0
    assert len(env.db.playlists()) == 20
    assert report.warnings and "1 listas de las 20" in report.warnings[0]
    assert report.warnings[0] in report.message
    # Si la siguiente sincronización trae lo mismo, es que se borraron de verdad.
    report = env.sync.run()
    assert report.playlists_removed == 19 and not report.warnings
    assert [p.playlist_id for p in env.db.playlists()] == ["PL0"]
    assert env.db.get_setting(sync_module.SHRINK_KEY) is None


def test_a_short_library_once_then_whole_again_deletes_nothing(env):
    env.client.library = [{"playlistId": f"PL{i}", "title": f"L{i}", "count": 1} for i in range(8)]
    env.client.playlists = {f"PL{i}": [item(f"v{i}")] for i in range(8)}
    env.sync.run()
    full = list(env.client.library)
    env.client.library = full[:2]
    assert env.sync.run().playlists_removed == 0
    env.client.library = full
    assert env.sync.run().playlists_removed == 0
    assert len(env.db.playlists()) == 8
    assert env.db.get_setting(sync_module.SHRINK_KEY) is None


def test_local_playlists_do_not_count_for_the_threshold(env):
    from ytm_native.core.db import Track

    for i in range(10):
        env.db.create_local_playlist(f"Mía {i}", [Track(f"m{i}", "M")])
    env.client.library = [
        {"playlistId": "PL1", "title": "Uno", "count": 1},
        {"playlistId": "PL2", "title": "Dos", "count": 1},
    ]
    env.client.playlists = {"PL1": [item("a")], "PL2": [item("b")]}
    env.sync.run()
    env.client.library = env.client.library[:1]
    report = env.sync.run()
    assert report.playlists_removed == 1 and not report.warnings
    assert len([p for p in env.db.playlists() if p.local]) == 10


# ---------------------------------------------------------------------------
# Cookies renovadas: escritura atómica y sin pisar credenciales nuevas
# ---------------------------------------------------------------------------
SESSION = "SID=1; SAPISID=abc; __Secure-3PSID=x"


def write_browser_json(path, cookie=SESSION):
    path.write_text(json.dumps({"Cookie": cookie, "User-Agent": "UA"}), encoding="utf-8")


def test_rotated_cookies_are_synced_to_disk_before_replacing(tmp_path, monkeypatch):
    path = tmp_path / "browser.json"
    write_browser_json(path)
    synced = []
    real_fsync = os.fsync
    monkeypatch.setattr(sync_module.os, "fsync", lambda fd: (synced.append(fd), real_fsync(fd)))
    keeper = CookieKeeper(path, baseline=SESSION)
    keeper.set_cookies = ["SID=2; Path=/; Secure"]
    assert keeper.persist() == ["SID"]
    assert len(synced) == 1
    assert json.loads(path.read_text(encoding="utf-8"))["Cookie"].startswith("SID=2;")
    assert not path.with_name("browser.json.tmp").exists()


def test_a_failed_replace_keeps_the_file_and_removes_the_temporary(tmp_path, monkeypatch):
    path = tmp_path / "browser.json"
    write_browser_json(path)

    def locked(src, dst):
        raise PermissionError("el archivo está en uso")

    monkeypatch.setattr(sync_module.os, "replace", locked)
    keeper = CookieKeeper(path, baseline=SESSION)
    keeper.set_cookies = ["SID=2; Path=/"]
    assert keeper.persist() == []
    assert json.loads(path.read_text(encoding="utf-8"))["Cookie"] == SESSION
    assert not path.with_name("browser.json.tmp").exists()


def test_cookies_of_an_old_session_do_not_overwrite_new_credentials(tmp_path):
    path = tmp_path / "browser.json"
    write_browser_json(path)
    keeper = CookieKeeper(path, baseline=SESSION)
    keeper.set_cookies = ["SID=2; Path=/"]
    new = "SID=nueva; SAPISID=otra; __Secure-3PSID=y"
    write_browser_json(path, new)              # el usuario importó cabeceras a mitad de sincronización
    assert keeper.persist() == []
    assert json.loads(path.read_text(encoding="utf-8"))["Cookie"] == new


def test_search_uses_given_client_and_skips_items_without_id():
    client = FakeClient()
    results = search_songs("algo", client_factory=lambda: client)
    assert [t.video_id for t in results] == ["s1"]
