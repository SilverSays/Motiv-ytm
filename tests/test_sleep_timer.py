"""Temporizador de apagado: por tiempo y al acabar la canción, con fundido."""

import pytest
from PySide6.QtCore import QObject, Signal

from ytm_native.playback.player import PLAYING, STOPPED
from ytm_native.playback.sleep_timer import TIME, TRACK, SleepTimer

from test_home_search import animations_on, net, window  # noqa: F401
from test_player import env, settle, start_playing, wait_until  # noqa: F401


class FakeMpv:
    def __init__(self, volume=80):
        self.volume = volume


class FakePlayer(QObject):
    position_changed = Signal(float, float)
    halted_after_track = Signal()
    current_changed = Signal(object)

    def __init__(self):
        super().__init__()
        self.mpv = FakeMpv()
        self.state = PLAYING
        self.stop_after_current = False
        self.log = []

    def set_volume(self, volume):
        self.mpv.volume = volume
        self.log.append(("volume", volume))

    def stop(self):
        self.state = STOPPED
        self.log.append(("stop",))


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def timer(qapp):
    player, clock = FakePlayer(), Clock()
    sleep = SleepTimer(player, clock=clock, fade_s=12)
    sleep.events = []
    sleep.changed.connect(lambda: sleep.events.append("changed"))
    sleep.expired.connect(lambda: sleep.events.append("expired"))
    sleep.remaining_changed.connect(lambda s: sleep.events.append(s))
    yield sleep, player, clock
    sleep.cancel()


def advance(sleep, clock, to):
    clock.now = 1000.0 + to
    sleep._tick()


def test_por_tiempo_baja_el_volumen_al_final_y_para(timer):
    sleep, player, clock = timer
    sleep.start_minutes(1)
    assert sleep.active and sleep.mode == TIME and sleep.remaining() == 60
    assert sleep.events[:2] == ["changed", 60]
    advance(sleep, clock, 40)                                 # faltan 20 s: aún sin fundido
    assert player.log == [] and not sleep.fading
    advance(sleep, clock, 54)                                 # faltan 6 de 12: la mitad
    assert player.log == [("volume", 40)]
    advance(sleep, clock, 57)
    assert player.log[-1] == ("volume", 20)
    advance(sleep, clock, 60)
    # Para y, ya parado, devuelve el volumen: la próxima vez no arranca muda.
    assert player.log[-2:] == [("stop",), ("volume", 80)]
    assert not sleep.active and sleep.events[-2:] == ["changed", "expired"]
    assert not sleep._timer.isActive()


def test_recien_puesto_marca_justo_el_tiempo_pedido(timer):
    sleep, _player, clock = timer
    clock.now = 1000.3                      # (1000.3 + 1800) - 1000.3 da 1800,0000000000002
    sleep.start_minutes(30)
    assert sleep.remaining_seconds() == 1800
    assert 1800 in sleep.events and 1801 not in sleep.events
    clock.now += 1799.5
    assert sleep.remaining_seconds() == 1                     # el último segundo aún cuenta


def test_posponer_en_pleno_fundido_devuelve_el_volumen(timer):
    sleep, player, clock = timer
    sleep.start(30)
    advance(sleep, clock, 24)
    assert player.mpv.volume == 40
    sleep.postpone(10)
    assert player.mpv.volume == 80 and not sleep.fading
    assert sleep.remaining() == pytest.approx(606)
    advance(sleep, clock, 100)
    assert ("stop",) not in player.log


def test_cancelar_devuelve_el_volumen_y_no_para(timer):
    sleep, player, clock = timer
    sleep.start(30)
    advance(sleep, clock, 27)
    assert player.mpv.volume == 20
    sleep.cancel()
    assert player.mpv.volume == 80 and not sleep.active
    advance(sleep, clock, 40)
    assert ("stop",) not in player.log


def test_mover_el_volumen_durante_el_fundido_cambia_el_nivel_de_vuelta(timer):
    sleep, player, clock = timer
    sleep.start(30)
    advance(sleep, clock, 24)                                 # fundido desde 80
    player.set_volume(100)                                    # el usuario lo sube
    sleep.note_volume(100)
    advance(sleep, clock, 27)
    assert player.mpv.volume == 25                            # 100 * 3/12
    advance(sleep, clock, 30)
    assert player.log[-1] == ("volume", 100)


def test_un_temporizador_corto_de_verdad_para_la_reproduccion(qapp):
    player = FakePlayer()
    sleep = SleepTimer(player)                                # reloj y temporizador reales
    expired = []
    sleep.expired.connect(lambda: expired.append(True))
    sleep.start(1)
    assert wait_until(qapp, lambda: expired, timeout=3)
    assert player.log.count(("stop",)) == 1
    assert player.mpv.volume == 80 and not sleep.active
    levels = [v for kind, *v in player.log if kind == "volume" for v in v]
    assert levels[-1] == 80 and min(levels) < 80              # bajó antes de parar


def test_al_acabar_la_cancion_baja_al_final_de_la_pista(timer):
    sleep, player, _clock = timer
    sleep.stop_after_track()
    assert sleep.mode == TRACK and player.stop_after_current and sleep.remaining() is None
    player.position_changed.emit(100.0, 200.0)
    assert player.log == []
    player.position_changed.emit(194.0, 200.0)               # faltan 6 de 12
    assert player.log == [("volume", 40)]
    player.halted_after_track.emit()
    assert player.log[-1] == ("volume", 80)
    assert not sleep.active and sleep.events[-1] == "expired"


def test_saltar_de_cancion_en_pleno_fundido_devuelve_el_volumen(timer):
    sleep, player, _clock = timer
    sleep.stop_after_track()
    player.position_changed.emit(195.0, 200.0)
    player.current_changed.emit(object())
    assert player.mpv.volume == 80
    assert sleep.mode == TRACK and player.stop_after_current  # sigue: parará al acabar la nueva


def test_cancelar_al_acabar_la_cancion_deja_seguir_la_cola(timer):
    sleep, player, _clock = timer
    sleep.stop_after_track()
    sleep.cancel()
    assert not player.stop_after_current and not sleep.active


# -- con el reproductor de verdad ---------------------------------------------


def test_parar_tras_la_cancion_no_empieza_la_siguiente(env):
    sleep = SleepTimer(env.player)
    expired = []
    sleep.expired.connect(lambda: expired.append(True))
    entry = start_playing(env)
    sleep.stop_after_track()
    env.mpv.at(99.5)
    env.mpv.file_ended.emit(entry, "eof", "")
    settle(env.qapp, 0.3)
    assert expired and env.player.state == STOPPED
    assert env.mpv.loaded == ["url://v0/1"]                   # la siguiente no se cargó
    assert env.player.current_track().video_id == "v0"
    assert not env.player.stop_after_current
    # Sin temporizador, el final de pista vuelve a pasar a la siguiente.
    env.player.toggle_pause()
    assert wait_until(env.qapp, lambda: len(env.mpv.loaded) == 2)
    entry = env.mpv.begin()
    env.mpv.at(99.5)
    env.mpv.file_ended.emit(entry, "eof", "")
    assert wait_until(env.qapp, lambda: len(env.mpv.loaded) == 3)


# -- en la ventana --------------------------------------------------------------


def test_el_indicador_aparece_cuenta_y_se_cancela(window):
    assert not window.sleep_btn.isVisible() and not window.sleep_cancel_action.isEnabled()
    labels = [a.text() for a in window.sleep_menu.actions() if a.text()]
    assert labels[:6] == ["15 minutos", "30 minutos", "45 minutos", "1 hora", "Otro tiempo…",
                          "Al terminar esta canción"]
    window.sleep_menu.actions()[1].trigger()                  # 30 minutos
    assert window.sleep_btn.isVisible() and window.sleep_btn.text() == "30:00"
    assert "se parará a las" in window.sleep_btn.toolTip()
    width = window.sleep_btn.width()
    window._on_sleep_remaining(1199)
    assert window.sleep_btn.text() == "19:59" and window.sleep_btn.width() == width   # no baila
    window.sleep.stop_after_track()
    assert window.sleep_btn.text() == "Al acabar"
    window.sleep_cancel_action.trigger()
    assert not window.sleep_btn.isVisible() and not window.sleep.active


def test_al_terminar_puede_cerrar_la_app(window, monkeypatch, db):
    quits = []
    monkeypatch.setattr(window, "quit_app", lambda: quits.append(True))
    window.sleep.expired.emit()
    assert quits == []
    assert any("Temporizador" in t.text for t in window.toasts._toasts)
    window.sleep_quit_action.setChecked(True)
    assert db.get_bool("sleep_quit_app")
    window.sleep.expired.emit()
    assert quits == [True]
