"""Cifrado en reposo con DPAPI: primitivas (``core/crypto.py``) y credenciales
de la cuenta (``core/credentials.py``, Tarea 8.1).

Usan el DPAPI real de Windows: cifrar y descifrar en memoria o en la carpeta
temporal de cada prueba no deja nada en el equipo. Fuera de Windows se
comprueba que falla de forma controlada.
"""

import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from ytm_native.core import credentials, crypto, paths
from ytm_native.playback.extractor import ExtractorManager, RunResult, WarmProcess

windows = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI solo existe en Windows")


# ===========================================================================
# Primitivas
# ===========================================================================
@windows
def test_lo_cifrado_no_lleva_el_secreto_en_claro_y_vuelve_igual():
    secret = b"SAPISID=secreto_123; otra=cosa"
    blob = crypto.dpapi_protect(secret)
    assert b"SAPISID" not in blob and b"secreto_123" not in blob
    assert crypto.dpapi_unprotect(blob) == secret


@windows
def test_sellado_lleva_la_cabecera_y_la_entropia_cuenta():
    blob = crypto.seal(b"clave", b"uso-a")
    assert blob.startswith(crypto.MAGIC) and crypto.is_sealed(blob)
    assert crypto.unseal(blob, b"uso-a") == b"clave"
    with pytest.raises(crypto.CryptoError):
        crypto.unseal(blob, b"uso-b")           # el blob de un uso no sirve para otro


@windows
def test_datos_alterados_dan_un_error_controlado_sin_los_datos():
    blob = bytearray(crypto.seal(b"secreto_123"))
    blob[len(crypto.MAGIC) + 30] ^= 0xFF
    with pytest.raises(crypto.CryptoError) as info:
        crypto.unseal(bytes(blob))
    assert "secreto" not in str(info.value)


@windows
def test_lo_que_no_esta_sellado_no_se_acepta():
    with pytest.raises(crypto.CryptoError):
        crypto.unseal(b'{"session_key": "en claro"}')
    with pytest.raises(crypto.CryptoError):
        crypto.dpapi_unprotect(b"")


@windows
def test_vacio_tambien_se_cifra():
    assert crypto.unseal(crypto.seal(b"")) == b""


def test_sin_windows_falla_con_un_error_propio(monkeypatch):
    monkeypatch.setattr(crypto, "available", lambda: False)
    with pytest.raises(crypto.CryptoError):
        crypto.dpapi_protect(b"x")
    with pytest.raises(crypto.CryptoError):
        crypto.dpapi_unprotect(b"x")


# ===========================================================================
# Tarea 8.1: credenciales cifradas en reposo
# ===========================================================================
SECRET = "SAPISID=secreto_123"
BROWSER = json.dumps({"cookie": f"{SECRET}; __Secure-3PAPISID=otro_secreto", "x-goog-authuser": "0"})
COOKIES = (
    "# Netscape HTTP Cookie File\n"
    ".youtube.com\tTRUE\t/\tTRUE\t1999999999\tSAPISID\tsecreto_123\n"
    ".youtube.com\tTRUE\t/\tTRUE\t1999999999\tSID\tsid_viejo\n"
)
EPHEMERAL_NAME = re.compile(r"^\.cookies_ephemeral_(\d+)_[0-9a-f]{32}\.tmp$")


def everything_in(folder) -> bytes:
    return b"".join(p.read_bytes() for p in Path(folder).rglob("*") if p.is_file())


@windows
def test_las_credenciales_en_claro_se_cifran_al_arrancar(isolated_home):
    paths.ensure_dirs()
    paths.browser_json().write_bytes(BROWSER.encode())
    paths.cookies_txt().write_bytes(COOKIES.encode())
    paths.browser_json().with_name("browser.json.tmp").write_text(BROWSER, encoding="utf-8")  # resto viejo
    paths.secure_credentials()
    assert credentials.is_sealed_file(paths.browser_json())
    assert credentials.is_sealed_file(paths.cookies_txt())
    assert b"SAPISID" not in everything_in(paths.cred_dir())       # nada queda en claro
    assert credentials.read_text(paths.browser_json()) == BROWSER
    assert credentials.read_text(paths.cookies_txt()) == COOKIES
    # Una segunda vez no cambia nada.
    before = paths.cookies_txt().read_bytes()
    assert credentials.migrate_plaintext([paths.browser_json(), paths.cookies_txt()]) == []
    assert paths.cookies_txt().read_bytes() == before


def test_una_carpeta_de_credenciales_ajena_no_se_reescribe(isolated_home, tmp_path, monkeypatch):
    foreign = tmp_path / "ajena"
    foreign.mkdir()
    (foreign / "cookies.txt").write_text(COOKIES, encoding="utf-8")
    monkeypatch.setenv("YTMN_CRED_DIR", str(foreign))
    paths.secure_credentials()
    assert (foreign / "cookies.txt").read_text(encoding="utf-8") == COOKIES


def test_el_perfil_de_firefox_de_un_intento_cortado_se_borra(isolated_home):
    profile = paths.firefox_login_profile()
    profile.mkdir(parents=True)
    (profile / "cookies.sqlite").write_bytes(b"cookies en claro")
    paths.secure_credentials()
    assert not profile.exists()


@windows
def test_escribir_es_atomico_y_un_fallo_deja_el_anterior(isolated_home, monkeypatch):
    target = paths.cred_dir() / "browser.json"
    credentials.write_text(target, "uno")

    def locked(src, dst):
        raise PermissionError("en uso")

    monkeypatch.setattr(credentials.os, "replace", locked)
    with pytest.raises(PermissionError):
        credentials.write_text(target, "dos")
    monkeypatch.undo()
    assert credentials.read_text(target) == "uno"
    assert [p.name for p in target.parent.iterdir()] == ["browser.json"]   # sin temporales


@windows
def test_un_archivo_danado_da_un_error_controlado(isolated_home):
    target = paths.cred_dir() / "browser.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(crypto.MAGIC + b"esto no es dpapi")
    with pytest.raises(credentials.CredentialError) as info:
        credentials.read_text(target)
    assert "esto no es" not in str(info.value)
    assert credentials.read_text_or_empty(target) == "" and not credentials.readable(target)


@windows
def test_credenciales_de_otro_usuario_cuentan_como_sesion_caducada(db, isolated_home):
    """browser.json que no se puede descifrar: la sincronización queda en
    «sesión caducada» (se pide renovar), sin excepciones ni red."""
    from ytm_native.services.sync import LibrarySync, SyncState

    paths.ensure_dirs()
    paths.browser_json().write_bytes(crypto.MAGIC + b"\x01\x02 de otro usuario")
    report = LibrarySync(db).run()
    assert report.state is SyncState.AUTH_EXPIRED
    assert LibrarySync(db).state() is SyncState.AUTH_EXPIRED


# ---------------------------------------------------------------------------
# Cookies efímeras para yt-dlp
# ---------------------------------------------------------------------------
class SpyYtdlp:
    """Un yt-dlp que mira el archivo de cookies que recibe mientras corre."""

    def __init__(self, rotate: str = "", fail: bool = False, before_exit=None):
        self.seen: list[tuple[str, str]] = []      # (nombre, contenido) durante la ejecución
        self.rotate = rotate
        self.fail = fail
        self.before_exit = before_exit

    def __call__(self, args, timeout, should_stop=None):
        if "--version" in args:
            return RunResult(0, stdout="2026.01.01\n")
        if "--cookies" in args:
            jar = Path(args[args.index("--cookies") + 1])
            self.seen.append((jar.name, jar.read_bytes().decode()))
            if self.rotate:
                jar.write_bytes(self.rotate.encode())   # yt-dlp guarda las renovadas al salir
            if self.before_exit:
                self.before_exit()
        if self.fail:
            raise RuntimeError("yt-dlp se cayó")
        video_id = args[-1].split("v=")[-1]
        return RunResult(0, stdout=json.dumps({
            "url": f"https://rr1.googlevideo.com/videoplayback?expire=1999999999&id={video_id}",
            "format_id": "251", "acodec": "opus", "http_headers": {"User-Agent": "UA"},
        }))


def manager(db, tmp_path, runner, notices=None):
    exe = tmp_path / "bin" / "yt-dlp.exe"
    exe.parent.mkdir(exist_ok=True)
    exe.write_text("2026.01.01")
    db.set_setting("last_update_timestamp", 1_800_000_000.0)
    db.set_setting("cookies_risk_accepted", "1")
    db.set_setting("playback_mode", "cookies")
    return ExtractorManager(
        db, ytdlp=exe, deno=tmp_path / "bin" / "deno.exe", cookies=paths.cookies_txt(),
        cache=tmp_path / "cache", runner=runner, latest_tag_fetcher=lambda: None,
        clock=lambda: 1_800_000_000.0, wait=lambda s, stop: True,
        on_notice=(notices.append if notices is not None else None),
    )


@pytest.fixture
def sealed_cookies(isolated_home):
    paths.ensure_dirs()
    credentials.write_text(paths.cookies_txt(), COOKIES)
    return paths.cookies_txt()


@windows
def test_yt_dlp_recibe_una_copia_efimera_que_desaparece_al_acabar(db, tmp_path, sealed_cookies):
    spy = SpyYtdlp()
    res = manager(db, tmp_path, spy).resolve("abc")
    assert res.ok and res.used_cookies
    name, content = spy.seen[0]
    match = EPHEMERAL_NAME.match(name)
    assert match and int(match.group(1)) == os.getpid()
    assert content == COOKIES                        # legible solo mientras corre yt-dlp
    assert [p.name for p in paths.cred_dir().iterdir()] == ["cookies.txt"]
    assert credentials.is_sealed_file(sealed_cookies)


@windows
def test_la_copia_efimera_se_borra_aunque_yt_dlp_falle(db, tmp_path, sealed_cookies):
    spy = SpyYtdlp(fail=True)
    mgr = manager(db, tmp_path, spy)
    with pytest.raises(RuntimeError):
        mgr._run_tier("abc", True, "default", None)
    assert spy.seen
    assert [p.name for p in paths.cred_dir().iterdir()] == ["cookies.txt"]


@windows
def test_las_cookies_que_renueva_yt_dlp_se_guardan_cifradas(db, tmp_path, sealed_cookies):
    rotated = COOKIES.replace("sid_viejo", "sid_nuevo")
    assert manager(db, tmp_path, SpyYtdlp(rotate=rotated)).resolve("abc").ok
    assert credentials.read_text(sealed_cookies) == rotated
    assert b"sid_nuevo" not in sealed_cookies.read_bytes()


@windows
def test_un_tarro_sin_sesion_o_credenciales_nuevas_no_se_pisan(db, tmp_path, sealed_cookies):
    lost = "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tYSC\tx\n"
    assert manager(db, tmp_path, SpyYtdlp(rotate=lost)).resolve("abc").ok
    assert credentials.read_text(sealed_cookies) == COOKIES          # sin SAPISID: no se guarda
    imported = COOKIES.replace("secreto_123", "importada")

    def user_imports():                                              # a mitad de la ejecución
        credentials.write_text(sealed_cookies, imported)

    rotated = COOKIES.replace("sid_viejo", "sid_nuevo")
    assert manager(db, tmp_path, SpyYtdlp(rotate=rotated, before_exit=user_imports)).resolve("abc").ok
    assert credentials.read_text(sealed_cookies) == imported


@windows
def test_cookies_que_no_se_descifran_son_como_no_tenerlas(db, tmp_path, isolated_home):
    paths.ensure_dirs()
    paths.cookies_txt().write_bytes(crypto.MAGIC + b"de otro equipo")
    notices = []
    spy = SpyYtdlp()
    mgr = manager(db, tmp_path, spy, notices)
    assert not mgr.cookies_usable()
    assert not mgr.cookies_usable() and len(notices) == 1        # se avisa una vez
    res = mgr.resolve("abc")
    assert res.ok and not res.used_cookies and spy.seen == []


@windows
def test_el_yt_dlp_precalentado_borra_sus_cookies_al_terminar(sealed_cookies):
    """Proceso real (un Python que espera en stdin): la copia vive mientras él."""
    jar = credentials.EphemeralCookies(sealed_cookies)
    path = jar.open()
    warm = WarmProcess([sys.executable, "-c", "import sys; sys.stdin.read()"], (True, "default"),
                       on_exit=jar.close)
    assert path.is_file()
    warm.discard()
    for _ in range(100):
        if not path.exists():
            break
        threading.Event().wait(0.02)
    assert not path.exists()


def test_al_arrancar_se_borran_las_cookies_huerfanas(isolated_home):
    paths.ensure_dirs()
    child = subprocess.Popen(["cmd", "/c", "exit 0"] if sys.platform == "win32" else ["true"])
    child.wait()                          # su pid ya no es de ningún proceso vivo
    folder = paths.cred_dir()
    orphan = folder / f".cookies_ephemeral_{child.pid}_{'a' * 32}.tmp"
    mine = folder / f".cookies_ephemeral_{os.getpid()}_{'b' * 32}.tmp"
    other = folder / "otra_cosa.tmp"
    for path in (orphan, mine, other):
        path.write_text(COOKIES, encoding="utf-8")
    paths.ensure_dirs()
    assert not orphan.exists()
    assert mine.exists() and other.exists()   # las de un proceso vivo y lo ajeno no se tocan


# ---------------------------------------------------------------------------
# Guardar desde el diálogo de la cuenta
# ---------------------------------------------------------------------------
@windows
def test_las_cabeceras_pegadas_se_guardan_cifradas(qapp, db, isolated_home, monkeypatch):
    from ytm_native.ui import widgets

    monkeypatch.setattr(widgets.QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(widgets.QMessageBox, "warning", lambda *a, **k: None)
    dialog = widgets.AccountDialog(db)
    dialog.headers_edit.setPlainText(
        "accept: */*\nx-goog-authuser: 0\ncontent-encoding: gzip\n"
        f"cookie: {SECRET}; __Secure-3PAPISID=otro_secreto\n"
    )
    changed = []
    dialog.credentials_changed.connect(lambda: changed.append(True))
    dialog._save_headers()
    assert changed
    saved = json.loads(credentials.read_text(paths.browser_json()))
    assert SECRET in saved["cookie"] and "content-encoding" not in saved
    assert b"SAPISID" not in everything_in(paths.cred_dir())
    assert "cifrado" in dialog.headers_status.text()


@windows
def test_un_cookies_txt_elegido_se_guarda_cifrado(qapp, db, isolated_home, tmp_path, monkeypatch):
    from ytm_native.ui import widgets

    source = tmp_path / "exportadas.txt"
    source.write_bytes(COOKIES.encode())
    monkeypatch.setattr(widgets.QFileDialog, "getOpenFileName", lambda *a, **k: (str(source), ""))
    shown = []
    monkeypatch.setattr(widgets.QMessageBox, "information", lambda *a, **k: shown.append(a[2]))
    dialog = widgets.AccountDialog(db)
    dialog._pick_cookies()
    assert credentials.read_text(paths.cookies_txt()) == COOKIES
    assert b"SAPISID" not in everything_in(paths.cred_dir())
    assert shown and "borrar el archivo original" in shown[0]
