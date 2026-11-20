"""Salida de audio, ecualizador y transición entre canciones (Tarea 10.5), sin red.

Cuatro niveles: la composición de filtros sin mpv, el IPC con el mpv simulado
por tubería, el fundido en el reproductor (eventos a mano) y Opciones en la
ventana. Si mpv está instalado, también el de verdad, en silencio.
"""

import json
import os
import sys
from pathlib import Path

import pytest

from ytm_native.core import config
from ytm_native.core.db import Track
from ytm_native.playback.mpv_ipc import (
    AUTO_DEVICE, EQ_BANDS, EQ_LABEL, EQ_PRESETS, LOUDNORM, MpvPlayer, eq_preamp, equalizer_graph,
)
from ytm_native.playback.player import (
    CROSSFADE_KEY, PAUSED, PLAYING, TRANSITION_CROSSFADE, TRANSITION_GAPLESS, TRANSITION_KEY, TRANSITION_OFF,
    PlaybackController,
)
from ytm_native.services import backup
from ytm_native.ui import main_window as mw
from ytm_native.ui.settings import OptionsDialog

from test_gapless import GaplessMpv, chained, current, make_env, start, teardown
from test_home_search import animations_on, net, window  # noqa: F401
from test_player import settle, wait_until

FAKE = str(Path(__file__).with_name("fake_mpv.py"))
REAL_MPV = Path(os.environ.get("LOCALAPPDATA", "")) / "YTMusicNativo" / "bin" / "mpv.exe"
CASCOS, ALTAVOCES = "wasapi/{cascos}", "wasapi/{altavoces}"


# ---------------------------------------------------------------------------
# El filtro compuesto, sin mpv
# ---------------------------------------------------------------------------
def test_cada_preajuste_tiene_una_ganancia_por_banda():
    assert set(EQ_PRESETS) == {"flat", "bass", "vocal", "rock", "pop", "electronic", "acoustic"}
    for gains in EQ_PRESETS.values():
        assert len(gains) == len(EQ_BANDS) and all(-12 <= g <= 12 for g in gains)
    assert set(EQ_PRESETS["flat"]) == {0}


def test_el_grafo_del_ecualizador_lleva_etiqueta_atenuacion_y_bandas():
    graph = equalizer_graph(EQ_PRESETS["bass"])
    assert graph.startswith(f"@{EQ_LABEL}:lavfi=[volume=volume=") and graph.endswith("]")
    assert graph.count("equalizer=") == len(EQ_BANDS)
    assert "equalizer=f=60:t=q:w=1:g=6" in graph
    # Lo que se realza no satura: se atenúa antes lo mismo que el mayor realce.
    assert eq_preamp(EQ_PRESETS["bass"]) == pytest.approx(10 ** (-6 / 20))
    assert eq_preamp(EQ_PRESETS["flat"]) == 1.0
    assert eq_preamp((-3, -1, 0, 0, 0, 0)) == 1.0           # solo cortes: sin atenuar


def test_ecualizador_y_normalizacion_se_componen_sin_pisarse(qapp):
    mpv = MpvPlayer(mpv_path=Path("no-existe.exe"))
    mpv.set_normalization("loudnorm")
    assert mpv.audio_filter == LOUDNORM
    mpv.set_equalizer("rock")
    assert mpv.audio_filter == equalizer_graph(EQ_PRESETS["rock"]) + "," + LOUDNORM   # primero el ecualizador
    mpv.set_normalization(None)
    assert mpv.audio_filter == equalizer_graph(EQ_PRESETS["rock"])                 # el ecualizador sigue
    mpv.set_normalization("dynaudnorm")
    mpv.set_equalizer("flat")
    assert mpv.audio_filter == "dynaudnorm=f=150:g=15"                             # la normalización sigue
    mpv.set_equalizer("no-existe")
    assert mpv.equalizer == "flat"


def test_si_mpv_no_cambia_el_ecualizador_en_marcha_se_rehace_la_cadena(qapp):
    mpv = MpvPlayer(mpv_path=Path("no-existe.exe"))
    mpv.set_equalizer("pop")
    mpv._eq_requests = {7, 8}
    mpv._on_reply({"request_id": 7, "error": "error running command"})
    assert mpv._eq_requests == set()                          # una sola vez, aunque fallen más
    assert mpv.audio_filter == equalizer_graph(EQ_PRESETS["pop"])


def test_el_fundido_multiplica_el_volumen_elegido(qapp, monkeypatch):
    mpv = MpvPlayer(mpv_path=Path("no-existe.exe"), volume=80)
    sent = []
    monkeypatch.setattr(mpv, "command", lambda *args: sent.append(args) or 1)
    mpv.set_fade(0.5)
    assert sent[-1] == ("set_property", "volume", 40.0)
    mpv.set_volume(60)                                        # el usuario lo mueve a mitad de fundido
    assert sent[-1] == ("set_property", "volume", 30.0) and mpv.volume == 60
    mpv.set_fade(0.504)                                       # cambios ínfimos no se mandan
    assert sent[-1] == ("set_property", "volume", 30.0) and len(sent) == 2
    mpv.set_fade(1.0)
    assert sent[-1] == ("set_property", "volume", 60)
    mpv.set_fade(3)
    assert mpv.fade == 1.0


# ---------------------------------------------------------------------------
# IPC con el mpv simulado por tubería
# ---------------------------------------------------------------------------
@pytest.fixture
def piped(qapp):
    player = MpvPlayer(mpv_path=Path(sys.executable), pre_args=[FAKE])
    events = []
    player.crashed.connect(lambda m: events.append(("crashed", m)))
    player.file_started.connect(lambda e: events.append(("started", e)))
    player.devices_changed.connect(lambda: events.append(("devices",)))
    player.device_lost.connect(lambda name: events.append(("lost", name)))
    player.events = events
    player.start()
    assert wait_until(qapp, player.is_ready, timeout=10)
    assert wait_until(qapp, lambda: ("devices",) in events)
    yield player
    player.shutdown()


def ask(qapp, player, name):
    """Lee una propiedad del mpv simulado."""
    answer = []
    real = player._on_reply

    def capture(msg):
        if msg.get("request_id") == request:
            answer.append(msg.get("data"))
        real(msg)

    player._on_reply = capture
    request = player.command("get_property", name)
    assert wait_until(qapp, lambda: answer)
    player._on_reply = real
    return answer[0]


def plug(player, *devices):
    rows = [{"name": "auto", "description": "Autoselect device"}]
    rows += [{"name": name, "description": desc} for name, desc in devices]
    player.command("ytmn-test-devices", json.dumps(rows))


def test_la_lista_de_salidas_llega_al_conectar(qapp, piped):
    assert piped.audio_devices() == [(ALTAVOCES, "Altavoces (Realtek)"), (CASCOS, "Auriculares (USB)")]
    assert all(name != AUTO_DEVICE for name, _ in piped.audio_devices())
    assert piped.audio_device == AUTO_DEVICE and ask(qapp, piped, "audio-device") == "auto"


def test_cambiar_de_salida_no_relanza_mpv_ni_mueve_la_pista(qapp, piped):
    piped.load("https://example.invalid/normal", token=1)
    assert wait_until(qapp, lambda: ("started", 1) in piped.events)
    piped.seek(42)
    assert wait_until(qapp, lambda: piped.last_time_pos == 42.0)
    pid = ask(qapp, piped, "ytmn-pid")
    piped.set_audio_device(CASCOS, "Auriculares (USB)")
    assert ask(qapp, piped, "audio-device") == CASCOS
    piped.set_audio_device(AUTO_DEVICE)
    assert ask(qapp, piped, "audio-device") == "auto"
    assert ask(qapp, piped, "ytmn-pid") == pid                  # el mismo proceso
    assert piped.last_time_pos == 42.0
    assert [e for e in piped.events if e[0] in ("crashed", "started")] == [("started", 1)]


def test_si_se_desconecta_la_elegida_suena_por_la_de_windows_y_vuelve_sola(qapp, piped):
    piped.set_audio_device(CASCOS, "Auriculares (USB)")
    assert ask(qapp, piped, "audio-device") == CASCOS
    plug(piped, (ALTAVOCES, "Altavoces (Realtek)"))            # se desenchufan los cascos
    assert wait_until(qapp, lambda: ("lost", "Auriculares (USB)") in piped.events)
    assert ask(qapp, piped, "audio-device") == "auto" and piped.audio_device_in_use == AUTO_DEVICE
    assert piped.audio_device == CASCOS                          # la elección se recuerda
    plug(piped, (ALTAVOCES, "Altavoces (Realtek)"), (CASCOS, "Auriculares (USB)"))
    assert wait_until(qapp, lambda: piped.audio_device_in_use == CASCOS)
    assert ask(qapp, piped, "audio-device") == CASCOS
    assert [e for e in piped.events if e[0] == "lost"] == [("lost", "Auriculares (USB)")]


def test_una_salida_elegida_que_no_esta_al_arrancar_no_avisa(qapp):
    player = MpvPlayer(mpv_path=Path(sys.executable), pre_args=[FAKE])
    lost = []
    player.device_lost.connect(lost.append)
    player.set_audio_device("wasapi/{guardada}", "Altavoz del salón")
    player.start()
    try:
        assert wait_until(qapp, lambda: player.is_ready() and player.audio_devices())
        assert player.audio_device_in_use == AUTO_DEVICE and lost == []
        assert ask(qapp, player, "audio-device") == "auto"
    finally:
        player.shutdown()


def test_el_preajuste_cambia_en_marcha_sin_quitar_la_normalizacion(qapp, piped):
    piped.load("https://example.invalid/normal", token=1)
    piped.set_normalization("loudnorm")
    piped.set_equalizer("bass")
    bass = equalizer_graph(EQ_PRESETS["bass"]) + "," + LOUDNORM
    assert wait_until(qapp, lambda: piped._filter_request == -1)
    assert ask(qapp, piped, "af") == bass
    # Otro preajuste: solo órdenes a los filtros, la cadena no se rehace.
    piped.set_equalizer("rock")
    commands = ask(qapp, piped, "ytmn-af-commands")
    assert [EQ_LABEL, "volume", f"{eq_preamp(EQ_PRESETS['rock']):.6f}", "Parsed_volume_0"] in commands
    for i, gain in enumerate(EQ_PRESETS["rock"], start=1):
        assert [EQ_LABEL, "gain", str(gain), f"Parsed_equalizer_{i}"] in commands
    assert ask(qapp, piped, "af") == bass                         # sin set_property af: sin corte
    # Al empezar la siguiente pista, ``af`` se pone al día.
    piped.load("https://example.invalid/normal", token=2)
    rock = equalizer_graph(EQ_PRESETS["rock"]) + "," + LOUDNORM
    assert wait_until(qapp, lambda: ask(qapp, piped, "af") == rock)
    # Plano: ganancias a cero en marcha; la normalización, intacta.
    piped.set_equalizer("flat")
    commands = ask(qapp, piped, "ytmn-af-commands")
    assert [EQ_LABEL, "gain", "0", "Parsed_equalizer_1"] in commands
    piped.load("https://example.invalid/normal", token=3)
    assert wait_until(qapp, lambda: ask(qapp, piped, "af") == LOUDNORM)


def test_un_mpv_relanzado_recupera_salida_y_ecualizador(qapp, piped):
    piped.set_equalizer("vocal")
    piped.set_audio_device(CASCOS, "Auriculares (USB)")
    piped.load("https://example.invalid/crash")
    assert wait_until(qapp, lambda: any(e[0] == "crashed" for e in piped.events))
    assert wait_until(qapp, piped.is_ready, timeout=10)
    assert wait_until(qapp, lambda: ask(qapp, piped, "af") == equalizer_graph(EQ_PRESETS["vocal"]))
    assert ask(qapp, piped, "audio-device") == CASCOS


# ---------------------------------------------------------------------------
# Fundido entre canciones en el reproductor
# ---------------------------------------------------------------------------
class FadeMpv(GaplessMpv):
    def __init__(self):
        super().__init__()
        self.fades = []

    def set_fade(self, level):
        if not self.fades or abs(self.fades[-1] - level) > 1e-9:
            self.fades.append(level)


@pytest.fixture
def fenv(qapp, db, monkeypatch):
    monkeypatch.setattr(sys.modules["test_gapless"], "GaplessMpv", FadeMpv)
    env = make_env(qapp, db, monkeypatch)
    env.player.set_transition(TRANSITION_CROSSFADE)
    yield env
    teardown(env)


def level(env):
    return env.player.fade_level()


def test_con_fundido_la_que_acaba_baja_y_la_siguiente_sube(fenv):
    env = fenv
    start(env)
    assert chained(env, "url://v1/1")
    env.mpv.at(50)
    assert level(env) == 1.0 and env.mpv.fades[-1] == 1.0
    env.mpv.at(97)                                            # a 3 s del final, con 4 s de fundido
    assert level(env) == pytest.approx(0.75, abs=0.02)
    assert env.mpv.fades[-1] == pytest.approx(0.75, abs=0.02)
    assert env.player._fade_timer.isActive()                  # se recalcula a menudo, sin escalones
    env.mpv.at(99.6)
    assert level(env) < 0.15
    env.mpv.finish()                                          # mpv sigue con la encadenada
    assert current(env) == "v1" and env.mpv.fades[-1] == 0.0  # empieza en silencio
    env.mpv.at(1)
    assert level(env) == pytest.approx(0.25, abs=0.02)
    env.mpv.at(4.2)
    assert level(env) == 1.0 and env.mpv.fades[-1] == 1.0
    assert not env.player._fade_timer.isActive()              # a mitad de pista, quieto


def test_sin_pausas_no_hay_fundido(fenv):
    env = fenv
    env.player.set_transition(TRANSITION_GAPLESS)
    start(env)
    assert chained(env, "url://v1/1")
    env.mpv.at(99)
    env.mpv.finish()
    env.mpv.at(1)
    assert set(env.mpv.fades) == {1.0}


def test_al_final_de_la_cola_la_ultima_acaba_entera(fenv):
    env = fenv
    start(env, 5)
    settle(env.qapp, 0.1)
    env.mpv.at(99)
    assert level(env) == 1.0                                  # no viene otra: nada que fundir


def test_en_pausa_el_fundido_se_queda_donde_iba(fenv):
    env = fenv
    start(env)
    assert chained(env, "url://v1/1")
    env.mpv.at(98)
    before = level(env)
    env.mpv.pause_changed.emit(True)
    assert env.player.state == PAUSED and not env.player._fade_timer.isActive()
    settle(env.qapp, 0.3)
    assert level(env) == pytest.approx(before, abs=0.01)


def test_volver_atras_durante_el_fundido_recupera_el_volumen(fenv):
    env = fenv
    start(env)
    assert chained(env, "url://v1/1")
    env.mpv.at(98)
    assert level(env) < 1.0
    env.player.seek(30)
    env.mpv.at(30)
    assert level(env) == 1.0 and env.mpv.fades[-1] == 1.0


@pytest.mark.parametrize("action", ["next", "off", "stop"])
def test_un_salto_o_quitar_el_fundido_devuelve_el_volumen_entero(fenv, action):
    env = fenv
    start(env)
    assert chained(env, "url://v1/1")
    env.mpv.finish()
    assert env.mpv.fades[-1] == 0.0
    {"next": env.player.next_track, "off": lambda: env.player.set_transition(TRANSITION_OFF),
     "stop": env.player.stop}[action]()
    assert env.mpv.fades[-1] == 1.0


def test_en_una_pista_corta_el_fundido_es_un_tercio_de_ella(fenv):
    env = fenv
    start(env)
    env.player._duration = 6.0
    assert env.player._fade_length() == 2.0
    env.player.set_crossfade_seconds(8)
    env.player._duration = 300.0
    assert env.player._fade_length() == 8.0


def test_la_transicion_y_la_duracion_se_guardan_y_se_validan(db, qapp):
    player = PlaybackController(db, None, GaplessMpv())
    assert player.transition == TRANSITION_GAPLESS and player.crossfade_s == 4      # lo de fábrica
    player.set_transition(TRANSITION_CROSSFADE)
    player.set_transition("otra cosa")                        # se ignora
    player.set_crossfade_seconds(30)
    assert db.get_setting(TRANSITION_KEY) == "crossfade" and player.crossfade_s == 8
    player.set_crossfade_seconds(0)
    assert player.crossfade_s == 2
    player.set_crossfade_seconds(5)
    again = PlaybackController(db, None, GaplessMpv())
    assert again.transition == TRANSITION_CROSSFADE and again.crossfade_s == 5
    db.set_setting(TRANSITION_KEY, "rara")
    db.set_setting(CROSSFADE_KEY, "mucho")
    odd = PlaybackController(db, None, GaplessMpv())
    assert odd.transition == TRANSITION_GAPLESS and odd.crossfade_s == 4


def test_con_transicion_normal_no_se_encadena_ni_se_funde(fenv):
    env = fenv
    env.player.set_transition(TRANSITION_OFF)
    start(env)
    settle(env.qapp, 0.15)
    assert env.mpv.appended == []
    env.mpv.at(99)
    assert level(env) == 1.0


# ---------------------------------------------------------------------------
# Opciones › Reproducción
# ---------------------------------------------------------------------------
@pytest.fixture
def options(window):
    window.mpv._on_devices([{"name": "auto", "description": "Autoselect device"},
                            {"name": ALTAVOCES, "description": "Altavoces (Realtek)"},
                            {"name": CASCOS, "description": "Auriculares (USB)"}])
    dialog = OptionsDialog(window, "playback")
    dialog.show()
    yield dialog
    dialog.close()
    dialog.deleteLater()


def items(combo):
    return [(combo.itemData(i), combo.itemText(i)) for i in range(combo.count())]


def pick(row, value):
    combo = row.control
    combo.setCurrentIndex(combo.findData(value))


def test_reproduccion_tiene_salida_ecualizador_y_transicion(options):
    assert items(options.device_row.control) == [
        ("auto", "Predeterminado de Windows"), (ALTAVOCES, "Altavoces (Realtek)"), (CASCOS, "Auriculares (USB)")]
    assert [text for _value, text in items(options.equalizer_row.control)] == [
        "Plano (desactivado)", "Realce de graves", "Voces claras", "Rock", "Pop", "Electrónica", "Acústico"]
    assert [text for _value, text in items(options.transition_row.control)] == ["Sin pausas", "Fundido cruzado", "Normal"]
    assert [text for _value, text in items(options.fade_row.control)] == [
        f"{n} segundos" for n in range(2, 9)]
    assert options.fade_row.control.currentData() == 4
    titles = [row.title.text() for row in (options.device_row, options.equalizer_row,
                                           options.transition_row, options.fade_row)]
    assert titles == ["Dispositivo de audio", "Ecualizador", "Al pasar de una canción a otra", "Duración del fundido"]


def test_elegir_salida_la_aplica_y_la_guarda(options, window, db):
    pick(options.device_row, CASCOS)
    assert window.mpv.audio_device == CASCOS
    assert db.get_setting(mw.AUDIO_DEVICE_KEY) == CASCOS
    assert db.get_setting(mw.AUDIO_DEVICE_NAME_KEY) == "Auriculares (USB)"
    assert window.audio_device() == (CASCOS, "Auriculares (USB)")


def test_la_salida_elegida_desconectada_se_ve_y_avisa(options, window, monkeypatch):
    shown = []
    monkeypatch.setattr(window, "_notice", lambda text, kind="info": shown.append(text))
    sent = []
    monkeypatch.setattr(window.mpv, "_connected", True)       # como con mpv en marcha
    monkeypatch.setattr(window.mpv, "command", lambda *args: sent.append(args) or 1)
    pick(options.device_row, CASCOS)
    assert sent[-1] == ("set_property", "audio-device", CASCOS)   # sonaba por los cascos
    window.mpv._on_devices([{"name": ALTAVOCES, "description": "Altavoces (Realtek)"}])
    assert shown == ["Se desconectó «Auriculares (USB)»: la música sigue por la salida predeterminada de Windows."]
    assert sent[-1] == ("set_property", "audio-device", "auto")
    assert items(options.device_row.control)[-1] == (CASCOS, "Auriculares (USB) (no conectado)")
    assert options.device_row.control.currentData() == CASCOS


def test_una_salida_nueva_aparece_en_la_lista_al_momento(options, window):
    window.mpv._on_devices([{"name": ALTAVOCES, "description": "Altavoces (Realtek)"},
                            {"name": "wasapi/{bt}", "description": "Altavoz Bluetooth"}])
    assert ("wasapi/{bt}", "Altavoz Bluetooth") in items(options.device_row.control)


def test_el_ecualizador_se_aplica_y_se_guarda(options, window, db):
    window.equalize_action.setChecked(True)
    pick(options.equalizer_row, "electronic")
    assert db.get_setting(mw.EQUALIZER_KEY) == "electronic" and window.equalizer_preset() == "electronic"
    assert window.mpv.equalizer == "electronic"
    assert window.mpv.audio_filter == equalizer_graph(EQ_PRESETS["electronic"]) + "," + LOUDNORM


def test_la_duracion_solo_se_elige_con_fundido(options, window):
    assert window.transition_mode() == TRANSITION_GAPLESS
    assert not options.fade_row.control.isEnabled() and options.fade_row.note.text() == "Solo con «Fundido cruzado»."
    assert "sin silencio" in options.transition_row.note.text()
    pick(options.transition_row, TRANSITION_CROSSFADE)
    assert window.player.transition == TRANSITION_CROSSFADE
    assert options.fade_row.control.isEnabled() and options.fade_row.note.text() == ""
    pick(options.fade_row, 7)
    assert window.crossfade_seconds() == 7 and window.player.crossfade_s == 7
    pick(options.transition_row, TRANSITION_OFF)
    assert not options.fade_row.control.isEnabled() and "silencio breve" in options.transition_row.note.text()


def test_la_copia_de_seguridad_lleva_el_sonido_pero_no_la_salida(window, db, options):
    assert {"equalizer_preset", "transition_mode", "crossfade_seconds"} <= set(backup.PREFERENCE_KEYS)
    assert mw.AUDIO_DEVICE_KEY not in backup.PREFERENCE_KEYS                # es de este equipo
    db.set_setting(mw.EQUALIZER_KEY, "acoustic")
    db.set_setting(TRANSITION_KEY, "crossfade")
    db.set_setting(CROSSFADE_KEY, 6)
    window._apply_saved_preferences()                         # lo que hace al importar una copia
    options.refresh()
    assert window.mpv.equalizer == "acoustic" and window.player.transition == "crossfade"
    assert window.player.crossfade_s == 6
    assert options.equalizer_row.control.currentData() == "acoustic"
    assert options.transition_row.control.currentData() == "crossfade"
    assert options.fade_row.control.currentData() == 6 and options.fade_row.control.isEnabled()


# ---------------------------------------------------------------------------
# El mpv de verdad, en silencio
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not REAL_MPV.is_file(), reason="sin mpv instalado")
def test_mpv_real_cambia_preajustes_en_marcha_sin_cortar(qapp, caplog):
    player = MpvPlayer(mpv_path=REAL_MPV, pre_args=["--ao=null"])
    positions, ended, reconfigs = [], [], []
    player.position_changed.connect(positions.append)
    player.file_ended.connect(lambda *a: ended.append(a))
    real_event = player._on_event
    player._on_event = lambda msg: (reconfigs.append(1) if msg.get("event") == "audio-reconfig" else None,
                                    real_event(msg))
    player.start()
    try:
        assert wait_until(qapp, player.is_ready, timeout=15)
        assert wait_until(qapp, lambda: player._devices_known, timeout=10)
        assert all(name != AUTO_DEVICE for name, _ in player.audio_devices())
        player.load("av://lavfi:sine=frequency=440:duration=60")
        assert wait_until(qapp, lambda: positions and positions[-1] > 0.5, timeout=15)
        with caplog.at_level("INFO", logger="ytmn.mpv"):
            player.set_normalization("loudnorm")
            player.set_equalizer("bass")
            assert wait_until(qapp, lambda: player._filter_request == -1, timeout=10)
            settle(qapp, 0.3)
            before = len(reconfigs)
            for preset in ("rock", "vocal", "flat", "electronic"):
                player.set_equalizer(preset)
                assert wait_until(qapp, lambda: not player._eq_requests, timeout=10)
                mark = positions[-1]
                assert wait_until(qapp, lambda: positions[-1] > mark + 0.3, timeout=10)
            assert len(reconfigs) == before                    # ningún preajuste rehízo la salida
            messages = [r.getMessage() for r in caplog.records]
            assert not [m for m in messages if "ecualizador" in m or "rechazó" in m], messages
            player.set_fade(0.3)
            player.set_fade(1.0)
        assert not ended
    finally:
        player.shutdown()
