"""Aviso de versión nueva de la app: frecuencia, opción apagada, enlace seguro (sin red)."""

import pytest

from ytm_native import __version__
from ytm_native.core import config
from ytm_native.services import updater
from ytm_native.services.updater import AppUpdateChecker, check, is_newer, parse_version, safe_release_url
from ytm_native.ui import main_window as mw

from test_home_search import animations_on, net, wait, window  # noqa: F401

PAGE = config.APP_RELEASES_PAGE
NOW = 2_000_000_000.0


class FakeGitHub:
    """La API de GitHub Releases en memoria; cuenta las peticiones."""

    def __init__(self, tag="v9.0.0", status=200, **extra):
        self.calls = 0
        self.status = status
        self.offline = False
        self.data = {"tag_name": tag, "html_url": f"{PAGE}/tag/{tag}", "draft": False,
                     "prerelease": False, **extra}

    def __call__(self, url, timeout):
        self.calls += 1
        assert url == config.APP_RELEASES_API
        if self.offline:
            raise OSError("sin red")
        return self.status, self.data


def sync_runner(fn, done):
    try:
        result = fn()
    except Exception as exc:  # noqa: BLE001
        done(None, exc)
    else:
        done(result, None)


# ---------------------------------------------------------------------------
# Versiones
# ---------------------------------------------------------------------------
def test_comparar_versiones():
    assert parse_version("v1.2.3") == (1, 2, 3)
    assert parse_version("1.2.0") == parse_version("1.2") == (1, 2)
    assert parse_version("1.2.0-beta") is None and parse_version("nightly") is None
    assert is_newer("v0.2.0", "0.1.0") and is_newer("0.10.0", "0.9.9")
    assert not is_newer("0.1.0", "0.1.0") and not is_newer("0.0.9", "0.1.0")
    assert not is_newer("basura", "0.1.0")


def test_el_enlace_solo_lleva_a_las_versiones_del_proyecto():
    good = f"{PAGE}/tag/v1.0.0"
    assert safe_release_url(good, "v1.0.0") == good
    assert safe_release_url("https://malo.example/descarga.exe", "v1.0.0") == f"{PAGE}/tag/v1.0.0"
    assert safe_release_url(PAGE + "-falso/tag/x", "v1") == f"{PAGE}/tag/v1"
    assert safe_release_url(None, "../../x y") == PAGE


# ---------------------------------------------------------------------------
# Comprobación
# ---------------------------------------------------------------------------
def test_version_nueva_se_avisa_y_se_guarda_la_hora(db):
    github = FakeGitHub("v9.1.0")
    release = check(db, github, now=NOW)
    assert release == updater.Release("9.1.0", f"{PAGE}/tag/v9.1.0")
    assert github.calls == 1
    assert db.get_float(updater.LAST_CHECK_KEY) == NOW


def test_antes_de_24_horas_no_se_pregunta_otra_vez(db):
    github = FakeGitHub("v9.1.0")
    check(db, github, now=NOW)
    again = check(db, github, now=NOW + config.APP_UPDATE_CHECK_INTERVAL_S - 60)
    assert github.calls == 1                           # ninguna petición más...
    assert again is not None and again.version == "9.1.0"   # ...pero el aviso se repite
    check(db, github, now=NOW + config.APP_UPDATE_CHECK_INTERVAL_S + 1)
    assert github.calls == 2


def test_apagado_no_sale_ninguna_peticion(db):
    github = FakeGitHub()
    db.set_setting(updater.ENABLED_KEY, "0")
    assert check(db, github, now=NOW) is None
    assert github.calls == 0


def test_misma_version_o_anterior_no_avisa(db):
    assert check(db, FakeGitHub(f"v{__version__}"), now=NOW) is None
    assert check(db, FakeGitHub("v0.0.1"), now=NOW, force=True) is None


def test_sin_red_no_gasta_el_dia(db):
    github = FakeGitHub()
    github.offline = True
    with pytest.raises(OSError):
        check(db, github, now=NOW)
    assert db.get_setting(updater.LAST_CHECK_KEY) is None
    github.offline = False
    assert check(db, github, now=NOW + 5) is not None and github.calls == 2


def test_un_error_de_github_tampoco_gasta_el_dia(db):
    with pytest.raises(OSError):
        check(db, FakeGitHub(status=503), now=NOW)
    assert db.get_setting(updater.LAST_CHECK_KEY) is None


def test_sin_versiones_publicadas_o_de_prueba_no_avisa(db):
    assert check(db, FakeGitHub(status=404), now=NOW) is None
    assert db.get_float(updater.LAST_CHECK_KEY) == NOW
    assert check(db, FakeGitHub("v9.0.0", prerelease=True), now=NOW, force=True) is None
    assert check(db, FakeGitHub("v9.0.0", draft=True), now=NOW, force=True) is None


# ---------------------------------------------------------------------------
# Servicio en segundo plano
# ---------------------------------------------------------------------------
def test_el_servicio_emite_el_aviso(qapp, db):
    checker = AppUpdateChecker(db, fetch=FakeGitHub("v9.2.0"), runner=sync_runner)
    seen = []
    checker.available.connect(lambda version, url: seen.append((version, url)))
    checker.check()
    assert seen == [("9.2.0", f"{PAGE}/tag/v9.2.0")]
    assert checker.release.version == "9.2.0"


def test_a_mano_pregunta_aunque_este_apagado_y_avisa_de_fallos(qapp, db):
    github = FakeGitHub(f"v{__version__}")
    checker = AppUpdateChecker(db, fetch=github, runner=sync_runner)
    checker.set_enabled(False)
    checker.check()                                    # automática y apagada: nada
    assert github.calls == 0
    results, failures = [], []
    checker.checked.connect(results.append)
    checker.failed.connect(failures.append)
    checker.check(manual=True)
    assert github.calls == 1 and results == [False]
    github.offline = True
    checker.check(manual=True)
    assert len(failures) == 1


def test_un_fallo_automatico_es_silencioso(qapp, db):
    github = FakeGitHub()
    github.offline = True
    checker = AppUpdateChecker(db, fetch=github, runner=sync_runner)
    failures = []
    checker.failed.connect(failures.append)
    checker.check()
    assert failures == [] and checker.release is None


# ---------------------------------------------------------------------------
# En la ventana
# ---------------------------------------------------------------------------
def test_la_ventana_ensena_el_enlace_y_solo_abre_la_pagina(window, monkeypatch):
    opened = []
    monkeypatch.setattr(mw.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    assert window.footer.update_btn.isHidden() and not window.app_update_action.isVisible()
    window.app_updates._fetch = FakeGitHub("v9.3.0")
    window.app_updates._run = sync_runner
    window.app_updates.check()
    assert not window.footer.update_btn.isHidden()
    assert "9.3.0" in window.footer.update_btn.text()
    assert window.app_update_action.isVisible()
    window.footer.update_btn.click()
    assert opened == [f"{PAGE}/tag/v9.3.0"]
