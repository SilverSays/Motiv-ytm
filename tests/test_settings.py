"""Opciones: la ventana de preferencias, atada a las acciones de la ventana
principal, accesible con teclado y lector de pantalla (sin red)."""

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QPushButton

from test_home_search import animations_on, net, window  # noqa: F401
from ytm_native.core import i18n
from ytm_native.ui.settings import OptionsDialog, Row, Switch


@pytest.fixture
def options(window):
    dialog = OptionsDialog(window)
    dialog.show()
    # Activa, como cuando la abre el usuario: sus atajos (Ctrl+Tab) son de
    # la ventana y Qt solo los atiende en la ventana activa.
    dialog.activateWindow()
    assert QTest.qWaitForWindowActive(dialog)
    yield dialog
    dialog.close()
    dialog.deleteLater()


def _row_for(dialog, action) -> Row:
    return dialog.row_for(action)


def test_las_preferencias_van_por_temas(options):
    titles = [options.nav.item(i).text() for i in range(options.nav.count())]
    assert titles == ["General", "Reproducción", "Cuenta", "Integraciones", "Mantenimiento"]
    for row, key in enumerate(OptionsDialog.PAGES):
        options.nav.setCurrentRow(row)
        assert options.current_page() == key
    options.show_page("integrations")
    assert options.nav.currentRow() == 3 and options.stack.currentWidget() is options.pages["integrations"]


def test_ctrl_tab_pasa_al_tema_siguiente(options):
    options.show_page("general")
    QTest.keyClick(options, Qt.Key.Key_Tab, Qt.KeyboardModifier.ControlModifier)
    assert options.current_page() == "playback"
    QTest.keyClick(options, Qt.Key.Key_PageUp, Qt.KeyboardModifier.ControlModifier)
    assert options.current_page() == "general"


def test_cada_opcion_dice_que_es_y_que_hace(options):
    rows = options.findChildren(Row)
    assert len(rows) > 20
    for row in rows:
        control = row.control
        assert control is not None
        name = control.accessibleName()
        assert name.startswith(row.title.text()) and row.title.text()
        if isinstance(control, QPushButton):
            # El nombre no tapa lo que pone el botón (WCAG 2.5.3): la fila y la acción.
            assert name == f"{row.title.text()}: {control.text()}"
            continue
        assert name == row.title.text()
        if isinstance(control, QComboBox):
            continue
        assert isinstance(control, Switch)
        assert control.focusPolicy() & Qt.FocusPolicy.TabFocus      # se llega con Tab
    # Las que no se explican solas llevan su explicación a la vista, no en una nota emergente.
    for action in (options.window_.autoplay_action, options.window_.resume_action,
                   options.window_.equalize_action, options.window_.history_action):
        row = _row_for(options, action)
        assert row.note.text() and not row.note.isHidden()
        assert row.control.accessibleDescription() == row.note.text()


def test_intro_cambia_el_interruptor_y_no_cierra_opciones(options):
    """Intro sobre un interruptor lo cambia, como Espacio; antes caía al
    botón Cerrar (el de por defecto) y la ventana se cerraba sin cambiar nada."""
    w = options.window_
    w.equalize_action.setChecked(False)
    options.show_page("playback")
    switch = options.row_for(w.equalize_action).control
    switch.setFocus()
    QTest.keyClick(switch, Qt.Key.Key_Return)
    assert w.equalize_action.isChecked() and options.isVisible()
    QTest.keyClick(switch, Qt.Key.Key_Enter)
    assert not w.equalize_action.isChecked() and options.isVisible()
    QTest.keyClick(switch, Qt.Key.Key_Space)
    assert w.equalize_action.isChecked()
    # Intro en la lista de temas o en una lista desplegable tampoco la cierra.
    options.nav.setFocus()
    QTest.keyClick(options.nav, Qt.Key.Key_Return)
    options.show_page("general")
    options.theme_row.control.setFocus()
    QTest.keyClick(options.theme_row.control, Qt.Key.Key_Enter)
    assert options.isVisible()
    # Intro sí pulsa el botón que tiene el foco (aquí, Cerrar).
    close = next(b for b in options.findChildren(QPushButton) if b.text() == "Cerrar")
    close.setFocus()
    QTest.keyClick(close, Qt.Key.Key_Return)
    assert not options.isVisible()


def test_el_boton_de_last_fm_anuncia_su_accion_al_cambiar(options, window):
    from types import SimpleNamespace

    row = next(r for r in options.findChildren(Row) if r.title.text() == "Cuenta de Last.fm")
    assert row.control.accessibleName() == "Cuenta de Last.fm: Conectar…"
    real = window.lastfm
    window.lastfm = SimpleNamespace(connected=True, user="ana")
    try:
        options.refresh()
        assert row.control.text() == "Desconectar"
        assert row.control.accessibleName() == "Cuenta de Last.fm: Desconectar"
    finally:
        window.lastfm = real                      # la ventana lo necesita al cerrarse


def test_las_listas_siguen_lo_que_cambia_fuera(options, window, db):
    """El tema cambiado desde fuera y el idioma traído por una copia
    importada se ven en sus listas, sin tomarse por una elección nueva."""
    window.set_theme_mode("dark")
    assert options.theme_row.control.currentData() == "dark"
    window.set_theme_mode("light")
    assert options.theme_row.control.currentData() == "light"
    i18n.set_preference(db, "en")                 # como al importar una copia
    options.refresh()
    assert options.language_combo.currentData() == "en" and not options.restart_row.isHidden()
    i18n.set_preference(db, "es")
    options.refresh()
    assert options.language_combo.currentData() == "es" and options.restart_row.isHidden()


def test_la_lista_de_temas_ensena_el_foco_del_teclado(options):
    """Al llegar con el teclado a la lista de temas, la fila actual lleva
    anillo (WCAG 2.4.7); con el ratón, no."""
    from ytm_native.ui import focus

    options.nav.setFocus()
    focus.mouse_pressed()
    assert not options.ring.shown()
    focus.key_pressed(Qt.Key.Key_Down)
    QTest.keyClick(options.nav, Qt.Key.Key_Down)
    assert options.ring.shown() and options.current_page() == "playback"
    options.ring.grab()                           # se pinta sin fallar
    focus.mouse_pressed()
    assert not options.ring.shown()


def test_un_interruptor_y_su_accion_van_a_la_par(options, db):
    w = options.window_
    w.equalize_action.setChecked(False)
    row = _row_for(options, w.equalize_action)
    row.control.click()
    assert w.equalize_action.isChecked() and db.get_bool("equalize_volume")
    w.equalize_action.setChecked(False)                 # desde fuera (bandeja, copia importada…)
    assert not row.control.isChecked()
    # Toda la fila responde al clic, no solo el carril.
    QTest.mouseClick(row, Qt.MouseButton.LeftButton, pos=QPoint(20, row.height() // 2))
    assert w.equalize_action.isChecked()


def test_lo_que_no_se_puede_usar_va_apagado_y_dice_por_que(options, window):
    window.mutations_action.setChecked(False)
    window.account_history_action.setEnabled(False)
    row = _row_for(options, window.account_history_action)
    assert not row.control.isEnabled()
    assert "Necesita los cambios" in row.note.text()
    QTest.mouseClick(row, Qt.MouseButton.LeftButton, pos=QPoint(20, row.height() // 2))
    assert not window.account_history_action.isChecked()


def test_si_la_accion_se_niega_el_interruptor_vuelve(options, window, monkeypatch):
    monkeypatch.setattr(window, "_ask_discord_client_id", lambda: None)
    assert not window.discord.client_id               # sin id de aplicación de Discord
    row = _row_for(options, window.discord_action)
    row.control.click()
    assert not window.discord_action.isChecked() and not row.control.isChecked()


def test_el_tema_se_elige_de_una_lista(options, window, db):
    combo = options.theme_row.control
    combo.setCurrentIndex(combo.findData("dark"))
    assert window._theme_mode == "dark" and window.theme_actions["dark"].isChecked()
    combo.setCurrentIndex(combo.findData("light"))
    assert window._theme_mode == "light"


def test_cambiar_el_idioma_lo_guarda_y_ofrece_reiniciar(options, window, db, monkeypatch):
    assert options.restart_row.isHidden()
    combo = options.language_combo
    assert [combo.itemData(i) for i in range(combo.count())] == ["system", "es", "en"]
    assert combo.itemText(2) == "English"                  # cada idioma, en su propio idioma
    combo.setCurrentIndex(combo.findData("en"))
    assert i18n.preference(db) == "en"
    assert not options.restart_row.isHidden()
    assert "English" in options.restart_row.note.text()
    quits = []
    monkeypatch.setattr(window, "quit_app", lambda: quits.append(True))
    options.restart_button.click()
    assert quits and QApplication.instance().property("ytmn_restart")
    QApplication.instance().setProperty("ytmn_restart", False)
    combo.setCurrentIndex(combo.findData("es"))
    assert options.restart_row.isHidden()


def test_opciones_se_abre_desde_el_menu_y_con_ctrl_coma(window, monkeypatch):
    opened = []
    monkeypatch.setattr(OptionsDialog, "exec", lambda self: opened.append(self.current_page()) or 0)
    window.options_action.trigger()
    window.open_options("maintenance")
    assert opened == ["general", "maintenance"]
    assert window.options_dialog is None


def test_abrir_opciones_no_asoma_ventanas_sueltas(window):
    """Nada de la ventana de Opciones se abre como ventana propia mientras
    se construye (antes asomaban unas ventanitas vacías un instante)."""
    before = {w for w in QApplication.topLevelWidgets() if w.isVisible()}
    dialog = OptionsDialog(window)
    shown = {w for w in QApplication.topLevelWidgets() if w.isVisible()} - before
    assert not shown, [type(w).__name__ for w in shown]
    for row in dialog.findChildren(Row):
        assert row.title.parent() is row and row.note.parent() is row
    dialog.deleteLater()


def test_cerrar_opciones_suelta_sus_conexiones(window, monkeypatch):
    """Con Opciones ya cerrada (y destruida), cambiar una preferencia desde
    fuera no puede tocar sus controles: antes la app se cerraba con
    «Internal C++ object (Switch) already deleted»."""
    import sys

    import shiboken6

    errors = []                                   # lo que falla dentro de una señal llega aquí
    monkeypatch.setattr(sys, "excepthook", lambda *exc: errors.append(exc[1]))
    dialog = OptionsDialog(window)
    dialog.show()
    dialog.close()
    shiboken6.delete(dialog)
    for action in (window.equalize_action, window.close_to_tray_action, window.account_history_action,
                   window.lastfm_action):
        action.setEnabled(not action.isEnabled())
        action.setEnabled(not action.isEnabled())
        action.setChecked(not action.isChecked())     # sin RuntimeError
        action.setChecked(not action.isChecked())
    assert not errors, errors
