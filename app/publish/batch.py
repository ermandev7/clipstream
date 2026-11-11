"""Publicación por tandas: N vídeos al día, uno cada X minutos desde una hora fija; los demás, los días siguientes.

TikTok exige que el usuario revise y acepte cada publicación hecha por una app: aquí solo se calculan los
huecos y se prepara la siguiente tanda; la interfaz la muestra para aprobar clip a clip («Aprobar tanda»).
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta

from config.settings import settings

from app.core import db
from app.publish import tiktok

PREFS_FILE = "publish_prefs.json"
DEFAULTS = {"per_day": 5, "interval_min": 60, "start": "18:00"}
PER_DAY_MAX = 10  # TikTok admite ~15 al día por cuenta; más de 10 ya es ritmo de spam
INTERVALS = (30, 60, 90, 120, 180)
LEAD_S = 5 * 60  # la primera publicación, como pronto dentro de 5 minutos
ACTIVE = ("scheduled", "queued", "uploading", "processing", "published")


def load_prefs() -> dict:
    p = settings.storage_dir / PREFS_FILE
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        d = {}
    return clean_prefs({**DEFAULTS, **d})


def clean_prefs(d: dict) -> dict:
    per_day = min(PER_DAY_MAX, max(1, int(d.get("per_day") or DEFAULTS["per_day"])))
    interval = int(d.get("interval_min") or DEFAULTS["interval_min"])
    interval = interval if interval in INTERVALS else DEFAULTS["interval_min"]
    start = str(d.get("start") or DEFAULTS["start"])
    try:
        h, m = (int(x) for x in start.split(":"))
        assert 0 <= h < 24 and 0 <= m < 60
    except (ValueError, AssertionError):
        h, m = (int(x) for x in DEFAULTS["start"].split(":"))
    return {"per_day": per_day, "interval_min": interval, "start": f"{h:02d}:{m:02d}"}


def save_prefs(d: dict) -> dict:
    prefs = clean_prefs({**load_prefs(), **d})
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    (settings.storage_dir / PREFS_FILE).write_text(json.dumps(prefs), encoding="utf-8")
    return prefs


def next_slots(n: int, now: float, taken: list[float], per_day: int, interval_min: int, start: str,
               max_days: int = 120) -> list[float]:
    """Los `n` próximos huecos libres (epoch, hora local): cada día `per_day` huecos desde `start`, separados
    `interval_min`. Un día que ya tiene `per_day` publicaciones programadas o hechas se salta entero."""
    h, m = (int(x) for x in start.split(":"))
    step = interval_min * 60
    taken = sorted(taken)
    out: list[float] = []
    today = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
    for d in range(max_days):
        day0 = today + timedelta(days=d)
        lo, hi = day0.timestamp(), (day0 + timedelta(days=1)).timestamp()
        free = per_day - sum(1 for t in taken if lo <= t < hi)
        base = day0.replace(hour=h, minute=m).timestamp()
        for k in range(per_day):
            if free <= 0 or len(out) >= n:
                break
            slot = base + k * step
            if slot < now + LEAD_S or slot >= hi or any(abs(slot - t) < step / 2 for t in taken):
                continue
            out.append(slot)
            taken.append(slot)
            free -= 1
        if len(out) >= n:
            break
    return out


def pending_clips(source_id: int | None = None) -> list[dict]:
    """Clips listos que aún no se han publicado ni programado, de mejor a peor puntuación."""
    sql = ("SELECT * FROM clips c WHERE c.status='ready' AND COALESCE(c.hidden, 0)=0 AND NOT EXISTS ("
           f"SELECT 1 FROM posts p WHERE p.clip_id=c.id AND p.status IN ({','.join('?' * len(ACTIVE))}))")
    params: tuple = ACTIVE
    if source_id is not None:
        sql += " AND c.source_id=?"
        params += (source_id,)
    return db.query(sql + " ORDER BY c.score DESC, c.start", params)


def taken_times() -> list[float]:
    rows = db.query("SELECT COALESCE(scheduled_at, started_at, created_at) AS t FROM posts WHERE platform='tiktok' "
                    "AND status IN ('scheduled','queued','uploading','processing','published')")
    return [float(r["t"]) for r in rows if r["t"]]


def next_batch(source_id: int | None = None, now: float | None = None) -> dict:
    """La siguiente tanda propuesta: hasta `per_day` clips con su hora, y cuántos quedan después."""
    now = now or time.time()
    prefs = load_prefs()
    clips = pending_clips(source_id)
    batch = clips[:prefs["per_day"]]
    slots = next_slots(len(batch), now, taken_times(), prefs["per_day"], prefs["interval_min"], prefs["start"])
    items = [{"clip": c, "scheduled_at": t} for c, t in zip(batch, slots)]
    return {"prefs": prefs, "items": items, "remaining": len(clips) - len(items), "daily_cap": tiktok.DAILY_CAP}
