"""Tareas de la cola. Solo reciben ids (se serializan en SQLite)."""
from __future__ import annotations

from app.core.queue import huey


@huey.task()
def analyze_media_task(source_id: int) -> None:
    from app.clipper.pipeline import analyze_media

    analyze_media(source_id)


@huey.task()
def metadata_task(clip_id: int) -> None:
    """Regenera título EN, descripciones y hashtags con el LLM (sin volver a renderizar el vídeo)."""
    from app.clipper import common
    from app.clipper.detection.types import Candidate
    from app.clipper.pipeline import write_clip_txt
    from app.core import db
    from app.core.events import clip_changed

    clip = db.get("clips", clip_id)
    if not clip:
        return
    src = db.get("sources", clip["source_id"])
    c = Candidate(clip["start"], clip["end"], clip["peak"], clip["score"], title=clip["title"], hashtags=clip["hashtags"])
    meta = {**(clip.get("meta") or {}), **common.clip_metadata(c, common.load_transcript(src["id"]), src["title"] or "")}
    meta.pop("title_es", None)  # el título ES lo decide el usuario: no se sobrescribe
    db.update("clips", clip_id, meta=meta, hashtags=meta.get("hashtags") or clip["hashtags"])
    write_clip_txt(db.get("clips", clip_id), src, common.clip_dir(src["id"], clip_id))
    clip_changed(clip_id)


@huey.task(retries=36, retry_delay=300)
def reprocess_live_task(source_id: int) -> None:
    """Tras un directo que terminó solo: espera a la repetición (cada 5 min, hasta 3 h) y la analiza."""
    from app.clipper.reprocess import NotReady, start_reprocess
    from app.core.events import log_event

    try:
        start_reprocess(source_id)
    except NotReady as e:
        log_event(source_id, f"Reprocesado pendiente: {e}. Se reintenta en 5 min.")
        raise  # Huey lo reintenta


@huey.task()
def publish_post_task(post_id: int) -> None:
    from app.publish.service import run_post

    run_post(post_id)


@huey.task()
def render_clip_task(clip_id: int) -> None:
    from app.clipper.pipeline import render_clip

    render_clip(clip_id)
