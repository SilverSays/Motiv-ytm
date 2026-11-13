"""``ExtractorManager``: único punto de contacto con ``yt-dlp.exe``.

Responsabilidades:

* Resolver un ``videoId`` a una URL de audio aplicando la taxonomía de errores
  (fallback a cookies, backoff anti-bot, reintentos de red, vídeo de control).
* Garantizar exclusión entre resoluciones y actualizaciones: en Windows no se
  puede reemplazar ``yt-dlp.exe`` mientras un proceso lo ejecuta. Una
  actualización espera a que terminen las resoluciones (o las mata tras un
  plazo), cierra el proceso precalentado y, mientras dura, las resoluciones
  nuevas quedan en espera.
* Concurrencia acotada: como mucho una resolución de reproducción y una por
  adelantado a la vez. Así un clic nunca espera a un prefetch ni hay que
  matarlo (matar un ``yt-dlp.exe`` deja 29 MB huérfanos en ``%TEMP%``).
* Rapidez: un ``yt-dlp`` precalentado espera la URL por ``stdin`` (ahorra el
  1,3 s de arranque del ejecutable) y ``await_playable`` sondea la URL recién
  emitida hasta que deja de devolver 403.
* Actualizar con respaldo ``.bak``, smoke test, rollback y memoria de versiones
  descartadas.

No depende de Qt: se puede probar con un ``runner`` falso.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from ytm_native.core import config, paths
from ytm_native.core.db import Database
from ytm_native.core.errors import MESSAGES_ES, ErrorKind, classify, cookies_look_invalid, summarize

log = logging.getLogger("ytmn.extractor")

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class ExtractorState(str, Enum):
    IDLE = "IDLE"
    EXTRACTING = "EXTRACTING"
    UPDATING = "UPDATING"


class UpdateStatus(str, Enum):
    UPDATED = "updated"
    UPDATED_UNVERIFIED = "updated_unverified"   # smoke test no concluyente (bloqueo/red)
    UP_TO_DATE = "up_to_date"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"
    SKIPPED_COOLDOWN = "skipped_cooldown"
    SKIPPED_BAD_VERSION = "skipped_bad_version"
    BINARY_MISSING = "binary_missing"


@dataclass
class RunResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    stopped: bool = False
    elapsed: float = 0.0


@dataclass
class Attempt:
    """Una ejecución concreta de yt-dlp para un vídeo."""

    ok: bool
    kind: Optional[ErrorKind]
    used_cookies: bool
    run: RunResult
    info: dict = field(default_factory=dict)
    tier: str = "default"                 # "fast" | "default" | "full"

    @property
    def stderr(self) -> str:
        return self.run.stderr


@dataclass
class ResolveResult:
    video_id: str
    kind: Optional[ErrorKind] = None      # None = éxito
    url: str = ""
    message: str = ""
    used_cookies: bool = False
    format_desc: str = ""
    duration_s: Optional[float] = None
    user_agent: str = ""
    resolved_at: float = 0.0
    expire_ts: float = 0.0
    blocked_until: float = 0.0
    # Se puede cargar ya: viene del cliente rápido (sin espera del anuncio) o
    # una sonda ya recibió 206 de esta URL.
    ready: bool = False

    @property
    def ok(self) -> bool:
        return self.kind is None

    def is_fresh(self, now: Optional[float] = None) -> bool:
        """¿La URL sigue siendo utilizable? (caducidad del prefetch)."""
        now = time.time() if now is None else now
        return (
            self.ok
            and now < self.expire_ts - config.URL_EXPIRY_MARGIN_S
            and now - self.resolved_at < config.PREFETCH_MAX_AGE_S
        )


@dataclass
class UpdateResult:
    status: UpdateStatus
    old_version: str = ""
    new_version: str = ""
    message: str = ""


# ---------------------------------------------------------------------------
# Ejecución de procesos
# ---------------------------------------------------------------------------
def kill_tree(proc: subprocess.Popen) -> None:
    """Mata el proceso y sus hijos.

    ``yt-dlp.exe`` es un ejecutable *onefile*: el proceso visible lanza un hijo
    (y este a ``deno``). Matar solo al padre deja al hijo con el ``.exe``
    bloqueado, que es justo lo que impide actualizarlo.
    """
    if proc.poll() is not None:
        return
    if paths.IS_WINDOWS:
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                timeout=10,
                creationflags=_NO_WINDOW,
            )
        except Exception:  # noqa: BLE001 - último recurso abajo
            pass
    try:
        proc.kill()
    except Exception:  # noqa: BLE001
        pass
    if paths.IS_WINDOWS:
        threading.Thread(
            target=_remove_onefile_leftovers, args=(proc.pid,), name="limpieza-mei", daemon=True
        ).start()


def _remove_onefile_leftovers(pid: int) -> None:
    """Borra la carpeta temporal de un ``yt-dlp.exe`` que hubo que matar.

    El ejecutable *onefile* se desempaqueta en ``%TEMP%\\_MEI<pid><n>`` y solo
    la borra si termina por sí mismo. Se comprueba que sea de yt-dlp antes de
    tocar nada: el prefijo ``_MEI`` lo usa cualquier programa de PyInstaller.
    """
    tmp = Path(tempfile.gettempdir())
    for attempt in range(4):
        time.sleep(0.5 * (attempt + 1))   # los archivos siguen bloqueados un instante
        pending = False
        for n in range(10):
            folder = tmp / f"_MEI{pid}{n}"
            if folder.is_dir() and (folder / "yt_dlp_ejs").is_dir():
                shutil.rmtree(folder, ignore_errors=True)
                pending = pending or folder.exists()
        if not pending:
            return


def _spawn(args: list[str], stdin) -> subprocess.Popen:
    return subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=stdin,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_NO_WINDOW,
    )


def run_process(
    args: list[str],
    timeout: float,
    should_stop: Optional[Callable[[], bool]] = None,
) -> RunResult:
    start = time.monotonic()
    try:
        proc = _spawn(args, subprocess.DEVNULL)
    except OSError as exc:
        return RunResult(returncode=-1, stderr=f"ERROR: no se pudo ejecutar {args[0]}: {exc}")
    return _collect(proc, start, timeout, should_stop)


def _collect(
    proc: subprocess.Popen,
    start: float,
    timeout: float,
    should_stop: Optional[Callable[[], bool]] = None,
) -> RunResult:
    """Espera a que ``proc`` termine, atendiendo a la cancelación y al plazo."""
    timed_out = stopped = False
    while True:
        try:
            out, err = proc.communicate(timeout=0.05)
            break
        except subprocess.TimeoutExpired:
            if should_stop is not None and should_stop():
                stopped = True
            elif time.monotonic() - start > timeout:
                timed_out = True
            else:
                continue
            kill_tree(proc)
            try:
                out, err = proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                out, err = "", ""
            break
    return RunResult(
        returncode=proc.returncode if proc.returncode is not None else -1,
        stdout=out or "",
        stderr=err or "",
        timed_out=timed_out,
        stopped=stopped,
        elapsed=time.monotonic() - start,
    )


def _close_pipes(proc: subprocess.Popen) -> None:
    for pipe in (proc.stdout, proc.stderr):
        try:
            if pipe is not None:
                pipe.close()
        except (OSError, ValueError):
            pass


def _collect_early(
    proc: subprocess.Popen,
    start: float,
    timeout: float,
    should_stop: Optional[Callable[[], bool]] = None,
) -> tuple[RunResult, Optional[threading.Thread]]:
    """Como ``_collect``, pero devuelve en cuanto yt-dlp imprime el JSON.

    El proceso tarda ~0,5 s más en borrar su carpeta temporal y salir; de eso
    se encarga un hilo "segador", que se devuelve para que quien vaya a
    reemplazar el binario pueda esperarlo.
    """
    out_lines: list[str] = []
    err_lines: list[str] = []
    got_json = threading.Event()

    def read_out() -> None:
        try:
            for line in proc.stdout:
                out_lines.append(line)
                if line.lstrip().startswith("{"):
                    got_json.set()
        except (OSError, ValueError):
            pass

    def read_err() -> None:
        try:
            for line in proc.stderr:
                err_lines.append(line)
        except (OSError, ValueError):
            pass

    readers = [
        threading.Thread(target=read_out, name="yt-dlp-stdout", daemon=True),
        threading.Thread(target=read_err, name="yt-dlp-stderr", daemon=True),
    ]
    for reader in readers:
        reader.start()

    timed_out = stopped = False
    while not got_json.wait(0.05):
        if proc.poll() is not None:
            readers[0].join(5)       # terminó: lo que quede en la tubería ya está leído
            break
        if should_stop is not None and should_stop():
            stopped = True
        elif time.monotonic() - start > timeout:
            timed_out = True
        else:
            continue
        kill_tree(proc)
        break

    def finish(limit: float) -> None:
        try:
            proc.wait(timeout=limit)
        except subprocess.TimeoutExpired:
            kill_tree(proc)
        for reader in readers:
            reader.join(5)
        _close_pipes(proc)

    elapsed = time.monotonic() - start
    if got_json.is_set() and not (stopped or timed_out):
        reaper = threading.Thread(target=finish, args=(30,), name="yt-dlp-segador", daemon=True)
        reaper.start()
        return RunResult(0, "".join(out_lines), "".join(err_lines), elapsed=elapsed), reaper
    finish(10)
    return (
        RunResult(
            returncode=proc.returncode if proc.returncode is not None else -1,
            stdout="".join(out_lines),
            stderr="".join(err_lines),
            timed_out=timed_out,
            stopped=stopped,
            elapsed=time.monotonic() - start,
        ),
        None,
    )


class WarmProcess:
    """Un ``yt-dlp`` ya arrancado que espera la URL por ``stdin`` (``-a -``).

    Con ``-a -`` yt-dlp lee todas las URL hasta el fin de ``stdin`` antes de
    empezar: el proceso queda con el ejecutable desempaquetado y los módulos
    cargados, y arranca la extracción en cuanto se le escribe la URL y se
    cierra la tubería. Si la app muere, la tubería se cierra sola y el proceso
    termina sin URL (y borra su carpeta temporal).
    """

    def __init__(self, args: list[str], key: tuple):
        self.key = key                    # (con cookies, nivel): con qué opciones arrancó
        self.born = time.monotonic()
        self.reaper: Optional[threading.Thread] = None
        self.proc = _spawn(args, subprocess.PIPE)

    def usable(self, key: tuple) -> bool:
        return (
            self.proc.poll() is None
            and self.key == key
            and time.monotonic() - self.born < config.WARM_MAX_AGE_S
        )

    def run(
        self, url: str, timeout: float, should_stop: Optional[Callable[[], bool]] = None
    ) -> Optional[RunResult]:
        """Entrega la URL y espera el resultado. ``None`` si el proceso ya no servía."""
        start = time.monotonic()
        stdin, self.proc.stdin = self.proc.stdin, None
        if stdin is None or self.proc.poll() is not None:
            return None
        try:
            stdin.write(url + "\n")
            stdin.close()
        except (OSError, ValueError):
            self.discard(0.0)
            return None
        result, self.reaper = _collect_early(self.proc, start, timeout, should_stop)
        return result

    def discard(self, wait: float = 5.0) -> None:
        """Cierre limpio: sin URL, yt-dlp termina solo. Si no, se mata."""
        stdin, self.proc.stdin = self.proc.stdin, None
        try:
            if stdin is not None:
                stdin.close()
        except (OSError, ValueError):
            pass
        try:
            self.proc.wait(timeout=wait)
        except subprocess.TimeoutExpired:
            kill_tree(self.proc)
        _close_pipes(self.proc)


def probe_url(url: str, user_agent: str = "", timeout: float = 6.0) -> int:
    """Pide el primer byte de la URL. Devuelve el código HTTP (0 = sin red)."""
    headers = {"Range": "bytes=0-0"}
    if user_agent:
        headers["User-Agent"] = user_agent
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as resp:
            return int(resp.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except Exception:  # noqa: BLE001 - red caída, DNS, TLS...
        return 0


def fetch_latest_nightly_tag(timeout: float = 10.0) -> Optional[str]:
    """Etiqueta de la última nightly publicada (best effort; ``None`` si falla)."""
    try:
        req = urllib.request.Request(
            config.NIGHTLY_RELEASE_API,
            headers={"User-Agent": "ytmn-updater", "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return str(json.loads(resp.read().decode("utf-8")).get("tag_name") or "") or None
    except Exception:  # noqa: BLE001
        return None


def _default_wait(seconds: float, should_stop: Callable[[], bool]) -> bool:
    """Espera cancelable. Devuelve False si se interrumpió."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if should_stop():
            return False
        time.sleep(0.05)
    return True


_EXPIRE_RE = re.compile(r"[?&/]expire[=/](\d{9,11})")


def parse_expire(url: str, now: float) -> float:
    m = _EXPIRE_RE.search(url or "")
    if m:
        return float(m.group(1))
    return now + 4 * 3600  # sin dato: supuesto conservador


def parse_version_tuple(text: str) -> tuple[int, ...]:
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(g) for g in m.groups()) if m else ()


# ---------------------------------------------------------------------------
class ExtractorManager:
    def __init__(
        self,
        db: Database,
        *,
        ytdlp: Optional[Path] = None,
        deno: Optional[Path] = None,
        cookies: Optional[Path] = None,
        cache: Optional[Path] = None,
        runner: Callable[..., RunResult] = run_process,
        latest_tag_fetcher: Callable[[], Optional[str]] = fetch_latest_nightly_tag,
        clock: Callable[[], float] = time.time,
        wait: Callable[[float, Callable[[], bool]], bool] = _default_wait,
        on_state: Optional[Callable[[ExtractorState], None]] = None,
        on_notice: Optional[Callable[[str], None]] = None,
        prewarm: bool = False,
        url_probe: Optional[Callable[[str, str], int]] = None,
    ):
        self.db = db
        self.ytdlp = Path(ytdlp) if ytdlp else paths.ytdlp_exe()
        self.deno = Path(deno) if deno else paths.deno_exe()
        self.cookies = Path(cookies) if cookies else paths.cookies_txt()
        self.cache = Path(cache) if cache else paths.cache_dir() / "yt-dlp"
        self.bak = self.ytdlp.with_name("yt-dlp.bak")
        self._runner = runner
        self._latest_tag = latest_tag_fetcher
        self._clock = clock
        self._wait = wait
        self.on_state = on_state
        self.on_notice = on_notice
        self.state = ExtractorState.IDLE
        # Candado. Caben a la vez una resolución de reproducción y una por
        # adelantado; una actualización (o la recuperación del binario) exige
        # estar sola y tiene prioridad sobre las resoluciones en espera.
        self._cond = threading.Condition()
        self._active = 0            # resoluciones en curso
        self._active_play = 0       # de ellas, las de reproducción
        self._exclusive = False     # el binario puede estar siendo reemplazado
        self._update_waiting = 0
        self._kill = threading.Event()
        # Proceso precalentado (solo con el runner real: las pruebas inyectan otro).
        self._prewarm_enabled = prewarm and runner is run_process
        self._url_probe = url_probe
        self._warm: Optional[WarmProcess] = None
        self._warm_lock = threading.Lock()
        self._warm_hold = 0         # >0: no lanzar yt-dlp en espera (se va a reemplazar)
        self._reapers: list[threading.Thread] = []   # yt-dlp que ya respondieron y están saliendo
        self._fast_misses = 0       # fallos seguidos del cliente rápido que el modo por defecto resolvió
        self._closed = False

    # -- utilidades ---------------------------------------------------------
    def _set_state(self, state: ExtractorState) -> None:
        self.state = state
        if self.on_state:
            try:
                self.on_state(state)
            except Exception:  # noqa: BLE001
                log.exception("on_state falló")

    def _notice(self, text: str) -> None:
        log.info("aviso: %s", text)
        if self.on_notice:
            try:
                self.on_notice(text)
            except Exception:  # noqa: BLE001
                log.exception("on_notice falló")

    def playback_mode(self) -> str:
        mode = self.db.get_setting("playback_mode", "hybrid")
        return mode if mode in ("anon", "hybrid", "cookies") else "hybrid"

    def cookies_usable(self) -> bool:
        """Las cookies solo se pasan a yt-dlp si existen Y el usuario aceptó el
        riesgo de forma explícita (yt-dlp advierte que usar una cuenta puede
        acabar en un baneo temporal o permanente)."""
        return self.cookies.is_file() and self.db.get_bool("cookies_risk_accepted")

    def _default_use_cookies(self) -> bool:
        if not self.cookies_usable():
            return False
        mode = self.playback_mode()
        if mode == "cookies":
            return True
        if mode == "hybrid":
            return self._clock() < self.db.get_float("cookies_sticky_until")
        return False

    def _can_fallback_to_cookies(self) -> bool:
        return self.playback_mode() != "anon" and self.cookies_usable()

    # -- candado ------------------------------------------------------------
    def _enter_extract(self, is_cancelled: Callable[[], bool], play: bool = False) -> bool:
        """Una reproducción solo espera a otra reproducción; el resto (prefetch,
        diagnóstico, sonda de la Fase 0) espera a estar solo."""
        with self._cond:
            while (
                self._exclusive
                or self._update_waiting
                or (self._active_play if play else self._active)
            ):
                if is_cancelled():
                    return False
                self._cond.wait(0.1)
            self._active += 1
            if play:
                self._active_play += 1
            return True

    def _leave_extract(self, play: bool = False) -> None:
        with self._cond:
            self._active -= 1
            if play:
                self._active_play -= 1
            idle = self._active == 0 and not self._exclusive
            self._cond.notify_all()
        if idle:
            self._set_state(ExtractorState.IDLE)

    def _enter_update(self, drain_timeout: float) -> None:
        with self._cond:
            self._update_waiting += 1
            try:
                deadline = time.monotonic() + drain_timeout
                while self._active or self._exclusive:
                    if self._active and time.monotonic() > deadline:
                        self._kill.set()  # drenado agotado: matar las resoluciones en curso
                    self._cond.wait(0.1)
                self._exclusive = True
                self._kill.clear()
            finally:
                self._update_waiting -= 1

    def _leave_update(self) -> None:
        with self._cond:
            self._exclusive = False
            self._cond.notify_all()

    # -- proceso precalentado ----------------------------------------------
    def _first_tier(self, use_cookies: bool) -> str:
        """Con qué clientes de YouTube se intenta primero (ver ``config``)."""
        if (
            config.FAST_PLAYER_CLIENT
            and not use_cookies
            and self._clock() >= self.db.get_float("fast_client_off_until")
        ):
            return "fast"
        return "default"

    def prewarm(self) -> None:
        """Deja un yt-dlp arrancado y a la espera de la próxima reproducción."""
        if not self._prewarm_enabled or self._closed or not self.ytdlp.is_file():
            return
        use_cookies = self._default_use_cookies()
        key = (use_cookies, self._first_tier(use_cookies))
        stale = None
        with self._warm_lock:
            if self._warm_hold or self._closed:
                return
            if self._warm is not None:
                if self._warm.usable(key):
                    return
                stale, self._warm = self._warm, None
            try:
                self._warm = WarmProcess(self.warm_args(*key), key)
            except OSError as exc:
                log.warning("no se pudo precalentar yt-dlp: %s", exc)
        if stale is not None:
            self._discard_async(stale)

    def _take_warm(self, key: tuple) -> Optional[WarmProcess]:
        with self._warm_lock:
            warm, self._warm = self._warm, None
        if warm is None:
            return None
        if warm.usable(key):
            return warm
        self._discard_async(warm)
        return None

    @staticmethod
    def _discard_async(warm: WarmProcess) -> None:
        threading.Thread(target=warm.discard, name="yt-dlp-descartado", daemon=True).start()

    def _hold_warm(self) -> None:
        """Antes de reemplazar el binario: ningún yt-dlp nuestro debe tenerlo
        abierto, ni el que espera ni los que aún están saliendo."""
        with self._warm_lock:
            self._warm_hold += 1
            warm, self._warm = self._warm, None
            reapers, self._reapers = self._reapers, []
        if warm is not None:
            warm.discard()
        for reaper in reapers:
            reaper.join(35)

    def _release_warm(self) -> None:
        with self._warm_lock:
            self._warm_hold -= 1

    def shutdown(self) -> None:
        """Al cerrar la app: el yt-dlp en espera termina solo al cerrarle stdin."""
        self._closed = True
        with self._warm_lock:
            warm, self._warm = self._warm, None
        if warm is not None:
            warm.discard(2.0)

    # -- línea de comandos --------------------------------------------------
    def base_args(self, use_cookies: bool, tier: str = "default") -> list[str]:
        """``tier``: "fast" (solo el cliente rápido), "default" (los clientes
        que elija yt-dlp) o "full" (además, sin recortar manifiestos)."""
        args = [
            str(self.ytdlp),
            "--ignore-config",
            "--no-playlist",
            "--no-progress",
            "--color", "never",
            "--socket-timeout", "15",
            "--retries", "2",
            "--extractor-retries", "1",
            "--cache-dir", str(self.cache),
        ]
        if tier == "fast":
            args += [
                "--extractor-args",
                f"youtube:player_client={config.FAST_PLAYER_CLIENT};{config.LEAN_SKIP}",
            ]
        elif tier == "default":
            args += ["--extractor-args", f"youtube:{config.LEAN_SKIP}"]
        if self.deno.is_file():
            # Ruta explícita: no se depende de que yt-dlp encuentre deno solo.
            args += ["--js-runtimes", f"deno:{self.deno}"]
        if use_cookies:
            args += ["--cookies", str(self.cookies)]
        return args

    def warm_args(self, use_cookies: bool, tier: str = "default") -> list[str]:
        return self.base_args(use_cookies, tier) + ["-f", config.AUDIO_FORMAT, "--dump-json", "-a", "-"]

    def _extract_once(
        self,
        video_id: str,
        use_cookies: bool,
        should_stop: Optional[Callable[[], bool]],
        warm: bool = False,
    ) -> Attempt:
        tier = self._first_tier(use_cookies)
        att = self._run_tier(video_id, use_cookies, tier, should_stop, warm)
        if tier == "fast":
            if att.ok:
                self._fast_misses = 0
            elif not att.run.stopped and att.kind is not ErrorKind.NETWORK:
                # El cliente rápido no pudo (o YouTube lo cambió): el veredicto
                # lo dan los clientes por defecto, como antes de existir el atajo.
                log.warning(
                    "cliente %s falló con %s (%s): se repite con los clientes por defecto",
                    config.FAST_PLAYER_CLIENT, video_id, summarize(att.stderr),
                )
                fallback = self._run_tier(video_id, use_cookies, "default", should_stop)
                if fallback.ok:
                    self._note_fast_miss()
                att = fallback
        if (
            not att.ok
            and not att.run.stopped
            and "requested format is not available" in att.stderr.lower()
        ):
            # Pista sin audio por https directo: se repite pidiendo también HLS/DASH.
            att = self._run_tier(video_id, use_cookies, "full", should_stop)
        return att

    def _note_fast_miss(self) -> None:
        self._fast_misses += 1
        if self._fast_misses >= config.FAST_CLIENT_MAX_MISSES:
            self._fast_misses = 0
            self.db.set_setting("fast_client_off_until", self._clock() + config.FAST_CLIENT_PAUSE_S)
            log.warning(
                "el cliente %s falla donde el modo por defecto funciona: en pausa %d h",
                config.FAST_PLAYER_CLIENT, config.FAST_CLIENT_PAUSE_S // 3600,
            )

    def _run_tier(
        self,
        video_id: str,
        use_cookies: bool,
        tier: str,
        should_stop: Optional[Callable[[], bool]],
        warm: bool = False,
    ) -> Attempt:
        url = f"https://www.youtube.com/watch?v={video_id}"
        run = None
        if warm and self._prewarm_enabled:
            process = self._take_warm((use_cookies, tier))
            self.prewarm()   # el relevo arranca ya: estará listo para el siguiente clic
            if process is not None:
                run = process.run(url, config.RESOLVE_TIMEOUT_S, should_stop)
                if process.reaper is not None:
                    with self._warm_lock:
                        self._reapers = [r for r in self._reapers if r.is_alive()] + [process.reaper]
        was_warm = run is not None
        if run is None:
            args = self.base_args(use_cookies, tier) + ["-f", config.AUDIO_FORMAT, "--dump-json", "--", url]
            run = self._runner(args, config.RESOLVE_TIMEOUT_S, should_stop)
        log.info(
            "yt-dlp %s: %.2f s (%s, %s, rc=%s)", video_id, run.elapsed,
            "precalentado" if was_warm else "en frío",
            {"fast": f"cliente {config.FAST_PLAYER_CLIENT}", "default": "clientes por defecto"}.get(tier, "completo"),
            run.returncode,
        )
        if run.stopped:
            return Attempt(False, ErrorKind.CANCELLED, use_cookies, run, tier=tier)
        if run.returncode == 0:
            info = _parse_info(run.stdout)
            if info.get("url"):
                return Attempt(True, None, use_cookies, run, info, tier=tier)
            return Attempt(False, ErrorKind.EXTRACTION, use_cookies, run, tier=tier)
        return Attempt(False, classify(run.stderr, run.timed_out), use_cookies, run, tier=tier)

    # -- resolución ---------------------------------------------------------
    def resolve(
        self,
        video_id: str,
        cancel: Optional[threading.Event] = None,
        purpose: str = "play",
    ) -> ResolveResult:
        """Resuelve ``video_id`` (bloqueante; llamar desde un hilo de trabajo)."""
        cancel = cancel or threading.Event()
        if not self.ytdlp.is_file():
            return self._fail(video_id, ErrorKind.BINARY_MISSING)
        now = self._clock()
        blocked_until = self.db.get_float("bot_blocked_until")
        if now < blocked_until:
            return self._fail(video_id, ErrorKind.BOT_BLOCK, blocked_until=blocked_until)
        if (
            purpose == "prefetch"
            and self.db.resolves_since(now - 3600) >= config.MAX_RESOLVES_PER_HOUR
        ):
            return self._fail(video_id, ErrorKind.LOCAL_RATE_LIMIT)

        play = purpose == "play"
        result = self._fail(video_id, ErrorKind.CANCELLED)
        for _ in range(3):
            if not self._enter_extract(cancel.is_set, play):
                return self._fail(video_id, ErrorKind.CANCELLED)
            try:
                self._set_state(ExtractorState.EXTRACTING)
                result = self._resolve_locked(video_id, cancel, warm=play)
            finally:
                self._leave_extract(play)
            # PREEMPTED: una actualización mató el proceso. Se vuelve a la cola
            # de espera y se reintenta cuando la actualización termine.
            if result.kind is not ErrorKind.PREEMPTED:
                break
        return result

    def await_playable(self, res: ResolveResult, cancel: Optional[threading.Event] = None) -> bool:
        """Espera (bloqueante, cancelable) a que una URL recién emitida responda.

        Durante unos segundos la URL devuelve 403. En vez de una espera fija y
        de dejar que mpv falle y reintente, se pide un byte hasta recibir 206.
        Devuelve False si no se pudo comprobar: entonces decide mpv.
        """
        if self._url_probe is None or not res.ok or res.ready:
            return res.ready
        stop = cancel.is_set if cancel is not None else (lambda: False)
        first = config.URL_PROBE_FIRST_DELAY_S - (self._clock() - res.resolved_at)
        if first > 0 and not self._wait(first, stop):
            return False
        started = time.monotonic()
        while time.monotonic() - started < config.URL_PROBE_TIMEOUT_S:
            status = self._url_probe(res.url, res.user_agent)
            if status in (200, 206):
                res.ready = True
                log.info(
                    "URL de %s lista %.2f s después de resolver", res.video_id,
                    self._clock() - res.resolved_at,
                )
                return True
            if status != 403:
                log.info("sonda de %s: HTTP %s; se deja la carga a mpv", res.video_id, status)
                return False
            if not self._wait(config.URL_PROBE_INTERVAL_S, stop):
                return False
        return False

    def _fail(
        self,
        video_id: str,
        kind: ErrorKind,
        message: str = "",
        used_cookies: bool = False,
        blocked_until: float = 0.0,
    ) -> ResolveResult:
        return ResolveResult(
            video_id=video_id,
            kind=kind,
            message=message or MESSAGES_ES.get(kind, kind.value),
            used_cookies=used_cookies,
            blocked_until=blocked_until,
        )

    def _stopped_result(self, video_id: str, cancel: threading.Event) -> ResolveResult:
        if self._kill.is_set() and not cancel.is_set():
            return self._fail(video_id, ErrorKind.PREEMPTED)
        return self._fail(video_id, ErrorKind.CANCELLED)

    def _resolve_locked(
        self, video_id: str, cancel: threading.Event, warm: bool = False
    ) -> ResolveResult:
        def stop() -> bool:
            return cancel.is_set() or self._kill.is_set()

        use_cookies = self._default_use_cookies()
        net_retries = unknown_retries = 0
        login_retry_done = False
        recovery_tried = False

        while True:
            att = self._extract_once(video_id, use_cookies, stop, warm=warm)
            if att.run.stopped:
                return self._stopped_result(video_id, cancel)
            self._log_attempt(video_id, att)
            if att.ok:
                self._on_success()
                return self._success(video_id, att)

            kind = att.kind or ErrorKind.UNKNOWN
            detail = summarize(att.stderr)
            log.warning("resolver %s falló (%s, cookies=%s): %s", video_id, kind.value, use_cookies, detail)
            if use_cookies and cookies_look_invalid(att.stderr):
                self._notice("Las cookies de la cuenta ya no son válidas: renueva cookies.txt.")

            # 1) Bloqueo anti-bot: nunca se culpa al binario ni a la pista.
            if kind is ErrorKind.BOT_BLOCK:
                if not use_cookies and self._can_fallback_to_cookies():
                    self._stick_to_cookies()
                    use_cookies = True
                    continue
                return self._enter_backoff(video_id, use_cookies)

            # 2) Red: hasta N reintentos con espera.
            if kind is ErrorKind.NETWORK:
                if net_retries < config.NETWORK_RETRIES:
                    net_retries += 1
                    if not self._wait(config.NETWORK_RETRY_WAIT_S, stop):
                        return self._stopped_result(video_id, cancel)
                    continue
                return self._fail(video_id, kind, used_cookies=use_cookies)

            if kind is ErrorKind.RUNTIME_MISSING:
                return self._fail(video_id, kind, used_cookies=use_cookies)

            # 3) Restricción de edad / requiere sesión: un reintento con cookies.
            if kind is ErrorKind.NEEDS_LOGIN:
                if not use_cookies and self._can_fallback_to_cookies() and not login_retry_done:
                    login_retry_done = True
                    use_cookies = True
                    continue
                return self._fail(video_id, kind, message=detail, used_cookies=use_cookies)

            # 4) No disponible: decide quien llama (marca con fecha, no permanente).
            if kind is ErrorKind.UNAVAILABLE:
                return self._fail(video_id, kind, message=detail, used_cookies=use_cookies)

            # 5) Extracción / desconocido.
            if kind is ErrorKind.UNKNOWN and unknown_retries < config.UNKNOWN_RETRIES:
                unknown_retries += 1
                continue
            if recovery_tried:
                return self._fail(video_id, kind, message=detail, used_cookies=use_cookies)

            control = self._extract_once(config.CONTROL_VIDEO_ID, use_cookies, stop)
            if control.run.stopped:
                return self._stopped_result(video_id, cancel)
            if control.ok:
                # El binario funciona: el problema es de esta pista.
                return self._fail(
                    video_id, kind,
                    message=f"{detail} (el vídeo de control sí funciona)",
                    used_cookies=use_cookies,
                )
            ckind = control.kind or ErrorKind.UNKNOWN
            if ckind is ErrorKind.BOT_BLOCK:
                if not use_cookies and self._can_fallback_to_cookies():
                    self._stick_to_cookies()
                    use_cookies = True
                    continue
                return self._enter_backoff(video_id, use_cookies)
            if ckind in (ErrorKind.NETWORK, ErrorKind.RUNTIME_MISSING):
                return self._fail(video_id, ckind, used_cookies=use_cookies)

            # El vídeo de control también falla por extracción: el binario es
            # el sospechoso. Rollback de una actualización sin verificar, o
            # Flujo de Actualización Controlada (si el cooldown lo permite).
            recovery_tried = True
            if self._recover_binary():
                continue
            return self._fail(
                video_id, ErrorKind.EXTRACTION,
                message=f"{detail} (el vídeo de control también falla)",
                used_cookies=use_cookies,
            )

    def _stick_to_cookies(self) -> None:
        until = self._clock() + config.COOKIES_STICKY_S
        self.db.set_setting("cookies_sticky_until", until)
        self._notice(
            "Bloqueo anti-bot en modo anónimo: se usarán las cookies de la cuenta "
            f"secundaria durante {config.COOKIES_STICKY_S // 3600} h."
        )

    def _enter_backoff(self, video_id: str, used_cookies: bool) -> ResolveResult:
        steps = config.BOT_BACKOFF_STEPS_S
        level = max(0, min(self.db.get_int("bot_backoff_level"), len(steps) - 1))
        until = self._clock() + steps[level]
        self.db.set_setting("bot_blocked_until", until)
        self.db.set_setting("bot_backoff_level", min(level + 1, len(steps) - 1))
        hint = (
            "Revisa el estado de cookies.txt."
            if used_cookies
            else "No hay cookies utilizables para el fallback."
        )
        return self._fail(
            video_id, ErrorKind.BOT_BLOCK,
            message=f"Bloqueo anti-bot. Cola en pausa {steps[level] // 60} min. {hint}",
            used_cookies=used_cookies,
            blocked_until=until,
        )

    def _on_success(self) -> None:
        if self.db.get_int("bot_backoff_level") or self.db.get_float("bot_blocked_until"):
            self.db.set_setting("bot_backoff_level", 0)
            self.db.set_setting("bot_blocked_until", 0)
        # Una resolución real exitosa verifica una actualización pendiente.
        if self.db.get_setting("update_unverified"):
            self.db.set_setting("update_unverified", "")

    def _success(self, video_id: str, att: Attempt) -> ResolveResult:
        info = att.info
        now = self._clock()
        url = info["url"]
        abr = info.get("abr")
        fmt = " ".join(
            str(x) for x in (
                info.get("format_id"), info.get("acodec"),
                f"{abr:.0f}k" if isinstance(abr, (int, float)) else None,
            ) if x
        )
        headers = info.get("http_headers") or {}
        return ResolveResult(
            video_id=video_id,
            url=url,
            used_cookies=att.used_cookies,
            format_desc=fmt,
            duration_s=info.get("duration"),
            user_agent=headers.get("User-Agent", ""),
            resolved_at=now,
            expire_ts=parse_expire(url, now),
            # Las URL del cliente rápido no pasan por la espera del anuncio.
            ready=att.tier == "fast",
        )

    def _log_attempt(self, video_id: str, att: Attempt) -> None:
        try:
            self.db.log_resolve(
                video_id,
                "cookies" if att.used_cookies else "anon",
                "ok" if att.ok else (att.kind.value if att.kind else "unknown"),
                att.run.elapsed,
                str(att.info.get("format_id", "")) if att.ok else "",
            )
        except Exception:  # noqa: BLE001
            log.exception("no se pudo registrar la resolución")

    def clear_bot_block(self) -> None:
        """Permite un reintento manual una vez vencido (o descartado) el backoff."""
        self.db.set_setting("bot_blocked_until", 0)

    # -- sonda para la Fase 0 ----------------------------------------------
    def probe_once(self, video_id: str, use_cookies: bool) -> Attempt:
        """Una ejecución sin política de reintentos (script de la Fase 0)."""
        self._enter_extract(lambda: False)
        try:
            self._set_state(ExtractorState.EXTRACTING)
            return self._extract_once(video_id, use_cookies, None)
        finally:
            self._leave_extract()

    # -- diagnóstico --------------------------------------------------------
    def ytdlp_version(self) -> str:
        if not self.ytdlp.is_file():
            return ""
        run = self._runner([str(self.ytdlp), "--version"], 30, None)
        return run.stdout.strip().splitlines()[0].strip() if run.returncode == 0 and run.stdout.strip() else ""

    def deno_status(self) -> tuple[bool, str, str]:
        """(ok, versión, mensaje). Detecta deno ausente o anterior a la mínima."""
        if not self.deno.is_file():
            return False, "", f"No se encontró {self.deno.name} en la carpeta bin."
        run = self._runner([str(self.deno), "--version"], 30, None)
        version = parse_version_tuple(run.stdout)
        text = ".".join(map(str, version))
        if run.returncode != 0 or not version:
            return False, "", "deno no se pudo ejecutar."
        if version < config.MIN_DENO_VERSION:
            minimum = ".".join(map(str, config.MIN_DENO_VERSION))
            return False, text, f"deno {text} es anterior a la versión mínima {minimum}."
        return True, text, ""

    def diagnostics(self) -> dict:
        self._enter_extract(lambda: False)
        try:
            ok, deno_version, deno_msg = self.deno_status()
            return {
                "ytdlp_present": self.ytdlp.is_file(),
                "ytdlp_version": self.ytdlp_version(),
                "deno_ok": ok,
                "deno_version": deno_version,
                "deno_message": deno_msg,
                "cookies_present": self.cookies.is_file(),
                "cookies_usable": self.cookies_usable(),
                "playback_mode": self.playback_mode(),
            }
        finally:
            self._leave_extract()

    # -- actualización ------------------------------------------------------
    def update(self, reason: str = "proactive", force: bool = False) -> UpdateResult:
        """Actualiza yt-dlp respetando el cooldown unificado.

        ``proactive``: al arrancar, solo si pasaron 24 h.
        ``reactive``: tras fallos, solo si pasaron 6 h (incluida la de arranque).
        ``manual``/``force``: sin cooldown.
        """
        if not self.ytdlp.is_file():
            return UpdateResult(UpdateStatus.BINARY_MISSING, message="Falta yt-dlp en bin.")
        if not force and reason != "manual":
            cooldown = (
                config.PROACTIVE_UPDATE_COOLDOWN_S
                if reason == "proactive"
                else config.REACTIVE_UPDATE_COOLDOWN_S
            )
            if self._clock() - self.db.get_float("last_update_timestamp") < cooldown:
                return UpdateResult(UpdateStatus.SKIPPED_COOLDOWN)
        self._enter_update(config.UPDATE_DRAIN_TIMEOUT_S)
        self._hold_warm()
        try:
            self._set_state(ExtractorState.UPDATING)
            return self._update_locked(reason)
        finally:
            self._release_warm()
            self._set_state(ExtractorState.IDLE)
            self._leave_update()
            self.prewarm()

    def _recover_binary(self) -> bool:
        """Llamado desde una resolución cuando el binario es sospechoso.
        Devuelve True si el binario cambió y vale la pena reintentar."""
        with self._cond:
            # Reemplazar el .exe exige que no lo ejecute nadie más: si hay otra
            # resolución en curso se deja para el siguiente fallo.
            if self._active != 1 or self._exclusive:
                return False
            self._exclusive = True
        self._hold_warm()
        try:
            return self._recover_binary_exclusive()
        finally:
            self._release_warm()
            with self._cond:
                self._exclusive = False
                self._cond.notify_all()

    def _recover_binary_exclusive(self) -> bool:
        unverified = self.db.get_setting("update_unverified") or ""
        if unverified and self.bak.is_file():
            if self._restore_backup():
                self.db.add_bad_version(unverified, "falló tras una actualización sin verificar")
                self.db.set_setting("update_unverified", "")
                self._notice(f"yt-dlp {unverified} falla: se restauró la versión anterior.")
                return True
            return False
        if self._clock() - self.db.get_float("last_update_timestamp") < config.REACTIVE_UPDATE_COOLDOWN_S:
            return False
        result = self._update_locked("reactive")
        return result.status in (UpdateStatus.UPDATED, UpdateStatus.UPDATED_UNVERIFIED)

    def _update_locked(self, reason: str) -> UpdateResult:
        old_version = self.ytdlp_version()
        self.db.set_setting("last_update_timestamp", self._clock())

        # Versión descartada: no reinstalarla; esperar a una más nueva.
        latest = self._latest_tag()
        if latest:
            if self.db.is_bad_version(latest):
                return UpdateResult(
                    UpdateStatus.SKIPPED_BAD_VERSION, old_version, latest,
                    f"La última versión ({latest}) ya se descartó; se espera una más nueva.",
                )
            if latest == old_version:
                return UpdateResult(UpdateStatus.UP_TO_DATE, old_version, old_version)

        try:
            shutil.copy2(self.ytdlp, self.bak)
        except OSError as exc:
            return UpdateResult(UpdateStatus.FAILED, old_version, message=f"No se pudo crear el respaldo: {exc}")

        run = self._runner([str(self.ytdlp), "--ignore-config", "-U"], config.UPDATE_TIMEOUT_S, None)
        if run.returncode not in (0, 100):
            self._restore_backup()
            return UpdateResult(
                UpdateStatus.FAILED, old_version,
                message=f"yt-dlp -U falló: {summarize(run.stderr) or run.returncode}",
            )

        new_version = self.ytdlp_version()
        if not new_version:
            self._restore_backup()
            return UpdateResult(UpdateStatus.ROLLED_BACK, old_version, message="El binario nuevo no arranca.")
        if new_version == old_version:
            return UpdateResult(UpdateStatus.UP_TO_DATE, old_version, new_version)
        if self.db.is_bad_version(new_version):
            self._restore_backup()
            return UpdateResult(UpdateStatus.SKIPPED_BAD_VERSION, old_version, new_version)

        # Smoke test. Solo un fallo de EXTRACCIÓN justifica el rollback: un
        # bloqueo anti-bot o un fallo de red no dicen nada del binario.
        smoke = self._extract_once(config.CONTROL_VIDEO_ID, self._default_use_cookies(), None)
        if smoke.ok:
            self.db.set_setting("update_unverified", "")
            self._notice(f"yt-dlp actualizado a {new_version}.")
            return UpdateResult(UpdateStatus.UPDATED, old_version, new_version)

        skind = smoke.kind or ErrorKind.UNKNOWN
        if skind in (ErrorKind.BOT_BLOCK, ErrorKind.NETWORK):
            # No concluyente: se conserva el binario nuevo y el .bak. Si la
            # próxima resolución real falla por extracción, se hace rollback.
            self.db.set_setting("update_unverified", new_version)
            return UpdateResult(
                UpdateStatus.UPDATED_UNVERIFIED, old_version, new_version,
                f"Actualizado a {new_version}; la verificación quedó pendiente ({MESSAGES_ES[skind]}).",
            )

        self._restore_backup()
        if skind is ErrorKind.RUNTIME_MISSING:
            # No es culpa de la versión: puede necesitar un deno más nuevo.
            return UpdateResult(
                UpdateStatus.ROLLED_BACK, old_version, new_version,
                "La versión nueva no encontró un runtime JS compatible; revisa deno.",
            )
        self.db.add_bad_version(new_version, f"smoke test: {summarize(smoke.stderr)}")
        self._notice(f"yt-dlp {new_version} no pasó la prueba: se restauró {old_version}.")
        return UpdateResult(
            UpdateStatus.ROLLED_BACK, old_version, new_version,
            f"La versión {new_version} no pasó el smoke test; se restauró {old_version}.",
        )

    def _restore_backup(self) -> bool:
        if not self.bak.is_file():
            return False
        tmp = self.ytdlp.with_name(self.ytdlp.name + ".restore")
        for attempt in range(10):
            try:
                shutil.copy2(self.bak, tmp)
                os.replace(tmp, self.ytdlp)
                return True
            except OSError:
                # El .exe puede seguir bloqueado un instante (antivirus, cierre).
                time.sleep(0.3 * (attempt + 1) if attempt < 3 else 1.0)
        log.error("no se pudo restaurar %s", self.bak)
        return False


def _parse_info(stdout: str) -> dict:
    for line in reversed((stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                data = json.loads(line)
            except ValueError:
                continue
            if isinstance(data, dict):
                return data
    return {}
