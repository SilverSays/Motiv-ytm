"""Descarga de los binarios portables a ``%LOCALAPPDATA%\\YTMusicNativo\\bin``.

* ``yt-dlp.exe``: directamente del repositorio de builds *nightly*, así el
  binario ya nace en ese canal y ``-U`` se queda en él.
* ``deno.exe``: release oficial (zip).
* ``mpv.exe``: build de Windows de shinchiro (7z; se extrae con ``tar`` de
  Windows, que usa libarchive).

Uso: ``python -m ytm_native.playback.bootstrap [--force]``
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Optional

from ytm_native.core import paths

Progress = Callable[[str], None]

YTDLP_BASE = "https://github.com/yt-dlp/yt-dlp-nightly-builds/releases/latest/download/"
DENO_BASE = "https://github.com/denoland/deno/releases/latest/download/"
DENO_ASSET = "deno-x86_64-pc-windows-msvc.zip"
MPV_RELEASE_API = "https://api.github.com/repos/shinchiro/mpv-winbuild-cmake/releases/latest"
_MPV_ASSET_RE = re.compile(r"^mpv-x86_64-\d{8}-git-[0-9a-f]+\.7z$")
_UA = {"User-Agent": "ytmn-bootstrap"}
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class BootstrapError(RuntimeError):
    pass


def missing_binaries() -> list[str]:
    return [p.name for p in (paths.ytdlp_exe(), paths.deno_exe(), paths.mpv_exe()) if not p.is_file()]


def _download(url: str, dest: Path, progress: Progress, label: str, attempts: int = 3) -> None:
    """Descarga con reintentos: los cortes de conexión a mitad son habituales."""
    for attempt in range(1, attempts + 1):
        try:
            _download_once(url, dest, progress, label)
            return
        except OSError as exc:
            if attempt == attempts:
                raise
            progress(f"La descarga de {label} se cortó ({exc}); reintento {attempt + 1} de {attempts}…")
            time.sleep(2 * attempt)


def _download_once(url: str, dest: Path, progress: Progress, label: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as fh:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        last_pct = -10
        while True:
            chunk = resp.read(256 * 1024)
            if not chunk:
                break
            fh.write(chunk)
            done += len(chunk)
            if total:
                pct = done * 100 // total
                if pct >= last_pct + 10:
                    last_pct = pct
                    progress(f"Descargando {label}… {pct} %")
    tmp.replace(dest)


def _fetch_text(url: str) -> Optional[str]:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=_UA), timeout=30) as resp:
            return resp.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _verify(path: Path, sums_text: Optional[str], filename: str, progress: Progress) -> None:
    """Verifica el SHA-256 si el proyecto publica la suma; si no, lo avisa."""
    if not sums_text:
        progress(f"Aviso: no se pudo obtener la suma SHA-256 de {filename}.")
        return
    hex64 = re.compile(r"\b([0-9a-fA-F]{64})\b")
    expected = None
    for line in sums_text.splitlines():
        m = hex64.search(line)
        if m and filename in line:
            expected = m.group(1).lower()
            break
    if expected is None:
        # Archivo de suma dedicado a un solo fichero (cualquier formato).
        every = hex64.findall(sums_text)
        if len(every) == 1:
            expected = every[0].lower()
    if expected is None:
        progress(f"Aviso: la suma SHA-256 de {filename} no está en el listado.")
        return
    if _sha256(path) != expected:
        path.unlink(missing_ok=True)
        raise BootstrapError(f"La suma SHA-256 de {filename} no coincide; descarga descartada.")
    progress(f"{filename}: suma SHA-256 verificada.")


def install_ytdlp(progress: Progress) -> None:
    dest = paths.ytdlp_exe()
    tmp = dest.with_name("yt-dlp.download.exe")
    _download(YTDLP_BASE + "yt-dlp.exe", tmp, progress, "yt-dlp (nightly)")
    _verify(tmp, _fetch_text(YTDLP_BASE + "SHA2-256SUMS"), "yt-dlp.exe", progress)
    tmp.replace(dest)


def install_deno(progress: Progress) -> None:
    work = paths.app_home() / "descargas"
    archive = work / DENO_ASSET
    _download(DENO_BASE + DENO_ASSET, archive, progress, "deno")
    _verify(archive, _fetch_text(DENO_BASE + DENO_ASSET + ".sha256sum"), DENO_ASSET, progress)
    with zipfile.ZipFile(archive) as zf:
        member = next((n for n in zf.namelist() if n.lower().endswith("deno.exe")), None)
        if member is None:
            raise BootstrapError("El zip de deno no contiene deno.exe.")
        with zf.open(member) as src, open(paths.deno_exe(), "wb") as dst:
            shutil.copyfileobj(src, dst)
    archive.unlink(missing_ok=True)


def install_mpv(progress: Progress) -> None:
    text = _fetch_text(MPV_RELEASE_API)
    if not text:
        raise BootstrapError("No se pudo consultar la última versión de mpv.")
    assets = json.loads(text).get("assets") or []
    asset = next((a for a in assets if _MPV_ASSET_RE.match(a.get("name", ""))), None)
    if asset is None:
        raise BootstrapError("No se encontró el paquete mpv-x86_64 en la última versión publicada.")
    work = paths.app_home() / "descargas"
    archive = work / asset["name"]
    _download(asset["browser_download_url"], archive, progress, "mpv")
    digest = str(asset.get("digest") or "")
    if digest.startswith("sha256:"):
        if _sha256(archive) != digest.split(":", 1)[1].lower():
            archive.unlink(missing_ok=True)
            raise BootstrapError("La suma SHA-256 de mpv no coincide; descarga descartada.")
        progress("mpv: suma SHA-256 verificada.")
    out = work / "mpv_extraido"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    progress("Extrayendo mpv…")
    try:
        proc = subprocess.run(
            ["tar", "-xf", str(archive), "-C", str(out)],
            capture_output=True, text=True, timeout=300, creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BootstrapError(f"No se pudo ejecutar tar para extraer mpv: {exc}") from exc
    found = next(iter(out.rglob("mpv.exe")), None)
    if proc.returncode != 0 or found is None:
        raise BootstrapError(
            "No se pudo extraer el .7z de mpv con tar. Extrae a mano "
            f"{archive} y copia mpv.exe a {paths.bin_dir()}. Detalle: {proc.stderr.strip()[:200]}"
        )
    shutil.copy2(found, paths.mpv_exe())
    shutil.rmtree(out, ignore_errors=True)
    archive.unlink(missing_ok=True)


def install_all(progress: Progress = print, force: bool = False) -> list[str]:
    """Instala lo que falte (o todo con ``force``). Devuelve la lista de errores."""
    paths.ensure_dirs()
    errors: list[str] = []
    steps = (
        (paths.ytdlp_exe(), install_ytdlp),
        (paths.deno_exe(), install_deno),
        (paths.mpv_exe(), install_mpv),
    )
    for target, installer in steps:
        if target.is_file() and not force:
            progress(f"{target.name}: ya está instalado.")
            continue
        try:
            installer(progress)
            progress(f"{target.name}: instalado en {target.parent}.")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{target.name}: {exc}")
            progress(f"ERROR con {target.name}: {exc}")
    shutil.rmtree(paths.app_home() / "descargas", ignore_errors=True)
    return errors


if __name__ == "__main__":
    sys.exit(1 if install_all(force="--force" in sys.argv[1:]) else 0)
