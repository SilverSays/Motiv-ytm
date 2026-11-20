"""Claves de las vistas y utilidades que comparten la ventana principal y sus
vistas.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt

from ytm_native.core.i18n import tr

HOME_KEY = "__inicio__"
SEARCH_KEY = "__buscar__"
QUEUE_KEY = "__cola__"
# Preferencias de sonido: la salida elegida (su nombre en mpv y cómo se llama)
# y el preajuste del ecualizador.
AUDIO_DEVICE_KEY = "audio_device"
AUDIO_DEVICE_NAME_KEY = "audio_device_name"
EQUALIZER_KEY = "equalizer_preset"
HISTORY_KEY = "__historial__"
LOCAL_KEY = "__equipo__"          # canciones de la carpeta de música del disco
FIXED_KEYS = (HOME_KEY, SEARCH_KEY, QUEUE_KEY, HISTORY_KEY, LOCAL_KEY)
# Pestañas del historial: lo último, lo más escuchado en 30 días y desde siempre.
HEARD_RECENT, HEARD_MONTH, HEARD_ALL = "recientes", "mes", "siempre"
HEARD_LABELS = (
    (HEARD_RECENT, tr("Recientes")),
    (HEARD_MONTH, tr("Lo más escuchado del mes")),
    (HEARD_ALL, tr("Lo más escuchado")),
)
# Fila de la barra lateral que no es una vista: abre el diálogo de lista nueva.
NEW_LIST_KEY = "__nueva_lista__"
# Vista de una lista ajena a la biblioteca (una recomendación abierta).
REMOTE_PREFIX = "remota:"
# Página de un artista (por su canal).
ARTIST_PREFIX = "artista:"
ROLE_ID = Qt.ItemDataRole.UserRole
ROLE_ART = Qt.ItemDataRole.UserRole + 1

SEARCH_IDLE_NOTE = tr("Busca sin iniciar sesión")


def is_remote(key: Optional[str]) -> bool:
    return bool(key) and key.startswith(REMOTE_PREFIX)


def is_artist(key: Optional[str]) -> bool:
    return bool(key) and key.startswith(ARTIST_PREFIX)


def is_library(key: Optional[str]) -> bool:
    """¿La vista es una lista de la biblioteca (y no una vista fija o ajena)?"""
    return (bool(key) and key not in FIXED_KEYS and key != NEW_LIST_KEY
            and not is_remote(key) and not is_artist(key))


def format_total(seconds: Optional[float]) -> str:
    """Duración total legible: "42 min", "5 h 12 min"."""
    if not seconds or seconds <= 0:
        return ""
    minutes = int(round(seconds / 60))
    hours, minutes = divmod(minutes, 60)
    if hours and minutes:
        return tr("{hours} h {minutes} min", hours=hours, minutes=minutes)
    return tr("{hours} h", hours=hours) if hours else tr("{minutes} min", minutes=minutes)
