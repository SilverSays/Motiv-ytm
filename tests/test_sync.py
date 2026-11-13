import pytest

from ytm_native.core import config
from ytm_native.services.sync import (
    LibrarySync,
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
    assert not is_auth_error(KeyError("contents"))


def test_search_uses_given_client_and_skips_items_without_id():
    client = FakeClient()
    results = search_songs("algo", client_factory=lambda: client)
    assert [t.video_id for t in results] == ["s1"]
