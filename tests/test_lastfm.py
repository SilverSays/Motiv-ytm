"""Scrobbling a Last.fm: firma, umbrales, cola sin red, sesión cifrada (sin red)."""

import hashlib
import sys
import time
from urllib.parse import parse_qs, urlparse

import pytest

from ytm_native.core import config, crypto, paths
from ytm_native.core.db import Track
from ytm_native.playback.history import PlayTracker
from ytm_native.services import lastfm
from ytm_native.services.lastfm import (
    LastfmClient, LastfmCredentials, LastfmError, LastfmScrobbler, Scrobble, ScrobbleQueue, SessionStore,
    scrobble_from, scrobble_threshold, sign,
)

from test_player import env, start_playing, wait_until  # noqa: F401

windows = pytest.mark.skipif(sys.platform != "win32", reason="la sesión se cifra con DPAPI")

SESSION_KEY = "SK-secreta-0123456789abcdef"


class FakeLastfm:
    """La API de Last.fm en memoria: anota cada llamada y responde como ella."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.offline = False
        self.error_code = 0

    def __call__(self, method: str, params: dict, timeout: float) -> dict:
        name = params["method"]
        self.calls.append((name, dict(params)))
        if self.offline:
            raise LastfmError("sin conexión con Last.fm (ConnectionError)", network=True)
        if self.error_code:
            return {"error": self.error_code, "message": f"fallo {self.error_code}"}
        if name == "auth.getToken":
            return {"token": "tok-1"}
        if name == "auth.getSession":
            return {"session": {"name": "ana", "key": SESSION_KEY, "subscriber": 0}}
        if name == "track.updateNowPlaying":
            return {"nowplaying": {}}
        if name == "track.scrobble":
            sent = sum(1 for key in params if key.startswith("track["))
            return {"scrobbles": {"@attr": {"accepted": sent, "ignored": 0}}}
        return {"error": 3, "message": "método no válido"}

    def named(self, name: str) -> list[dict]:
        return [params for method, params in self.calls if method == name]


def sync_runner(fn, done):
    """En las pruebas lo «de fondo» corre en el acto."""
    try:
        result = fn()
    except Exception as exc:  # noqa: BLE001
        done(None, exc)
    else:
        done(result, None)


class MemoryStore(SessionStore):
    """Mismo almacén, pero sin DPAPI (para las pruebas que no van de eso)."""

    def __init__(self, creds: LastfmCredentials = LastfmCredentials()):
        self.creds = creds

    def load(self):
        return self.creds

    def save(self, creds):
        self.creds = creds

    def clear(self):
        self.creds = LastfmCredentials()


NOW = float(int(time.time()))
CONNECTED = LastfmCredentials("clave-app", "secreto-app", "ana", SESSION_KEY)


@pytest.fixture
def api():
    return FakeLastfm()


def scrobbler_for(db, api, creds=CONNECTED, enabled=True):
    s = LastfmScrobbler(db, store=MemoryStore(creds), http=api, runner=sync_runner)
    if enabled:
        s.set_enabled(True)
    return s


# ---------------------------------------------------------------------------
# Piezas sueltas
# ---------------------------------------------------------------------------
def test_firma_como_la_pide_la_api():
    params = {"api_key": "k", "method": "auth.getSession", "token": "t", "format": "json"}
    expected = hashlib.md5(b"api_keykmethodauth.getSessiontokentsecreto").hexdigest()
    assert sign(params, "secreto") == expected        # «format» no entra en la firma


def test_umbral_mitad_o_cuatro_minutos():
    assert scrobble_threshold(600) == 240
    assert scrobble_threshold(200) == 100
    assert scrobble_threshold(30) is None              # 30 s o menos no se registran
    assert scrobble_threshold(0) == 240                # sin duración, los 4 minutos


def test_que_se_envia_de_una_pista():
    entry = scrobble_from(Track("v1", "Canción", "Artista Uno, Invitada", "Álbum", 200), 1000.4)
    assert (entry.artist, entry.track, entry.album, entry.duration_s, entry.timestamp) == (
        "Artista Uno", "Canción", "Álbum", 200, 1000)
    assert scrobble_from(Track("local:abc", "Mía", "Yo"), 1000) is None
    assert scrobble_from(Track("v2", "Sin artista", ""), 1000) is None


def test_cliente_firma_y_manda_la_sesion(api):
    client = LastfmClient("clave-app", "secreto-app", SESSION_KEY, http=api)
    client.scrobble([Scrobble("A", "T", "", 200, 1000)])
    params = api.named("track.scrobble")[0]
    assert params["sk"] == SESSION_KEY and params["artist[0]"] == "A" and params["timestamp[0]"] == "1000"
    assert "album[0]" not in params                    # vacío: no se manda
    unsigned = {k: v for k, v in params.items() if k != "api_sig"}
    assert params["api_sig"] == sign(unsigned, "secreto-app")


def test_un_error_de_la_api_se_clasifica(api):
    api.error_code = 9
    client = LastfmClient("k", "s", SESSION_KEY, http=api)
    with pytest.raises(LastfmError) as info:
        client.update_now_playing(Scrobble("A", "T", "", 200, 1))
    assert info.value.kind == lastfm.AUTH and not info.value.transient
    assert LastfmError("x", code=16).transient and LastfmError("x", network=True).transient


# ---------------------------------------------------------------------------
# Con la música sonando
# ---------------------------------------------------------------------------
def listen(env, start: float, seconds: float) -> float:
    pos = start
    for _ in range(int(seconds * 2)):
        pos += 0.5
        env.mpv.at(pos)
    return pos


def test_se_anuncia_al_empezar_y_se_registra_a_mitad_de_pista(env, api):
    scrobbler = scrobbler_for(env.db, api)
    tracker = PlayTracker(env.db, env.player, scrobbler=scrobbler, clock=lambda: NOW)  # noqa: F841 - vivo durante la prueba
    start_playing(env)                                 # pistas de 100 s: el umbral es 50 s
    pos = listen(env, 0, 1)
    assert len(api.named("track.updateNowPlaying")) == 1
    pos = listen(env, pos, 48)
    assert api.named("track.scrobble") == []
    listen(env, pos, 2)
    sent = api.named("track.scrobble")
    assert len(sent) == 1
    assert sent[0]["track[0]"] == "Pista 0" and sent[0]["artist[0]"] == "Artista"
    # La marca es cuándo empezó a sonar, no cuándo se cumplió el umbral.
    assert int(NOW) - 1 <= int(sent[0]["timestamp[0]"]) <= int(NOW)
    listen(env, pos + 1, 40)                           # seguir oyéndola no la manda otra vez
    assert len(api.named("track.scrobble")) == 1
    assert scrobbler.queue.count() == 0


def test_mover_la_barra_no_suma_para_last_fm(env, api):
    scrobbler = scrobbler_for(env.db, api)
    tracker = PlayTracker(env.db, env.player, scrobbler=scrobbler)  # noqa: F841 - vivo durante la prueba
    start_playing(env)
    listen(env, 0, 10)
    env.mpv.at(90)                                     # salto hasta casi el final
    listen(env, 90, 5)
    assert api.named("track.scrobble") == []


def test_apagado_por_defecto_no_sale_nada(env, api):
    scrobbler = LastfmScrobbler(env.db, store=MemoryStore(CONNECTED), http=api, runner=sync_runner)
    assert not scrobbler.enabled and not scrobbler.active
    tracker = PlayTracker(env.db, env.player, scrobbler=scrobbler)  # noqa: F841 - vivo durante la prueba
    start_playing(env)
    listen(env, 0, 99)
    assert api.calls == [] and scrobbler.queue.count() == 0


def test_sin_red_se_encola_y_sale_en_bloque_al_volver(env, api):
    scrobbler = scrobbler_for(env.db, api)
    api.offline = True
    now = time.time()
    for i in range(3):
        assert scrobbler.submit(Track(f"v{i}", f"Pista {i}", "Artista", "", 200), now - 600 + i * 200, 200)
    assert scrobbler.queue.count() == 3
    assert scrobbler._retry.isActive()                 # reintento programado, no en bucle
    attempts = scrobbler.queue.pending()
    assert all(e.row_id for e in attempts)
    # Los cortes de red no cuentan como rechazos: las escuchas no caducan por eso.
    with env.db.transaction() as c:
        assert {r[0] for r in c.execute("SELECT attempts FROM lastfm_queue")} == {0}
    api.offline = False
    api.calls.clear()
    scrobbler._retry.timeout.emit()                    # vence la espera
    sent = api.named("track.scrobble")
    assert len(sent) == 1                              # las tres en una sola petición
    assert [sent[0][f"track[{i}]"] for i in range(3)] == ["Pista 0", "Pista 1", "Pista 2"]
    assert scrobbler.queue.count() == 0 and not scrobbler._retry.isActive()


def test_la_cola_vieja_o_rechazada_se_descarta(db, monkeypatch):
    queue = ScrobbleQueue(db)
    now = 2_000_000_000
    queue.add(Scrobble("A", "Vieja", "", 200, now - config.LASTFM_MAX_AGE_S - 10))
    queue.add(Scrobble("A", "Nueva", "", 200, now - 60))
    queue.add(Scrobble("A", "Nueva", "", 200, now - 60))      # repetida: no se duplica
    assert [e.track for e in queue.pending(now=now)] == ["Nueva"]
    ids = [e.row_id for e in queue.pending(now=now)]
    for _ in range(config.LASTFM_MAX_ATTEMPTS):
        queue.note_failure(ids, "rechazada", rejected=True)
    assert queue.pending(now=now) == []


def test_una_sesion_rechazada_se_olvida_y_la_cola_espera(db, api):
    scrobbler = scrobbler_for(db, api)
    lost = []
    scrobbler.session_lost.connect(lost.append)
    api.error_code = 9
    scrobbler.submit(Track("v1", "Pista", "Artista", "", 200), time.time() - 300, 200)
    assert lost and not scrobbler.connected and not scrobbler.active
    assert scrobbler.queue.count() == 1                # nada se pierde
    assert scrobbler.store.creds.session_key == ""


def test_una_clave_de_aplicacion_rechazada_no_insiste(db, api):
    scrobbler = scrobbler_for(db, api)
    halted = []
    scrobbler.halted.connect(halted.append)
    api.error_code = 10
    scrobbler.submit(Track("v1", "Pista", "Artista", "", 200), time.time() - 300, 200)
    assert halted and not scrobbler._retry.isActive()
    api.calls.clear()
    scrobbler.flush()
    assert api.calls == []


def test_desconectar_borra_la_sesion_y_la_cola(db, api):
    scrobbler = scrobbler_for(db, api)
    api.offline = True
    scrobbler.submit(Track("v1", "Pista", "Artista", "", 200), time.time() - 300, 200)
    scrobbler.disconnect()
    assert not scrobbler.connected and not scrobbler.enabled and scrobbler.queue.count() == 0
    assert scrobbler.store.creds.api_key == "clave-app"     # la clave de la aplicación se queda


# ---------------------------------------------------------------------------
# Conexión y cifrado de la sesión
# ---------------------------------------------------------------------------
def every_byte_under(folder) -> bytes:
    return b"".join(p.read_bytes() for p in folder.rglob("*") if p.is_file())


@windows
def test_conectar_guarda_la_sesion_cifrada_y_nunca_en_claro(db, api, isolated_home):
    scrobbler = LastfmScrobbler(db, http=api, runner=sync_runner)
    scrobbler.set_app_credentials("clave-app", "secreto-de-la-app")
    pages = []
    scrobbler.request_authorization(lambda url, error: pages.append((url, error)))
    url, error = pages[0]
    assert error is None
    query = parse_qs(urlparse(url).query)
    assert url.startswith(config.LASTFM_AUTH_URL) and query == {"api_key": ["clave-app"], "token": ["tok-1"]}
    users = []
    scrobbler.complete_authorization(lambda user, error: users.append((user, error)))
    assert users == [("ana", None)]
    assert scrobbler.connected and scrobbler.active and db.get_bool(lastfm.ENABLED_KEY)

    sealed = paths.cred_dir() / lastfm.SESSION_FILE
    raw = sealed.read_bytes()
    assert raw.startswith(crypto.MAGIC)
    # Ni la clave de sesión ni el secreto aparecen en claro en ningún archivo
    # de la app (base SQLite y su WAL incluidos) ni en los ajustes.
    everything = every_byte_under(isolated_home)
    for secret in (SESSION_KEY, "secreto-de-la-app"):
        assert secret.encode() not in everything
        assert secret.encode("utf-16-le") not in everything
    with db.transaction() as c:
        values = " ".join(str(r[0]) for r in c.execute("SELECT value FROM settings"))
    assert SESSION_KEY not in values
    # Y se recupera al abrir la app otra vez.
    again = SessionStore().load()
    assert again.session_key == SESSION_KEY and again.user == "ana"


@windows
def test_una_sesion_ilegible_cuenta_como_no_conectada(db, isolated_home):
    target = paths.cred_dir() / lastfm.SESSION_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(crypto.MAGIC + b"basura que no es dpapi")
    creds = SessionStore().load()
    assert not creds.connected
    scrobbler = LastfmScrobbler(db, http=FakeLastfm(), runner=sync_runner)
    assert not scrobbler.connected


def test_autorizar_sin_token_pendiente_falla_limpio(db, api):
    scrobbler = LastfmScrobbler(db, store=MemoryStore(LastfmCredentials("k", "s")), http=api,
                                runner=sync_runner)
    results = []
    scrobbler.complete_authorization(lambda user, error: results.append((user, error)))
    assert results[0][0] is None and isinstance(results[0][1], LastfmError)
    assert api.calls == []
