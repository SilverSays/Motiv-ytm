"""Bandeja del sistema, salida de verdad, inicio con Windows y --minimized."""

import subprocess
import sys
from pathlib import Path

import pytest

from ytm_native import app as ytm_app
from ytm_native.core.db import Track
from ytm_native.core.instance import SingleInstance
from ytm_native.playback.player import PAUSED, PLAYING
from ytm_native.ui import tray
from ytm_native.ui.tray import MINIMIZED_ARG, RUN_KEY, Autostart, TrayIcon, app_icon, launch_command

from test_home_search import animations_on, net, wait, wait_until, window  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def tray_on(monkeypatch):
    """La plataforma de pruebas no tiene bandeja: se finge que sí."""
    monkeypatch.setattr(TrayIcon, "available", staticmethod(lambda: True))


@pytest.fixture
def win(tray_on, window, monkeypatch):
    calls = []
    monkeypatch.setattr(window, "_exit_app", lambda: calls.append("exit"))
    window.exit_calls = calls
    yield window
    # Sin esto el cierre del fixture solo la escondería en la bandeja.
    window.quit_app()


def spy_shutdown(window, monkeypatch):
    calls = []
    monkeypatch.setattr(window.player, "stop", lambda: calls.append("player"))
    monkeypatch.setattr(window.mpv, "shutdown", lambda: calls.append("mpv"))
    monkeypatch.setattr(window.extractor, "shutdown", lambda: calls.append("yt-dlp"))
    return calls


# -- icono y orden de arranque --------------------------------------------------


def test_el_icono_se_dibuja_a_cada_tamano(qapp):
    icon = app_icon()
    sizes = {(s.width(), s.height()) for s in icon.availableSizes()}
    assert {(16, 16), (32, 32), (256, 256)} <= sizes
    assert not icon.pixmap(16, 16).isNull()


def test_la_orden_de_inicio_abre_esta_copia_en_la_bandeja():
    command = launch_command()
    assert command.endswith(" " + MINIMIZED_ARG)
    assert str(ROOT / "ytm_native" / "__main__.py") in command
    if sys.platform == "win32" and Path(sys.executable).with_name("pythonw.exe").is_file():
        assert "pythonw.exe" in command.lower()          # sin ventana de consola


def test_main_por_ruta_encuentra_el_paquete_sin_carpeta_de_trabajo(tmp_path):
    """El inicio con Windows lanza ``__main__.py`` por ruta desde cualquier carpeta."""
    main = ROOT / "ytm_native" / "__main__.py"
    probe = (
        "import runpy, sys, types\n"
        "fake = types.ModuleType('ytm_native.app'); fake.main = lambda: 7\n"
        "sys.modules['ytm_native.app'] = fake\n"
        f"runpy.run_path({str(main)!r}, run_name='__main__')\n"
    )
    result = subprocess.run([sys.executable, "-c", probe], cwd=tmp_path, capture_output=True,
                            text=True, timeout=60)
    assert result.returncode == 7, result.stderr


# -- inicio con Windows -------------------------------------------------------


def test_autostart_escribe_la_orden_con_minimized_y_la_borra(fake_registry):
    auto = Autostart()
    assert not auto.is_enabled()
    assert auto.enable()
    value = fake_registry.keys[(fake_registry.HKEY_CURRENT_USER, RUN_KEY)][tray.APP_NAME]
    assert value == launch_command() and value.endswith(MINIMIZED_ARG)
    assert auto.is_enabled()
    assert auto.disable()
    assert not auto.is_enabled()
    assert auto.disable()                              # quitarla dos veces no falla


def test_autostart_renueva_la_orden_si_la_app_cambio_de_sitio(fake_registry):
    auto = Autostart()
    auto.enable('"C:\\viejo\\pythonw.exe" "C:\\viejo\\__main__.py" --minimized')
    assert auto.sync(True)
    assert auto.command() == launch_command()
    assert auto.sync(False) and not auto.is_enabled()


def test_autostart_sin_permiso_no_rompe_nada(fake_registry):
    fake_registry.fail = True
    auto = Autostart()
    assert not auto.enable()
    assert not auto.is_enabled()


def test_opcion_del_menu_activa_y_desactiva_el_inicio(win, fake_registry, db):
    assert not win.autostart_action.isChecked()            # desactivado por defecto
    win.autostart_action.setChecked(True)
    assert win.autostart.is_enabled() and db.get_bool("autostart_with_windows")
    win.autostart_action.setChecked(False)
    assert not win.autostart.is_enabled() and not db.get_bool("autostart_with_windows", True)


def test_si_el_registro_falla_la_opcion_vuelve_atras(win, fake_registry, db):
    fake_registry.fail = True
    win.autostart_action.setChecked(True)
    assert not win.autostart_action.isChecked()
    assert not db.get_bool("autostart_with_windows")
    assert any("inicio con Windows" in t.text for t in win.toasts._toasts)


# -- cerrar, minimizar y salir -----------------------------------------------


def test_cerrar_con_la_opcion_activa_deja_la_musica_sonando(win, monkeypatch, db):
    calls = spy_shutdown(win, monkeypatch)
    assert win.close_to_tray_action.isChecked()            # activa por defecto
    assert win.tray_ready()
    win.close()
    assert not win.isVisible()
    assert calls == []                                     # mpv y la cola siguen vivos
    assert db.get_bool("tray_hint_shown")                  # el aviso sale una sola vez
    win.tray.show_requested.emit()                         # clic en el icono
    assert win.isVisible()


def test_salir_definitivamente_cierra_procesos_aunque_este_en_la_bandeja(win, monkeypatch):
    calls = spy_shutdown(win, monkeypatch)
    win.close()                                            # a la bandeja
    win.tray.quit_requested.emit()                         # "Salir definitivamente"
    assert calls == ["player", "mpv", "yt-dlp"]
    assert win.exit_calls == ["exit"]
    win.quit_app()                                         # dos veces no repite el cierre
    assert calls == ["player", "mpv", "yt-dlp"]


def test_sin_la_opcion_cerrar_cierra_de_verdad(win, monkeypatch, db):
    calls = spy_shutdown(win, monkeypatch)
    win.close_to_tray_action.setChecked(False)
    assert not db.get_bool("tray_close_to_tray", True)
    win.close()
    assert calls == ["player", "mpv", "yt-dlp"]


def test_minimizar_a_la_bandeja_es_opcional(win, qapp):
    assert not win.minimize_to_tray_action.isChecked()
    win.showMinimized()
    wait(50)
    assert win.isVisible()                                 # por defecto sigue en la barra de tareas
    win.bring_to_front()
    win.minimize_to_tray_action.setChecked(True)
    win.showMinimized()
    assert wait_until(lambda: not win.isVisible())
    win.bring_to_front()
    assert win.isVisible() and not win.isMinimized()


def test_menu_de_la_bandeja_sigue_a_la_cancion(win):
    track = Track("t1", "Canción larga", "Artista")
    win.tray.set_track(None)
    assert not win.tray.play_action.isEnabled()
    win.tray.set_track(track)
    assert win.tray.track_action.text() == "Canción larga — Artista"
    assert win.tray.play_action.isEnabled()
    win._on_player_state(PLAYING)
    assert win.tray.play_action.text() == "Pausar"
    win._on_player_state(PAUSED)
    assert win.tray.play_action.text() == "Reproducir"


def test_controles_de_la_bandeja_mandan_al_reproductor(tray_on, net, qapp, db, monkeypatch):
    from ytm_native.playback.extractor import ExtractorManager
    from ytm_native.playback.mpv_ipc import MpvPlayer
    from ytm_native.playback.player import PlaybackController
    from ytm_native.services.sync import LibrarySync
    from ytm_native.ui import main_window as mw

    calls = []
    player = PlaybackController(db, ExtractorManager(db), MpvPlayer(volume=50))
    for name in ("toggle_pause", "next_track", "previous_track"):
        monkeypatch.setattr(player, name, lambda n=name: calls.append(n))
    win = mw.MainWindow(db, player.extractor, player.mpv, player, LibrarySync(db), mw.Bridge())
    win._exit_app = lambda: None
    try:
        win.tray.toggle_requested.emit()
        win.tray.next_requested.emit()
        win.tray.previous_requested.emit()
        assert calls == ["toggle_pause", "next_track", "previous_track"]
    finally:
        win._tick.stop()
        win.quit_app()
        qapp.removeEventFilter(win)


# -- arranque minimizado y segunda instancia ---------------------------------


def test_minimized_arranca_oculta_en_la_bandeja(win):
    win.hide()
    ytm_app.show_initial(win, ["ytm", MINIMIZED_ARG])
    assert not win.isVisible() and win.tray_ready()
    ytm_app.show_initial(win, ["ytm"])
    assert win.isVisible()


def test_minimized_sin_bandeja_arranca_minimizada(window):
    assert not window.tray_ready()                          # plataforma sin bandeja
    window.hide()
    ytm_app.show_initial(window, ["ytm", MINIMIZED_ARG])
    assert window.isVisible() and window.isMinimized()


def test_abrir_otra_instancia_devuelve_la_ventana_desde_la_bandeja(win, isolated_home):
    first = SingleInstance(home=isolated_home)
    assert first.acquire()
    try:
        first.activated.connect(win.bring_to_front)
        win.close()                                         # a la bandeja
        assert not win.isVisible()
        second = SingleInstance(home=isolated_home)
        assert not second.acquire()                        # avisa y sale
        assert wait_until(win.isVisible)
        assert not win.isMinimized()
    finally:
        first.release()
