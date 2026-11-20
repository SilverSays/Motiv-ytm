"""Integridad de la base (Tarea 10.6): escuchas con su pista, nombres de listas,
transacciones anidadas y el cierre con el WAL volcado."""

import sqlite3

import pytest

from ytm_native.core.db import Database, Track, is_local_playlist


# ---------------------------------------------------------------------------
# Escuchas: la pista y la escucha, juntas (OBS-000006)
# ---------------------------------------------------------------------------
def test_una_escucha_con_su_pista_la_registra_en_la_misma_transaccion(db):
    statements = []
    db.conn.set_trace_callback(statements.append)
    try:
        assert db.log_play(Track("nueva", "Recién oída", "Alguien", "", 200), now=100.0) is True
    finally:
        db.conn.set_trace_callback(None)
    assert sum(1 for s in statements if s.strip().upper() == "COMMIT") == 1
    assert db.get_track("nueva").title == "Recién oída"
    assert db.play_stats() == (1, 1)
    assert [t.video_id for t, _at, _n in db.recent_plays()] == ["nueva"]


def test_una_escucha_sin_pista_no_descuadra_las_cuentas(db):
    assert db.log_play("fantasma") is False
    assert db.play_stats() == (0, 0)
    assert db.recent_plays() == [] and db.top_plays() == []
    db.upsert_track(Track("fantasma", "Ya existe"))
    assert db.log_play("fantasma", now=5.0) is True
    assert db.play_stats() == (1, 1) and len(db.recent_plays()) == 1


def test_si_falla_la_escucha_tampoco_queda_la_pista(db, monkeypatch):
    import ytm_native.core.db as dbmod

    monkeypatch.setattr(dbmod, "_track_params", lambda track: (_ for _ in ()).throw(sqlite3.OperationalError("disk")))
    with pytest.raises(sqlite3.OperationalError):
        db.log_play(Track("x", "X"))
    assert db.get_track("x") is None and db.play_stats() == (0, 0)


# ---------------------------------------------------------------------------
# Cambiar el nombre de una lista (OBS-000007)
# ---------------------------------------------------------------------------
def test_solo_se_renombran_las_listas_locales(db):
    db.upsert_playlist_meta("PLcuenta", "De la cuenta", 0)
    assert db.rename_playlist("PLcuenta", "Otro nombre") is False
    assert db.get_playlist("PLcuenta").title == "De la cuenta"


def test_un_nombre_en_blanco_queda_como_lista_sin_nombre(db):
    pid = db.create_local_playlist("Para correr")
    assert is_local_playlist(pid)
    assert db.rename_playlist(pid, "  Para caminar  ") is True
    assert db.get_playlist(pid).title == "Para caminar"
    assert db.rename_playlist(pid, "   ") is True
    assert db.get_playlist(pid).title == "Lista sin nombre"
    assert db.get_playlist(db.create_local_playlist("  ")).title == "Lista sin nombre"


def test_renombrar_una_lista_local_que_no_existe_lo_dice(db):
    assert db.rename_playlist("local:noexiste000", "Nombre") is False


# ---------------------------------------------------------------------------
# Transacciones anidadas (OBS-000011)
# ---------------------------------------------------------------------------
@pytest.fixture(params=["archivo", "memoria"])
def anydb(request, tmp_path):
    return Database(tmp_path / "anidada.sqlite3" if request.param == "archivo" else ":memory:")


def test_un_fallo_fuera_deshace_tambien_lo_de_dentro(anydb):
    anydb.set_setting("k", "antes")
    with pytest.raises(RuntimeError):
        with anydb.transaction() as c:
            c.execute("UPDATE settings SET value='fuera' WHERE key='k'")
            anydb.set_setting("otra", "dentro")          # su propia transacción, anidada
            anydb.upsert_playlist_meta("PLx", "Dentro", 0)
            raise RuntimeError("falla después de la de dentro")
    assert anydb.get_setting("k") == "antes"
    assert anydb.get_setting("otra") is None and anydb.get_playlist("PLx") is None


def test_lo_de_dentro_no_se_confirma_antes_que_lo_de_fuera(tmp_path):
    db = Database(tmp_path / "visible.sqlite3")
    reader = sqlite3.connect(tmp_path / "visible.sqlite3")
    try:
        with db.transaction():
            db.set_setting("dentro", "1")
            # Otra conexión aún no lo ve: la de dentro no hizo commit.
            assert reader.execute("SELECT value FROM settings WHERE key='dentro'").fetchone() is None
        assert reader.execute("SELECT value FROM settings WHERE key='dentro'").fetchone() == ("1",)
    finally:
        reader.close()


def test_un_fallo_dentro_deshace_solo_lo_suyo(anydb):
    with anydb.transaction() as c:
        c.execute("INSERT INTO settings (key, value) VALUES ('fuera', '1')")
        with pytest.raises(RuntimeError):
            with anydb.transaction() as inner:
                inner.execute("INSERT INTO settings (key, value) VALUES ('dentro', '1')")
                raise RuntimeError("falla la de dentro")
        c.execute("INSERT INTO settings (key, value) VALUES ('despues', '1')")
    assert anydb.get_setting("fuera") == "1" and anydb.get_setting("despues") == "1"
    assert anydb.get_setting("dentro") is None


def test_tres_niveles_y_la_base_sigue_usable(anydb):
    with anydb.transaction():
        with anydb.transaction():
            with anydb.transaction() as c:
                c.execute("INSERT INTO settings (key, value) VALUES ('hondo', '3')")
    assert anydb.get_setting("hondo") == "3"
    assert not anydb.conn.in_transaction
    anydb.set_setting("luego", "1")
    assert anydb.get_setting("luego") == "1"


def test_anidar_no_cuenta_de_mas_entre_hilos(tmp_path):
    import threading

    db = Database(tmp_path / "hilos.sqlite3")
    errors = []

    def worker():
        try:
            for i in range(20):
                db.set_setting(f"h{i}", i)
        except Exception as e:  # noqa: BLE001 - se mira en la prueba
            errors.append(e)
        finally:
            db.close_thread_connection()

    with db.transaction():
        db.set_setting("principal", "1")
    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(10)
    assert not t.is_alive() and errors == []
    # Otro hilo empieza en el nivel de fuera: cada escritura suya se confirma.
    assert db.get_int("h19") == 19 and db.get_setting("principal") == "1"


# ---------------------------------------------------------------------------
# WAL y cierre (OBS-000014)
# ---------------------------------------------------------------------------
def test_la_base_del_disco_va_en_modo_wal(tmp_path):
    db = Database(tmp_path / "wal.sqlite3")
    assert db.journal_mode == "wal"
    assert db.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert Database(":memory:").journal_mode == "memory"


def test_si_no_hay_wal_se_avisa(tmp_path, monkeypatch, caplog):
    real_connect = sqlite3.connect

    class NoWal:
        def __init__(self, conn):
            self._c = conn

        def execute(self, sql, *args):
            if sql.strip().upper() == "PRAGMA JOURNAL_MODE=WAL":
                return self._c.execute("PRAGMA journal_mode=DELETE")
            return self._c.execute(sql, *args)

        def __getattr__(self, name):
            return getattr(self._c, name)

        def __setattr__(self, name, value):
            if name == "_c":
                object.__setattr__(self, name, value)
            else:
                setattr(self._c, name, value)

    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: NoWal(real_connect(*a, **k)))
    with caplog.at_level("WARNING", logger="ytmn.db"):
        db = Database(tmp_path / "sinwal.sqlite3")
    assert db.journal_mode == "delete"
    assert any("WAL" in r.getMessage() for r in caplog.records)


def test_cerrar_vuelca_el_wal_y_la_base_sigue_sirviendo(tmp_path):
    path = tmp_path / "cierre.sqlite3"
    db = Database(path)
    for i in range(50):
        db.set_setting(f"k{i}", i)
    wal = path.with_name(path.name + "-wal")
    assert wal.exists() and wal.stat().st_size > 0
    db.close()
    assert not wal.exists() or wal.stat().st_size == 0
    # Lo guardado está en el archivo principal: otra conexión lo lee sin el WAL.
    check = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        assert check.execute("SELECT value FROM settings WHERE key='k49'").fetchone() == ("49",)
    finally:
        check.close()
    db.close()                                             # dos veces no pasa nada
    db.set_setting("despues", "1")                         # y si algo escribe luego, abre otra
    assert db.get_setting("despues") == "1"


def test_cerrar_tambien_cierra_las_conexiones_de_otros_hilos(tmp_path):
    import threading

    db = Database(tmp_path / "otros.sqlite3")
    seen = {}

    def worker():
        seen["conn"] = db.conn
        db.set_setting("hilo", "1")

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(10)
    db.close()
    with pytest.raises(sqlite3.ProgrammingError):
        seen["conn"].execute("SELECT 1")
    assert db.get_setting("hilo") == "1"


def test_cerrar_una_base_en_memoria_no_la_borra():
    db = Database(":memory:")
    db.set_setting("k", "1")
    db.close()
    assert db.get_setting("k") == "1"
