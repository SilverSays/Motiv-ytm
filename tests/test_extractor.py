import json
import sys
import threading
import time

import pytest

from ytm_native.core import config
from ytm_native.core.errors import ErrorKind
from ytm_native.playback.extractor import ExtractorManager, ExtractorState, RunResult, UpdateStatus

BOT = "ERROR: [youtube] x: Sign in to confirm you're not a bot."
NSIG = "ERROR: [youtube] x: nsig extraction failed"
DNS = "ERROR: Unable to download webpage: getaddrinfo failed"
UNAVAILABLE = "ERROR: [youtube] x: Video unavailable"
AGE = "ERROR: [youtube] x: Sign in to confirm your age"
CONTROL = config.CONTROL_VIDEO_ID


def ok_json(video_id="x"):
    return json.dumps({
        "url": f"https://rr1.googlevideo.com/videoplayback?expire=1999999999&id={video_id}",
        "format_id": "251", "acodec": "opus", "abr": 133.2, "duration": 200,
        "http_headers": {"User-Agent": "UA/1.0"},
    })


class FakeYtdlp:
    """Simula yt-dlp.exe: el archivo en disco contiene su 'versión'."""

    def __init__(self, exe, script=None, next_version="2026.02.02"):
        self.exe = exe
        self.script = script or {}        # (video_id, cookies) -> lista de stderr|"OK"
        self.calls = []                   # (video_id, cookies)
        self.next_version = next_version
        self.update_rc = 0
        self.updates = 0
        self.by_version = {}              # versión -> {video_id: resultado}

    def __call__(self, args, timeout, should_stop=None):
        if "--version" in args:
            if args[0].endswith("deno.exe") or "deno" in args[0]:
                return RunResult(0, stdout="deno 2.9.8 (stable)\n")
            return RunResult(0, stdout=self.exe.read_text() + "\n")
        if "-U" in args:
            self.updates += 1
            if self.update_rc == 0:
                self.exe.write_text(self.next_version)
            return RunResult(self.update_rc, stderr="" if self.update_rc == 0 else "ERROR: update failed")
        video_id = args[-1].split("v=")[-1]
        cookies = "--cookies" in args
        self.calls.append((video_id, cookies))
        version = self.exe.read_text()
        if version in self.by_version and video_id in self.by_version[version]:
            outcome = self.by_version[version][video_id]
        else:
            queue = self.script.get((video_id, cookies)) or self.script.get(video_id)
            outcome = (queue.pop(0) if len(queue) > 1 else queue[0]) if queue else "OK"
        if outcome == "OK":
            return RunResult(0, stdout=ok_json(video_id), elapsed=0.5)
        return RunResult(1, stderr=outcome, elapsed=0.5)


class Clock:
    def __init__(self, now=1_800_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def env(db, tmp_path, monkeypatch):
    # La taxonomía de errores se prueba con los clientes por defecto; el
    # cliente rápido tiene sus propias pruebas más abajo.
    monkeypatch.setattr(config, "FAST_PLAYER_CLIENT", "")
    exe = tmp_path / "bin" / "yt-dlp.exe"
    exe.parent.mkdir()
    exe.write_text("2026.01.01")
    cookies = tmp_path / "cookies.txt"
    fake = FakeYtdlp(exe)
    clock = Clock()
    notices = []
    mgr = ExtractorManager(
        db, ytdlp=exe, deno=tmp_path / "bin" / "deno.exe", cookies=cookies, cache=tmp_path / "cache",
        runner=fake, latest_tag_fetcher=lambda: None, clock=clock,
        wait=lambda seconds, stop: True, on_notice=notices.append,
    )
    # Fuera del cooldown reactivo por defecto.
    db.set_setting("last_update_timestamp", clock.now - 7 * 3600)
    return type("Env", (), dict(mgr=mgr, fake=fake, clock=clock, db=db, exe=exe, cookies=cookies, notices=notices))


def enable_cookies(env):
    env.cookies.write_text("# Netscape HTTP Cookie File\n")
    env.db.set_setting("cookies_risk_accepted", "1")


def test_success_parses_url_and_expiry(env):
    res = env.mgr.resolve("abc")
    assert res.ok and res.url.startswith("https://rr1.googlevideo.com")
    assert res.expire_ts == 1999999999
    assert res.user_agent == "UA/1.0" and "opus" in res.format_desc
    assert not res.used_cookies
    assert env.fake.calls == [("abc", False)]
    assert env.mgr.state is ExtractorState.IDLE


def test_command_line_is_explicit(env, tmp_path):
    (tmp_path / "bin" / "deno.exe").write_text("")
    args = env.mgr.base_args(use_cookies=False)
    assert "--ignore-config" in args
    assert f"deno:{tmp_path / 'bin' / 'deno.exe'}" in args      # ruta explícita a deno
    assert "--cookies" not in args


def test_cookies_never_used_without_explicit_acceptance(env):
    env.cookies.write_text("# Netscape HTTP Cookie File\n")      # existen, pero sin aceptar el riesgo
    env.db.set_setting("playback_mode", "cookies")
    env.fake.script = {"abc": [BOT]}
    res = env.mgr.resolve("abc")
    assert res.kind is ErrorKind.BOT_BLOCK
    assert all(not cookies for _, cookies in env.fake.calls)


def test_bot_block_falls_back_to_cookies_and_sticks(env):
    enable_cookies(env)
    env.fake.script = {("abc", False): [BOT]}
    res = env.mgr.resolve("abc")
    assert res.ok and res.used_cookies
    assert env.fake.calls == [("abc", False), ("abc", True)]
    assert env.fake.updates == 0                                   # nunca se actualiza por un bloqueo
    # Modo pegajoso: la siguiente va directa con cookies...
    env.fake.calls.clear()
    assert env.mgr.resolve("def").used_cookies
    assert env.fake.calls == [("def", True)]
    # ...y pasado el plazo se vuelve a probar el modo anónimo.
    env.clock.now += config.COOKIES_STICKY_S + 1
    env.fake.calls.clear()
    assert not env.mgr.resolve("ghi").used_cookies


def test_bot_block_with_cookies_applies_backoff(env):
    enable_cookies(env)
    env.db.set_setting("playback_mode", "cookies")
    env.fake.script = {"abc": [BOT]}
    first = env.mgr.resolve("abc")
    assert first.kind is ErrorKind.BOT_BLOCK
    assert first.blocked_until == env.clock.now + 60
    # Durante el backoff ni siquiera se lanza yt-dlp.
    calls = len(env.fake.calls)
    again = env.mgr.resolve("abc")
    assert again.kind is ErrorKind.BOT_BLOCK and len(env.fake.calls) == calls
    # Escalado 1 -> 5 -> 15 min.
    env.clock.now += 61
    assert env.mgr.resolve("abc").blocked_until == env.clock.now + 300
    env.clock.now += 301
    assert env.mgr.resolve("abc").blocked_until == env.clock.now + 900
    # Un éxito reinicia el nivel.
    env.clock.now += 901
    env.fake.script = {}
    assert env.mgr.resolve("abc").ok
    assert env.db.get_int("bot_backoff_level") == 0
    assert env.fake.updates == 0


def test_network_error_retries_twice_then_gives_up(env):
    env.fake.script = {"abc": [DNS]}
    res = env.mgr.resolve("abc")
    assert res.kind is ErrorKind.NETWORK
    assert len(env.fake.calls) == 1 + config.NETWORK_RETRIES
    assert env.fake.updates == 0


def test_network_error_recovers_on_retry(env):
    env.fake.script = {"abc": [DNS, "OK"]}
    assert env.mgr.resolve("abc").ok


def test_unavailable_does_not_update_or_retry(env):
    env.fake.script = {"abc": [UNAVAILABLE]}
    res = env.mgr.resolve("abc")
    assert res.kind is ErrorKind.UNAVAILABLE
    assert env.fake.calls == [("abc", False)] and env.fake.updates == 0


def test_age_restriction_retries_once_with_cookies_without_sticking(env):
    enable_cookies(env)
    env.fake.script = {("abc", False): [AGE]}
    res = env.mgr.resolve("abc")
    assert res.ok and res.used_cookies
    assert env.db.get_float("cookies_sticky_until") == 0


def test_extraction_failure_with_working_control_blames_the_track(env):
    env.fake.script = {"abc": [NSIG]}
    res = env.mgr.resolve("abc")
    assert res.kind is ErrorKind.EXTRACTION
    assert ("%s" % CONTROL, False) in env.fake.calls
    assert env.fake.updates == 0


def test_extraction_failure_with_bot_blocked_control_does_not_update(env):
    env.fake.script = {"abc": [NSIG], CONTROL: [BOT]}
    res = env.mgr.resolve("abc")
    assert res.kind is ErrorKind.BOT_BLOCK and env.fake.updates == 0


def test_broken_binary_triggers_controlled_update_and_retry(env):
    env.fake.by_version = {"2026.01.01": {"abc": NSIG, CONTROL: NSIG}}
    res = env.mgr.resolve("abc")
    assert res.ok
    assert env.fake.updates == 1
    assert env.exe.read_text() == "2026.02.02"
    assert env.db.get_float("last_update_timestamp") == env.clock.now


def test_reactive_update_respects_six_hour_cooldown(env):
    env.db.set_setting("last_update_timestamp", env.clock.now - 3600)
    env.fake.by_version = {"2026.01.01": {"abc": NSIG, CONTROL: NSIG}}
    res = env.mgr.resolve("abc")
    assert res.kind is ErrorKind.EXTRACTION and env.fake.updates == 0


def test_unknown_error_has_bounded_retries(env):
    env.fake.script = {"abc": ["ERROR: mensaje nuevo que no conocemos"]}
    res = env.mgr.resolve("abc")
    assert res.kind is ErrorKind.UNKNOWN
    track_calls = [c for c in env.fake.calls if c[0] == "abc"]
    assert len(track_calls) == 1 + config.UNKNOWN_RETRIES


def test_proactive_update_cooldown(env):
    env.db.set_setting("last_update_timestamp", env.clock.now - 23 * 3600)
    assert env.mgr.update("proactive").status is UpdateStatus.SKIPPED_COOLDOWN
    env.db.set_setting("last_update_timestamp", env.clock.now - 25 * 3600)
    result = env.mgr.update("proactive")
    assert result.status is UpdateStatus.UPDATED
    assert (result.old_version, result.new_version) == ("2026.01.01", "2026.02.02")
    assert env.mgr.bak.read_text() == "2026.01.01"


def test_update_rolls_back_on_extraction_smoke_failure_and_remembers_version(env):
    env.fake.by_version = {"2026.02.02": {CONTROL: NSIG}}
    result = env.mgr.update("manual")
    assert result.status is UpdateStatus.ROLLED_BACK
    assert env.exe.read_text() == "2026.01.01"
    assert env.db.is_bad_version("2026.02.02")
    # La siguiente comprobación no reinstala la versión descartada.
    env.mgr._latest_tag = lambda: "2026.02.02"
    again = env.mgr.update("manual")
    assert again.status is UpdateStatus.SKIPPED_BAD_VERSION
    assert env.fake.updates == 1
    # Cuando sale una más nueva, sí se instala.
    env.mgr._latest_tag = lambda: "2026.03.03"
    env.fake.next_version = "2026.03.03"
    assert env.mgr.update("manual").status is UpdateStatus.UPDATED
    assert env.exe.read_text() == "2026.03.03"


def test_bad_version_is_caught_even_without_release_info(env):
    env.db.add_bad_version("2026.02.02", "antes")
    result = env.mgr.update("manual")          # latest_tag_fetcher devuelve None
    assert result.status is UpdateStatus.SKIPPED_BAD_VERSION
    assert env.exe.read_text() == "2026.01.01"


def test_bot_block_during_smoke_test_does_not_roll_back(env):
    env.fake.by_version = {"2026.02.02": {CONTROL: BOT}}
    result = env.mgr.update("manual")
    assert result.status is UpdateStatus.UPDATED_UNVERIFIED
    assert env.exe.read_text() == "2026.02.02"            # el binario nuevo se conserva
    assert not env.db.is_bad_version("2026.02.02")
    assert env.db.get_setting("update_unverified") == "2026.02.02"
    # Una resolución real exitosa lo da por verificado.
    assert env.mgr.resolve("abc").ok
    assert env.db.get_setting("update_unverified") == ""


def test_unverified_update_is_rolled_back_when_it_later_fails(env):
    env.fake.by_version = {"2026.02.02": {CONTROL: BOT}}
    assert env.mgr.update("manual").status is UpdateStatus.UPDATED_UNVERIFIED
    # Ya sin bloqueo, resulta que la versión nueva sí estaba rota.
    env.fake.by_version = {"2026.02.02": {CONTROL: NSIG, "abc": NSIG}}
    res = env.mgr.resolve("abc")                 # dentro del cooldown: no puede actualizar, pero sí restaurar
    assert res.ok
    assert env.exe.read_text() == "2026.01.01"
    assert env.db.is_bad_version("2026.02.02")
    assert env.fake.updates == 1


def test_failed_update_command_restores_backup(env):
    env.fake.update_rc = 1
    result = env.mgr.update("manual")
    assert result.status is UpdateStatus.FAILED
    assert env.exe.read_text() == "2026.01.01"


def test_up_to_date_when_release_matches(env):
    env.mgr._latest_tag = lambda: "2026.01.01"
    assert env.mgr.update("manual").status is UpdateStatus.UP_TO_DATE
    assert env.fake.updates == 0


def test_prefetch_respects_local_hourly_limit(env):
    for _ in range(config.MAX_RESOLVES_PER_HOUR):
        env.db.log_resolve("v", "anon", "ok", 0.1)
    env.clock.now = time.time()
    assert env.mgr.resolve("abc", purpose="prefetch").kind is ErrorKind.LOCAL_RATE_LIMIT
    assert env.mgr.resolve("abc", purpose="play").ok


def test_deno_status_detects_missing_and_old(env, tmp_path):
    ok, _version, message = env.mgr.deno_status()
    assert not ok and "deno" in message
    (tmp_path / "bin" / "deno.exe").write_text("")
    assert env.mgr.deno_status()[0]
    original = env.fake.__call__
    env.mgr._runner = lambda args, timeout, stop=None: RunResult(0, stdout="deno 2.2.9\n")
    ok, version, message = env.mgr.deno_status()
    assert not ok and version == "2.2.9" and "2.3.0" in message
    env.mgr._runner = original


def test_update_drains_running_resolution_then_requeues_it(env, monkeypatch):
    """La actualización espera/mata la resolución en curso y esta se reintenta después."""
    monkeypatch.setattr(config, "UPDATE_DRAIN_TIMEOUT_S", 0.2)
    started = threading.Event()
    order = []
    inner = env.fake

    def runner(args, timeout, should_stop=None):
        if "--dump-json" in args and "slow" in args[-1] and not order.count("updated"):
            started.set()
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if should_stop and should_stop():
                    order.append("killed")
                    return RunResult(-9, stopped=True)
                time.sleep(0.01)
            return RunResult(1, stderr="ERROR: el test no fue interrumpido")
        result = inner(args, timeout, should_stop)
        if "-U" in args:
            order.append("updated")
        return result

    env.mgr._runner = runner
    states = []
    env.mgr.on_state = states.append
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("res", env.mgr.resolve("slow")))
    t.start()
    assert started.wait(5)
    result = env.mgr.update("manual")
    t.join(10)
    assert result.status is UpdateStatus.UPDATED
    assert order[:2] == ["killed", "updated"]
    assert out["res"].ok                               # se reintentó tras la actualización
    assert ExtractorState.UPDATING in states


def test_cancel_while_waiting_for_update(env):
    """Un clic que ya no interesa no se queda esperando a la actualización."""
    env.mgr._enter_update(0)                            # simula una actualización en curso
    cancel = threading.Event()
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("res", env.mgr.resolve("abc", cancel)))
    t.start()
    time.sleep(0.2)
    assert t.is_alive() and env.fake.calls == []       # en cola de espera, sin lanzar yt-dlp
    cancel.set()
    t.join(5)
    env.mgr._leave_update()
    assert out["res"].kind is ErrorKind.CANCELLED


# -- rapidez: recorte de manifiestos, concurrencia, sonda y precalentado ------
LEAN = f"youtube:{config.LEAN_SKIP}"
FAST = f"youtube:player_client=visionos;{config.LEAN_SKIP}"


def tier_of(args):
    return "fast" if FAST in args else "default" if LEAN in args else "full"


def test_lean_args_skip_manifests_and_fall_back_when_no_direct_format(env):
    assert LEAN in env.mgr.base_args(use_cookies=False)
    seen = []
    inner = env.fake

    def runner(args, timeout, should_stop=None):
        seen.append(tier_of(args))
        if seen[-1] != "full":
            return RunResult(1, stderr="ERROR: [youtube] abc: Requested format is not available")
        return inner(args, timeout, should_stop)

    env.mgr._runner = runner
    assert env.mgr.resolve("abc").ok                    # solo HLS: se repite sin el recorte
    assert seen == ["default", "full"]


@pytest.fixture
def fast(env, monkeypatch):
    """El mismo entorno con el cliente rápido activo y registro del nivel usado."""
    monkeypatch.setattr(config, "FAST_PLAYER_CLIENT", "visionos")
    env.tiers = []
    env.fast_fails = None                               # stderr con el que falla el cliente rápido
    inner = env.fake

    def runner(args, timeout, should_stop=None):
        if "--dump-json" in args:
            env.tiers.append(tier_of(args))
            if env.tiers[-1] == "fast" and env.fast_fails:
                return RunResult(1, stderr=env.fast_fails, elapsed=0.3)
        return inner(args, timeout, should_stop)

    env.mgr._runner = runner
    return env


def test_fast_client_is_tried_first_and_its_url_is_ready(fast):
    res = fast.mgr.resolve("abc")
    assert res.ok and res.ready                         # sin espera del anuncio ni sonda
    assert fast.tiers == ["fast"]
    probe, calls = make_probe([403])
    fast.mgr._url_probe = probe
    assert fast.mgr.await_playable(res) and calls == []


def test_fast_client_failure_defers_to_the_default_clients(fast):
    fast.fast_fails = UNAVAILABLE                       # p. ej. un vídeo vetado en ese cliente
    res = fast.mgr.resolve("abc")
    assert res.ok and not res.ready                     # la URL por defecto sí necesita madurar
    assert fast.tiers == ["fast", "default"]
    # Si los clientes por defecto tampoco pueden, manda su veredicto.
    fast.tiers.clear()
    fast.fake.script = {"gone": [UNAVAILABLE]}
    assert fast.mgr.resolve("gone").kind is ErrorKind.UNAVAILABLE
    assert fast.tiers == ["fast", "default"]


def test_fast_client_is_paused_after_repeated_misses(fast):
    fast.fast_fails = NSIG
    for n in range(config.FAST_CLIENT_MAX_MISSES):
        assert fast.mgr.resolve(f"v{n}").ok
    assert fast.db.get_float("fast_client_off_until") == fast.clock.now + config.FAST_CLIENT_PAUSE_S
    fast.tiers.clear()
    assert fast.mgr.resolve("next").ok
    assert fast.tiers == ["default"]                    # ya no paga el intento fallido
    fast.clock.now += config.FAST_CLIENT_PAUSE_S + 1
    fast.fast_fails = None
    fast.tiers.clear()
    assert fast.mgr.resolve("later").ready              # pasada la pausa se vuelve a probar
    assert fast.tiers == ["fast"]


def test_fast_client_is_not_blamed_when_nothing_works(fast):
    fast.fast_fails = NSIG
    fast.fake.script = {"gone": [UNAVAILABLE]}
    for _ in range(config.FAST_CLIENT_MAX_MISSES + 1):
        fast.mgr.resolve("gone")
    assert fast.db.get_float("fast_client_off_until") == 0


def test_fast_client_network_error_does_not_switch_clients(fast):
    fast.fast_fails = DNS
    res = fast.mgr.resolve("abc")
    assert res.kind is ErrorKind.NETWORK
    assert set(fast.tiers) == {"fast"} and len(fast.tiers) == 1 + config.NETWORK_RETRIES


def test_fast_client_is_not_used_with_cookies(fast):
    enable_cookies(fast)
    fast.db.set_setting("playback_mode", "cookies")
    res = fast.mgr.resolve("abc")
    assert res.ok and res.used_cookies and fast.tiers == ["default"]


class BlockingRunner:
    """Detiene las resoluciones de los vídeos indicados hasta que se liberen."""

    def __init__(self, inner, blocked):
        self.inner = inner
        self.gates = {vid: threading.Event() for vid in blocked}
        self.started = {vid: threading.Event() for vid in blocked}

    def __call__(self, args, timeout, should_stop=None):
        vid = args[-1].split("v=")[-1]
        if "--dump-json" in args and vid in self.gates:
            self.started[vid].set()
            self.gates[vid].wait(10)
        return self.inner(args, timeout, should_stop)


def in_thread(fn):
    out = {}
    thread = threading.Thread(target=lambda: out.setdefault("res", fn()))
    thread.start()
    return thread, out


def test_play_runs_alongside_a_prefetch_in_flight(env):
    """Un clic no espera al prefetch (ni hay que matarlo)."""
    runner = BlockingRunner(env.fake, ["pre"])
    env.mgr._runner = runner
    t, out = in_thread(lambda: env.mgr.resolve("pre", purpose="prefetch"))
    assert runner.started["pre"].wait(5)
    assert env.mgr.resolve("click", purpose="play").ok  # termina con el prefetch aún en curso
    assert t.is_alive()
    runner.gates["pre"].set()
    t.join(5)
    assert out["res"].ok
    assert env.mgr.state is ExtractorState.IDLE


def test_prefetch_waits_for_a_play_in_flight(env):
    runner = BlockingRunner(env.fake, ["click"])
    env.mgr._runner = runner
    t_play, play = in_thread(lambda: env.mgr.resolve("click", purpose="play"))
    assert runner.started["click"].wait(5)
    t_pre, pre = in_thread(lambda: env.mgr.resolve("pre", purpose="prefetch"))
    time.sleep(0.3)
    assert ("pre", False) not in env.fake.calls         # en espera: no compite con el clic
    runner.gates["click"].set()
    t_play.join(5)
    t_pre.join(5)
    assert play["res"].ok and pre["res"].ok


def test_update_waits_for_every_resolution_in_flight(env, monkeypatch):
    monkeypatch.setattr(config, "UPDATE_DRAIN_TIMEOUT_S", 30)
    runner = BlockingRunner(env.fake, ["pre", "click"])
    env.mgr._runner = runner
    t_pre, _ = in_thread(lambda: env.mgr.resolve("pre", purpose="prefetch"))
    assert runner.started["pre"].wait(5)
    t_play, _ = in_thread(lambda: env.mgr.resolve("click", purpose="play"))
    assert runner.started["click"].wait(5)
    t_upd, upd = in_thread(lambda: env.mgr.update("manual"))
    time.sleep(0.3)
    assert env.fake.updates == 0                        # el .exe sigue en uso
    runner.gates["pre"].set()
    time.sleep(0.3)
    assert env.fake.updates == 0                        # aún queda la reproducción
    runner.gates["click"].set()
    for t in (t_pre, t_play, t_upd):
        t.join(10)
    assert upd["res"].status is UpdateStatus.UPDATED


def make_probe(statuses):
    calls = []

    def probe(url, user_agent):
        calls.append((url, user_agent))
        return statuses.pop(0) if len(statuses) > 1 else statuses[0]

    return probe, calls


def test_await_playable_polls_until_the_url_stops_returning_403(env):
    probe, calls = make_probe([403, 403, 206])
    env.mgr._url_probe = probe
    res = env.mgr.resolve("abc")
    assert not res.ready
    assert env.mgr.await_playable(res) and res.ready
    assert len(calls) == 3 and calls[0] == (res.url, "UA/1.0")
    assert env.mgr.await_playable(res) and len(calls) == 3      # ya verificada: no vuelve a sondear


def test_await_playable_gives_up_on_network_errors_and_cancellation(env):
    probe, calls = make_probe([0])
    env.mgr._url_probe = probe
    res = env.mgr.resolve("abc")
    assert not env.mgr.await_playable(res) and not res.ready and len(calls) == 1
    # Cancelada (el usuario cambió de pista) mientras la URL sigue dando 403.
    probe, calls = make_probe([403])
    env.mgr._url_probe = probe
    env.mgr._wait = lambda seconds, stop: False
    assert not env.mgr.await_playable(res) and len(calls) <= 1


def test_await_playable_is_a_no_op_without_a_probe(env):
    res = env.mgr.resolve("abc")
    assert env.mgr.await_playable(res) is False


FAKE_YTDLP = (
    "import sys, json\n"
    "urls = sys.stdin.read().split()\n"
    "if not urls: sys.exit(2)\n"
    "print(json.dumps({'url': 'https://x/videoplayback?expire=1999999999', 'page': urls[0]}))\n"
)


def test_warm_process_waits_for_the_url_on_stdin():
    from ytm_native.playback.extractor import WarmProcess

    warm = WarmProcess([sys.executable, "-c", FAKE_YTDLP], key=(False, "fast"))
    time.sleep(0.3)
    assert warm.usable((False, "fast")) and not warm.usable((True, "default"))   # vivo, a la espera
    run = warm.run("https://www.youtube.com/watch?v=abc", 20)
    assert run.returncode == 0
    assert json.loads(run.stdout)["page"].endswith("v=abc")
    # El resultado llega al imprimirse el JSON; un hilo espera a que el proceso salga.
    assert warm.reaper is not None
    warm.reaper.join(10)
    assert warm.proc.returncode == 0


def test_warm_process_reports_failure_and_cancellation():
    from ytm_native.playback.extractor import WarmProcess

    failing = "import sys; sys.stdin.read(); sys.stderr.write('ERROR: Video unavailable\\n'); sys.exit(1)"
    warm = WarmProcess([sys.executable, "-c", failing], key=(False, "fast"))
    run = warm.run("https://x", 20)
    assert run.returncode == 1 and "Video unavailable" in run.stderr and warm.reaper is None
    hanging = "import sys, time; sys.stdin.read(); time.sleep(60)"
    warm = WarmProcess([sys.executable, "-c", hanging], key=(False, "fast"))
    t0 = time.monotonic()
    run = warm.run("https://x", 20, should_stop=lambda: time.monotonic() - t0 > 0.3)
    assert run.stopped and time.monotonic() - t0 < 10
    assert warm.proc.poll() is not None                         # no queda vivo


def test_warm_process_exits_by_itself_when_discarded():
    from ytm_native.playback.extractor import WarmProcess

    warm = WarmProcess([sys.executable, "-c", FAKE_YTDLP], key=(False, "fast"))
    warm.discard(10)
    assert warm.proc.returncode == 2                            # salió solo, sin matarlo
    assert warm.run("https://x", 5) is None                     # ya no sirve: quien llama va en frío


class FakeWarm:
    instances = []

    def __init__(self, args, key):
        self.args, self.key = args, key
        self.ran, self.discarded, self.reaper = [], False, None
        FakeWarm.instances.append(self)

    def usable(self, key):
        return not self.ran and not self.discarded and self.key == key

    def run(self, url, timeout, should_stop=None):
        self.ran.append(url)
        return RunResult(0, stdout=ok_json(url.split("v=")[-1]), elapsed=0.1)

    def discard(self, wait=5.0):
        self.discarded = True


@pytest.fixture
def warm_env(db, tmp_path, monkeypatch):
    from ytm_native.playback import extractor

    FakeWarm.instances = []
    monkeypatch.setattr(extractor, "WarmProcess", FakeWarm)
    cold = []

    def cold_runner(args, timeout, should_stop=None):
        cold.append(args)
        if "--version" in args:
            return RunResult(0, stdout="2026.01.01\n")
        return RunResult(0, stdout=ok_json(args[-1].split("v=")[-1]), elapsed=0.5)

    monkeypatch.setattr(extractor, "run_process", cold_runner)
    exe = tmp_path / "bin" / "yt-dlp.exe"
    exe.parent.mkdir()
    exe.write_text("2026.01.01")
    mgr = ExtractorManager(
        db, ytdlp=exe, deno=tmp_path / "bin" / "deno.exe", cookies=tmp_path / "cookies.txt",
        cache=tmp_path / "cache", runner=cold_runner, latest_tag_fetcher=lambda: "2026.01.01",
        wait=lambda seconds, stop: True, prewarm=True,
    )
    return type("WarmEnv", (), dict(mgr=mgr, cold=cold, db=db, cookies=tmp_path / "cookies.txt"))


def test_play_uses_the_warm_process_and_spawns_a_replacement(warm_env):
    mgr = warm_env.mgr
    mgr.prewarm()
    mgr.prewarm()                                               # idempotente
    assert len(FakeWarm.instances) == 1
    first = FakeWarm.instances[0]
    assert first.args[-2:] == ["-a", "-"] and FAST in first.args and first.key == (False, "fast")
    res = mgr.resolve("abc", purpose="play")
    assert res.ok and res.ready
    assert first.ran == ["https://www.youtube.com/watch?v=abc"]
    assert warm_env.cold == []                                  # no hubo arranque en frío
    assert len(FakeWarm.instances) == 2                         # el relevo ya está en marcha


def test_prefetch_leaves_the_warm_process_for_the_next_click(warm_env):
    mgr = warm_env.mgr
    mgr.prewarm()
    assert mgr.resolve("abc", purpose="prefetch").ok
    assert FakeWarm.instances[0].ran == [] and len(warm_env.cold) == 1


def test_warm_process_with_the_wrong_cookie_mode_is_replaced(warm_env):
    mgr = warm_env.mgr
    mgr.prewarm()                                               # anónimo
    warm_env.cookies.write_text("# Netscape HTTP Cookie File\n")
    warm_env.db.set_setting("cookies_risk_accepted", "1")
    warm_env.db.set_setting("playback_mode", "cookies")
    res = mgr.resolve("abc", purpose="play")
    assert res.ok and res.used_cookies
    assert FakeWarm.instances[0].ran == []                      # el anónimo no se usó
    assert FakeWarm.instances[-1].key == (True, "default")      # el relevo ya va con cookies
    for _ in range(50):
        if FakeWarm.instances[0].discarded:
            break
        time.sleep(0.02)
    assert FakeWarm.instances[0].discarded


def test_update_closes_the_warm_process_before_touching_the_binary(warm_env):
    mgr = warm_env.mgr
    mgr.prewarm()
    first = FakeWarm.instances[0]
    assert mgr.update("manual").status is UpdateStatus.UP_TO_DATE
    assert first.discarded                                      # nadie bloquea el .exe
    assert len(FakeWarm.instances) == 2 and not FakeWarm.instances[1].discarded


def test_shutdown_closes_the_warm_process_and_stops_respawning(warm_env):
    mgr = warm_env.mgr
    mgr.prewarm()
    mgr.shutdown()
    assert FakeWarm.instances[0].discarded
    mgr.prewarm()
    assert len(FakeWarm.instances) == 1
