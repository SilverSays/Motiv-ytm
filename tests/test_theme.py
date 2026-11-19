"""Fase 8.6: tema oscuro que sigue a Windows, aplicado en caliente."""

import ctypes
import sys

import pytest
from PySide6.QtGui import QColor, QImage

from ytm_native.core import config
from ytm_native.ui import art, theme

TEXT = ("PAPEL", "HUMO", "TENUE")
SURFACES = ("PANEL", "RELIEVE", "RELIEVE_2", "BANDA_NEUTRA")


@pytest.fixture
def dark():
    theme.use_scheme("dark")
    art.on_theme_changed()
    yield
    theme.use_scheme("light")
    art.on_theme_changed()


# ---------------------------------------------------------------------------
# Paleta y contraste (WCAG AA >= 4,5:1)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("fg", TEXT)
@pytest.mark.parametrize("bg", SURFACES)
def test_text_on_every_surface_meets_aa_in_both_schemes(scheme, fg, bg):
    colors = theme.SCHEMES[scheme]
    assert theme.contrast_ratio(colors[fg], colors[bg]) >= theme.MIN_TEXT_CONTRAST


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_text_on_solid_surfaces_and_tooltips_meets_aa(scheme):
    c = theme.SCHEMES[scheme]
    assert theme.contrast_ratio(c["SOBRE_SOLIDO"], c["SOLIDO"]) >= theme.MIN_TEXT_CONTRAST
    assert theme.contrast_ratio(c["SOBRE_SOLIDO"], c["SOLIDO_HOVER"]) >= theme.MIN_TEXT_CONTRAST
    assert theme.contrast_ratio(c["PANEL"], c["PAPEL"]) >= theme.MIN_TEXT_CONTRAST       # ayudas emergentes


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_controls_and_marks_stand_out_from_the_panel(scheme):
    """WCAG 1.4.11: un control encendido (lo sólido, el interruptor) y la
    marca de la vista activa, a 3:1 o más de lo que tienen al lado."""
    c = theme.SCHEMES[scheme]
    assert theme.contrast_ratio(c["SOLIDO"], c["PANEL"]) >= 3.0
    assert theme.contrast_ratio(c["MARCA"], c["PANEL"]) >= 3.0
    assert theme.contrast_ratio(c["MARCA"], c["RELIEVE_2"]) >= 3.0      # sobre la fila elegida
    # En oscuro el sólido no llega a 3:1 sobre el hover de la fila: el
    # interruptor encendido lleva un filete CREMA, que sí.
    assert theme.contrast_ratio(c["CREMA"], c["RELIEVE"]) >= 3.0


@pytest.mark.parametrize("fg", ["PAPEL_SUAVE", "CREMA", "AVISO", "HUMO", "TENUE"])
@pytest.mark.parametrize("bg", ["PANEL", "RELIEVE", "RELIEVE_2"])
def test_dark_secondary_inks_meet_aa(fg, bg):
    c = theme.DARK
    assert theme.contrast_ratio(c[fg], c[bg]) >= theme.MIN_TEXT_CONTRAST


def test_dark_palette_follows_the_plan():
    c = theme.DARK
    assert (c["FONDO"], c["PANEL"], c["RELIEVE"]) == ("#11181c", "#182228", "#222f36")
    assert theme.contrast_ratio(c["PAPEL"], c["PANEL"]) >= 12.0
    assert theme.contrast_ratio(c["HUMO"], c["PANEL"]) >= 5.5
    assert c["ORO"] == theme.LIGHT["ORO"] and c["HALO"] == theme.LIGHT["HALO"]
    assert set(theme.LIGHT) == set(theme.DARK)          # los dos esquemas dan los mismos nombres


def test_band_text_reads_on_the_neutral_band_in_both_schemes(dark):
    assert theme.contrast_ratio(art.BAND_TEXT, theme.BANDA_NEUTRA) >= theme.MIN_TEXT_CONTRAST
    theme.use_scheme("light")
    art.on_theme_changed()
    assert theme.contrast_ratio(art.BAND_TEXT, theme.BANDA_NEUTRA) >= theme.MIN_TEXT_CONTRAST


def _plain(color: str) -> QImage:
    image = QImage(48, 48, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    return image


@pytest.mark.parametrize("color", ["#f5d90a", "#3ddc4a", "#29e3ea", "#ff9f1a", "#2a3bd8", "#7a1f9e", "#b8122c"])
def test_dark_tints_from_covers_stay_readable(qapp, dark, color):
    tint = art.tint_from_image(_plain(color))
    assert tint.from_art
    panel = QColor(theme.PANEL)
    row = QColor(tint.ink)
    row.setAlphaF(art.INK_ROW_ALPHA)
    assert theme.contrast_ratio(tint.ink, panel) >= theme.MIN_TEXT_CONTRAST
    assert theme.contrast_ratio(tint.ink, theme.composite(row, panel)) >= theme.MIN_TEXT_CONTRAST
    # La banda es honda y su texto (claro) se lee encima.
    assert QColor(tint.band).lightnessF() < 0.3
    assert theme.contrast_ratio(art.BAND_TEXT, tint.band) >= theme.MIN_TEXT_CONTRAST
    assert theme.contrast_ratio(theme.PAPEL, tint.band) >= theme.MIN_TEXT_CONTRAST


def test_qss_and_palette_follow_the_scheme(qapp, dark):
    assert f"QFrame#panel {{ background: {theme.DARK['PANEL']};" in theme.QSS
    assert "rgba(240, 244, 246, 0.80)" in theme.QSS                  # texto claro sobre la banda honda
    assert theme.palette().color(theme.QPalette.ColorRole.Window) == QColor(theme.DARK["PANEL"])
    theme.use_scheme("light")
    assert "rgba(24, 38, 46, 0.80)" in theme.QSS


def test_icon_buttons_keep_their_name_and_take_the_new_ink(qapp):
    from ytm_native.ui.components import IconButton

    button = IconButton("heart", "Me gusta")
    assert button.toolTip() == "Me gusta" and button.accessibleName() == "Me gusta"
    theme.use_scheme("dark")
    try:
        button.retheme("light")
        assert button._ink == QColor(theme.DARK["CREMA"])
    finally:
        theme.use_scheme("light")


def test_translate_keeps_the_role_of_a_color():
    theme.use_scheme("dark")
    try:
        assert theme.translate(theme.LIGHT["HUMO"], "light") == QColor(theme.DARK["HUMO"])
        assert theme.translate(theme.LIGHT["PAPEL"], "light") == QColor(theme.DARK["PAPEL"])
        half = QColor(theme.LIGHT["PAPEL"])
        half.setAlpha(100)
        assert theme.translate(half, "light").alpha() == 100
        assert theme.translate("#123456", "light") == QColor("#123456")     # no es de la paleta
    finally:
        theme.use_scheme("light")


# ---------------------------------------------------------------------------
# Seguir a Windows
# ---------------------------------------------------------------------------
class FakeWinreg:
    HKEY_CURRENT_USER = "HKCU"

    def __init__(self, value=None):
        self.value = value

    class _Key:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    def OpenKey(self, root, path):  # noqa: N802 - API de winreg
        assert root == "HKCU" and path.endswith(r"Themes\Personalize")
        if self.value is None:
            raise FileNotFoundError(path)
        return self._Key()

    def QueryValueEx(self, key, name):  # noqa: N802
        assert name == "AppsUseLightTheme"
        return self.value, 4


def test_apps_use_light_theme_is_read_from_the_registry():
    assert theme._read_apps_use_light(FakeWinreg(1)) is True
    assert theme._read_apps_use_light(FakeWinreg(0)) is False
    assert theme._read_apps_use_light(FakeWinreg(None)) is True          # sin valor: claro


def test_modes_resolve_to_a_scheme(monkeypatch):
    monkeypatch.setattr(theme, "system_prefers_dark", lambda: True)
    assert theme.resolve_mode("system") == "dark"
    assert theme.resolve_mode("light") == "light"
    assert theme.resolve_mode("dark") == "dark"
    assert theme.resolve_mode("raro") == "dark"                           # desconocido = sistema
    monkeypatch.setattr(theme, "system_prefers_dark", lambda: False)
    assert theme.resolve_mode("system") == "light"


@pytest.mark.skipif(sys.platform != "win32", reason="mensajes de Windows")
def test_only_the_color_set_change_counts():
    yes = ctypes.create_unicode_buffer("ImmersiveColorSet")
    no = ctypes.create_unicode_buffer("Policy")
    assert theme.is_color_set_change(ctypes.addressof(yes))
    assert not theme.is_color_set_change(ctypes.addressof(no))
    assert not theme.is_color_set_change(0)


# ---------------------------------------------------------------------------
# Ventana: cambio en caliente
# ---------------------------------------------------------------------------
from test_home_search import FakeNet, net, wait_until, window  # noqa: E402,F401 - fixtures


def test_switching_to_dark_recolors_without_touching_playback(window, db, qapp, monkeypatch):
    from ytm_native.core.db import Track

    restarts = []
    monkeypatch.setattr(window.mpv, "start", lambda *a: restarts.append("start"))
    monkeypatch.setattr(window.mpv, "shutdown", lambda *a: restarts.append("shutdown"))
    window.player.play_tracks([Track("PLuno-0", "Canción 0", "Artista")], 0)
    current = window.player.current_track()
    humo_before = QColor(theme.HUMO)
    window.set_theme_mode("dark")
    assert theme.is_dark() and db.get_setting(config.THEME_KEY) == "dark"
    assert theme.DARK["PANEL"] in qapp.styleSheet()
    assert window.delegate._papel == QColor(theme.DARK["PAPEL"])
    assert window.pos_label._color == QColor(theme.DARK["HUMO"]) != humo_before
    assert window.theme_actions["dark"].isChecked() and not window.theme_actions["light"].isChecked()
    # La música no se toca: ni se relanza mpv ni cambia la cola.
    assert restarts == [] and window.player.current_track() == current
    window.set_theme_mode("light")
    assert not theme.is_dark() and window.pos_label._color == QColor(theme.LIGHT["HUMO"])


def test_system_mode_follows_windows_on_wm_settingchange(window, monkeypatch):
    from ctypes import wintypes

    window.set_theme_mode("system")
    assert not theme.is_dark()
    monkeypatch.setattr(theme, "system_prefers_dark", lambda: True)
    text = ctypes.create_unicode_buffer("ImmersiveColorSet")
    msg = wintypes.MSG()
    msg.message = theme.WM_SETTINGCHANGE
    msg.lParam = ctypes.addressof(text)
    handled, _result = window.nativeEvent(b"windows_generic_MSG", ctypes.addressof(msg))
    assert handled is False                       # Windows sigue repartiendo el aviso
    assert wait_until(lambda: theme.is_dark(), 2000)
    # Con el tema elegido a mano, Windows ya no manda.
    window.set_theme_mode("light")
    window.nativeEvent(b"windows_generic_MSG", ctypes.addressof(msg))
    wait_until(lambda: False, 400)
    assert not theme.is_dark()


def test_saved_dark_theme_is_applied_before_building(qapp, db, net):
    from ytm_native.playback.extractor import ExtractorManager
    from ytm_native.playback.mpv_ipc import MpvPlayer
    from ytm_native.playback.player import PlaybackController
    from ytm_native.services.sync import LibrarySync
    from ytm_native.ui import main_window as mw

    db.set_setting(config.THEME_KEY, "dark")
    extractor = ExtractorManager(db)
    mpv = MpvPlayer(volume=50)
    win = mw.MainWindow(db, extractor, mpv, PlaybackController(db, extractor, mpv), LibrarySync(db), mw.Bridge())
    try:
        assert theme.is_dark()
        assert win.pos_label._color == QColor(theme.DARK["HUMO"])         # nacieron ya oscuros
        assert win.theme_actions["dark"].isChecked()
    finally:
        win._tick.stop()
        win.close()
        qapp.removeEventFilter(win)
