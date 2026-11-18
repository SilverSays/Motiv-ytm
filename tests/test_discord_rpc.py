"""Discord Rich Presence: protocolo, opción apagada, Discord ausente o cerrado (sin Discord real)."""

import json
import struct
import threading
import time

import pytest

from ytm_native.core import config
from ytm_native.core.db import Track
from ytm_native.playback.player import PLAYING
from ytm_native.services import discord_rpc
from ytm_native.services.discord_rpc import (
    OP_FRAME, OP_HANDSHAKE, OP_PING, OP_PONG, DiscordPresence, build_activity, encode, read_frame,
)
from ytm_native.ui import main_window as mw

from test_home_search import animations_on, net, window  # noqa: F401
from test_player import env, start_playing, wait_until  # noqa: F401

CLIENT_ID = "123456789012345678"
TRACK = Track("v1", "Canción", "Artista", "Álbum", 200, thumb_url="https://i.ytimg.com/vi/v1/hq.jpg")


class FakeDiscord:
    """Un extremo de la tubería de Discord en memoria."""

    def __init__(self, ping_first: bool = False):
        self.frames: list[tuple[int, dict]] = []
        self._replies = bytearray()
        self._lock = threading.Lock()
        self.broken = False
        self.closed = False
        self.ping_first = ping_first

    def _reply(self, op: int, payload: dict) -> None:
        with self._lock:
            self._replies += encode(op, payload)

    def write(self, data: bytes) -> None:
        if self.broken or self.closed:
            raise BrokenPipeError("Discord se cerró")
        op, length = struct.unpack("<II", data[:8])
        payload = json.loads(data[8:8 + length].decode("utf-8"))
        self.frames.append((op, payload))
        if op == OP_HANDSHAKE:
            self._reply(OP_FRAME, {"cmd": "DISPATCH", "evt": "READY", "data": {"v": 1}})
        elif op == OP_FRAME:
            if self.ping_first:
                self.ping_first = False
                self._reply(OP_PING, {"n": 1})
            self._reply(OP_FRAME, {"cmd": "SET_ACTIVITY", "nonce": payload["nonce"], "data": {}})

    def read(self, size: int, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                if len(self._replies) >= size:
                    out = bytes(self._replies[:size])
                    del self._replies[:size]
                    return out
            if self.broken:
                raise OSError("la tubería de Discord se cerró")
            if time.monotonic() > deadline:
                raise TimeoutError("Discord no responde")
            time.sleep(0.005)

    def close(self) -> None:
        self.closed = True

    def activities(self) -> list:
        return [p["args"]["activity"] for op, p in self.frames
                if op == OP_FRAME and p.get("cmd") == "SET_ACTIVITY"]


class Connector:
    """Lo que abre la tubería: cuenta los intentos y simula Discord cerrado."""

    def __init__(self):
        self.attempts = 0
        self.running = True
        self.pipes: list[FakeDiscord] = []

    def __call__(self):
        self.attempts += 1
        if not self.running:
            raise FileNotFoundError(r"\\.\pipe\discord-ipc-0")
        pipe = FakeDiscord()
        self.pipes.append(pipe)
        return pipe


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_MIN_UPDATE_S", 0.0)
    monkeypatch.setattr(config, "DISCORD_RETRY_DELAYS_S", (0.15,))


@pytest.fixture
def connector():
    return Connector()


@pytest.fixture
def presence(qapp, db, connector):
    db.set_setting(discord_rpc.CLIENT_ID_KEY, CLIENT_ID)
    p = DiscordPresence(db, connect=connector)
    yield p
    p.shutdown()


def eventually(cond, timeout=3.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


# ---------------------------------------------------------------------------
# Protocolo y actividad
# ---------------------------------------------------------------------------
def test_mensajes_con_cabecera_little_endian():
    data = encode(OP_HANDSHAKE, {"v": 1, "client_id": CLIENT_ID})
    op, length = struct.unpack("<II", data[:8])
    assert op == OP_HANDSHAKE and length == len(data) - 8
    pipe = FakeDiscord()
    pipe._reply(OP_FRAME, {"evt": "READY"})
    assert read_frame(pipe) == (OP_FRAME, {"evt": "READY"})


def test_actividad_escuchando_con_tiempos_y_caratula():
    activity = build_activity(TRACK, TRACK.thumb_url, True, 30.0, 200.0, now=1000.0)
    assert activity["type"] == discord_rpc.ACTIVITY_LISTENING
    assert activity["details"] == "Canción" and activity["state"] == "Artista"
    assert activity["timestamps"] == {"start": 970_000, "end": 1_170_000}
    assert activity["assets"] == {"large_image": TRACK.thumb_url, "large_text": "Álbum"}


def test_en_pausa_sin_tiempos_y_textos_dentro_de_los_limites():
    track = Track("v2", "X" * 300, "Y", "", None)
    activity = build_activity(track, "http://sin-https.example/a.jpg", False, 10.0, 0.0, now=1000.0)
    assert "timestamps" not in activity and "assets" not in activity
    assert len(activity["details"]) == 128 and activity["details"].endswith("…")
    assert activity["state"] == "Y (en pausa)"
    short = build_activity(Track("v3", "Z", "Q"), "", True, 0, 0, 0)
    assert len(short["details"]) >= 2                 # Discord pide al menos 2 caracteres


# ---------------------------------------------------------------------------
# Opción apagada
# ---------------------------------------------------------------------------
def test_apagado_por_defecto_no_abre_ninguna_conexion(presence, connector):
    assert not presence.enabled
    for pos in range(0, 30, 3):
        presence.update(TRACK, TRACK.thumb_url, True, float(pos), 200.0)
    presence.update(None)
    time.sleep(0.1)
    assert connector.attempts == 0 and presence.link is None


def test_sin_id_de_aplicacion_tampoco_se_conecta(qapp, db, connector, monkeypatch):
    monkeypatch.setattr(config, "DISCORD_CLIENT_ID", "")
    monkeypatch.delenv("YTMN_DISCORD_CLIENT_ID", raising=False)
    p = DiscordPresence(db, connect=connector)
    p.set_enabled(True)
    p.update(TRACK, TRACK.thumb_url, True, 0.0, 200.0)
    time.sleep(0.1)
    assert connector.attempts == 0 and p.link is None


# ---------------------------------------------------------------------------
# Con Discord
# ---------------------------------------------------------------------------
def test_activado_publica_la_cancion(presence, connector):
    presence.set_enabled(True)
    presence.update(TRACK, TRACK.thumb_url, True, 5.0, 200.0)
    assert eventually(lambda: connector.pipes and connector.pipes[0].activities())
    pipe = connector.pipes[0]
    handshake = pipe.frames[0]
    assert handshake == (OP_HANDSHAKE, {"v": 1, "client_id": CLIENT_ID})
    activity = pipe.activities()[0]
    assert activity["details"] == "Canción" and activity["state"] == "Artista"
    assert set(activity["timestamps"]) == {"start", "end"}


def test_el_avance_normal_no_se_reenvia_pero_un_salto_si(presence, connector):
    clock = [1000.0]
    presence._clock = lambda: clock[0]
    presence.set_enabled(True)
    presence.update(TRACK, TRACK.thumb_url, True, 0.0, 200.0)
    assert eventually(lambda: connector.pipes and len(connector.pipes[0].activities()) == 1)
    for step in range(1, 20):                          # 19 s sonando con normalidad
        clock[0] += 1
        presence.update(TRACK, TRACK.thumb_url, True, float(step), 200.0)
    time.sleep(0.1)
    assert len(connector.pipes[0].activities()) == 1
    presence.update(TRACK, TRACK.thumb_url, True, 120.0, 200.0)   # salto en la barra
    assert eventually(lambda: len(connector.pipes[0].activities()) == 2)
    presence.update(TRACK, TRACK.thumb_url, False, 120.0, 200.0)  # pausa
    assert eventually(lambda: len(connector.pipes[0].activities()) == 3)
    assert "timestamps" not in connector.pipes[0].activities()[-1]


def test_un_ping_de_discord_se_contesta(qapp, db):
    db.set_setting(discord_rpc.CLIENT_ID_KEY, CLIENT_ID)
    pipe = FakeDiscord(ping_first=True)
    p = DiscordPresence(db, connect=lambda: pipe)
    p.set_enabled(True)
    p.update(TRACK, "", True, 0.0, 200.0)
    assert eventually(lambda: any(op == OP_PONG for op, _ in pipe.frames))
    p.shutdown()


def test_apagar_quita_la_actividad_y_cierra(presence, connector):
    presence.set_enabled(True)
    presence.update(TRACK, TRACK.thumb_url, True, 0.0, 200.0)
    assert eventually(lambda: connector.pipes and connector.pipes[0].activities())
    link = presence.link
    presence.set_enabled(False)
    assert presence.link is None
    link.join(2)
    pipe = connector.pipes[0]
    assert pipe.activities()[-1] is None and pipe.closed


# ---------------------------------------------------------------------------
# Discord ausente o cerrado a mitad
# ---------------------------------------------------------------------------
def test_sin_discord_no_bloquea_ni_reintenta_en_bucle(presence, connector):
    connector.running = False
    presence.set_enabled(True)
    started = time.monotonic()
    presence.update(TRACK, TRACK.thumb_url, True, 0.0, 200.0)
    assert time.monotonic() - started < 0.05          # la ventana no espera a Discord
    time.sleep(0.5)
    # Con esperas de 0,15 s entre intentos: unos pocos, no cientos.
    assert 1 <= connector.attempts <= 5
    connector.running = True                           # Discord se abre
    assert eventually(lambda: connector.pipes and connector.pipes[0].activities())


def test_discord_cerrado_a_mitad_se_recupera_solo(presence, connector):
    presence.set_enabled(True)
    presence.update(TRACK, TRACK.thumb_url, True, 0.0, 200.0)
    assert eventually(lambda: connector.pipes and connector.pipes[0].activities())
    connector.pipes[0].broken = True                   # Discord se cierra
    connector.running = False
    other = Track("v9", "Otra", "Otro artista", "", 180)
    presence.update(other, "", True, 0.0, 180.0)
    time.sleep(0.4)
    assert len(connector.pipes) == 1                   # sigue cerrado: nada se publica
    connector.running = True                           # vuelve a abrirse
    assert eventually(lambda: len(connector.pipes) == 2 and connector.pipes[1].activities())
    assert connector.pipes[1].activities()[-1]["details"] == "Otra"


def test_la_musica_sigue_aunque_discord_falle(env, db, monkeypatch):
    """Con Discord caído (la tubería se rompe en cada intento), reproducir,
    avanzar y saltar de pista funciona igual y sin esperas."""
    db.set_setting(discord_rpc.CLIENT_ID_KEY, CLIENT_ID)

    def broken():
        pipe = FakeDiscord()
        pipe.broken = True
        return pipe

    presence = DiscordPresence(env.db, connect=broken)
    presence.set_enabled(True)
    slowest = [0.0]

    def follow(pos, duration):
        started = time.monotonic()
        presence.update(env.player.current_track(), "", env.player.state == PLAYING, pos, duration)
        slowest[0] = max(slowest[0], time.monotonic() - started)

    env.player.position_changed.connect(follow)
    start_playing(env)
    for i in range(1, 40):
        env.mpv.at(i * 0.5)
    env.player.next_track()
    assert wait_until(env.qapp, lambda: len(env.mpv.loaded) == 2)
    env.mpv.begin()
    env.mpv.at(1.0)
    assert env.player.state == PLAYING and env.player.current_track().video_id == "v1"
    assert slowest[0] < 0.05
    presence.shutdown()


# ---------------------------------------------------------------------------
# En la ventana
# ---------------------------------------------------------------------------
def test_activar_sin_id_lo_pide_y_cancelar_lo_deja_apagado(window, monkeypatch):
    monkeypatch.setattr(config, "DISCORD_CLIENT_ID", "")
    monkeypatch.delenv("YTMN_DISCORD_CLIENT_ID", raising=False)
    asked = []

    def cancel(*args, **kwargs):
        asked.append(args[1])
        return "", False

    monkeypatch.setattr(mw.QInputDialog, "getText", cancel)
    window.discord_action.setChecked(True)
    assert asked and not window.discord_action.isChecked()
    assert not window.discord.enabled and window.discord.link is None


def test_activar_con_id_publica_lo_que_suena(window, monkeypatch, connector):
    monkeypatch.setattr(config, "DISCORD_CLIENT_ID", "")
    monkeypatch.setattr(mw.QInputDialog, "getText", lambda *a, **k: (CLIENT_ID, True))
    window.discord._connect = connector
    window.discord_action.setChecked(True)
    assert window.discord.enabled and window.db.get_setting(discord_rpc.CLIENT_ID_KEY) == CLIENT_ID
    sent = []
    monkeypatch.setattr(window.discord, "update", lambda *args: sent.append(args))
    monkeypatch.setattr(window.player, "current_track", lambda: TRACK)
    monkeypatch.setattr(window.player, "state", PLAYING)
    window._on_position(12.0, 200.0)
    assert sent and sent[-1][0] is TRACK and sent[-1][2] is True and sent[-1][3] == 12.0
    window.discord_action.setChecked(False)
    assert not window.discord.enabled
    window.discord.shutdown()


def test_last_fm_solo_se_puede_activar_con_cuenta(window):
    assert not window.lastfm_action.isEnabled() and not window.lastfm_action.isChecked()
    assert window.lastfm_account_action.text() == "Conectar con Last.fm…"
