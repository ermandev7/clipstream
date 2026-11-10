"""Reprocesar un directo terminado con su repetición completa.

Durante el directo solo se analiza un buffer de 15 min y con poco contexto; al terminar, la
repetición permite analizar todo el directo con chat de repetición, comparación entre momentos, etc.
- YouTube: la misma URL sirve cuando la repetición está procesada (live_status «was_live»).
- Kick: el VOD más reciente del canal que empezó cerca del inicio de la captura.
"""
from __future__ import annotations

import datetime as dt
import logging

from app.core import db
from app.core.events import log_event, source_changed

log = logging.getLogger(__name__)


class NotReady(Exception):
    """La repetición todavía no está disponible (se reintenta más tarde)."""


def resolve_replay(src: dict) -> str:
    """URL de la repetición publicada del directo, o NotReady."""
    if src.get("platform") == "kick":
        import httpx

        from app.clipper.ingest.kick import HEADERS, parse

        slug = parse(src["url"])["slug"]
        vids = httpx.get(f"https://kick.com/api/v2/channels/{slug}/videos", headers=HEADERS, timeout=20).json()
        started = dt.datetime.fromtimestamp(src["created_at"], dt.UTC)
        best = None
        for v in vids:
            if v.get("is_live"):
                continue
            try:
                st = dt.datetime.fromisoformat(str(v.get("start_time")).replace(" ", "T")).replace(tzinfo=dt.UTC)
            except ValueError:
                continue
            # El directo empezó antes que nuestra captura (como mucho 24 h antes).
            if dt.timedelta(0) <= started - st <= dt.timedelta(hours=24) or abs(started - st) < dt.timedelta(minutes=30):
                if best is None or st > best[0]:
                    best = (st, v)
        if not best:
            raise NotReady("Kick aún no ha publicado el VOD de este directo")
        return f"https://kick.com/{slug}/videos/{best[1]['video']['uuid']}"

    from app.clipper.ingest.vod import fetch_info

    info = fetch_info(src["url"])
    status = info.get("live_status")
    if info.get("is_live") or status in ("is_live", "is_upcoming"):
        raise NotReady("El directo sigue en emisión")
    if status == "post_live":
        raise NotReady("YouTube todavía está procesando la repetición")
    if status != "was_live" and not info.get("was_live"):
        raise NotReady("Este directo no tiene repetición disponible")
    return info.get("webpage_url") or src["url"]


def start_reprocess(source_id: int) -> int:
    """Crea la fuente «Repetición» y la encola. Devuelve su id. Lanza NotReady si aún no se puede."""
    src = db.get("sources", source_id)
    if src is None or src["kind"] != "live":
        raise ValueError("Solo se reprocesan directos")
    if src["status"] == "live":
        raise NotReady("Detén la captura antes de reprocesar")
    existing = db.query("SELECT id FROM sources WHERE parent_id=? AND status NOT IN ('error')", (source_id,))
    if existing:
        return existing[0]["id"]
    url = resolve_replay(src)
    from app.workers.tasks import analyze_media_task

    sid = db.insert("sources", kind="vod", platform=src.get("platform") or "youtube", url=url, status="pending",
                    stage="En cola", title=f"Repetición · {src.get('title') or ''}".strip(" ·"),
                    channel=src.get("channel") or "", parent_id=source_id)
    analyze_media_task(sid)
    log_event(source_id, f"Reprocesando el directo completo (fuente #{sid})")
    log_event(sid, f"Repetición del directo #{source_id}")
    source_changed(sid)
    return sid
