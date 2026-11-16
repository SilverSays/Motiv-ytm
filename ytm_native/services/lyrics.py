"""Letras sincronizadas desde LRCLIB (https://lrclib.net), un servicio público sin cuenta.

El enfoque (consulta exacta con duración, luego búsqueda filtrada por duración,
limpieza de sufijos tipo "(Official Video)") está adaptado de
JustAnotherMusicClient (Apache-2.0). Las letras no pasan por YouTube: no añaden
peticiones a la cuenta ni a la IP frente a YouTube.
"""

from __future__ import annotations

import http.client
import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

from ytm_native.core import config

_TIME = r"(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?"
# Marca de línea: una o varias al principio ("[00:20.00][01:05.00]Estribillo").
_TIMESTAMP = re.compile(r"\s*\[" + _TIME + r"\]")
# Marcas por palabra (LRC "enriquecido"): "<00:12.50>" o "[00:12.50]" en mitad
# del texto. Solo dicen cuándo suena cada palabra: se quitan del texto.
_WORD_MARK = re.compile(r"<" + _TIME + r">|\[" + _TIME + r"\]")
_LEADING_WORD_MARK = re.compile(r"\s*<" + _TIME + r">")
_OFFSET = re.compile(r"^\s*\[offset:\s*([+-]?\d+)\s*\]\s*$", re.IGNORECASE)
_NOISE_PAREN = re.compile(
    r"\s*[\[(](?:official\s*)?(?:music\s*)?(?:video|visualizer|audio|lyrics?|lyric\s*video|"
    r"remaster(?:ed)?(?:\s*\d{4})?|\d{4}\s*remaster(?:ed)?|radio edit|single version|album version|"
    r"live|explicit|feat\.?[^\])]*|ft\.?[^\])]*|with [^\])]*)[\])]",
    re.IGNORECASE,
)
_NOISE_DASH = re.compile(
    r"\s+-\s+(?:official\s*)?(?:music\s*)?(?:video|visualizer|audio|lyrics?|lyric\s*video|"
    r"remaster(?:ed)?(?:\s*\d{4})?|\d{4}\s*remaster(?:ed)?).*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class LyricLine:
    start_s: float
    text: str


@dataclass(frozen=True)
class Lyrics:
    lines: tuple[LyricLine, ...] = ()
    plain: str = ""
    source: str = "LRCLIB"

    @property
    def synced(self) -> bool:
        return bool(self.lines)

    @property
    def empty(self) -> bool:
        return not self.lines and not self.plain.strip()

    def to_json(self) -> str:
        return json.dumps(
            {"lines": [[ln.start_s, ln.text] for ln in self.lines], "plain": self.plain, "source": self.source},
            ensure_ascii=False,
        )

    @staticmethod
    def from_json(text: object) -> "Lyrics":
        """La letra guardada. Lo que no tenga la forma esperada (caché dañada,
        otra versión) se ignora: en el peor caso devuelve una letra vacía, que
        quien llama trata como si no estuviera guardada."""
        try:
            data = json.loads(text) if isinstance(text, (str, bytes, bytearray)) else None
        except ValueError:
            data = None
        if not isinstance(data, dict):
            return Lyrics()
        lines = []
        raw_lines = data.get("lines")
        for row in raw_lines if isinstance(raw_lines, list) else ():
            if not (isinstance(row, (list, tuple)) and len(row) == 2):
                continue
            start, text_ = row
            if (isinstance(start, (int, float)) and not isinstance(start, bool)
                    and math.isfinite(start) and start >= 0 and isinstance(text_, str)):
                lines.append(LyricLine(float(start), text_))
        lines.sort(key=lambda ln: ln.start_s)
        plain, source = data.get("plain"), data.get("source")
        return Lyrics(
            lines=tuple(lines),
            plain=plain if isinstance(plain, str) else "",
            source=source if isinstance(source, str) and source else "LRCLIB",
        )


def clean_lookup_text(value: str) -> str:
    value = _NOISE_PAREN.sub("", value or "")
    value = _NOISE_DASH.sub("", value)
    return re.sub(r"\s{2,}", " ", value).strip()


def _seconds(minutes: str, seconds: str, fraction: Optional[str]) -> float:
    return int(minutes) * 60 + int(seconds) + int((fraction or "0").ljust(3, "0")[:3]) / 1000


def parse_lrc(lrc: str) -> tuple[LyricLine, ...]:
    """Convierte texto LRC en líneas ordenadas.

    * Varias marcas al principio de una línea la repiten en cada momento.
    * Las marcas por palabra (``<mm:ss.xx>`` o ``[mm:ss.xx]`` dentro del texto)
      se quitan: cada línea sale una sola vez, con su texto limpio.
    * ``[offset:+/-ms]`` adelanta (positivo) o retrasa (negativo) toda la letra.
    * Las etiquetas de cabecera (``[ar:…]``, ``[ti:…]``) y lo repetido se ignoran.
    """
    offset = 0.0
    found: dict[tuple[float, str], None] = {}
    for raw in (lrc or "").splitlines():
        shift = _OFFSET.match(raw)
        if shift:
            offset = int(shift.group(1)) / 1000
            continue
        starts: list[float] = []
        pos = 0
        while (m := _TIMESTAMP.match(raw, pos)) is not None:
            starts.append(_seconds(*m.groups()))
            pos = m.end()
        if not starts:
            # Hay letras que solo traen marcas por palabra: vale la primera.
            m = _LEADING_WORD_MARK.match(raw)
            if m is None:
                continue
            starts.append(_seconds(*m.groups()))
        text = " ".join(_WORD_MARK.sub("", raw[pos:]).split())
        for start in starts:
            found.setdefault((start, text), None)
    lines = [LyricLine(round(max(0.0, start - offset), 3), text) for start, text in found]
    lines.sort(key=lambda ln: ln.start_s)
    return tuple(lines)


def current_line_index(lines: tuple[LyricLine, ...], position_s: float) -> int:
    """Índice de la línea que suena en ``position_s`` (-1 si aún no empieza)."""
    lo, hi = 0, len(lines)
    while lo < hi:
        mid = (lo + hi) // 2
        if lines[mid].start_s <= position_s:
            lo = mid + 1
        else:
            hi = mid
    return lo - 1


def build_queries(title: str, artists: str, album: str = "") -> list[dict]:
    """Variantes de búsqueda: título y artista originales y limpios (máx. 6)."""
    artist_options: list[str] = []
    for candidate in [artists] + [a.strip() for a in (artists or "").split(",")]:
        for value in (candidate, clean_lookup_text(candidate)):
            if value and value not in artist_options:
                artist_options.append(value)
    title_options = [t for t in dict.fromkeys([title, clean_lookup_text(title)]) if t]
    queries, seen = [], set()
    for t in title_options:
        for a in artist_options:
            key = (t.lower(), a.lower())
            if key not in seen:
                seen.add(key)
                queries.append({"title": t, "artist": a, "album": album or ""})
    return queries[:6]


def _http_json(url: str, timeout: float):
    req = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": config.LRCLIB_USER_AGENT}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def _from_match(match: dict, duration_s: Optional[float], require_synced: bool) -> Optional[Lyrics]:
    if not isinstance(match, dict):
        return None
    provider_duration = match.get("duration")
    if duration_s and isinstance(provider_duration, (int, float)):
        if abs(provider_duration - duration_s) > config.LYRICS_MAX_DURATION_DELTA_S:
            return None
    lines = parse_lrc(match.get("syncedLyrics") or "")
    plain = (match.get("plainLyrics") or "").strip()
    if lines:
        return Lyrics(lines=lines, plain=plain)
    if plain and not require_synced:
        return Lyrics(plain=plain)
    return None


class _Stop(Exception):
    """Se deja de preguntar: falló la red o se acabó el tiempo."""


def fetch_lyrics(
    title: str,
    artists: str,
    album: str = "",
    duration_s: Optional[float] = None,
    http_json: Callable[[str, float], object] = _http_json,
    budget_s: float = config.LYRICS_BUDGET_S,
    clock: Callable[[], float] = time.monotonic,
) -> Optional[Lyrics]:
    """Busca la letra (bloqueante), como mucho ``budget_s`` segundos en total.

    ``None`` si no hay. Si la red falla o se acaba el tiempo, se devuelve la
    letra sin sincronizar que ya se hubiera encontrado; si no había ninguna,
    se lanza ``OSError`` (``TimeoutError`` si fue el tiempo), para que no se
    guarde como "sin letra" algo que no se llegó a buscar entero.
    """
    queries = build_queries(title, artists, album)
    plain_fallback: Optional[Lyrics] = None
    failure: Optional[OSError] = None
    deadline = clock() + budget_s

    def ask(url: str, timeout: float) -> object:
        nonlocal failure
        remaining = deadline - clock()
        if remaining < config.LYRICS_MIN_REQUEST_S:
            failure = TimeoutError(f"la búsqueda de la letra superó {budget_s:g} s")
            raise _Stop
        try:
            return http_json(url, min(timeout, remaining))
        except ValueError:
            return None                   # respuesta que no es JSON: como si no hubiera
        except (OSError, http.client.HTTPException) as exc:
            failure = exc if isinstance(exc, OSError) else OSError(f"respuesta cortada: {exc}")
            raise _Stop from exc

    try:
        # 1) Coincidencia exacta (requiere duración).
        if duration_s and duration_s > 0:
            for q in queries:
                params = {"track_name": q["title"], "artist_name": q["artist"],
                          "duration": str(round(duration_s))}
                if q["album"]:
                    params["album_name"] = q["album"]
                match = ask(f"{config.LRCLIB_BASE}/get?{urllib.parse.urlencode(params)}", 4.0)
                found = _from_match(match, duration_s, require_synced=False) if match else None
                if found and found.synced:
                    return found
                plain_fallback = plain_fallback or found

        # 2) Búsqueda, ordenada por cercanía de duración.
        for q in queries[:3]:
            params = {"track_name": q["title"], "artist_name": q["artist"]}
            matches = ask(f"{config.LRCLIB_BASE}/search?{urllib.parse.urlencode(params)}", 6.0) or []
            if not isinstance(matches, list):
                continue

            def delta(m: dict) -> float:
                d = m.get("duration") if isinstance(m, dict) else None
                return abs(d - duration_s) if duration_s and isinstance(d, (int, float)) else 0.0

            for match in sorted(matches, key=delta):
                found = _from_match(match, duration_s, require_synced=False)
                if found and found.synced:
                    return found
                plain_fallback = plain_fallback or found
    except _Stop:
        pass
    if plain_fallback is None and failure is not None:
        raise failure
    return plain_fallback
