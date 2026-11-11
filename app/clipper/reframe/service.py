"""Calcula (y cachea) el plan de reencuadre de un tramo de vídeo."""
from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path

from app.clipper.reframe.detector import analyze_range, detect_letterbox, scene_cuts
from app.clipper.reframe.manual import manual_plan
from app.clipper.reframe.planner import LAYOUTS, Tracking, plan_clip
from app.shared.render.ffmpeg import probe

log = logging.getLogger(__name__)


PLAN_VERSION = 3  # súbelo si cambia el análisis: los planes guardados se recalculan


def plan_key(start: float, end: float, force: str | None, tracking: Tracking | None, manual: dict | None) -> str:
    raw = json.dumps({"v": PLAN_VERSION, "s": round(start, 2), "e": round(end, 2), "f": force if force in LAYOUTS else None,
                      "t": (tracking or Tracking()).__dict__, "m": manual}, sort_keys=True)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def compute_plan(src: str | Path, start: float, end: float, force: str | None = None,
                 tracking: Tracking | None = None, manual: dict | None = None,
                 words: list[tuple[float, float]] | None = None) -> dict:
    t0 = time.perf_counter()
    info = probe(src)
    crop = detect_letterbox(src, start, end, info.width, info.height)
    w, h = (crop[0], crop[1]) if crop else (info.width, info.height)
    if manual and manual.get("boxes") is not None:
        d = manual_plan(manual, w, h, end - start)
    else:
        frames, activity = analyze_range(src, start, end, crop=crop)
        cuts = scene_cuts(src, start, end)
        if words is not None:  # labios + voz: quién habla (solo si hay varias personas)
            from app.clipper.reframe.speaker import annotate

            try:
                annotate(frames, src, start, end, words, crop)
            except Exception:  # noqa: BLE001 - sin hablante activo el reencuadre sigue funcionando
                log.exception("Análisis de hablante activo fallido")
        d = plan_clip(frames, cuts, end - start, w, h, force=force if force in LAYOUTS else None,
                      tracking=tracking, activity=activity).to_dict()
    d["crop"] = list(crop) if crop else None
    log.info("Reencuadre %s: %s, %d planos, bandas %s%s (%.1f s)", Path(src).name, d["layout"], len(d["shots"]),
             "sí" if crop else "no", ", manual" if d.get("manual") else "", time.perf_counter() - t0)
    return d


def load_or_compute(plan_path: Path, src: str | Path, start: float, end: float, force: str | None = None,
                    tracking: Tracking | None = None, manual: dict | None = None,
                    words: list[tuple[float, float]] | None = None) -> dict:
    """Reutiliza el plan guardado si nada de lo que lo determina ha cambiado."""
    key = plan_key(start, end, force, tracking, manual)
    if plan_path.exists():
        try:
            d = json.loads(plan_path.read_text(encoding="utf-8"))
            if d.get("key") == key:
                return d
        except (OSError, json.JSONDecodeError):
            pass
    d = compute_plan(src, start, end, force, tracking, manual, words)
    d["key"] = key
    plan_path.write_text(json.dumps(d), encoding="utf-8")
    return d
