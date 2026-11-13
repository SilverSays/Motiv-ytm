"""Fase 0: prueba de viabilidad con criterios definidos de antemano.

Uso (desde la carpeta del proyecto, con el entorno de la app):

    python -m ytm_native.tools.fase0 run --mode anon --count 25
    python -m ytm_native.tools.fase0 run --mode cookies --count 25 --acepto-riesgo-cuenta
    python -m ytm_native.tools.fase0 report

Cada ejecución resuelve ``--count`` canciones con esperas aleatorias entre una
y otra y anota el resultado en ``fase0/resultados.jsonl``. ``report`` aplica los
criterios de ``config.FASE0_*`` y dice qué opción del plan elegir.

Criterios (fijados ANTES de medir):

* Se necesitan al menos ``FASE0_MIN_DAYS`` días distintos y ``FASE0_MIN_SAMPLES``
  resoluciones por modo; con menos, el informe dice "datos insuficientes".
* Bloqueos anti-bot <= 2 %  -> el modo es viable.
* Bloqueos anti-bot  > 10 % -> el modo no es viable.
* Entre ambos: zona gris, ampliar la muestra.

Decisión:

* Anónimo viable                       -> Opción B (híbrido).
* Anónimo no viable, cookies viable    -> Opción A (siempre con cookies).
* Cookies no viable                    -> hacen falta PO tokens: punto de
  decisión (seguir con un proveedor externo, rediseñar o abandonar).

Subida gradual: empezar con 25/día y, elegida la opción, repetir con 50 y 100
para acercarse al volumen de un oyente real antes de confiar en el resultado.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from ytm_native.core import config, paths
from ytm_native.core.db import Database
from ytm_native.core.errors import ErrorKind, summarize
from ytm_native.playback.extractor import ExtractorManager


def results_path() -> Path:
    return paths.fase0_dir() / "resultados.jsonl"


@dataclass
class ModeStats:
    mode: str
    samples: int = 0
    days: int = 0
    ok: int = 0
    bot_blocks: int = 0
    other: int = 0

    @property
    def block_rate(self) -> float:
        return self.bot_blocks / self.samples if self.samples else 0.0

    @property
    def enough(self) -> bool:
        return self.days >= config.FASE0_MIN_DAYS and self.samples >= config.FASE0_MIN_SAMPLES

    @property
    def verdict(self) -> str:
        if not self.enough:
            return "insuficiente"
        if self.block_rate <= config.FASE0_OK_BLOCK_RATE:
            return "viable"
        if self.block_rate > config.FASE0_BAD_BLOCK_RATE:
            return "no viable"
        return "zona gris"


def compute_stats(records: Iterable[dict]) -> dict[str, ModeStats]:
    stats: dict[str, ModeStats] = {}
    days: dict[str, set] = {}
    for rec in records:
        mode = rec.get("mode", "anon")
        st = stats.setdefault(mode, ModeStats(mode))
        st.samples += 1
        days.setdefault(mode, set()).add(datetime.fromtimestamp(rec.get("ts", 0)).date())
        outcome = rec.get("outcome")
        if outcome == "ok":
            st.ok += 1
        elif outcome == ErrorKind.BOT_BLOCK.value:
            st.bot_blocks += 1
        else:
            st.other += 1
    for mode, st in stats.items():
        st.days = len(days[mode])
    return stats


def decide(stats: dict[str, ModeStats]) -> str:
    anon = stats.get("anon")
    cookies = stats.get("cookies")
    av = anon.verdict if anon else "insuficiente"
    cv = cookies.verdict if cookies else "insuficiente"
    if av == "viable":
        return "Opción B (híbrido): el modo anónimo es viable; las cookies quedan como fallback."
    if cv == "no viable":
        return (
            "Las cookies no bastan: hacen falta PO tokens. PUNTO DE DECISIÓN: seguir con un "
            "proveedor externo (otro proceso más), rediseñar o abandonar."
        )
    if av == "no viable" and cv == "viable":
        return "Opción A: usar siempre las cookies de la cuenta secundaria."
    if av == "insuficiente" or cv == "insuficiente":
        return (
            f"Datos insuficientes: hacen falta al menos {config.FASE0_MIN_DAYS} días y "
            f"{config.FASE0_MIN_SAMPLES} resoluciones por modo."
        )
    return "Zona gris: amplía la muestra antes de decidir."


def load_records() -> list[dict]:
    path = results_path()
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _pick_ids(db: Database, ids_file: Optional[str], count: int) -> list[str]:
    if ids_file:
        ids = [ln.strip() for ln in Path(ids_file).read_text(encoding="utf-8").splitlines() if ln.strip()]
    else:
        rows = db.conn.execute("SELECT video_id FROM tracks").fetchall()
        ids = [r["video_id"] for r in rows]
    random.shuffle(ids)
    return ids[:count]


def cmd_run(args: argparse.Namespace) -> int:
    paths.ensure_dirs()
    paths.fase0_dir().mkdir(parents=True, exist_ok=True)
    db = Database(paths.db_path())
    extractor = ExtractorManager(db)
    if not extractor.ytdlp.is_file():
        print("Falta yt-dlp: ejecuta primero  python -m ytm_native.playback.bootstrap")
        return 2
    ok, version, message = extractor.deno_status()
    if not ok:
        print(f"Aviso sobre deno: {message}")
    modes = ["anon", "cookies"] if args.mode == "both" else [args.mode]
    if "cookies" in modes:
        if not args.acepto_riesgo_cuenta:
            print("El modo cookies exige --acepto-riesgo-cuenta (riesgo de baneo de la cuenta secundaria).")
            return 2
        if not extractor.cookies.is_file():
            print(f"No existe {extractor.cookies}.")
            return 2
    ids = _pick_ids(db, args.ids_file, args.count)
    if not ids:
        print("No hay videoIds: sincroniza la biblioteca o pasa --ids-file con un id por línea.")
        return 2
    print(f"yt-dlp {extractor.ytdlp_version()} · deno {version or '?'} · {len(ids)} pistas · modos: {modes}")
    with open(results_path(), "a", encoding="utf-8") as fh:
        for n, video_id in enumerate(ids, 1):
            for mode in modes:
                att = extractor.probe_once(video_id, use_cookies=(mode == "cookies"))
                outcome = "ok" if att.ok else (att.kind.value if att.kind else "unknown")
                rec = {
                    "ts": time.time(),
                    "video_id": video_id,
                    "mode": mode,
                    "outcome": outcome,
                    "elapsed_s": round(att.run.elapsed, 2),
                    "format_id": att.info.get("format_id"),
                    "acodec": att.info.get("acodec"),
                    "abr": att.info.get("abr"),
                    "detail": "" if att.ok else summarize(att.stderr),
                }
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fh.flush()
                print(f"[{n}/{len(ids)}] {video_id} {mode}: {outcome} "
                      f"({rec['elapsed_s']} s) {rec['acodec'] or ''} {rec['abr'] or ''} {rec['detail']}")
                if outcome == ErrorKind.BOT_BLOCK.value:
                    print("Bloqueo anti-bot: se detiene la tanda para no insistir.")
                    return 1
            if n < len(ids):
                time.sleep(random.uniform(args.min_wait, args.max_wait))
    return 0


def cmd_report(_args: argparse.Namespace) -> int:
    records = load_records()
    if not records:
        print(f"Sin resultados todavía en {results_path()}.")
        return 1
    stats = compute_stats(records)
    for mode, st in sorted(stats.items()):
        print(
            f"{mode:8s} muestras={st.samples:4d} días={st.days:2d} ok={st.ok:4d} "
            f"anti-bot={st.bot_blocks:3d} ({st.block_rate:.1%}) otros={st.other:3d} -> {st.verdict}"
        )
        formats = Counter(
            f"{r.get('acodec')}@{r.get('abr')}" for r in records
            if r.get("mode") == mode and r.get("outcome") == "ok"
        )
        if formats:
            print("         calidad obtenida: " + ", ".join(f"{k} x{v}" for k, v in formats.most_common(4)))
        others = Counter(
            r.get("outcome") for r in records
            if r.get("mode") == mode and r.get("outcome") not in ("ok", ErrorKind.BOT_BLOCK.value)
        )
        if others:
            print("         otros fallos: " + ", ".join(f"{k} x{v}" for k, v in others.most_common()))
    print()
    print("Decisión:", decide(stats))
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ytm_native.tools.fase0", description="Prueba de viabilidad (Fase 0)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="resolver una tanda de canciones")
    run.add_argument("--mode", choices=("anon", "cookies", "both"), default="anon")
    run.add_argument("--count", type=int, default=25)
    run.add_argument("--ids-file", help="archivo con un videoId por línea (por defecto: biblioteca local)")
    run.add_argument("--min-wait", type=float, default=30.0)
    run.add_argument("--max-wait", type=float, default=120.0)
    run.add_argument("--acepto-riesgo-cuenta", action="store_true")
    run.set_defaults(func=cmd_run)
    rep = sub.add_parser("report", help="aplicar los criterios y recomendar una opción")
    rep.set_defaults(func=cmd_report)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
