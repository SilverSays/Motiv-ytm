"""Listas locales en la base: reordenar y quitar varias, siempre en una sola transacción."""

import itertools
import sqlite3

import pytest

from ytm_native.core.db import Track


def songs(n: int, prefix: str = "s") -> list[Track]:
    return [Track(f"{prefix}{i}", f"Canción {i}", "Artista") for i in range(n)]


def entries(db, playlist_id: str) -> list[tuple[int, str]]:
    """Posición y videoId tal cual están en ``playlist_tracks``."""
    with db.transaction() as c:
        return [(r["position"], r["video_id"]) for r in c.execute(
            "SELECT position, video_id FROM playlist_tracks WHERE playlist_id=? ORDER BY position",
            (playlist_id,),
        )]


def order(db, playlist_id: str) -> list[str]:
    return [t.video_id for t in db.playlist_tracks(playlist_id)]


def assert_compact(db, playlist_id: str) -> None:
    """Las posiciones van de 0 a N-1, sin huecos ni repetidas."""
    positions = [p for p, _ in entries(db, playlist_id)]
    assert positions == list(range(len(positions)))


@pytest.fixture
def local(db):
    return db.create_local_playlist("Mía", songs(6))


# ---------------------------------------------------------------------------
# Mover
# ---------------------------------------------------------------------------
def test_mover_del_principio_al_final_y_al_reves(db, local):
    assert db.move_in_local_playlist(local, 0, 5) is True
    assert order(db, local) == ["s1", "s2", "s3", "s4", "s5", "s0"]
    assert db.move_in_local_playlist(local, 5, 0) is True
    assert order(db, local) == [f"s{i}" for i in range(6)]
    assert_compact(db, local)


def test_mover_entre_posiciones_intermedias(db, local):
    assert db.move_in_local_playlist(local, 1, 3)
    assert order(db, local) == ["s0", "s2", "s3", "s1", "s4", "s5"]
    assert db.move_in_local_playlist(local, 4, 2)
    assert order(db, local) == ["s0", "s2", "s4", "s3", "s1", "s5"]
    assert_compact(db, local)


def test_cualquier_movimiento_deja_la_lista_como_una_lista_de_python(db):
    names = [f"s{i}" for i in range(5)]
    for src, dst in itertools.product(range(5), range(5)):
        pid = db.create_local_playlist(f"{src}-{dst}", songs(5))
        assert db.move_in_local_playlist(pid, src, dst)
        expected = list(names)
        expected.insert(dst, expected.pop(src))
        assert order(db, pid) == expected, (src, dst)
        assert_compact(db, pid)


def test_mover_a_su_sitio_no_cambia_nada(db, local):
    before = entries(db, local)
    assert db.move_in_local_playlist(local, 2, 2) is True
    assert entries(db, local) == before


@pytest.mark.parametrize("src, dst", [(-1, 0), (0, -1), (6, 0), (0, 6), (99, 99)])
def test_mover_fuera_de_la_lista_no_hace_nada(db, local, src, dst):
    before = entries(db, local)
    assert db.move_in_local_playlist(local, src, dst) is False
    assert entries(db, local) == before


def test_las_listas_de_la_cuenta_no_se_reordenan_aqui(db):
    db.upsert_playlist_meta("PLcuenta", "De la cuenta", 0)
    db.replace_playlist_tracks("PLcuenta", [(t, f"set-{t.video_id}") for t in songs(3)], 3)
    before = entries(db, "PLcuenta")
    assert db.move_in_local_playlist("PLcuenta", 0, 2) is False
    assert entries(db, "PLcuenta") == before


def test_una_lista_vacia_o_que_no_existe(db):
    empty = db.create_local_playlist("Vacía")
    assert db.move_in_local_playlist(empty, 0, 0) is False
    assert db.move_in_local_playlist("local:noexiste", 0, 1) is False


def test_mover_no_toca_las_demas_listas(db, local):
    other = db.create_local_playlist("Otra", songs(3, "o"))
    db.move_in_local_playlist(local, 0, 4)
    assert order(db, other) == ["o0", "o1", "o2"]


def test_un_fallo_a_mitad_deja_la_lista_como_estaba(db, local):
    # Una inserción que falla después de borrar: la transacción entera se deshace.
    with db.transaction() as c:
        c.execute(
            "CREATE TRIGGER falla BEFORE INSERT ON playlist_tracks "
            "WHEN NEW.video_id = 's5' AND NEW.position = 0 BEGIN SELECT RAISE(ABORT, 'falla'); END"
        )
    before = entries(db, local)
    with pytest.raises(sqlite3.DatabaseError):
        db.move_in_local_playlist(local, 5, 0)
    assert entries(db, local) == before
    assert db.move_in_local_playlist(local, 0, 1)      # la conexión sigue sirviendo


# ---------------------------------------------------------------------------
# Quitar varias
# ---------------------------------------------------------------------------
def test_quitar_varias_y_renumerar(db, local):
    assert db.remove_tracks_from_local_playlist(local, ["s1", "s4", "s2"]) == 3
    assert order(db, local) == ["s0", "s3", "s5"]
    assert_compact(db, local)


def test_quitar_varias_cuenta_solo_las_que_estaban(db, local):
    assert db.remove_tracks_from_local_playlist(local, ["s0", "no-esta", "s0"]) == 1
    assert order(db, local) == ["s1", "s2", "s3", "s4", "s5"]
    assert db.remove_tracks_from_local_playlist(local, ["nada"]) == 0
    assert db.remove_tracks_from_local_playlist(local, []) == 0


def test_quitar_todas(db, local):
    assert db.remove_tracks_from_local_playlist(local, [f"s{i}" for i in range(6)]) == 6
    assert order(db, local) == [] and db.get_playlist(local) is not None   # la lista sigue, vacía


def test_quitar_de_una_lista_de_la_cuenta_no_hace_nada(db):
    db.upsert_playlist_meta("PLcuenta", "De la cuenta", 0)
    db.replace_playlist_tracks("PLcuenta", [(t, None) for t in songs(3)], 3)
    assert db.remove_tracks_from_local_playlist("PLcuenta", ["s0"]) == 0
    assert order(db, "PLcuenta") == ["s0", "s1", "s2"]


def test_quitar_una_sigue_funcionando_como_antes(db, local):
    assert db.remove_from_local_playlist(local, "s2") is True
    assert db.remove_from_local_playlist(local, "s2") is False
    assert order(db, local) == ["s0", "s1", "s3", "s4", "s5"]
    assert_compact(db, local)


def test_tras_reordenar_y_quitar_se_sigue_anadiendo_al_final(db, local):
    db.move_in_local_playlist(local, 5, 0)
    db.remove_tracks_from_local_playlist(local, ["s1", "s2"])
    assert db.add_tracks_to_local_playlist(local, songs(2, "n")) == 2
    assert order(db, local) == ["s5", "s0", "s3", "s4", "n0", "n1"]
    assert_compact(db, local)
