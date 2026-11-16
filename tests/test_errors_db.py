import sqlite3
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


# -- candado y transacciones --------------------------------------------------


def _run_with_deadline(fn, seconds=5.0):
    """Ejecuta ``fn`` en otro hilo: si se queda bloqueado, la prueba falla en
    vez de colgar toda la batería."""
    out = {}

    def target():
        try:
            out["value"] = fn()
        except BaseException as e:  # noqa: BLE001 - se reexamina en la prueba
            out["error"] = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(seconds)
    assert not t.is_alive(), "la operación se quedó bloqueada"
    if "error" in out:
        raise out["error"]
    return out.get("value")


def test_failed_open_in_a_thread_does_not_keep_the_lock(db, monkeypatch):
    db.set_setting("k", "antes")
    real_open = db._open

    def flaky_open():
        if threading.current_thread().name == "sync-roto":
            raise sqlite3.OperationalError("unable to open database file")
        return real_open()

    monkeypatch.setattr(db, "_open", flaky_open)
    monkeypatch.setattr(db, "LOCK_TIMEOUT_S", 1.0)
    seen = {}

    def worker():
        try:
            db.get_setting("k")
        except sqlite3.OperationalError as e:
            seen["error"] = e

    t = threading.Thread(target=worker, name="sync-roto")
    t.start()
    t.join()
    assert "unable to open" in str(seen["error"])
    # Justo después, cualquier otro hilo sigue leyendo y escribiendo.
    assert _run_with_deadline(lambda: db.get_setting("k")) == "antes"
    _run_with_deadline(lambda: db.set_setting("k", "después"))
    assert _run_with_deadline(lambda: db.get_setting("k")) == "después"
    assert db.get_setting("k") == "después"


class _CommitFails:
    """Conexión que delega en una real salvo en ``commit``."""

    def __init__(self, real):
        self._real = real

    def commit(self):
        raise sqlite3.OperationalError("disk I/O error")

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_failed_commit_rolls_back_and_releases_the_lock(db, monkeypatch):
    db.set_setting("k", "1")
    real = db.conn
    monkeypatch.setattr(db._local, "conn", _CommitFails(real))
    try:
        db.set_setting("k", "2")
    except sqlite3.OperationalError as e:
        assert "disk I/O" in str(e)
    else:
        raise AssertionError("el fallo del commit debía propagarse")
    # La transacción se deshizo: nada quedó a medias en la conexión.
    assert not real.in_transaction
    monkeypatch.setattr(db._local, "conn", real)
    assert db.get_setting("k") == "1"
    # Y el candado quedó libre para los demás hilos.
    _run_with_deadline(lambda: db.set_setting("k", "3"))
    assert db.get_setting("k") == "3"


def test_failed_commit_in_memory_database(monkeypatch):
    from ytm_native.core.db import Database

    mem = Database(":memory:")
    mem.set_setting("k", "1")
    real = mem._shared
    monkeypatch.setattr(mem, "_shared", _CommitFails(real))
    try:
        mem.set_setting("k", "2")
    except sqlite3.OperationalError:
        pass
    else:
        raise AssertionError("el fallo del commit debía propagarse")
    monkeypatch.setattr(mem, "_shared", real)
    assert not real.in_transaction
    assert _run_with_deadline(lambda: mem.get_setting("k")) == "1"


def test_lock_wait_has_a_limit(db, monkeypatch):
    from ytm_native.core.db import DatabaseLockTimeout

    monkeypatch.setattr(db, "LOCK_TIMEOUT_S", 0.2)
    taken, release = threading.Event(), threading.Event()

    def holder():
        with db._tx():
            taken.set()
            release.wait(5)

    t = threading.Thread(target=holder)
    t.start()
    try:
        assert taken.wait(5)
        started = time.monotonic()
        try:
            _run_with_deadline(lambda: db.get_setting("k"))
        except DatabaseLockTimeout as e:
            assert "ocupada" in str(e)
            # Hereda de OperationalError: quien ya maneja esos errores lo atrapa.
            assert isinstance(e, sqlite3.OperationalError)
        else:
            raise AssertionError("debía rendirse al pasar el límite")
        assert time.monotonic() - started < 2
    finally:
        release.set()
        t.join()
    assert db.get_setting("k") is None


def test_open_closes_the_connection_if_a_pragma_fails(isolated_home, monkeypatch):
    from ytm_native.core import db as dbmod

    closed = []

    class Conn:
        row_factory = None

        def execute(self, sql, *args):
            raise sqlite3.OperationalError("database is locked")

        def close(self):
            closed.append(True)

    monkeypatch.setattr(dbmod.sqlite3, "connect", lambda *a, **k: Conn())
    try:
        dbmod.Database(isolated_home / "x.sqlite3")
    except sqlite3.OperationalError:
        pass
    else:
        raise AssertionError("el fallo del PRAGMA debía propagarse")
    assert closed == [True]


# ---------------------------------------------------------------------------
# Fase 3: metadatos, números y transacciones
# ---------------------------------------------------------------------------
def test_upsert_track_fills_in_what_was_missing(db):
    db.upsert_track(Track("v1", "(sin título)"))
    db.upsert_track(Track("v1", "Canción", "Artista", "Disco", 215, thumb_url="http://x/1.jpg"))
    assert db.get_track("v1") == Track("v1", "Canción", "Artista", "Disco", 215, thumb_url="http://x/1.jpg")


def test_upsert_track_never_erases_known_data(db):
    db.upsert_track(Track("v1", "Canción", "Artista", "Disco", 215, thumb_url="http://x/1.jpg"))
    db.upsert_track(Track("v1", "", "", "", None, thumb_url="http://x/otra.jpg"))
    db.upsert_track(Track("v1", "(sin título)"))
    assert db.get_track("v1") == Track("v1", "Canción", "Artista", "Disco", 215, thumb_url="http://x/1.jpg")
    db.upsert_track(Track("v1", "Canción (remasterizada)", "Artista", "Disco"))
    assert db.get_track("v1").title == "Canción (remasterizada)"
    assert db.get_track("v1").duration_s == 215


def test_upsert_track_keeps_the_unavailable_mark(db):
    db.upsert_track(Track("v1", "Canción"))
    db.mark_unavailable("v1", "no disponible", now=100.0)
    db.upsert_track(Track("v1", "Canción", "Artista"))
    assert db.get_track("v1").unavailable_since == 100.0


def test_numeric_settings_reject_nan_and_infinity(db):
    for raw in ("nan", "NaN", "inf", "-inf", "Infinity", "1e999", "basura", ""):
        db.set_setting("x", raw)
        assert db.get_float("x", 2.5) == 2.5
        assert db.get_int("x", 7) == 7


def test_get_int_is_exact_for_big_integers(db):
    big = 2**63 + 1
    db.set_setting("x", big)
    assert db.get_int("x") == big
    db.set_setting("x", " 42 ")
    assert db.get_int("x") == 42
    db.set_setting("x", "41.9")
    assert db.get_int("x") == 41
    db.set_setting("x", "1e3")
    assert db.get_int("x") == 1000
    assert db.get_int("no-existe", 5) == 5


def commits_during(db, action):
    statements = []
    db.conn.set_trace_callback(statements.append)
    try:
        result = action()
    finally:
        db.conn.set_trace_callback(None)
    return result, sum(1 for s in statements if s.strip().upper() == "COMMIT")


def test_create_local_playlist_with_50_songs_is_one_commit(db):
    tracks = [Track(f"v{i}", f"Canción {i}", "Artista") for i in range(50)]
    playlist_id, commits = commits_during(db, lambda: db.create_local_playlist("Mía", tracks))
    assert commits == 1
    assert [t.video_id for t in db.playlist_tracks(playlist_id)] == [f"v{i}" for i in range(50)]


def test_adding_many_songs_to_a_local_list_is_one_commit_and_skips_repeats(db):
    playlist_id = db.create_local_playlist("Mía", [Track("v0", "Cero")])
    batch = [Track("v1", "Uno"), Track("v0", "Cero"), Track("v2", "Dos"), Track("v1", "Uno")]
    added, commits = commits_during(db, lambda: db.add_tracks_to_local_playlist(playlist_id, batch))
    assert (added, commits) == (2, 1)
    assert [t.video_id for t in db.playlist_tracks(playlist_id)] == ["v0", "v1", "v2"]
    assert db.add_to_local_playlist(playlist_id, Track("v2", "Dos")) is False
    assert db.add_to_local_playlist(playlist_id, Track("v3", "Tres")) is True
    assert db.add_tracks_to_local_playlist("PLcuenta", batch) == 0


def test_a_local_playlist_is_created_whole_or_not_at_all(db, monkeypatch):
    import ytm_native.core.db as dbmod

    tracks = [Track("v0", "Cero"), Track("v1", "Uno")]
    monkeypatch.setattr(dbmod, "_UPSERT_TRACK_SQL", "INSERT INTO tabla_que_no_existe VALUES (?, ?, ?, ?, ?, ?)")
    try:
        db.create_local_playlist("Mía", tracks)
    except sqlite3.OperationalError:
        pass
    else:
        raise AssertionError("debía fallar")
    assert [p for p in db.playlists() if p.local] == []


def test_discover_cache_purge_drops_only_expired_entries(db):
    now = 1_000_000.0
    db.set_discover_entry("suena:vieja", "{}", now - 8 * 86400)
    db.set_discover_entry("suena:nueva", "{}", now - 3600)
    db.set_discover_entry("artista:viejo", "{}", now - 2 * 86400)
    db.set_discover_entry("artista_x", "{}", now - 30 * 86400)     # el "_" no es comodín
    db.set_discover_entry("inicio", "{}", now - 90 * 86400)
    removed = db.purge_discover_cache({"suena:": 7 * 86400, "artista:": 86400}, now)
    assert removed == 2
    assert db.get_discover_entry("suena:vieja") is None
    assert db.get_discover_entry("artista:viejo") is None
    for kept in ("suena:nueva", "artista_x", "inicio"):
        assert db.get_discover_entry(kept) is not None


def test_saving_discover_entries_purges_at_most_once_a_day(db):
    from ytm_native.core import config
    from ytm_native.services import discover

    now = 2_000_000.0
    db.set_discover_entry("suena:vieja", "{}", now - 8 * 86400)
    assert discover.purge_stale_cache(db, now) == 1
    db.set_discover_entry("suena:otra", "{}", now - 8 * 86400)
    assert discover.purge_stale_cache(db, now + 60) == 0          # ya se purgó hoy
    assert db.get_discover_entry("suena:otra") is not None
    assert discover.purge_stale_cache(db, now + config.DISCOVER_PURGE_INTERVAL_S) == 1
