"""Fase 8.4: cambios opcionales en la cuenta de YouTube Music."""

import pytest

from ytm_native.core import config
from ytm_native.core.db import Track
from ytm_native.services import account_actions as aa
from ytm_native.services.account_actions import (
    AccountActionError, AccountActions, AccountClient, MutationsDisabled, RateLimiter,
)

SONG = Track("vid1", "Canción", "Artista")
LOCAL = Track("local:abc", "Del disco", "Yo")


class FakeClient:
    """Doble de ``ytmusicapi.YTMusic`` que apunta cada llamada."""

    def __init__(self, fail=None, in_library=False, tokens=True):
        self.calls = []
        self.fail = fail
        self.in_library = in_library
        self.tokens = tokens

    def _call(self, name, *args):
        self.calls.append((name,) + args)
        if self.fail is not None:
            raise self.fail

    def rate_song(self, video_id, rating):
        self._call("rate_song", video_id, rating)
        return {}

    def get_watch_playlist(self, videoId=None, limit=25):
        self._call("get_watch_playlist", videoId)
        tokens = {"add": "TOK_ADD", "remove": "TOK_DEL"} if self.tokens else None
        return {"tracks": [{"videoId": videoId, "likeStatus": "LIKE", "inLibrary": self.in_library,
                            "feedbackTokens": tokens}]}

    def edit_song_library_status(self, tokens):
        self._call("edit_song_library_status", tokens)
        return {"feedbackResponses": [{"isProcessed": True}]}

    def create_playlist(self, title, description, privacy_status="PRIVATE", video_ids=None):
        self._call("create_playlist", title, privacy_status, video_ids)
        return "PLnueva"

    def add_playlist_items(self, playlist_id, video_ids, duplicates=False):
        self._call("add_playlist_items", playlist_id, video_ids)
        return {"status": "STATUS_SUCCEEDED"}

    def remove_playlist_items(self, playlist_id, videos):
        self._call("remove_playlist_items", playlist_id, videos)
        return "STATUS_SUCCEEDED"

    def rate_playlist(self, playlist_id, rating):
        self._call("rate_playlist", playlist_id, rating)
        return {}

    def get_song(self, video_id):
        self._call("get_song", video_id)
        return {"playbackTracking": {"videostatsPlaybackUrl": {"baseUrl": "https://x"}}}

    def add_history_item(self, song):
        self._call("add_history_item")
        return type("Resp", (), {"status_code": 204})()


class NoWait(RateLimiter):
    def __init__(self):
        super().__init__(min_interval=0.0)


def make_client(db, fake, present=True):
    created = []

    def factory():
        created.append(fake)
        return fake

    client = AccountClient(db, client_factory=factory, limiter=NoWait(), credentials_present=lambda: present)
    return client, created


def sync_runner(fn, done):
    try:
        result = fn()
    except Exception as exc:  # noqa: BLE001
        done(None, exc)
    else:
        done(result, None)


class DeferredRunner:
    """Guarda los trabajos y los termina cuando la prueba lo dice."""

    def __init__(self):
        self.jobs = []

    def __call__(self, fn, done):
        self.jobs.append((fn, done))

    def finish(self, index=0):
        fn, done = self.jobs.pop(index)
        sync_runner(fn, done)


def enable(db, history=False):
    db.set_setting(config.ACCOUNT_MUTATIONS_KEY, "1")
    db.set_setting(config.ACCOUNT_HISTORY_KEY, "1" if history else "0")


# ---------------------------------------------------------------------------
# Conmutador maestro: apagado, ninguna petición
# ---------------------------------------------------------------------------
def test_mutations_are_off_by_default(db):
    assert not db.get_bool(config.ACCOUNT_MUTATIONS_KEY, False)
    assert not aa.mutations_enabled(db) and not aa.history_reporting_enabled(db)


@pytest.mark.parametrize("call", [
    lambda c: c.rate_song("vid1", "LIKE"),
    lambda c: c.set_in_library("vid1", True),
    lambda c: c.track_status("vid1"),
    lambda c: c.create_playlist("Nueva", ["vid1"]),
    lambda c: c.add_playlist_items("PL1", ["vid1"]),
    lambda c: c.remove_playlist_items("PL1", [("vid1", "SET1")]),
    lambda c: c.rate_playlist("PL1", "LIKE"),
    lambda c: c.report_history("vid1"),
])
def test_with_the_switch_off_nothing_reaches_the_network(db, call):
    fake = FakeClient()
    client, created = make_client(db, fake)
    with pytest.raises(MutationsDisabled):
        call(client)
    assert created == [] and fake.calls == []        # ni siquiera se crea el cliente


def test_the_ui_layer_also_stops_before_the_network(db, qapp):
    fake = FakeClient()
    client, created = make_client(db, fake)
    ran = []
    actions = AccountActions(db, runner=lambda fn, done: ran.append(fn), client=client)
    notices = []
    actions.notice.connect(lambda text, kind: notices.append(text))
    assert actions.set_liked(SONG, True) is False
    assert actions.set_in_library(SONG, True) is False
    assert actions.create_playlist("Nueva", [SONG]) is False
    assert actions.report_history(SONG) is False
    assert actions.refresh_status(SONG) is False
    assert ran == [] and created == [] and fake.calls == []
    assert notices and "Permitir cambios" in notices[0]
    assert not actions.liked("vid1")


def test_files_from_disk_are_never_sent(db, qapp):
    enable(db, history=True)
    fake = FakeClient()
    client, created = make_client(db, fake)
    with pytest.raises(AccountActionError):
        client.rate_song(LOCAL.video_id, "LIKE")
    actions = AccountActions(db, runner=sync_runner, client=client)
    assert actions.set_liked(LOCAL, True) is False
    assert actions.report_history(LOCAL) is False
    assert fake.calls == [] and created == []


def test_without_credentials_nothing_is_sent(db):
    enable(db)
    fake = FakeClient()
    client, created = make_client(db, fake, present=False)
    with pytest.raises(AccountActionError):
        client.rate_song("vid1", "LIKE")
    assert created == []


# ---------------------------------------------------------------------------
# Operaciones
# ---------------------------------------------------------------------------
def test_rating_library_and_playlists_call_ytmusicapi(db):
    enable(db)
    fake = FakeClient()
    client, _ = make_client(db, fake)
    client.rate_song("vid1", "LIKE")
    client.set_in_library("vid1", True)
    assert client.create_playlist("Mi lista", ["vid1", "local:x", "vid1", "vid2"]) == "PLnueva"
    client.add_playlist_items("PL1", ["vid3"])
    client.remove_playlist_items("PL1", [("vid3", "SET3")])
    client.rate_playlist("PL1", "INDIFFERENT")
    assert fake.calls == [
        ("rate_song", "vid1", "LIKE"),
        ("get_watch_playlist", "vid1"),
        ("edit_song_library_status", ["TOK_ADD"]),
        ("create_playlist", "Mi lista", "PRIVATE", ["vid1", "vid2"]),   # sin duplicados ni locales
        ("add_playlist_items", "PL1", ["vid3"]),
        ("remove_playlist_items", "PL1", [{"videoId": "vid3", "setVideoId": "SET3"}]),
        ("rate_playlist", "PL1", "INDIFFERENT"),
    ]


def test_removing_from_library_uses_the_remove_token(db):
    enable(db)
    fake = FakeClient(in_library=True)
    client, _ = make_client(db, fake)
    client.set_in_library("vid1", False)
    assert fake.calls[-1] == ("edit_song_library_status", ["TOK_DEL"])


def test_library_change_without_tokens_is_refused(db):
    enable(db)
    fake = FakeClient(tokens=False)
    client, _ = make_client(db, fake)
    with pytest.raises(AccountActionError):
        client.set_in_library("vid1", True)
    assert [c[0] for c in fake.calls] == ["get_watch_playlist"]


def test_bad_input_is_refused_before_sending(db):
    enable(db)
    fake = FakeClient()
    client, _ = make_client(db, fake)
    with pytest.raises(ValueError):
        client.rate_song("vid1", "MEGUSTA")
    with pytest.raises(AccountActionError):
        client.create_playlist("  ", ["vid1"])
    with pytest.raises(AccountActionError):
        client.create_playlist("a <b>", ["vid1"])
    with pytest.raises(AccountActionError):
        client.remove_playlist_items("PL1", [("vid1", "")])
    assert fake.calls == []


def test_track_status_reads_rating_and_library(db):
    enable(db)
    client, _ = make_client(db, FakeClient(in_library=True))
    status = client.track_status("vid1")
    assert status.rating == "LIKE" and status.in_library is True and status.remove_token == "TOK_DEL"


def test_rotated_cookies_are_kept_after_each_change(db):
    enable(db)
    fake = FakeClient()
    persisted = []
    fake._ytmn_cookie_keeper = type("Keeper", (), {"persist": lambda self: persisted.append(1) or []})()
    client, _ = make_client(db, fake)
    client.rate_song("vid1", "LIKE")
    assert persisted == [1]


def test_rate_limiter_spaces_out_bursts():
    now = [100.0]
    slept = []

    def sleep(seconds):
        slept.append(round(seconds, 3))
        now[0] += seconds

    limiter = RateLimiter(min_interval=1.5, clock=lambda: now[0], sleep=sleep)
    for _ in range(3):
        limiter.run(lambda: None)
    assert slept == [1.5, 1.5]          # la primera sale ya; las siguientes, espaciadas
    now[0] += 10
    limiter.run(lambda: None)
    assert slept == [1.5, 1.5]          # tras una pausa larga no se espera


def test_describe_error_messages():
    class Http(Exception):
        def __init__(self, code):
            super().__init__(f"Server returned HTTP {code}")
            self.response = type("R", (), {"status_code": code})()

    assert "caducó" in aa.describe_error(Http(401))
    assert "esperar" in aa.describe_error(Http(429))
    assert aa.describe_error(ConnectionError("x")) == "Sin conexión."
    assert aa.describe_error(AccountActionError("Mensaje propio.")) == "Mensaje propio."


# ---------------------------------------------------------------------------
# Interfaz optimista con vuelta atrás
# ---------------------------------------------------------------------------
def test_like_shows_at_once_and_is_saved_when_it_succeeds(db, qapp):
    enable(db)
    fake = FakeClient()
    client, _ = make_client(db, fake)
    runner = DeferredRunner()
    actions = AccountActions(db, runner=runner, client=client)
    changes = []
    actions.status_changed.connect(changes.append)
    assert actions.set_liked(SONG, True)
    assert actions.liked("vid1") and changes == ["vid1"]          # ya, antes de la respuesta
    assert db.get_account_mark("vid1") == (None, None)            # la base espera a la cuenta
    runner.finish()
    assert fake.calls == [("rate_song", "vid1", "LIKE")]
    assert db.get_account_mark("vid1")[0] == "LIKE" and actions.liked("vid1")


def test_a_failed_like_is_reverted_with_a_notice(db, qapp):
    enable(db)

    class ServerError(Exception):
        def __init__(self):
            super().__init__("Server returned HTTP 500")
            self.response = type("R", (), {"status_code": 500})()

    fake = FakeClient(fail=ServerError())
    client, _ = make_client(db, fake)
    runner = DeferredRunner()
    actions = AccountActions(db, runner=runner, client=client)
    notices, changes = [], []
    actions.notice.connect(lambda text, kind: notices.append((text, kind)))
    actions.status_changed.connect(changes.append)
    actions.set_liked(SONG, True)
    assert actions.liked("vid1")
    runner.finish()
    assert not actions.liked("vid1")                       # vuelve a como estaba
    assert changes == ["vid1", "vid1"]
    assert notices and notices[0][1] == "error" and "Canción" in notices[0][0]
    assert db.get_account_mark("vid1") == (None, None)


def test_changing_your_mind_while_a_request_travels_sends_only_the_last_state(db, qapp):
    enable(db)
    db.set_account_rating("vid1", "INDIFFERENT")
    fake = FakeClient()
    client, _ = make_client(db, fake)
    runner = DeferredRunner()
    actions = AccountActions(db, runner=runner, client=client)
    actions.set_liked(SONG, True)
    actions.set_liked(SONG, False)          # aún no llegó la primera
    actions.set_liked(SONG, True)
    assert len(runner.jobs) == 1            # una sola petición en vuelo por pista
    runner.finish()
    assert runner.jobs == []                # el último estado (LIKE) ya es el confirmado
    assert fake.calls == [("rate_song", "vid1", "LIKE")]
    assert actions.liked("vid1")


def test_last_wish_differs_from_the_one_in_flight(db, qapp):
    enable(db)
    fake = FakeClient()
    client, _ = make_client(db, fake)
    runner = DeferredRunner()
    actions = AccountActions(db, runner=runner, client=client)
    actions.set_liked(SONG, True)
    actions.set_liked(SONG, False)
    runner.finish()
    assert len(runner.jobs) == 1 and not actions.liked("vid1")
    runner.finish()
    assert [c[2] for c in fake.calls] == ["LIKE", "INDIFFERENT"]
    assert db.get_account_mark("vid1")[0] == "INDIFFERENT"


def test_library_change_is_optimistic_too(db, qapp):
    enable(db)
    fake = FakeClient(tokens=False)                 # la cuenta no da token: falla
    client, _ = make_client(db, fake)
    actions = AccountActions(db, runner=sync_runner, client=client)
    notices = []
    actions.notice.connect(lambda text, kind: notices.append(kind))
    actions.set_in_library(SONG, True)
    assert actions.in_library("vid1") is None and notices == ["error"]


def test_refresh_status_asks_once_and_saves(db, qapp):
    enable(db)
    fake = FakeClient(in_library=True)
    client, _ = make_client(db, fake)
    actions = AccountActions(db, runner=sync_runner, client=client)
    assert actions.refresh_status(SONG)
    assert not actions.refresh_status(SONG)             # una vez por sesión
    assert actions.liked("vid1") and actions.in_library("vid1") is True
    assert [c[0] for c in fake.calls] == ["get_watch_playlist"]


def test_create_and_remove_playlist_items_report_back(db, qapp):
    enable(db)
    fake = FakeClient()
    client, _ = make_client(db, fake)
    actions = AccountActions(db, runner=sync_runner, client=client)
    created, removed = [], []
    actions.playlist_created.connect(lambda pid, title: created.append((pid, title)))
    actions.playlist_item_removed.connect(lambda pid, vid: removed.append((pid, vid)))
    assert actions.create_playlist("Copia", [SONG, LOCAL])
    assert created == [("PLnueva", "Copia")]
    assert ("create_playlist", "Copia", "PRIVATE", ["vid1"]) in fake.calls
    assert actions.remove_from_playlist("PL1", SONG, "SET1")
    assert removed == [("PL1", "vid1")]


# ---------------------------------------------------------------------------
# Historial de YouTube
# ---------------------------------------------------------------------------
def test_history_is_reported_only_with_both_options_on(db, qapp):
    fake = FakeClient()
    client, _ = make_client(db, fake)
    actions = AccountActions(db, runner=sync_runner, client=client)
    enable(db, history=False)
    assert actions.report_history(SONG) is False
    db.set_setting(config.ACCOUNT_HISTORY_KEY, "1")
    db.set_setting(config.ACCOUNT_MUTATIONS_KEY, "0")
    assert actions.report_history(SONG) is False        # la sub-opción sola no basta
    assert fake.calls == []
    enable(db, history=True)
    assert actions.report_history(SONG) is True
    assert fake.calls == [("get_song", "vid1"), ("add_history_item",)]


def test_tracker_reports_at_half_or_four_minutes(db, qapp):
    from PySide6.QtCore import QObject, Signal

    from ytm_native.playback.history import PlayTracker, youtube_history_seconds
    from ytm_native.playback.player import PLAYING

    class Player(QObject):
        current_changed = Signal(object)
        position_changed = Signal(float, float)
        state = PLAYING

    class Reporter:
        def __init__(self):
            self.reported = []

        def report_history(self, track):
            self.reported.append(track.video_id)
            return True

    assert youtube_history_seconds(200) == 100 and youtube_history_seconds(600) == 240
    player, reporter = Player(), Reporter()
    tracker = PlayTracker(db, player, reporter=reporter)   # noqa: F841 - vivo durante la prueba
    player.current_changed.emit(Track("vid1", "Larga", "A", duration_s=600))
    pos = 0.0
    player.position_changed.emit(pos, 600.0)
    while pos < 239:
        pos += 1.0
        player.position_changed.emit(pos, 600.0)
    assert reporter.reported == []
    player.position_changed.emit(pos + 1.0, 600.0)
    player.position_changed.emit(pos + 2.0, 600.0)
    assert reporter.reported == ["vid1"]                # una sola vez por escucha
    # Saltar con la barra no cuenta como sonado.
    player.current_changed.emit(Track("vid2", "Corta", "A", duration_s=200))
    player.position_changed.emit(0.0, 200.0)
    player.position_changed.emit(150.0, 200.0)
    assert reporter.reported == ["vid1"]


# ---------------------------------------------------------------------------
# Base de datos
# ---------------------------------------------------------------------------
def test_account_marks_and_playlist_entries(db):
    assert db.get_account_mark("x") == (None, None)
    db.set_account_rating("x", "LIKE")
    db.set_account_library("x", True)
    assert db.get_account_mark("x") == ("LIKE", True)
    db.set_account_library("x", False)
    assert db.get_account_mark("x") == ("LIKE", False)
    db.upsert_playlist_meta("PL1", "Lista", 0)
    db.replace_playlist_tracks("PL1", [(Track("a", "A"), "SA"), (Track("b", "B"), "SB"),
                                       (Track("c", "C"), "SC")], 3)
    assert db.playlist_set_video_id("PL1", "b") == "SB"
    assert db.remove_playlist_entry("PL1", "b", "SB")
    assert [t.video_id for t in db.playlist_tracks("PL1")] == ["a", "c"]
    assert db.playlist_set_video_id("PL1", "c") == "SC"
    assert not db.remove_playlist_entry("PL1", "zz")


# ---------------------------------------------------------------------------
# Ventana
# ---------------------------------------------------------------------------
from test_home_search import FakeNet, net, wait_until, window  # noqa: E402,F401 - fixtures


def test_like_button_only_with_the_switch_on(window, db, monkeypatch):
    from ytm_native.ui import main_window as mw

    monkeypatch.setattr(window.account, "_run", lambda fn, done: None)   # nada sale de la prueba
    window.player.play_tracks([SONG], 0)
    assert window.like_btn.isHidden()
    monkeypatch.setattr(mw.MainWindow, "_confirm", lambda self, *a: True)
    window.mutations_action.setChecked(True)
    assert window.account.enabled and not window.like_btn.isHidden()
    assert window.account_history_action.isEnabled()
    window.like_btn.click()
    assert window.like_btn.isChecked() and window.account.liked("vid1")
    window.mutations_action.setChecked(False)
    assert window.like_btn.isHidden() and not window.account_history_action.isEnabled()


def test_declining_the_warning_keeps_the_switch_off(window, monkeypatch):
    from ytm_native.ui import main_window as mw

    monkeypatch.setattr(mw.MainWindow, "_confirm", lambda self, *a: False)
    window.mutations_action.setChecked(True)
    assert not window.mutations_action.isChecked() and not window.account.enabled


def test_failed_like_reverts_the_button(window, db, monkeypatch):
    enable(db)
    fake = FakeClient(fail=ConnectionError("sin red"))
    client, _ = make_client(db, fake)
    window.account.client = client
    window.account._run = sync_runner
    window.player.play_tracks([SONG], 0)
    window._sync_like_button()
    notices = []
    monkeypatch.setattr(window, "_notice", lambda text, kind="info": notices.append(kind))
    window.account.notice.disconnect()
    window.account.notice.connect(window._notice)
    window.like_btn.click()
    assert not window.like_btn.isChecked() and notices == ["error"]


def test_track_menu_offers_account_actions_only_when_allowed(window, db, monkeypatch):
    monkeypatch.setattr(window.account, "_run", lambda fn, done: None)
    texts = [a.text() for a in window._track_menu(SONG, 0).actions()]
    assert "Me gusta" not in texts
    enable(db)
    texts = [a.text() for a in window._track_menu(SONG, 0).actions()]
    assert "Me gusta" in texts and "Guardar en tu biblioteca de YouTube Music" in texts
    texts = [a.text() for a in window._track_menu(LOCAL, 0).actions()]
    assert "Me gusta" not in texts and "Iniciar radio a partir de esta pista" not in texts
