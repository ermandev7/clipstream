"""Espacio en disco por vídeo y borrado completo de fuentes.

Cada fuente guarda en storage/sources/<id>/ el vídeo descargado, el audio, la transcripción y sus clips.
Borrar una fuente borra esa carpeta entera. El archivo original del usuario (fuente «local») está fuera de
storage y nunca se toca.
"""
from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

from config.settings import settings

from app.core import db

log = logging.getLogger(__name__)
BUSY = ("pending", "downloading", "analyzing", "rendering")  # se está escribiendo en su carpeta


class CleanupError(RuntimeError):
    pass


def folder(source_id: int) -> Path:
    return settings.storage_dir / "sources" / str(source_id)


def _size(p: Path) -> int:
    if not p.exists():
        return 0
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def usage(source: dict) -> dict:
    d = folder(source["id"])
    total = _size(d)
    clips = _size(d / "clips")
    n = db.query("SELECT COUNT(*) AS n FROM clips WHERE source_id=?", (source["id"],))[0]["n"]
    return {"id": source["id"], "kind": source["kind"], "title": source.get("title") or source.get("url") or source.get("path") or "",
            "status": source["status"], "created_at": source.get("created_at"), "clips": n,
            "total_bytes": total, "clips_bytes": clips, "original_bytes": total - clips,
            "busy": source["status"] in BUSY or source["status"] == "live"}


def storage_report() -> dict:
    rows = [usage(s) for s in db.query("SELECT * FROM sources ORDER BY id DESC")]
    return {"sources": rows, "total_bytes": sum(r["total_bytes"] for r in rows)}


def delete_source(source_id: int) -> int:
    """Borra la fuente, sus clips y su carpeta. Devuelve los bytes liberados."""
    src = db.get("sources", source_id)
    if not src:
        raise CleanupError("Fuente no encontrada")
    if src["status"] in BUSY:
        raise CleanupError("Ese vídeo se está procesando: espera a que termine para borrarlo")
    if src["status"] == "live":
        from app.clipper import live

        live.stop_live(source_id)
    d = folder(source_id)
    freed = _size(d)
    # Las publicaciones de TikTok se conservan como historial, sin enlace al clip borrado.
    db.conn().execute("UPDATE posts SET clip_id=NULL WHERE clip_id IN (SELECT id FROM clips WHERE source_id=?)", (source_id,))
    db.conn().execute("DELETE FROM clips WHERE source_id=?", (source_id,))
    db.conn().execute("DELETE FROM events WHERE source_id=?", (source_id,))
    db.conn().execute("DELETE FROM sources WHERE id=?", (source_id,))
    for attempt in range(3):  # en Windows un archivo recién cerrado puede seguir bloqueado un instante
        try:
            if d.exists():
                shutil.rmtree(d)
            break
        except OSError:
            if attempt == 2:
                log.warning("No se pudo borrar del todo %s", d)
                shutil.rmtree(d, ignore_errors=True)
            time.sleep(0.5)
    freed -= _size(d)
    log.info("Fuente %s borrada: %.0f MB liberados", source_id, freed / 1e6)
    return freed


def delete_others(keep_ids: set[int]) -> dict:
    """Borra todas las fuentes salvo `keep_ids` y las que se están procesando. Devuelve el resumen."""
    deleted, freed, skipped = 0, 0, 0
    for s in db.query("SELECT id, status FROM sources"):
        if s["id"] in keep_ids:
            continue
        if s["status"] in BUSY:
            skipped += 1
            continue
        try:
            freed += delete_source(s["id"])
            deleted += 1
        except CleanupError:
            skipped += 1
    return {"deleted": deleted, "freed_bytes": freed, "skipped": skipped}
