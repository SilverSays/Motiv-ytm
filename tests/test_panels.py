"""Panel "Ahora suena", barra lateral contraíble y listas locales en la ventana (sin red)."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from ytm_native.core.db import Track, is_local_playlist
from ytm_native.services.discover import NowInfo
from ytm_native.ui import main_window as mw
from ytm_native.ui.art import ArtLoader
from ytm_native.ui.components import ClickableCover
from ytm_native.ui.now import ClampText, NowPanel

# La ventana y la red falsa son las mismas que usan las pruebas del inicio.
from test_home_search import animations_on, net, wait, wait_until, window  # noqa: F401


@pytest.fixture
def loader(qapp, isolated_home):
    return ArtLoader(cache_dir=isolated_home / "caratulas")


def info_for(video_id="gl", bio=True) -> NowInfo:
    return NowInfo(
        video_id,
        similar=[Track(f"sim{i}", f"Parecida {i}", "Otro artista") for i in range(5)],
        artist="Daft Punk", artist_stats=[["Oyentes mensuales", "66,4 M"], ["Suscriptores", "7,21 M"]],
        bio=("Dúo francés formado en 1993 en París por dos amigos del instituto. " * 6
             + "\nSegundo párrafo.") if bio else "",
        facts=[["Álbum", "Random Access Memories"], ["Año", "2013"]],
    )


def test_texto_recortado_se_queda_en_sus_lineas(qapp):
    text = ClampText(3)
    text.resize(260, 10)
    text.show()
    text.setText("palabra " * 80)
    assert text.clipped and text.height() == 3 * text._line_height()
    text.setText("Corto.")
    assert not text.clipped and text.height() == text._line_height()
    text.setText("")
    assert text.height() == 0
    text.close()


def test_panel_ensena_lo_esencial_y_despliega_el_resto(loader):
    panel = NowPanel(loader)
    panel.resize(340, 700)
    panel.show()
    assert panel.body.isHidden() and not panel.message.isHidden()      # nada sonando
    track = Track("gl", "Get Lucky", "Daft Punk, Pharrell Williams")
    panel.set_track(track)
    panel.set_loading()
    assert not panel.body.isHidden() and panel.title.text() == "Get Lucky"
    assert panel.artist_box.isHidden() and not panel.status.isHidden()

    played = []
    panel.play_similar.connect(played.append)
    panel.set_info(info_for())
    wait(30)
    assert panel.status.isHidden() and not panel.artist_box.isHidden()
    assert panel.artist_name.text() == "Daft Punk"
    assert panel.artist_stats.text() == "66,4 M oyentes mensuales"     # junto al nombre, un solo dato
    # Sin tocar nada: un extracto corto. La historia completa y los datos, plegados.
    assert not panel.excerpt.isHidden() and panel.excerpt.clipped and panel.more.isHidden()
    assert panel.toggle.text() == "Más información"
    assert [row.track.title for row in panel._rows if not row.isHidden()] == [f"Parecida {i}" for i in range(5)]
    panel._rows[2].click()
    assert played == [2]

    panel.toggle.click()
    assert panel.is_expanded() and not panel.more.isHidden() and panel.excerpt.isHidden()
    assert "Segundo párrafo." in panel.bio.text() and panel.toggle.text() == "Menos información"
    shown = [(label.text(), value.text()) for label, value in panel._fact_labels if not label.isHidden()]
    assert shown == [("Álbum", "Random Access Memories"), ("Año", "2013")]
    artist = [(label.text(), value.text()) for label, value in panel._artist_labels if not label.isHidden()]
    assert artist == [("Oyentes mensuales", "66,4 M"), ("Suscriptores", "7,21 M")]
    panel.toggle.click()
    assert not panel.is_expanded() and panel.more.isHidden() and not panel.excerpt.isHidden()

    # Otra canción vuelve a empezar plegada; sin historia ni datos no hay nada que desplegar.
    panel.toggle.click()
    bare = info_for("otra", bio=False)
    bare.facts = []
    bare.artist_stats = []
    bare.similar = bare.similar[:2]
    panel.set_info(bare)
    assert not panel.is_expanded() and panel.toggle.isHidden() and panel.excerpt.isHidden()
    assert len([row for row in panel._rows if not row.isHidden()]) == 2
    panel.set_error()
    assert panel.similar_box.isHidden() and "No se pudo" in panel.status.text()
    # La miniatura de un vídeo se enseña entera (apaisada); la carátula de una canción, cuadrada.
    square = panel.cover.height()
    panel.set_track(Track("v", "Vídeo", "Canal", thumb_url="https://i.ytimg.com/vi/v/hq720.jpg"))
    assert panel.cover._wide and panel.cover.height() < square
    panel.set_track(track)
    assert not panel.cover._wide and panel.cover.height() == square
    panel.close()


def test_caratula_del_reproductor_responde_al_clic_y_al_teclado(loader):
    cover = ClickableCover(loader, 56)
    cover.show()
    clicks = []
    cover.clicked.connect(lambda: clicks.append(1))
    QTest.mouseClick(cover, Qt.MouseButton.LeftButton)
    QTest.keyClick(cover, Qt.Key.Key_Return)
    assert len(clicks) == 2
    QTest.mouseClick(cover, Qt.MouseButton.RightButton)
    assert len(clicks) == 2
    cover.close()


# ---------------------------------------------------------------------------
# En la ventana
# ---------------------------------------------------------------------------
def play(window, monkeypatch, track):
    """Pone ``track`` como pista actual sin pasar por yt-dlp ni mpv."""
    monkeypatch.setattr(window.player, "current_track", lambda: track)
    window._on_current_changed(track)


def test_clic_en_la_caratula_abre_el_panel_y_sustituye_a_la_letra(window, net, db, monkeypatch):
    monkeypatch.setattr(mw, "fetch_lyrics", lambda *a, **k: None)
    window.lyrics_action.setChecked(True)
    assert window.lyrics.isVisible() and not window.now_panel.isVisible()
    window.now_cover.clicked.emit()
    assert window.now_panel.isVisible() and not window.lyrics.isVisible()
    assert not window.lyrics_action.isChecked() and window.now_panel.body.isHidden()   # nada suena aún

    track = Track("gl", "Get Lucky", "Daft Punk")
    play(window, monkeypatch, track)
    assert window.now_panel.title.text() == "Get Lucky"
    assert wait_until(lambda: window.now_panel.info() is not None)
    assert window.now_panel.artist_name.text() == "Artista" and net.now_calls == 1
    # La misma canción no se vuelve a pedir: ni al reabrir el panel ni en otra sesión (queda guardada).
    window.now_cover.clicked.emit()
    # Con el ratón el panel se desliza hasta cerrarse; no desaparece de golpe.
    assert wait_until(lambda: not window.now_panel.isVisible())
    window.now_action.setChecked(True)
    window._now_for = None
    window._load_now(track)
    assert net.now_calls == 1 and window.now_panel.info().artist == "Artista"
    # Abrir la letra cierra el panel: comparten sitio.
    window.lyrics_action.setChecked(True)
    assert window.lyrics.isVisible() and not window.now_panel.isVisible() and not window.now_action.isChecked()


def test_musica_parecida_se_reproduce_desde_el_panel(window, net, db, monkeypatch):
    monkeypatch.setattr(mw.bootstrap, "missing_binaries", lambda: [])
    played = []
    monkeypatch.setattr(window.player, "play_tracks", lambda tracks, start=0: played.append((len(tracks), start)))
    window.now_action.setChecked(True)
    play(window, monkeypatch, Track("gl", "Get Lucky", "Daft Punk"))
    assert wait_until(lambda: window.now_panel.info() is not None)
    window.now_panel._rows[1].click()
    assert played == [(4, 1)] and db.get_track("sim1") is not None
    menu = window._track_menu(window.now_panel.info().similar[0])
    assert [a.text() for a in menu.actions() if a.text()][:3] == [
        "Reproducir", "Reproducir a continuación", "Añadir a la cola",
    ]


def test_barra_lateral_se_contrae_a_iconos_y_recuerda_el_estado(window, db):
    side = window.side_panel
    assert side.minimumWidth() == window.SIDE_MIN and window.sidebar.item(0).text() == "Inicio"
    window.side_toggle.click()
    assert window.collapse_action.isChecked() and db.get_bool("sidebar_collapsed")
    # Se pliega deslizándose: el ancho final llega al terminar la animación.
    assert wait_until(lambda: side.minimumWidth() == side.maximumWidth() == window.SIDE_COLLAPSED)
    assert all(window.sidebar.item(row).text() == "" for row in range(window.sidebar.count()))
    assert window.sidebar.item(0).toolTip() == "Inicio"            # el nombre sigue al alcance
    assert window.footer.status.isHidden()
    assert window._view_key == mw.HOME_KEY and window.sidebar.currentRow() == 0
    # Contraída sigue sirviendo para navegar.
    window.sidebar.setCurrentRow(window._sidebar_row("PLdos"))
    assert window._view_key == "PLdos"
    window.collapse_action.setChecked(False)
    assert wait_until(lambda: side.minimumWidth() == window.SIDE_MIN and side.maximumWidth() == window.SIDE_MAX)
    assert window.sidebar.item(0).text() == "Inicio" and not window.footer.status.isHidden()
    assert window._view_key == "PLdos"
    # Al arrancar vuelve como se dejó.
    db.set_setting("sidebar_collapsed", "1")
    window.collapse_action.setChecked(db.get_bool("sidebar_collapsed"))
    assert wait_until(lambda: window.sidebar.item(0).text() == "")


def test_lista_local_de_principio_a_fin(window, db, monkeypatch):
    answers = iter(["Para correr", "Para caminar"])
    monkeypatch.setattr(window, "_ask_text", lambda *a, **k: next(answers))
    monkeypatch.setattr(window, "_confirm", lambda *a, **k: True)

    # La fila «Nueva lista» no es una vista: crea la lista y entra en ella.
    window.sidebar.setCurrentRow(window._sidebar_row(mw.NEW_LIST_KEY))
    assert wait_until(lambda: is_local_playlist(window._view_key))
    pid = window._view_key
    assert window.band._full_title == "Para correr" and window.band._facts == ["0 canciones", "Lista local"]
    assert window.band.sync_btn.isHidden() and window.band.extra_btn.text() == "Opciones"
    assert not window.empty.isHidden()
    assert window.sidebar.currentItem().data(mw.ROLE_ID) == pid

    # Desde cualquier pista: «Añadir a una lista» enseña las locales.
    window._navigate("PLuno")
    track = window.model.tracks()[1]
    menu = window._track_menu(track, 1)
    add = next(a.menu() for a in menu.actions() if a.text() == "Añadir a una lista")
    assert [a.text() for a in add.actions() if a.text()] == ["Para correr", "Nueva lista…"]
    assert "Quitar de esta lista" not in [a.text() for a in menu.actions()]
    add.actions()[0].trigger()
    add.actions()[0].trigger()                                   # dos veces: no se duplica
    assert [t.video_id for t in db.playlist_tracks(pid)] == [track.video_id]
    assert window._view_key == "PLuno"                           # añadir no cambia de vista

    # Dentro de la lista local se puede quitar, renombrar y eliminar.
    window._navigate(pid)
    assert window.model.rowCount() == 1 and window.band._facts[0] == "1 canción"
    menu = window._track_menu(track, 0)
    next(a for a in menu.actions() if a.text() == "Quitar de esta lista").trigger()
    assert db.playlist_tracks(pid) == [] and window.model.rowCount() == 0
    window._rename_local_playlist(pid)
    assert db.get_playlist(pid).title == "Para caminar" and window.band._full_title == "Para caminar"
    window._delete_local_playlist(pid)
    assert db.get_playlist(pid) is None and window._view_key == mw.HOME_KEY
    assert db.get_playlist("PLuno") is not None


def test_lista_ajena_se_guarda_como_local(window, db, monkeypatch):
    monkeypatch.setattr(window, "_ask_text", lambda title, label, text="", ok="": text)   # acepta el nombre sugerido
    assert wait_until(lambda: window.home.shelf_count() == 2)
    window.home._sections[0].grid._cards[0].click()
    assert wait_until(lambda: window.model.rowCount() == 2)
    assert window.band.extra_btn.text() == "Guardar como lista local"
    window.band.extra_btn.click()
    saved = [p for p in db.playlists() if p.local]
    assert [p.title for p in saved] == ["Lista ajena"]
    assert [t.video_id for t in db.playlist_tracks(saved[0].playlist_id)] == ["r1", "r2"]
    assert window._view_key.startswith(mw.REMOTE_PREFIX)         # sigue en la lista que se estaba viendo
    # Y cuenta para «Tus listas» del inicio.
    window._navigate(mw.HOME_KEY)
    assert window.home.library.grid._cards[0].payload() == saved[0].playlist_id
