from __future__ import annotations

import re
import time
import zipfile
from pathlib import Path

from config.settings import settings
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.core import db

router = APIRouter(prefix="/api/clips", tags=["clips"])

MEDIA_TYPES = {"mp4": "video/mp4", "png": "image/png", "thumb": "image/jpeg", "txt": "text/plain; charset=utf-8",
               "srt": "application/x-subrip"}
EXT = {"mp4": ".mp4", "png": ".png", "thumb": ".jpg", "txt": ".txt", "srt": ".srt"}


def safe_name(s: str, max_len: int = 60) -> str:
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", s).strip().rstrip(".")
    return (s[:max_len].strip() or "clip").replace(" ", "_")


@router.get("")
def list_clips(source_id: int | None = None) -> list[dict]:
    if source_id is not None:
        return db.query("SELECT * FROM clips WHERE source_id=? ORDER BY start", (source_id,))
    return db.query(
        "SELECT c.*, s.title AS source_title, s.kind AS source_kind FROM clips c JOIN sources s ON s.id=c.source_id "
        "ORDER BY c.id DESC LIMIT 500")


@router.get("/{clip_id}/file/{kind}")
def clip_file(clip_id: int, kind: str, download: bool = False):
    clip = db.get("clips", clip_id)
    if not clip or kind not in MEDIA_TYPES:
        raise HTTPException(404)
    path = (clip.get("files") or {}).get(kind)
    if not path or not Path(path).exists():
        raise HTTPException(404, "Archivo no disponible todavía")
    name = f"{clip_id:04d}_{safe_name(clip['title'])}{EXT[kind]}" if download else None
    return FileResponse(path, media_type=MEDIA_TYPES[kind], filename=name)


class HideIn(BaseModel):
    ids: list[int]
    hidden: bool = True


@router.post("/hide")
def hide_clips(body: HideIn) -> dict:
    """Quitar de la lista de Exportar (o volver a mostrar). No borra ningún archivo."""
    for cid in body.ids:
        if db.get("clips", cid):
            db.update("clips", cid, hidden=1 if body.hidden else 0)
    return {"ok": True, "count": len(body.ids)}


class SaveIn(BaseModel):
    ids: list[int]
    dest: str
    mp4: bool = True
    png: bool = True
    txt: bool = True
    srt: bool = False


def _unique(p: Path) -> Path:
    if not p.exists():
        return p
    for n in range(2, 1000):
        q = p.with_name(f"{p.stem} ({n}){p.suffix}")
        if not q.exists():
            return q
    return p


@router.post("/save")
def save_clips(body: SaveIn) -> dict:
    """Copia los archivos de los clips a una carpeta del PC (la ventana de escritorio no descarga como un navegador).
    Solo el vídeo: un MP4 por clip en la carpeta. Con más archivos: una subcarpeta por clip."""
    import shutil

    dest = Path(body.dest)
    if not body.dest or not dest.is_dir():
        raise HTTPException(400, "La carpeta de destino no existe")
    kinds = [k for k in ("mp4", "png", "txt", "srt") if getattr(body, k)]
    if not body.ids or not kinds:
        raise HTTPException(400, "Nada que guardar")
    saved, clips = 0, 0
    for cid in body.ids:
        clip = db.get("clips", cid)
        if not clip or clip["status"] != "ready":
            continue
        base = f"{cid:04d}_{safe_name(clip['title'])}"
        folder = dest if kinds == ["mp4"] else dest / base
        folder.mkdir(parents=True, exist_ok=True)
        any_file = False
        for k in kinds:
            p = (clip.get("files") or {}).get(k)
            if p and Path(p).exists():
                shutil.copy2(p, _unique(folder / f"{base}{EXT[k]}"))
                saved += 1
                any_file = True
        clips += int(any_file)
    if not saved:
        raise HTTPException(400, "Ningún clip seleccionado está listo")
    return {"ok": True, "files": saved, "clips": clips, "dest": str(dest)}


class ZipIn(BaseModel):
    ids: list[int]
    mp4: bool = True
    png: bool = True
    txt: bool = True
    srt: bool = False


@router.post("/zip")
def zip_clips(body: ZipIn):
    kinds = [k for k in ("mp4", "png", "txt", "srt") if getattr(body, k)]
    if not body.ids or not kinds:
        raise HTTPException(400, "Nada que descargar")
    out_dir = settings.storage_dir / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.zip"):  # solo se conserva la última exportación
        old.unlink(missing_ok=True)
    zpath = out_dir / f"clipstream_{int(time.time())}.zip"
    added = 0
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_STORED) as z:  # MP4 ya está comprimido
        for cid in body.ids:
            clip = db.get("clips", cid)
            if not clip or clip["status"] != "ready":
                continue
            base = f"{cid:04d}_{safe_name(clip['title'])}"
            for k in kinds:
                p = (clip.get("files") or {}).get(k)
                if p and Path(p).exists():
                    z.write(p, f"{base}/{base}{EXT[k]}")
                    added += 1
    if not added:
        zpath.unlink(missing_ok=True)
        raise HTTPException(400, "Ningún clip seleccionado está listo")
    return FileResponse(zpath, media_type="application/zip", filename="clipstream_clips.zip")
