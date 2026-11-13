"""Inicio y búsqueda en vivo: piezas sueltas y la ventana entera, sin red."""

import pytest
from PySide6.QtCore import QEventLoop, QPointF, Qt, QTimer
from PySide6.QtTest import QTest

from ytm_native.core import config
from ytm_native.core.db import Track
from ytm_native.services.discover import PlaylistCard, RemotePlaylist, Shelf
from ytm_native.ui import main_window as mw
from ytm_native.ui import motion
from ytm_native.ui.art import ArtLoader
from ytm_native.ui.components import SuggestionStrip
from ytm_native.ui.home import CardGrid, HomePage, SleeveCard


def wait(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def wait_until(condition, timeout_ms: int = 3000) -> bool:
    waited = 0
    while not condition() and waited < timeout_ms:
        wait(20)
        waited += 20
    return condition()


@pytest.fixture(autouse=True)
def animations_on(monkeypatch):
    monkeypatch.setattr(motion, "_reduced", False)


@pytest.fixture
def loader(qapp, isolated_home):
    return ArtLoader(cache_dir=isolated_home / "caratulas")


# ---------------------------------------------------------------------------
# Sugerencias
# ---------------------------------------------------------------------------
def test_sugerencias_que_no_caben_se_ocultan_enteras(qapp):
    strip = SuggestionStrip()
    strip.resize(330, 32)
    strip.show()
    picked = []
    strip.picked.connect(picked.append)
    texts = ["daft punk get lucky", "daft punk one more time", "daft punk instant crush"]
    strip.set_items(texts, typed="daft p")
    shown = strip.shown()
    assert shown and shown == texts[: len(shown)] and len(shown) < len(texts)
    assert strip.height() == 32                      # mismo alto con o sin sugerencias
    last = strip._chips[len(shown) - 1]
    assert last.geometry().right() <= strip.width()
    strip.resize(900, 32)
    assert strip.shown() == texts
    QTest.mouseClick(strip._chips[1], Qt.MouseButton.LeftButton)
    assert picked == ["daft punk one more time"]
    strip.set_items([])
    assert strip.shown() == [] and strip.height() == 32
    strip.close()


def test_sugerencia_distingue_lo_escrito_de_lo_que_anade(qapp):
    strip = SuggestionStrip()
    strip.resize(600, 32)
    strip.set_items(["Daft Punk Around the World", "punk rock"], typed="daft punk")
    first, second = strip._chips[:2]
    assert (first._typed, first._rest) == ("Daft Punk", " Around the World")
    assert (second._typed, second._rest) == ("", "punk rock")      # no empieza por lo escrito
    assert first.suggestion() == "Daft Punk Around the World"


# ---------------------------------------------------------------------------
# Tarjetas del inicio
# ---------------------------------------------------------------------------
def specs(n: int):
    return [(f"Lista {i}", f"{i} canciones", "", f"PL{i}") for i in range(n)]


def test_rejilla_reparte_columnas_y_limita_las_filas(loader):
    grid = CardGrid(loader, max_rows=2)
    grid.resize(840, 10)
    grid.show()
    fitted = []
    grid.fitted.connect(lambda shown, total: fitted.append((shown, total)))
    grid.set_cards(specs(14))
    cols = grid.columns()
    assert cols == 5 and grid.shown() == 10 and fitted[-1] == (10, 14)
    visible = [c for c in grid._cards if not c.isHidden()]
    assert len(visible) == 10
    width = visible[0].width()
    assert all(c.width() == width for c in visible)
    assert visible[4].geometry().right() <= grid.width()
    assert visible[5].y() > visible[0].y() and visible[5].x() == 0
    assert grid.height() == 2 * SleeveCard.height_for(width) + CardGrid.GAP
    # Más estrecha: menos columnas, nunca desplazamiento horizontal.
    grid.resize(360, grid.height())
    assert grid.columns() == 2 and grid.shown() == 4
    grid.set_cards(specs(1))
    assert grid.shown() == 1 and grid.height() == SleeveCard.height_for(grid._cards[0].width())
    grid.close()


def test_tarjeta_abre_con_clic_y_reproduce_desde_el_disco(loader):
    card = SleeveCard(loader)
    card.resize(180, SleeveCard.height_for(180))
    card.set_card("Para trabajar", "48 canciones", "", "PLuno")
    card.show()
    opened, played = [], []
    card.opened.connect(opened.append)
    card.play_requested.connect(played.append)
    QTest.mouseClick(card, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     card.rect().center())
    assert opened == ["PLuno"] and played == []
    disc = card._disc_center()
    assert card._on_disc(disc) and not card._on_disc(QPointF(12, 12))
    QTest.mouseClick(card, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, disc.toPoint())
    assert played == ["PLuno"] and opened == ["PLuno"]
    # Con el teclado siempre abre, aunque el último clic fuera en el disco.
    QTest.keyClick(card, Qt.Key.Key_Return)
    assert opened == ["PLuno", "PLuno"] and played == ["PLuno"]
    card.close()


def test_inicio_avisa_cuando_no_caben_todas_las_listas(loader):
    home = HomePage(loader)
    home.resize(700, 600)
    home.show()
    home.set_library(specs(20), ["20 listas", "400 canciones"])
    wait(30)
    assert home.library.grid.shown() < 20
    assert not home.library.note.isHidden() and "barra lateral" in home.library.note.text()
    home.set_library(specs(3), ["3 listas"])
    wait(30)
    assert home.library.note.isHidden()
    home.set_library([], [], "Aquí aparecerán tus listas")
    assert home.library.isHidden() and not home.library_empty.isHidden()

    shelf = Shelf("Listas de la comunidad con Air", "Está en 3 canciones de tus listas",
                  tuple(PlaylistCard(f"PLr{i}", f"Ajena {i}", "De Fan", "") for i in range(5)))
    opened = []
    home.open_remote.connect(opened.append)
    home.add_shelf(shelf)
    home.add_shelf(shelf)
    assert home.shelf_count() == 2
    home.set_shelves([shelf])                      # una renovación sustituye, no acumula
    assert home.shelf_count() == 1 and home._sections[1].isHidden()
    home._sections[0].grid._cards[2].click()
    assert opened == [shelf.cards[2]]
    home.set_status("Buscando…")
    assert not home.status.isHidden() and home.action.isHidden()
    home.set_status("", "refresh")
    assert home.status.isHidden() and home.action.text() == "Otras recomendaciones"
    home.close()


# ---------------------------------------------------------------------------
# La ventana entera
# ---------------------------------------------------------------------------
class FakeNet:
    """Sustituye todo lo que saldría a la red."""

    def __init__(self):
        self.searches: list[str] = []
        self.suggests: list[str] = []
        self.rec_calls = 0
        self.fail_search = False

    def search_tracks(self, query, limit=40, top=None):
        self.searches.append(query)
        if self.fail_search:
            raise ConnectionError("sin red")
        if top is not None and query.startswith("artista"):
            top["artist"] = {"resultType": "artist", "category": "Top result", "subscribers": "1 M",
                             "artists": [{"name": "Artista", "id": "UCartista"}]}
        return [Track(f"{query}-{i}", f"{query} {i}", "Artista") for i in range(3)]

    def search_extras(self, query):
        from ytm_native.services.discover import ArtistCard, SearchExtras

        self.extras = getattr(self, "extras", []) + [query]
        return SearchExtras(
            artists=[ArtistCard("UCartista", "Artista", "1 M suscriptores", "")],
            playlists=[PlaylistCard(f"PLbus{i}", f"{query} lista {i}", "De Fan", "") for i in range(5)],
        )

    def artist_page(self, channel_id):
        from ytm_native.services.discover import ArtistCard, ArtistPage

        self.artist_calls = getattr(self, "artist_calls", []) + [channel_id]
        return ArtistPage(
            channel_id, "Artista", bio="Grupo formado en 1993.\nMás historia.",
            stats=[["Oyentes mensuales", "1 M"], ["Suscriptores", "200 K"]],
            top_songs=[Track(f"top{i}", f"Popular {i}", "Artista") for i in range(5)],
            songs_playlist="OLAKtodas",
            albums=[PlaylistCard(f"OLAKalb{i}", f"Disco {i}", "2020", "") for i in range(6)],
            singles=[PlaylistCard("MPREsingle", "Sencillo", "Single de 2024", "")],
            playlists=[PlaylistCard("PLcom", "Artista: lo mejor", "De Fan", "")],
            similar=[Track(f"par{i}", f"Parecida {i}", "Otro") for i in range(6)],
            related=[ArtistCard(f"UCrel{i}", f"Pariente {i}", "5 K suscriptores", "") for i in range(4)],
            radio_id="RDEMx", shuffle_id="RDAOx",
        )

    def find_artist(self, name):
        from ytm_native.services.discover import ArtistCard

        return ArtistCard("UCartista", name) if name == "Artista" else None

    def artist_mix(self, page, shuffle=False):
        return [Track(f"mix{i}", f"Mezcla {i}", "Artista") for i in range(8)]

    def search_suggestions(self, query):
        self.suggests.append(query)
        return [f"{query} uno", f"{query} dos"]

    def build_recommendations(self, db, on_shelf=None, **_kw):
        self.rec_calls += 1
        shelves = [
            Shelf(f"Parecidas a «Lista {i}»", "nota",
                  tuple(PlaylistCard(f"PLrec{i}{j}", f"Ajena {i}{j}", "De Fan", "") for j in range(4)))
            for i in range(2)
        ]
        for shelf in shelves:
            if on_shelf is not None:
                on_shelf(shelf)
        return "gustos", shelves

    def now_playing_info(self, track):
        from ytm_native.services.discover import NowInfo

        self.now_calls = getattr(self, "now_calls", 0) + 1
        return NowInfo(
            track.video_id,
            similar=[Track(f"sim{i}", f"Parecida {i}", "Otro artista") for i in range(4)],
            artist="Artista", artist_stats=[["Oyentes mensuales", "1 M"], ["Suscriptores", "200 K"]],
            bio="Grupo formado en 1993 en París.\nSegundo párrafo con más historia.",
            facts=[["Álbum", "Disco"], ["Año", "2001"]],
        )

    def fetch_playlist(self, playlist_id):
        return RemotePlaylist(playlist_id, "Lista ajena", author="Fan", year="2024",
                              tracks=[Track("r1", "Remota uno", "X", duration_s=200),
                                      Track("r2", "Remota dos", "Y", duration_s=100)])


@pytest.fixture
def net(monkeypatch):
    fake = FakeNet()
    monkeypatch.setattr(mw, "search_tracks", fake.search_tracks)
    monkeypatch.setattr(mw.discover, "now_playing_info", fake.now_playing_info)
    monkeypatch.setattr(mw.discover, "search_suggestions", fake.search_suggestions)
    monkeypatch.setattr(mw.discover, "build_recommendations", fake.build_recommendations)
    monkeypatch.setattr(mw.discover, "fetch_playlist", fake.fetch_playlist)
    for name in ("search_extras", "artist_page", "find_artist", "artist_mix"):
        monkeypatch.setattr(mw.discover, name, getattr(fake, name))
    monkeypatch.setattr(config, "SEARCH_SUGGEST_DEBOUNCE_MS", 20)
    monkeypatch.setattr(config, "SEARCH_LIVE_DEBOUNCE_MS", 60)
    return fake


@pytest.fixture
def window(qapp, db, net):
    from ytm_native.playback.extractor import ExtractorManager
    from ytm_native.playback.mpv_ipc import MpvPlayer
    from ytm_native.playback.player import PlaybackController
    from ytm_native.services.sync import LibrarySync

    for position, pid in enumerate(("PLuno", "PLdos")):
        db.upsert_playlist_meta(pid, f"Lista {pid}", position)
        db.replace_playlist_tracks(
            pid, [(Track(f"{pid}-{i}", f"Canción {i}", "Artista"), None) for i in range(3)], 3
        )
    extractor = ExtractorManager(db)
    mpv = MpvPlayer(volume=50)
    player = PlaybackController(db, extractor, mpv)
    win = mw.MainWindow(db, extractor, mpv, player, LibrarySync(db), mw.Bridge())
    win.resize(1180, 760)
    win.show()
    win._reload_sidebar()
    yield win
    win._tick.stop()
    win.close()
    qapp.removeEventFilter(win)


def test_arranca_en_el_inicio_con_tus_listas_y_recomendaciones(window, net, db):
    assert window._view_key == mw.HOME_KEY
    assert window.sidebar.currentItem().text() == "Inicio"
    assert not window.home.isHidden() and window.band.isHidden() and window.body_box.isHidden()
    assert window.home.library.grid.shown() == 2
    assert wait_until(lambda: window.home.shelf_count() == 2)
    assert wait_until(lambda: not window._recs_loading)
    assert not window.home.action.isHidden()
    # Lo recién traído queda guardado: otra visita al inicio no vuelve a pedirlo.
    window._navigate(mw.QUEUE_KEY)
    window._navigate(mw.HOME_KEY)
    wait(60)
    assert net.rec_calls == 1 and window.home.shelf_count() == 2
    # «Otras recomendaciones» sí renueva, y sustituye las filas en vez de sumarlas.
    window.home.action.click()
    assert wait_until(lambda: net.rec_calls == 2 and not window._recs_loading)
    assert window.home.shelf_count() == 2


def test_tarjeta_de_tu_lista_abre_la_lista_y_volver_regresa(window):
    window.home.library.grid._cards[1].click()
    assert window._view_key == "PLdos"
    assert window.sidebar.currentItem().data(mw.ROLE_ID) == "PLdos"
    assert window.model.rowCount() == 3 and window.home.isHidden() and not window.band.isHidden()
    assert window.band.back_btn.isHidden()            # en la biblioteca basta la barra lateral
    assert window.band.height() == window.band.HEIGHT and not window.suggestions.isVisible()
    assert window.band._facts[0] == "3 canciones" and window.band.search_note.isHidden()
    window._go_back()
    assert window._view_key == mw.HOME_KEY and window.sidebar.currentItem().text() == "Inicio"


def test_recomendacion_abre_la_lista_ajena_con_boton_de_volver(window, db):
    assert wait_until(lambda: window.home.shelf_count() == 2)
    window.home._sections[0].grid._cards[0].click()
    key = mw.REMOTE_PREFIX + "PLrec00"
    assert window._view_key == key and window.sidebar.currentRow() == -1
    assert not window.band.back_btn.isHidden() and window.band.sync_btn.isHidden()
    assert wait_until(lambda: window.model.rowCount() == 2)
    assert window.band._full_title == "Lista ajena"
    assert window.band._facts[:3] == ["2 canciones", "5 min", "De Fan"]
    assert db.get_track("r1") is None                 # verla no la mete en la base
    window.band.back_btn.click()
    assert window._view_key == mw.HOME_KEY
    # Una sincronización con la lista ajena abierta no cambia de vista.
    window._navigate(key)
    window._reload_sidebar()
    assert window._view_key == key and window.model.rowCount() == 2


def test_busqueda_en_vivo_sugiere_y_busca_sin_pulsar_intro(window, net, db):
    window._focus_search()
    assert window._view_key == mw.SEARCH_KEY and window.suggestions.isVisible()
    assert window.band.height() == window.band.SEARCH_HEIGHT
    QTest.keyClicks(window.search_edit, "da")
    assert wait_until(lambda: window.suggestions.shown() == ["da uno", "da dos"])
    assert wait_until(lambda: window.model.rowCount() == 3)
    assert net.searches == ["da"]                     # una búsqueda por pausa, no por tecla
    assert window._search_note == "3 resultados para «da»"
    # Intro con lo mismo ya en pantalla no repite la petición, pero lo recuerda.
    QTest.keyClick(window.search_edit, Qt.Key.Key_Return)
    wait(40)
    assert net.searches == ["da"] and mw.discover.recent_searches(db) == ["da"]
    # Una sugerencia rellena el campo y busca en el acto.
    window.suggestions._chips[0].click()
    assert window.search_edit.text() == "da uno"
    assert wait_until(lambda: window._search_note == "3 resultados para «da uno»")
    assert mw.discover.recent_searches(db) == ["da uno", "da"]
    # Flecha abajo pasa del campo al primer resultado.
    window.search_edit.setFocus()
    QTest.keyClick(window.search_edit, Qt.Key.Key_Down)
    assert window.table.currentIndex().row() == 0
    # Los resultados sobreviven a salir y volver.
    window._navigate(mw.HOME_KEY)
    window._navigate(mw.SEARCH_KEY)
    assert window.model.rowCount() == 3 and window.search_edit.text() == "da uno"
    # Vaciar el campo vuelve al punto de partida y enseña las recientes.
    window.search_edit.clear()
    assert window.model.rowCount() == 0 and not window.empty.isHidden()
    assert window.suggestions.shown() == ["da uno", "da"]
    assert window._search_note == mw.SEARCH_IDLE_NOTE


def test_respuesta_vieja_no_pisa_la_busqueda_nueva(window, net):
    window._focus_search()
    window.search_edit.setText("primera")
    window._do_search()
    window.search_edit.setText("segunda")
    window._do_search()
    assert wait_until(lambda: window._search_note == "3 resultados para «segunda»")
    wait(80)
    assert [t.title for t in window.model.tracks()][0] == "segunda 0"


def test_fallo_al_escribir_no_lanza_avisos_y_intro_reintenta(window, net):
    net.fail_search = True
    window._focus_search()
    window.search_edit.setText("air")
    window._do_search()
    assert wait_until(lambda: window._search_note.startswith("La búsqueda falló"))
    assert window.toasts._toasts == []                # escribir sin red no llena la pantalla de avisos
    net.fail_search = False
    QTest.keyClick(window.search_edit, Qt.Key.Key_Return)
    assert wait_until(lambda: window.model.rowCount() == 3)


def test_reproducir_desde_la_biblioteca_cuenta_para_las_principales(window, db, monkeypatch):
    monkeypatch.setattr(mw.bootstrap, "missing_binaries", lambda: [])
    played = []
    monkeypatch.setattr(window.player, "play_tracks", lambda tracks, start=0: played.append(len(tracks)))
    window._navigate("PLdos")
    window._play_from(0)
    window._play_library("PLdos")
    assert played == [3, 3]
    assert [p.playlist_id for p in db.main_playlists()] == ["PLdos", "PLuno"]
    assert db.get_playlist("PLdos").play_count == 2
    window._navigate(mw.HOME_KEY)
    assert window.home.library.grid._cards[0].payload() == "PLdos"
