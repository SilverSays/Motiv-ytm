"""Idioma de la interfaz: el catálogo inglés, cómo se elige el idioma y que
ningún texto del código se quede sin traducción."""

import ast
import string
from pathlib import Path

import pytest

from ytm_native.core import i18n
from ytm_native.core.i18n_en import CATALOG

PACKAGE = Path(__file__).resolve().parent.parent / "ytm_native"


@pytest.fixture
def lang(monkeypatch):
    """Deja el idioma como estaba al acabar."""
    monkeypatch.setattr(i18n, "_active", i18n._active)
    return i18n


# Qué argumentos de cada función son textos de la interfaz.
_TEXT_ARGS = {"tr": (0,), "plural": (1, 2)}


def _called(node: ast.Call) -> str:
    """El nombre de la función llamada, con o sin módulo (``tr`` o ``i18n.tr``)."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _tr_literals():
    """Cada texto literal que el código pasa a ``tr`` o a ``plural`` (también
    escritos ``i18n.tr`` e ``i18n.plural``)."""
    for path in sorted(PACKAGE.rglob("*.py")):
        if path.name.startswith("i18n"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for index in _TEXT_ARGS.get(_called(node), ()):
                if len(node.args) > index:
                    arg = node.args[index]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        yield f"{path.relative_to(PACKAGE)}:{node.lineno}", arg.value


def test_la_cobertura_ve_todas_las_formas_de_llamar():
    """La prueba de cobertura no puede dejarse textos por cómo se escribe la llamada."""
    sample = ast.parse('tr("a")\ni18n.tr("b")\nplural(n, "{n} c", "{n} d")\ni18n.plural(n, "{n} e", "{n} f")\n'
                       'other("x")\n')
    found = []
    for node in ast.walk(sample):
        if isinstance(node, ast.Call):
            found += [node.args[i].value for i in _TEXT_ARGS.get(_called(node), ()) if len(node.args) > i]
    assert sorted(found) == ["a", "b", "{n} c", "{n} d", "{n} e", "{n} f"]


def _fields(text: str) -> set:
    return {name for _lit, name, _spec, _conv in string.Formatter().parse(text) if name}


def test_en_espanol_el_texto_sale_tal_cual(lang):
    lang.use("es")
    assert i18n.tr("Sincronizar ahora") == "Sincronizar ahora"
    assert i18n.tr("Lista «{title}» eliminada.", title="Rock") == "Lista «Rock» eliminada."


def test_en_ingles_se_traduce_con_sus_huecos(lang):
    lang.use("en")
    assert i18n.tr("Sincronizar ahora") == "Sync now"
    assert i18n.tr("Lista «{title}» eliminada.", title="Rock") == "Playlist “Rock” deleted."
    assert "{n} canciones" in CATALOG                  # una traducción de verdad, no el texto de vuelta
    assert i18n.plural(1, "{n} canción", "{n} canciones") == "1 song"
    assert i18n.plural(3, "{n} canción", "{n} canciones") == "3 songs"
    # Lo que no está en el catálogo (un nombre propio, un dato) sale igual.
    assert i18n.tr("Daft Punk") == "Daft Punk"
    # Sin valores no se rellenan huecos: las llaves sueltas no rompen nada.
    assert i18n.tr("{literal}") == "{literal}"


def test_los_millares_van_a_la_manera_de_cada_idioma(lang):
    lang.use("es")
    assert i18n.plural(12480, "{n} canción", "{n} canciones") == "12 480 canciones"
    assert i18n.number(9999) == "9999"                 # en español, el espacio desde 10 000
    lang.use("en")
    assert i18n.plural(12480, "{n} canción", "{n} canciones") == "12,480 songs"
    assert i18n.number(9999) == "9,999"


def test_un_hueco_mal_puesto_no_tumba_la_app(lang, monkeypatch, caplog):
    """Un hueco que falta o una llave suelta en una traducción dejan el
    texto sin rellenar (y un aviso en el registro), sin excepción."""
    lang.use("es")
    assert i18n.tr("Texto con {missing_key}", other=1) == "Texto con {missing_key}"
    assert i18n.tr("Llave {suelta", value=1) == "Llave {suelta"
    assert i18n.tr("Posicional {0}", value=1) == "Posicional {0}"
    lang.use("en")
    monkeypatch.setitem(CATALOG, "Lista «{title}» eliminada.", "Playlist “{name}” deleted.")
    assert i18n.tr("Lista «{title}» eliminada.", title="Rock") == "Playlist “{name}” deleted."
    assert "mal formados" in caplog.text


def test_el_idioma_se_decide_una_sola_vez_aunque_lo_pidan_a_la_vez(lang, monkeypatch):
    import threading
    import time

    calls = []

    def slow_preference():
        calls.append(1)
        time.sleep(0.05)
        return "en"

    monkeypatch.delenv("YTMN_LANG", raising=False)
    monkeypatch.setattr(i18n, "_stored_preference", slow_preference)
    monkeypatch.setattr(i18n, "_active", None)
    seen = []
    threads = [threading.Thread(target=lambda: seen.append(i18n.active())) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert calls == [1] and seen == ["en"] * 8


def test_lo_que_cambia_con_el_idioma_se_traduce_al_pedirlo(lang):
    """Los mensajes de error y «nunca» no se fijan al importar sus módulos."""
    from ytm_native.core.errors import ErrorKind, error_message
    from ytm_native.ui.widgets import format_ago

    lang.use("es")
    assert error_message(ErrorKind.NETWORK) == "Sin conexión" and format_ago(None) == "nunca"
    lang.use("en")
    assert error_message(ErrorKind.NETWORK) == "No connection" and format_ago(None) == "never"


def test_los_datos_de_la_cancion_no_se_traducen(lang, qapp):
    """En "Ahora suena" se traduce la etiqueta, nunca el dato: un álbum que se
    llama «Inicio» no puede salir como «Home»."""
    from PySide6.QtWidgets import QGridLayout, QWidget

    from ytm_native.ui.now import NowPanel

    lang.use("en")
    host = QWidget()
    grid = QGridLayout(host)
    labels = []
    NowPanel._fill(grid, labels, [["Álbum", "Inicio"]])
    assert (labels[0][0].text(), labels[0][1].text()) == ("Album", "Inicio")
    host.deleteLater()


def test_todo_texto_del_codigo_tiene_traduccion():
    missing = [f"{where}: {text!r}" for where, text in _tr_literals() if text not in CATALOG]
    assert not missing, "\n".join(missing)


def test_las_traducciones_usan_los_mismos_huecos():
    wrong = [key for key, value in CATALOG.items() if _fields(key) != _fields(value)]
    assert not wrong, wrong


def test_el_idioma_sigue_a_windows_si_no_se_elige(lang, monkeypatch):
    monkeypatch.setattr(i18n, "system_language", lambda: "en")
    assert i18n.resolve("system") == "en"
    assert i18n.resolve("es") == "es"
    assert i18n.resolve("klingon") == "en"           # lo desconocido cuenta como "system"


def test_la_preferencia_guardada_manda_al_arrancar(lang, monkeypatch, isolated_home):
    from ytm_native.core import paths
    from ytm_native.core.db import Database

    db = Database(paths.db_path())
    i18n.set_preference(db, "en")
    assert i18n.preference(db) == "en"
    monkeypatch.delenv("YTMN_LANG", raising=False)
    monkeypatch.setattr(i18n, "system_language", lambda: "es")
    monkeypatch.setattr(i18n, "_active", None)
    assert i18n.active() == "en"
    # Una variable de entorno la pisa (pruebas, comprobaciones a mano).
    monkeypatch.setenv("YTMN_LANG", "es")
    monkeypatch.setattr(i18n, "_active", None)
    assert i18n.active() == "es"


def test_sin_base_todavia_vale_el_de_windows(lang, monkeypatch, isolated_home):
    monkeypatch.delenv("YTMN_LANG", raising=False)
    monkeypatch.setattr(i18n, "system_language", lambda: "en")
    monkeypatch.setattr(i18n, "_active", None)
    assert i18n.active() == "en"


def test_la_orden_de_reinicio_abre_la_misma_app(monkeypatch, tmp_path):
    from ytm_native import app

    exe = tmp_path / "python.exe"
    exe.write_text("")
    (tmp_path / "pythonw.exe").write_text("")
    monkeypatch.setattr(app.sys, "executable", str(exe))
    monkeypatch.delattr(app.sys, "frozen", raising=False)
    command = app.relaunch_command(["app", "--minimized", "--otro"])
    assert command[0].endswith("pythonw.exe") and command[1].endswith("__main__.py")
    # Los argumentos siguen, salvo --minimized: quien reinicia mira la ventana.
    assert command[2:] == ["--otro"]
    monkeypatch.setattr(app.sys, "frozen", True, raising=False)
    assert app.relaunch_command(["app"]) == [str(exe)]


def test_el_reinicio_no_hereda_el_idioma_forzado(monkeypatch):
    """Con ``YTMN_LANG`` heredada, la copia nueva volvería a abrirse en el
    idioma de antes y no en el recién elegido."""
    from ytm_native import app

    monkeypatch.setenv("YTMN_LANG", "es")
    monkeypatch.setenv("YTMN_HOME_TEST_MARK", "1")
    env = app.relaunch_env()
    assert "YTMN_LANG" not in env and env["YTMN_HOME_TEST_MARK"] == "1"
    started = []
    monkeypatch.setattr(app.subprocess, "Popen", lambda command, **kw: started.append(kw["env"]))
    app.relaunch()
    assert started and "YTMN_LANG" not in started[0]


def test_la_copia_de_seguridad_lleva_el_idioma(db):
    from ytm_native.services import backup

    i18n.set_preference(db, "en")
    assert backup.build_backup(db)["settings"]["language"] == "en"


def test_cambiar_de_idioma_vacia_lo_guardado_en_el_anterior(lang, db):
    """Lo guardado en el idioma de antes no se va a enseñar: se borra al
    momento, sin esperar a que caduque."""
    import json

    from ytm_native.services import discover

    lang.use("es")
    db.set_discover_entry("suena:es", json.dumps({"lang": "es"}))
    db.set_discover_entry("suena:viejo", json.dumps({}))            # de antes del inglés: español
    db.set_discover_entry("artista:en", json.dumps({"lang": "en"}))
    db.set_discover_entry("suena:roto", "no es json")
    db.set_discover_entry(discover.HOME_CACHE_KEY, json.dumps({"lang": "es"}))
    lang.use("en")
    assert discover.purge_other_language(db) == 3
    assert db.get_discover_entry("artista:en") is not None
    assert db.get_discover_entry(discover.HOME_CACHE_KEY) is not None   # el inicio se pisa al pedirlo
    assert discover.purge_other_language(db) == 0                     # una vez por cambio


def test_lo_guardado_en_otro_idioma_se_vuelve_a_pedir(lang, db):
    """Las recomendaciones del inicio, "Ahora suena" y la página del artista
    llevan títulos y datos en el idioma en que se pidieron."""
    import json

    from ytm_native.core.db import Track
    from ytm_native.services import discover
    from ytm_native.services.discover import ArtistPage, NowInfo, PlaylistCard, Shelf

    lang.use("es")
    shelves = [Shelf("Parecidas a «Rock»", "", (PlaylistCard("PL1", "Uno", "", ""),))]
    discover.save_shelves(db, "gustos", shelves)
    discover.save_now_info(db, NowInfo("v1", similar=[Track("v2", "Otra", "Ana")]))
    discover.save_artist_page(db, ArtistPage("UC1", "Ana"))
    assert discover.load_shelves(db) is not None
    assert discover.load_now_info(db, "v1") is not None
    assert discover.load_artist_page(db, "UC1") is not None

    lang.use("en")
    assert discover.load_shelves(db) is None
    assert discover.shelves_stale(db, discover.load_shelves(db))
    assert discover.load_now_info(db, "v1") is None
    assert discover.load_artist_page(db, "UC1") is None

    # Lo guardado antes de que existiera el inglés no dice su idioma: era español.
    _at, raw = db.get_discover_entry(discover.HOME_CACHE_KEY)
    data = json.loads(raw)
    data.pop("lang")
    db.set_discover_entry(discover.HOME_CACHE_KEY, json.dumps(data))
    assert discover.load_shelves(db) is None
    lang.use("es")
    assert discover.load_shelves(db) is not None
