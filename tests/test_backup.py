"""Copia de seguridad de listas locales, historial y preferencias."""

import json

import pytest

from ytm_native.core.db import Database, Track
from ytm_native.services import backup
from ytm_native.services.backup import MERGE, REPLACE, BackupError, export_backup, import_backup, read_backup

from test_home_search import animations_on, net, wait_until, window  # noqa: F401


def song(i: int) -> Track:
    return Track(f"vid{i:03d}", f"Canción {i}", f"Artista {i % 7}", f"Álbum {i % 5}", 120 + i,
                 thumb_url=f"https://img.example/{i}.jpg")


@pytest.fixture
def source(isolated_home):
    """3 listas locales con 50 pistas (una canción puede estar en dos listas),
    historial, preferencias y cosas que nunca deben salir en la copia."""
    db = Database(isolated_home / "origen.sqlite3")
    songs = [song(i) for i in range(50)]
    lists = [
        db.create_local_playlist("Para correr", songs[:20], now=100.0),
        db.create_local_playlist("Tranquila", songs[15:40], now=200.0),
        db.create_local_playlist("Vacía de momento", [], now=300.0),
    ]
    db.add_tracks_to_local_playlist(lists[2], songs[40:50])
    db.note_playlist_played(lists[0], now=500.0)
    for i in range(30):
        db.log_play(songs[i % 12].video_id, now=1_700_000_000 + i * 60)
    db.upsert_playlist_meta("PLcuenta", "Lista de la cuenta", 0)
    db.replace_playlist_tracks("PLcuenta", [(song(90), None)], 1)
    db.set_setting("volume", 63)
    db.set_setting("autoplay_radio", "1")
    db.set_setting("equalize_volume", "1")
    db.set_setting("cookies_risk_accepted", "1")      # consentimiento: no viaja
    db.set_setting("session_queue", "[...]")          # estado interno: no viaja
    db.set_setting("bot_blocked_until", 123)
    db.lists = lists
    return db


def snapshot(db: Database) -> dict:
    """Lo que la copia debe conservar, comparable entre dos bases."""
    result = {}
    for playlist in db.playlists():
        if playlist.local:
            result[playlist.playlist_id] = (
                playlist.title, playlist.play_count, playlist.last_played,
                [(t.video_id, t.title, t.artists, t.album, t.duration_s, t.thumb_url)
                 for t in db.playlist_tracks(playlist.playlist_id)],
            )
    return result


def plays(db: Database) -> list:
    return [(r["video_id"], r["played_at"]) for r in db.conn.execute(
        "SELECT video_id, played_at FROM plays ORDER BY played_at, id")]


def test_exportar_e_importar_en_una_base_limpia_recrea_todo_igual(source, tmp_path):
    path = tmp_path / "copia.json"
    exported = export_backup(source, path)
    assert len(exported.playlists) == 3 and len(exported.tracks) == 50
    target = Database(tmp_path / "limpia.sqlite3")
    report = import_backup(target, read_backup(path), MERGE)
    assert report.playlists_added == 3 and report.tracks_added == 50 and report.plays_added == 30
    assert snapshot(target) == snapshot(source)
    assert [p.title for p in target.playlists() if p.local] == ["Para correr", "Tranquila", "Vacía de momento"]
    assert plays(target) == plays(source)
    assert target.get_int("volume") == 63 and target.get_bool("autoplay_radio")
    assert target.get_bool("equalize_volume")
    # Solo preferencias: nada de consentimientos ni estados internos.
    for key in ("cookies_risk_accepted", "session_queue", "bot_blocked_until"):
        assert target.get_setting(key) is None
    assert target.get_playlist("PLcuenta") is None           # las de la cuenta vuelven al sincronizar


def test_la_copia_no_lleva_nada_sensible(source, tmp_path):
    path = tmp_path / "copia.json"
    export_backup(source, path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["format"] == backup.FORMAT and data["version"] == backup.VERSION
    assert set(data["settings"]) <= set(backup.PREFERENCE_KEYS)
    text = path.read_text(encoding="utf-8")
    for secret in ("cookies", "session_queue", "bot_blocked", "PLcuenta", "vid090"):
        assert secret not in text


def test_importar_dos_veces_no_duplica_nada(source, tmp_path):
    path = tmp_path / "copia.json"
    export_backup(source, path)
    target = Database(tmp_path / "limpia.sqlite3")
    import_backup(target, read_backup(path), MERGE)
    first = snapshot(target), plays(target)
    report = import_backup(target, read_backup(path), MERGE)
    assert report.playlists_added == 0 and report.playlists_merged == 3
    assert report.plays_added == 0 and report.tracks_added == 0
    assert (snapshot(target), plays(target)) == first


def test_fusionar_completa_la_lista_que_ya_existe_sin_tocar_su_nombre(source, tmp_path):
    path = tmp_path / "copia.json"
    export_backup(source, path)
    pid = source.lists[0]
    # Otra base con la misma lista (otro nombre y solo parte de sus canciones).
    target = Database(tmp_path / "otra.sqlite3")
    with target.transaction() as c:
        c.execute("INSERT INTO playlists (playlist_id, title, position) VALUES (?, 'Mi nombre', -5)", (pid,))
    target.add_tracks_to_local_playlist(pid, [song(19), song(3)])
    target.set_setting("volume", 40)
    import_backup(target, read_backup(path), MERGE)
    ids = [t.video_id for t in target.playlist_tracks(pid)]
    assert target.get_playlist(pid).title == "Mi nombre"
    assert ids[:2] == ["vid019", "vid003"]                    # su orden se respeta
    assert sorted(ids) == sorted(f"vid{i:03d}" for i in range(20)) and len(ids) == 20
    assert target.get_int("volume") == 40                     # fusionar no cambia lo elegido aquí


def test_sobrescribir_deja_las_listas_y_el_historial_como_en_la_copia(source, tmp_path):
    path = tmp_path / "copia.json"
    export_backup(source, path)
    target = Database(tmp_path / "otra.sqlite3")
    stray = target.create_local_playlist("Sobra", [song(77)])
    target.log_play("vid077", now=5.0)
    target.upsert_playlist_meta("PLcuenta2", "De la cuenta", 0)
    target.set_setting("volume", 40)
    import_backup(target, read_backup(path), REPLACE)
    assert target.get_playlist(stray) is None
    assert snapshot(target) == snapshot(source)
    assert plays(target) == plays(source)
    assert target.get_playlist("PLcuenta2") is not None      # la cuenta no se toca
    assert target.get_int("volume") == 63


@pytest.mark.parametrize("change, message", [
    (lambda d: d.update(format="otra-cosa"), "no es una copia"),
    (lambda d: d.update(version=99), "más nueva"),
    (lambda d: d["playlists"].append(dict(d["playlists"][0])), "aparece dos veces"),
    (lambda d: d["playlists"][0].update(id="PLdeLaCuenta"), "lista local no válido"),
    (lambda d: d["playlists"][0]["tracks"].append("no vale\n"), "identificador de canción"),
    (lambda d: d["history"].append(["vid001", "ayer"]), "número"),
    (lambda d: d["history"].append(["vid001", -3]), "fecha no válida"),
    (lambda d: d.update(tracks=[]), "se esperaba un objeto"),
])
def test_una_copia_mal_formada_se_rechaza_sin_tocar_la_base(source, tmp_path, change, message):
    data = backup.build_backup(source)
    change(data)
    path = tmp_path / "mala.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    target = Database(tmp_path / "otra.sqlite3")
    with pytest.raises(BackupError, match=message):
        read_backup(path)
    assert snapshot(target) == {}


def test_un_archivo_que_no_es_json_da_un_error_claro(tmp_path):
    path = tmp_path / "foto.json"
    path.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")
    with pytest.raises(BackupError):
        read_backup(path)
    with pytest.raises(BackupError, match="No se pudo"):
        read_backup(tmp_path / "no-existe.json")


def test_las_canciones_repetidas_de_una_lista_entran_una_vez(source, tmp_path):
    data = backup.build_backup(source)
    data["playlists"][0]["tracks"] += data["playlists"][0]["tracks"][:5]
    parsed = backup.parse_backup(data)
    assert len(parsed.playlists[0].tracks) == 20


def test_si_algo_falla_a_mitad_no_queda_nada_a_medias(source, tmp_path, monkeypatch):
    path = tmp_path / "copia.json"
    export_backup(source, path)
    target = Database(tmp_path / "otra.sqlite3")
    calls = []
    real = Database._append_tracks

    def flaky(c, playlist_id, tracks):
        calls.append(playlist_id)
        if len(calls) == 2:
            raise RuntimeError("disco lleno")
        return real(c, playlist_id, tracks)

    monkeypatch.setattr(Database, "_append_tracks", staticmethod(flaky))
    with pytest.raises(RuntimeError):
        import_backup(target, read_backup(path), MERGE)
    assert snapshot(target) == {} and plays(target) == []
    assert target.get_track("vid001") is None


def test_exportar_reemplaza_el_archivo_de_golpe(source, tmp_path):
    path = tmp_path / "copia.json"
    path.write_text("vieja", encoding="utf-8")
    export_backup(source, path)
    assert json.loads(path.read_text(encoding="utf-8"))["format"] == backup.FORMAT
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".ytmn-")] == []


# -- desde el menú ------------------------------------------------------------------


def test_exportar_e_importar_desde_el_menu(window, db, tmp_path, monkeypatch):
    from ytm_native.ui import main_window as mw

    pid = db.create_local_playlist("Mía", [song(1), song(2)])
    db.set_setting("autoplay_radio", "1")
    path = tmp_path / "desde-menu.json"
    monkeypatch.setattr(mw.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(path), "")))
    window.export_backup()
    assert wait_until(lambda: any("Copia guardada" in t.text for t in window.toasts._toasts), 5000)
    assert path.is_file()

    # Se borra la lista y se cambia la preferencia; la copia lo devuelve.
    db.delete_local_playlist(pid)
    window.autoplay_action.setChecked(False)
    window._reload_sidebar()
    monkeypatch.setattr(mw.QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(path), "")))
    asked = []
    monkeypatch.setattr(window, "_ask_backup_strategy", lambda data: asked.append(data.summary()) or REPLACE)
    window.import_backup()
    assert wait_until(lambda: any("Copia importada" in t.text for t in window.toasts._toasts), 5000)
    assert asked and "1 lista" in asked[0]
    assert db.get_playlist(pid).title == "Mía"
    assert window.autoplay_action.isChecked()                # la preferencia se ve sin reiniciar
    keys = [window.sidebar.item(r).data(mw.ROLE_ID) for r in range(window.sidebar.count())]
    assert pid in keys


def test_importar_una_copia_rota_avisa_sin_preguntar(window, tmp_path, monkeypatch):
    from ytm_native.ui import main_window as mw

    path = tmp_path / "rota.json"
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(mw.QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(path), "")))
    monkeypatch.setattr(window, "_ask_backup_strategy", lambda data: pytest.fail("no debía preguntar"))
    window.import_backup()
    assert wait_until(lambda: any("no es una copia" in t.text for t in window.toasts._toasts), 5000)


def test_cancelar_los_dialogos_no_hace_nada(window, monkeypatch):
    from ytm_native.ui import main_window as mw

    monkeypatch.setattr(mw.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: ("", "")))
    monkeypatch.setattr(mw.QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: ("", "")))
    window.export_backup()
    window.import_backup()
    assert window.toasts._toasts == []
