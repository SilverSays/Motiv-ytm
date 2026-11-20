"""Controles multimedia de Windows: qué se le cuenta al sistema y qué hacen sus botones.

La sesión de Windows se sustituye por una falsa: las pruebas no crean ninguna real.
"""

import sys

import pytest

from ytm_native.core import config
from ytm_native.core.db import Track
from ytm_native.playback import media_controls
from ytm_native.playback.media_controls import MediaControls, open_windows_session
from ytm_native.playback.mpv_ipc import MpvPlayer
from ytm_native.playback.player import PAUSED, PLAYING, STOPPED

from test_player import env, start_playing, wait_until  # noqa: F401


class FakeSession:
    def __init__(self, on_button, on_seek):
        self.on_button, self.on_seek = on_button, on_seek
        self.tracks, self.statuses, self.timelines = [], [], []
        self.closed = False
        self.broken = False

    def set_track(self, title, artist, album, art_url):
        if self.broken:
            raise OSError("Windows dijo que no")
        self.tracks.append((title, artist, album, art_url))

    def set_status(self, status):
        self.statuses.append(status)

    def set_timeline(self, position, duration):
        self.timelines.append((position, duration))

    def close(self):
        self.closed = True


@pytest.fixture
def controls(env):
    made = []

    def factory(on_button, on_seek):
        made.append(FakeSession(on_button, on_seek))
        return made[-1]

    controls = MediaControls(env.player, session_factory=factory)
    controls.session = made[0]
    return controls


def test_sin_sesion_no_hace_nada_y_lo_dice(env):
    controls = MediaControls(env.player, session_factory=lambda *_a: None)
    assert controls.active is False
    start_playing(env)                       # las señales del reproductor no rompen nada
    controls.close()


def test_fuera_de_windows_o_apagado_por_entorno_no_se_crea(monkeypatch):
    monkeypatch.setenv("YTMN_NO_MEDIA_CONTROLS", "1")
    assert open_windows_session(print, print) is None
    monkeypatch.delenv("YTMN_NO_MEDIA_CONTROLS")
    monkeypatch.setattr(sys, "platform", "linux")
    assert open_windows_session(print, print) is None


def test_sin_el_paquete_winrt_se_queda_con_los_controles_de_mpv(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")

    def missing(*_args):
        raise ImportError("No module named 'winrt'")

    monkeypatch.setattr(media_controls, "WindowsSession", missing)
    assert open_windows_session(print, print) is None

    def refused(*_args):
        raise OSError("clase no registrada")

    monkeypatch.setattr(media_controls, "WindowsSession", refused)
    assert open_windows_session(print, print) is None


def test_cuenta_la_pista_el_estado_y_la_duracion(env, controls):
    session = controls.session
    assert controls.active and session.tracks == []
    start_playing(env, 2)
    assert session.tracks == [("Pista 2", "Artista", "Álbum", "https://i.ytimg.com/vi/v2/mqdefault.jpg")]
    assert session.statuses == ["changing", "playing"]
    assert session.timelines[-1] == (0.0, 100.0)
    env.mpv.pause_changed.emit(True)
    assert session.statuses[-1] == "paused"
    env.player.next_track()
    assert wait_until(env.qapp, lambda: len(env.mpv.loaded) == 2)
    assert session.tracks[-1][0] == "Pista 3" and session.statuses[-1] == "changing"


def test_la_caratula_la_elige_quien_crea_los_controles(env):
    made = []
    controls = MediaControls(
        env.player, session_factory=lambda *a: made.append(FakeSession(*a)) or made[-1],
        art_url=lambda track: f"art://{track.video_id}/300",
    )
    start_playing(env)
    assert made[0].tracks[0][3] == "art://v0/300" and controls.active


def test_la_posicion_se_manda_de_vez_en_cuando_y_al_saltar(env):
    # Un reloj propio de los controles: congelar time.monotonic para todo el
    # proceso pararía también las esperas de las pruebas (y colgaría la batería).
    clock = [1000.0]
    made = []
    controls = MediaControls(env.player, session_factory=lambda *a: made.append(FakeSession(*a)) or made[-1],
                             clock=lambda: clock[0])
    session = made[0]
    assert controls.active
    start_playing(env)
    sent = len(session.timelines)
    for second in range(1, 5):               # suena con normalidad: nada nuevo que contar
        clock[0] += 1
        env.mpv.at(float(second))
    assert len(session.timelines) == sent
    clock[0] += 1
    env.mpv.at(5.0)                          # pasó el intervalo
    assert session.timelines[-1] == (5.0, 100.0) and len(session.timelines) == sent + 1
    clock[0] += 0.3
    env.mpv.at(60.0)                         # se movió la barra: al momento
    assert session.timelines[-1] == (60.0, 100.0)
    assert config.MEDIA_TIMELINE_INTERVAL_S == 5.0


def test_los_botones_de_windows_mueven_la_cola(env, controls):
    session = controls.session
    start_playing(env)
    session.on_button("pause")
    assert env.mpv.pauses[-1] is True
    env.mpv.pause_changed.emit(True)
    session.on_button("pause")               # ya en pausa: no la quita
    assert env.player.state == PAUSED and env.mpv.pauses.count(False) == 1
    session.on_button("play")
    assert env.mpv.pauses[-1] is False
    env.mpv.pause_changed.emit(False)
    session.on_button("play")                # ya suena: no la pausa
    assert env.player.state == PLAYING
    session.on_button("next")
    assert wait_until(env.qapp, lambda: env.player.current_track().video_id == "v1")
    assert wait_until(env.qapp, lambda: len(env.mpv.loaded) == 2)
    env.mpv.begin()
    session.on_button("previous")
    assert wait_until(env.qapp, lambda: env.player.current_track().video_id == "v0")
    session.on_button("desconocido")         # un botón que no se usa no hace nada


def test_reproducir_desde_windows_reanuda_una_cola_detenida(env, controls):
    start_playing(env)
    env.mpv.at(30)
    env.player.stop()
    assert env.player.state == STOPPED and controls.session.statuses[-1] == "stopped"
    controls.session.on_button("play")
    assert wait_until(env.qapp, lambda: len(env.mpv.loaded) == 2)
    env.mpv.begin()
    assert env.mpv.seeks == [30]


def test_la_barra_de_windows_salta_a_otro_punto(env, controls):
    start_playing(env)
    controls.session.on_seek(73.0)
    assert env.mpv.seeks == [73.0]


def test_los_avisos_de_windows_llegan_desde_otro_hilo(env, controls):
    import threading

    start_playing(env)
    thread = threading.Thread(target=lambda: controls.session.on_button("pause"))
    thread.start()
    thread.join()
    assert env.mpv.pauses[-1] is False       # aún no: espera al hilo de la GUI
    assert wait_until(env.qapp, lambda: env.mpv.pauses[-1] is True)


def test_un_fallo_de_windows_no_para_la_musica(env, controls):
    controls.session.broken = True
    start_playing(env)
    assert env.player.state == PLAYING and controls.session.tracks == []


def test_con_una_pista_ya_puesta_la_cuenta_al_crearse(env):
    start_playing(env, 1)
    made = []
    MediaControls(env.player, session_factory=lambda *a: made.append(FakeSession(*a)) or made[-1])
    assert made[0].tracks[0][0] == "Pista 1" and made[0].statuses == ["playing"]


def test_al_cerrar_suelta_la_sesion(env, controls):
    session = controls.session
    controls.close()
    assert session.closed and controls.active is False
    start_playing(env)
    assert session.tracks == []
    controls.close()                         # dos veces no rompe


# ---------------------------------------------------------------------------
# mpv deja de publicar la suya
# ---------------------------------------------------------------------------
def test_mpv_apaga_sus_controles_cuando_la_app_publica_los_suyos(qapp, monkeypatch):
    mpv = MpvPlayer()
    sent = []
    monkeypatch.setattr(mpv, "command", lambda *args: sent.append(args) or 1)
    mpv._connected = True
    mpv._on_connected()
    assert not any(args[:2] == ("set_property", "media-controls") for args in sent)
    mpv.set_media_controls(False)
    assert sent[-1] == ("set_property", "media-controls", "no")
    sent.clear()
    mpv._on_connected()                      # tras relanzarse lo vuelve a apagar
    assert ("set_property", "media-controls", "no") in sent
    # Antes de que mpv arranque (así lo llama la app) no se manda nada: se
    # aplica al conectar, sin órdenes descartadas por el camino.
    mpv._connected = False
    sent.clear()
    mpv.set_media_controls(False)
    assert sent == []
    mpv._connected = True
    mpv.set_media_controls(True)
    assert sent[-1] == ("set_property", "media-controls", "player")


# ---------------------------------------------------------------------------
# Sesión de Windows: una carátula rota no deja la pista sin título
# ---------------------------------------------------------------------------
class _Music:
    title = artist = album_title = None


class _Updater:
    def __init__(self):
        self.music_properties = _Music()
        self.thumbnail = None
        self.type = None
        self.updates = 0

    def clear_all(self):
        self.music_properties = _Music()
        self.thumbnail = None

    def update(self):
        self.updates += 1


class _Smtc:
    def __init__(self):
        self.display_updater = _Updater()
        self.is_enabled = False


def _bare_session(uri, stream_ref):
    """Una ``WindowsSession`` sin WinRT: solo lo que usa ``set_track``."""
    session = object.__new__(media_controls.WindowsSession)
    session._smtc = _Smtc()
    session._music = "MUSIC"
    session._uri = uri
    session._stream_ref = stream_ref
    return session


class _StreamRef:
    @staticmethod
    def create_from_uri(uri):
        return ("carátula", uri)


@pytest.mark.parametrize("art_url", ["no es una url", "http://[::1"])
def test_una_caratula_que_windows_no_acepta_no_quita_titulo_ni_artista(art_url):
    def uri(value):
        raise OSError("El parámetro no es correcto.")      # lo que lanza WinRT

    session = _bare_session(uri, _StreamRef)
    session.set_track("Canción", "Artista", "Disco", art_url)
    updater = session._smtc.display_updater
    music = updater.music_properties
    assert (music.title, music.artist, music.album_title) == ("Canción", "Artista", "Disco")
    assert updater.thumbnail is None and updater.updates == 1
    assert session._smtc.is_enabled is True


def test_si_falla_la_descarga_de_la_caratula_tambien_sigue():
    class Broken:
        @staticmethod
        def create_from_uri(uri):
            raise RuntimeError("WinRT no disponible")

    session = _bare_session(lambda value: value, Broken)
    session.set_track("Canción", "Artista", "", "https://i.ytimg.com/vi/x/mqdefault.jpg")
    assert session._smtc.display_updater.music_properties.title == "Canción"
    assert session._smtc.is_enabled is True


def test_con_caratula_valida_se_envia():
    session = _bare_session(lambda value: f"uri:{value}", _StreamRef)
    session.set_track("Canción", "Artista", "", "https://i.ytimg.com/vi/x/mqdefault.jpg")
    assert session._smtc.display_updater.thumbnail == ("carátula", "uri:https://i.ytimg.com/vi/x/mqdefault.jpg")
