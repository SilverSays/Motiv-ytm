import threading
import time

from ytm_native.core.db import Track
from ytm_native.core.errors import ErrorKind, classify, cookies_look_invalid, summarize

BOT = "ERROR: [youtube] abc: Sign in to confirm you’re not a bot. Use --cookies-from-browser"
UNAVAILABLE = "ERROR: [youtube] abc: Video unavailable. This video is not available"
PRIVATE = "ERROR: [youtube] abc: Private video. Sign in if you've been granted access to this video"
AGE = "ERROR: [youtube] abc: Sign in to confirm your age. This video may be inappropriate for some users."
NSIG = "ERROR: [youtube] abc: nsig extraction failed: Some formats may be missing"
DNS = "ERROR: Unable to download webpage: <urlopen error [Errno 11001] getaddrinfo failed>"
RATE = "ERROR: [youtube] abc: Video unavailable. This content isn't available, try again later."


def test_classification_basics():
    assert classify(BOT) is ErrorKind.BOT_BLOCK
    assert classify(UNAVAILABLE) is ErrorKind.UNAVAILABLE
    assert classify(PRIVATE) is ErrorKind.UNAVAILABLE
    assert classify(AGE) is ErrorKind.NEEDS_LOGIN
    assert classify(NSIG) is ErrorKind.EXTRACTION
    assert classify(DNS) is ErrorKind.NETWORK
    assert classify("ERROR: algo que nadie ha visto nunca") is ErrorKind.UNKNOWN
    assert classify("", timed_out=True) is ErrorKind.NETWORK


def test_bot_and_network_win_over_other_categories():
    # "Video unavailable" que en realidad es un límite de tasa.
    assert classify(RATE) is ErrorKind.BOT_BLOCK
    # Fallo de extracción causado por la red: no es culpa del binario.
    assert classify(DNS) is ErrorKind.NETWORK
    assert classify(BOT + "\n" + NSIG) is ErrorKind.BOT_BLOCK


def test_warnings_do_not_decide_the_category():
    text = "WARNING: [youtube] nsig extraction failed, retrying\n" + UNAVAILABLE
    assert classify(text) is ErrorKind.UNAVAILABLE


def test_cookie_warning_and_summary():
    warn = "WARNING: [youtube] The provided YouTube account cookies are no longer valid."
    assert cookies_look_invalid(warn)
    assert not cookies_look_invalid(BOT)
    assert summarize("WARNING: x\n" + UNAVAILABLE).startswith("ERROR: [youtube] abc: Video unavailable")
    assert summarize("") == ""


def test_settings_roundtrip(db):
    assert db.get_setting("nope") is None
    db.set_setting("a", 12.5)
    assert db.get_float("a") == 12.5
    assert db.get_int("a") == 12
    db.set_setting("flag", "1")
    assert db.get_bool("flag")
    assert not db.get_bool("missing")


def test_playlist_replace_keeps_unavailable_flag(db):
    db.upsert_playlist_meta("PL1", "Lista", 0)
    tracks = [(Track("v1", "Uno", "A", "X", 100), "s1"), (Track("v2", "Dos", "B", "Y", 200), "s2")]
    assert db.replace_playlist_tracks("PL1", tracks, remote_count=3) == 2
    db.mark_unavailable("v1", "Video unavailable", now=1000.0)
    db.replace_playlist_tracks("PL1", list(reversed(tracks)), remote_count=3)
    stored = db.playlist_tracks("PL1")
    assert [t.video_id for t in stored] == ["v2", "v1"]
    assert stored[1].unavailable_since == 1000.0
    pl = db.get_playlist("PL1")
    assert pl.remote_count == 3 and pl.stored_tracks == 2 and pl.last_full_sync is not None


def test_unavailable_is_dated_not_permanent(db):
    db.upsert_track(Track("v1", "Uno"))
    db.mark_unavailable("v1", "x", now=500.0)
    db.mark_unavailable("v1", "y", now=900.0)          # conserva la fecha original
    assert db.get_track("v1").unavailable_since == 500.0
    db.refresh_unavailable("v1", "z", now=900.0)       # re-chequeo fallido: reinicia el plazo
    assert db.get_track("v1").unavailable_since == 900.0
    db.clear_unavailable("v1")
    assert db.get_track("v1").unavailable_since is None


def test_bad_versions_and_resolve_log(db):
    assert not db.is_bad_version("2026.01.01")
    db.add_bad_version("2026.01.01", "smoke")
    assert db.is_bad_version("2026.01.01")
    db.log_resolve("v1", "anon", "ok", 1.2, "251")
    assert db.resolves_since(time.time() - 60) == 1
    assert db.resolves_since(time.time() + 60) == 0


def test_each_thread_gets_its_own_connection(db):
    """El worker de sincronización escribe con su conexión; la GUI lee con la suya."""
    main_conn = db.conn
    seen = {}

    def worker():
        seen["conn"] = db.conn
        db.upsert_playlist_meta("PLw", "Desde el hilo", 0)
        db.close_thread_connection()

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert seen["conn"] is not main_conn
    assert db.get_playlist("PLw").title == "Desde el hilo"
