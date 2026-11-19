"""Idioma de la interfaz: español (el del código) o inglés.

Los textos se escriben en español en el propio código y pasan por ``tr``,
que los cambia por su versión inglesa si toca (catálogo en ``i18n_en``). Un
texto con huecos lleva nombres (``tr("{n} canciones", n=3)``): el orden de
las palabras puede cambiar de un idioma a otro, los nombres no.

El idioma se decide una vez, la primera vez que hace falta un texto, y vale
para toda la ejecución: muchos textos se fijan al importar los módulos o al
construir la ventana, así que cambiarlo en caliente dejaría la interfaz a
medias. Por orden manda:

1. ``YTMN_LANG`` ("es" o "en"), para las pruebas y para probar a mano.
2. La preferencia guardada (``language`` en la base): "es", "en" o "system".
3. El idioma de Windows: español si lo es; si no, inglés.

La preferencia se cambia en Opciones y se aplica al volver a abrir la app.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import sys
import threading
from typing import Optional

log = logging.getLogger("ytmn.i18n")

LANGUAGE_KEY = "language"
ENV_KEY = "YTMN_LANG"
SPANISH, ENGLISH, SYSTEM = "es", "en", "system"
LANGUAGES = (SPANISH, ENGLISH)
PREFERENCES = (SYSTEM, SPANISH, ENGLISH)
# Cada idioma se nombra en sí mismo: quien no entiende el actual reconoce el suyo.
NATIVE_NAMES = {SPANISH: "Español", ENGLISH: "English"}

_active: Optional[str] = None
_catalog: Optional[dict] = None
_lock = threading.Lock()


def normalize(value: object) -> str:
    """"es", "en" o "system" (lo desconocido cuenta como "system")."""
    return value if value in PREFERENCES else SYSTEM


def system_language() -> str:
    """El idioma de la interfaz de Windows: "es" si es español, si no "en"."""
    if sys.platform == "win32":
        try:
            import ctypes

            langid = int(ctypes.windll.kernel32.GetUserDefaultUILanguage())
            return SPANISH if (langid & 0x3FF) == 0x0A else ENGLISH   # LANG_SPANISH
        except (AttributeError, OSError, ValueError):
            pass
    import locale

    try:
        code = locale.getlocale()[0] or ""
    except ValueError:
        code = ""
    code = code or os.environ.get("LANG", "")
    return SPANISH if code.lower().startswith(("es", "spanish")) else ENGLISH


def resolve(preference: object) -> str:
    """El idioma que toca para una preferencia ("system" → el de Windows)."""
    preference = normalize(preference)
    return system_language() if preference == SYSTEM else preference


def _stored_preference() -> str:
    """La preferencia guardada, leída sin abrir la base para escribir (puede
    que aún no exista: el primer arranque)."""
    try:
        from ytm_native.core import paths

        db = paths.db_path()
        if not db.is_file():
            return SYSTEM
        uri = db.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=1.0)
        try:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (LANGUAGE_KEY,)).fetchone()
        finally:
            conn.close()
        return normalize(row[0] if row else None)
    except Exception as exc:  # noqa: BLE001 - sin preferencia legible, la de Windows
        log.info("no se pudo leer el idioma guardado (%s)", type(exc).__name__)
        return SYSTEM


def active() -> str:
    """El idioma de esta ejecución ("es" o "en").

    ``app.main`` lo decide en el hilo principal antes de crear ningún hilo;
    el cerrojo cubre a quien llegue antes (un módulo que traduce al
    importarse, una prueba): solo uno lee la preferencia y los demás esperan
    su respuesta en vez de decidir cada uno por su lado."""
    global _active
    if _active is None:
        with _lock:
            if _active is None:
                forced = os.environ.get(ENV_KEY, "").strip().lower()
                _active = forced if forced in LANGUAGES else resolve(_stored_preference())
    return _active


def use(language: str) -> None:
    """Fija el idioma de la ejecución (al arrancar o en las pruebas)."""
    global _active
    with _lock:
        _active = language if language in LANGUAGES else resolve(language)


def _english() -> dict:
    global _catalog
    if _catalog is None:
        from ytm_native.core.i18n_en import CATALOG

        _catalog = CATALOG
    return _catalog


def tr(text: str, /, **values) -> str:
    """El texto en el idioma activo, con sus huecos rellenos.

    Un texto que no está en el catálogo sale tal cual (mejor en español que
    vacío). Los huecos solo se rellenan si se pasan valores: así un texto con
    llaves literales y sin huecos no se toca.
    """
    if active() == ENGLISH:
        text = _english().get(text, text)
    if not values:
        return text
    try:
        return text.format(**values)
    except (KeyError, IndexError, ValueError) as exc:
        # Un hueco que falta o una llave suelta en una traducción no puede
        # tumbar la app (esto se llama dentro de señales de Qt): mejor el
        # texto sin rellenar.
        log.warning("texto con huecos mal formados (%s: %s): %r", type(exc).__name__, exc, text)
        return text


def number(n: int) -> str:
    """Un entero con los millares a la manera del idioma: "12 480" en
    español (espacio fino, desde 10 000, como manda la RAE) y "12,480" en
    inglés."""
    if active() == ENGLISH:
        return f"{n:,}"
    return f"{n:,}".replace(",", " ") if abs(n) >= 10000 else str(n)


def plural(n: int, one: str, many: str, /, **values) -> str:
    """``one`` o ``many`` según ``n``, traducido; ``{n}`` vale ``n`` ya con
    sus millares (``number``). Los dos textos llevan el número dentro
    (``"{n} canciones"``): así cada idioma lo pone donde le toca y no hace
    falta traducir aparte las palabras."""
    return tr(one if n == 1 else many, n=number(n), **values)


def preference(db) -> str:
    return normalize(db.get_setting(LANGUAGE_KEY, SYSTEM))


def set_preference(db, value: str) -> None:
    db.set_setting(LANGUAGE_KEY, normalize(value))
