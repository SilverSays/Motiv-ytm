"""Taxonomía de errores de ``yt-dlp``.

Comparar texto de ``stderr`` es frágil, así que:

* Solo se miran las líneas ``ERROR:`` (las ``WARNING:`` no deciden la categoría).
* El **orden importa**: primero bloqueo anti-bot y red. Un "Video unavailable"
  o un fallo de extracción pueden ser consecuencia de un bloqueo, y en ese caso
  el culpable no es ni la pista ni el binario.
* Lo que no encaja en ningún patrón cae en ``UNKNOWN`` (con reintentos acotados).
"""

from __future__ import annotations

from enum import Enum


class ErrorKind(str, Enum):
    BOT_BLOCK = "bot_block"            # Caso 4
    NETWORK = "network"                # Caso 3
    RUNTIME_MISSING = "runtime"        # falta deno / runtime JS
    NEEDS_LOGIN = "needs_login"        # restricción de edad / requiere sesión
    UNAVAILABLE = "unavailable"        # Caso 1
    EXTRACTION = "extraction"          # Caso 5
    UNKNOWN = "unknown"                # caso por defecto
    # Categorías locales (no vienen de yt-dlp):
    CANCELLED = "cancelled"
    PREEMPTED = "preempted"            # la mató una actualización; se reintenta
    LOCAL_RATE_LIMIT = "local_rate_limit"
    BINARY_MISSING = "binary_missing"


_BOT = (
    "confirm you're not a bot",
    "confirm you’re not a bot",
    "confirm you are not a bot",
    "http error 429",
    "too many requests",
    "this content isn't available, try again later",
    "this content isn’t available, try again later",
    "rate-limited by youtube",
    "unusual traffic",
)

_NETWORK = (
    "getaddrinfo failed",
    "name or service not known",
    "temporary failure in name resolution",
    "failed to resolve",
    "connection reset",
    "connection refused",
    "connection aborted",
    "remote end closed connection",
    "network is unreachable",
    "no route to host",
    "timed out",
    "timeout",
    "winerror 10051",
    "winerror 10054",
    "winerror 10060",
    "winerror 10061",
    "winerror 10065",
    "unable to connect",
    "transporterror",
    "ssl:",
    "sslerror",
    "eof occurred in violation of protocol",
)

_RUNTIME = (
    "no supported javascript runtime",
    "javascript runtime could not be found",
    "js runtime",
)

_NEEDS_LOGIN = (
    "sign in to confirm your age",
    "age-restricted",
    "age restricted",
    "inappropriate for some users",
    "login required",
    "this video requires login",
    "members-only",
    "join this channel to get access",
    "only available to music premium members",
)

_UNAVAILABLE = (
    "video unavailable",
    "private video",
    "this video is private",
    "this video is not available",
    "this video is no longer available",
    "has been removed",
    "removed by the uploader",
    "copyright",
    "not available in your country",
    "not made this video available in your country",
    "blocked it in your country",
    "has been terminated",
    "account associated with this video",
    "premieres in",
    "this live event will begin",
    "video is unavailable",
)

_EXTRACTION = (
    "unable to extract",
    "failed to extract",
    "nsig extraction failed",
    "signature extraction failed",
    "challenge solving failed",
    "n challenge",
    "requested format is not available",
    "no video formats found",
    "failed to parse json",
    "unable to download webpage",
    "unable to download api page",
    "please report this issue",
    "unsupported url",
    "sabr",
)

_ORDER = (
    (ErrorKind.BOT_BLOCK, _BOT),
    (ErrorKind.NETWORK, _NETWORK),
    (ErrorKind.RUNTIME_MISSING, _RUNTIME),
    (ErrorKind.NEEDS_LOGIN, _NEEDS_LOGIN),
    (ErrorKind.UNAVAILABLE, _UNAVAILABLE),
    (ErrorKind.EXTRACTION, _EXTRACTION),
)

_COOKIES_INVALID = (
    "cookies are no longer valid",
    "account cookies are no longer valid",
)


def error_lines(stderr: str) -> list[str]:
    return [ln.strip() for ln in (stderr or "").splitlines() if ln.strip().upper().startswith("ERROR")]


def classify(stderr: str, timed_out: bool = False) -> ErrorKind:
    """Clasifica una ejecución fallida de yt-dlp."""
    if timed_out:
        return ErrorKind.NETWORK
    lines = error_lines(stderr)
    # Sin líneas ERROR (salida truncada, crash): se usa todo el texto.
    text = "\n".join(lines).lower() if lines else (stderr or "").lower()
    for kind, patterns in _ORDER:
        if any(p in text for p in patterns):
            return kind
    return ErrorKind.UNKNOWN


def cookies_look_invalid(stderr: str) -> bool:
    """yt-dlp avisa con un WARNING cuando las cookies de la cuenta ya no sirven."""
    text = (stderr or "").lower()
    return any(p in text for p in _COOKIES_INVALID)


def summarize(stderr: str, limit: int = 300) -> str:
    """Última línea de error, recortada, para mostrar en la GUI o en el log."""
    lines = error_lines(stderr)
    if lines:
        return lines[-1][:limit]
    tail = (stderr or "").strip().splitlines()
    return tail[-1][:limit] if tail else ""


MESSAGES_ES = {
    ErrorKind.BOT_BLOCK: "YouTube pidió verificación anti-bot",
    ErrorKind.NETWORK: "Sin conexión",
    ErrorKind.RUNTIME_MISSING: "Falta el runtime de JavaScript (deno)",
    ErrorKind.NEEDS_LOGIN: "La pista requiere iniciar sesión",
    ErrorKind.UNAVAILABLE: "Pista no disponible",
    ErrorKind.EXTRACTION: "yt-dlp no pudo extraer el audio",
    ErrorKind.UNKNOWN: "Error desconocido de yt-dlp",
    ErrorKind.CANCELLED: "Cancelado",
    ErrorKind.PREEMPTED: "Interrumpido por una actualización",
    ErrorKind.LOCAL_RATE_LIMIT: "Límite local de resoluciones por hora",
    ErrorKind.BINARY_MISSING: "Falta yt-dlp en la carpeta bin",
}
