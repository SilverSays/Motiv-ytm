"""Cifrado de secretos en reposo con la protección de datos de Windows (DPAPI).

``CryptProtectData`` cifra con una clave ligada al usuario de Windows que tiene
la sesión abierta: otro usuario del mismo equipo, o el mismo archivo copiado a
otro equipo, no puede descifrarlo. No hay contraseña que guardar ni pedir.

* ``dpapi_protect`` / ``dpapi_unprotect``: las primitivas, bytes a bytes.
* ``seal`` / ``unseal``: lo mismo con una cabecera mágica delante
  (``YTMN_DPAPI\\x01``), para distinguir en disco lo cifrado de lo que aún
  está en claro.

``entropy`` es un dato adicional que también hace falta para descifrar: cada
uso (la sesión de Last.fm, las cookies) pasa el suyo, y así un blob de un uso
no sirve para otro.

Cualquier fallo (datos dañados, de otro usuario, fuera de Windows) se eleva
como ``CryptoError`` con un mensaje que nunca incluye los datos.
"""

from __future__ import annotations

import sys
from typing import Optional
from ytm_native.core.i18n import tr

MAGIC = b"YTMN_DPAPI\x01"
CRYPTPROTECT_UI_FORBIDDEN = 0x1
# Descripción que Windows guarda junto al blob (no es secreta).
_DESCRIPTION = "Motiv"


class CryptoError(Exception):
    """No se pudo cifrar o descifrar (el mensaje nunca lleva los datos)."""


def available() -> bool:
    return sys.platform == "win32"


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _CryptProtectData = _crypt32.CryptProtectData
    _CryptProtectData.argtypes = [
        ctypes.POINTER(_Blob), wintypes.LPCWSTR, ctypes.POINTER(_Blob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob),
    ]
    _CryptProtectData.restype = wintypes.BOOL

    _CryptUnprotectData = _crypt32.CryptUnprotectData
    _CryptUnprotectData.argtypes = [
        ctypes.POINTER(_Blob), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(_Blob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob),
    ]
    _CryptUnprotectData.restype = wintypes.BOOL

    _LocalFree = _kernel32.LocalFree
    _LocalFree.argtypes = [ctypes.c_void_p]
    _LocalFree.restype = ctypes.c_void_p

    def _blob(data: bytes) -> tuple["_Blob", object]:
        """El ``DATA_BLOB`` de ``data`` y el búfer que lo respalda (debe
        seguir vivo mientras se use el blob)."""
        buffer = ctypes.create_string_buffer(data, len(data))
        blob = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
        return blob, buffer

    def _take(blob: "_Blob") -> bytes:
        """Copia la salida de Windows y libera su memoria."""
        try:
            return ctypes.string_at(blob.pbData, blob.cbData)
        finally:
            _LocalFree(ctypes.cast(blob.pbData, ctypes.c_void_p))


def dpapi_protect(data: bytes, entropy: Optional[bytes] = None) -> bytes:
    """Cifra ``data`` para el usuario de Windows actual."""
    if not available():
        raise CryptoError(tr("el cifrado de Windows (DPAPI) no está disponible en este sistema"))
    data_in, keep_data = _blob(bytes(data))
    extra = _blob(entropy) if entropy else None
    out = _Blob()
    ok = _CryptProtectData(
        ctypes.byref(data_in), _DESCRIPTION, ctypes.byref(extra[0]) if extra else None,
        None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out),
    )
    del keep_data, extra
    if not ok:
        raise CryptoError(tr("Windows no pudo cifrar los datos (error {get_last_error})", get_last_error=ctypes.get_last_error()))
    return _take(out)


def dpapi_unprotect(data: bytes, entropy: Optional[bytes] = None) -> bytes:
    """Descifra lo que cifró ``dpapi_protect`` con la misma ``entropy``."""
    if not available():
        raise CryptoError(tr("el cifrado de Windows (DPAPI) no está disponible en este sistema"))
    if not data:
        raise CryptoError(tr("no hay datos cifrados"))
    data_in, keep_data = _blob(bytes(data))
    extra = _blob(entropy) if entropy else None
    out = _Blob()
    ok = _CryptUnprotectData(
        ctypes.byref(data_in), None, ctypes.byref(extra[0]) if extra else None,
        None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out),
    )
    del keep_data, extra
    if not ok:
        # Datos dañados, de otro usuario o de otro equipo, o entropía distinta.
        raise CryptoError(tr("Windows no pudo descifrar los datos (error {get_last_error})", get_last_error=ctypes.get_last_error()))
    return _take(out)


def is_sealed(data: bytes) -> bool:
    return bytes(data[: len(MAGIC)]) == MAGIC


def seal(data: bytes, entropy: Optional[bytes] = None) -> bytes:
    """``data`` cifrado con DPAPI y con la cabecera mágica delante."""
    return MAGIC + dpapi_protect(data, entropy)


def unseal(data: bytes, entropy: Optional[bytes] = None) -> bytes:
    """Lo contrario de ``seal``. Sin la cabecera, ``CryptoError``: lo que no
    se cifró nunca se acepta como si lo estuviera."""
    if not is_sealed(data):
        raise CryptoError(tr("los datos no están cifrados con la cabecera de la aplicación"))
    return dpapi_unprotect(bytes(data[len(MAGIC):]), entropy)
