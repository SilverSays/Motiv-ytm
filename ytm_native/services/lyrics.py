"""Letras sincronizadas desde LRCLIB (https://lrclib.net), un servicio público sin cuenta.

El enfoque (consulta exacta con duración, luego búsqueda filtrada por duración,
limpieza de sufijos tipo "(Official Video)") está adaptado de
JustAnotherMusicClient (Apache-2.0). Las letras no pasan por YouTube: no añaden
peticiones a la cuenta ni a la IP frente a YouTube.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

from ytm_native.core import config

_TIMESTAMP = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]")
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
    def from_json(text: str) -> "Lyrics":
        data = json.loads(text)
        return Lyrics(
            lines=tuple(LyricLine(float(s), str(t)) for s, t in data.get("lines", [])),
            plain=data.get("plain", ""),
            source=data.get("source", "LRCLIB"),
        )


def clean_lookup_text(value: str) -> str:
    value = _NOISE_PAREN.sub("", value or "")
    value = _NOISE_DASH.sub("", value)
    return re.sub(r"\s{2,}", " ", value).strip()


def parse_lrc(lrc: str) -> tuple[LyricLine, ...]:
    """Convierte texto LRC en líneas ordenadas (admite varias marcas por línea)."""
    lines: list[LyricLine] = []
    for raw in (lrc or "").splitlines():
        text = _TIMESTAMP.sub("", raw).strip()
        for m in _TIMESTAMP.finditer(raw):
            fraction = (m.group(3) or "0").ljust(3, "0")[:3]
            start = int(m.group(1)) * 60 + int(m.group(2)) + int(fraction) / 1000
            lines.append(LyricLine(start, text))
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


def fetch_lyrics(
    title: str,
    artists: str,
    album: str = "",
    duration_s: Optional[float] = None,
    http_json: Callable[[str, float], object] = _http_json,
) -> Optional[Lyrics]:
    """Busca la letra (bloqueante). ``None`` si no hay; lanza ``OSError`` si no hay red."""
    queries = build_queries(title, artists, album)
    plain_fallback: Optional[Lyrics] = None

    # 1) Coincidencia exacta (requiere duración).
    if duration_s and duration_s > 0:
        for q in queries:
            params = {"track_name": q["title"], "artist_name": q["artist"], "duration": str(round(duration_s))}
            if q["album"]:
                params["album_name"] = q["album"]
            match = http_json(f"{config.LRCLIB_BASE}/get?{urllib.parse.urlencode(params)}", 4.0)
            found = _from_match(match, duration_s, require_synced=False) if match else None
            if found and found.synced:
                return found
            plain_fallback = plain_fallback or found

    # 2) Búsqueda, ordenada por cercanía de duración.
    for q in queries[:3]:
        params = {"track_name": q["title"], "artist_name": q["artist"]}
        matches = http_json(f"{config.LRCLIB_BASE}/search?{urllib.parse.urlencode(params)}", 6.0) or []
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
    return plain_fallback
