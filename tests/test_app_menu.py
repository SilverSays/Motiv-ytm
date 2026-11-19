"""Menú de la app (⋯): grupos con rótulo, casillas para los interruptores y
un menú que cabe en una columna también en pantallas bajas (sin red)."""

import pytest
from PySide6.QtWidgets import QWidgetAction

from test_home_search import animations_on, net, window  # noqa: F401
from ytm_native.ui import chrome, theme


@pytest.fixture
def styled(qapp):
    """La hoja de estilos de la app, como al arrancar (las medidas salen de ella)."""
    previous = qapp.styleSheet()
    chrome.apply_theme(qapp)
    yield
    qapp.setStyleSheet(previous)


def _sections(menu) -> list[str]:
    return [a.text() for a in menu.actions() if isinstance(a, QWidgetAction)]


def _submenu(menu, title: str):
    return next(a.menu() for a in menu.actions() if a.menu() is not None and a.menu().title() == title)


def test_el_menu_va_por_grupos_y_los_rotulos_no_se_eligen(window):
    menu = window.app_menu
    assert _sections(menu) == ["Biblioteca", "Reproducción", "Vista"]
    for action in menu.actions():
        if isinstance(action, QWidgetAction):
            assert not action.isEnabled()         # ni el ratón ni las flechas se paran en un rótulo
    # Las preferencias ya no están en el menú: van en Opciones, que se abre desde él.
    texts = [a.text() for a in menu.actions()]
    assert "Opciones…" in texts and texts[-1] == "Salir"
    assert window.options_action.shortcut().toString() == "Ctrl+,"
    for action in (window.autoplay_action, window.equalize_action, window.app_updates_action,
                    window.discord_action, window.close_to_tray_action):
        assert action not in menu.actions()
    assert _submenu(menu, "Temporizador de apagado") is window.sleep_menu


def test_un_interruptor_lleva_casilla_aunque_este_apagado_y_una_accion_no(window):
    toggle = window.lyrics_action
    toggle.setChecked(False)
    off = toggle.icon().pixmap(16).toImage()
    assert not toggle.icon().isNull() and off.pixelColor(2, 8).alpha() > 0      # el borde de la casilla
    toggle.setChecked(True)
    assert toggle.icon().pixmap(16).toImage() != off
    toggle.setChecked(False)
    actions = [a for a in window.app_menu.actions() if a.text() == "Sincronizar ahora"]
    assert actions and actions[0].icon().isNull()


def test_en_una_pantalla_baja_el_menu_se_aprieta_en_vez_de_partirse(window, styled):
    window._fit_app_menu(2000)
    roomy = window._app_menu_height()
    assert window.app_menu.styleSheet() == ""
    window._fit_app_menu(roomy - 20)
    assert window.app_menu.styleSheet() == theme.MENU_COMPACT_QSS
    assert window._app_menu_height() < roomy - 60
    window._fit_app_menu(2000)                    # con sitio vuelve a respirar
    assert window.app_menu.styleSheet() == "" and window._app_menu_height() == roomy
