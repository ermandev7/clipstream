"""API de las pantallas Editor y Recorte."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from config.settings import ALLOWED_LICENSES, CLIP_MAX_S, CLIP_MIN_S
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.clipper import common
from app.clipper.pipeline import crop_settings
from app.clipper.reframe.planner import LAYOUTS
from app.core import db
from app.core.events import clip_changed, log_event
from app.shared.music import library as music_library
from app.shared.render import ffmpeg
from app.shared.subtitles.ass import STYLE_LABELS

router = APIRouter(tags=["editor"])

LAYOUT_LABELS = {"follow": "Seguir cara", "facecam": "Facecam + juego", "blur": "Desenfocado",
                 "split2": "Dividido ×2", "split3": "Dividido ×3", "group": "Grupo", "speaker": "Quien habla"}


def _clip_or_404(clip_id: int) -> dict:
    clip = db.get("clips", clip_id)
    if not clip:
        raise HTTPException(404, "Clip no encontrado")
    return clip


def media_of(clip: dict, src: dict) -> tuple[Path, float, float]:
    """(archivo, desplazamiento, duración) del vídeo del que sale el clip."""
    files = clip.get("files") or {}
    path = Path(files.get("src") or src.get("path") or "")
    if not path.is_file():
        raise HTTPException(404, "El vídeo de origen ya no está disponible")
    offset = float(files.get("src_offset", 0.0))
    return path, offset, ffmpeg.probe(path).duration


def limits(clip: dict, src: dict) -> tuple[float, float]:
    """Rango en el que se puede mover la entrada/salida (en tiempo de la fuente)."""
    _, offset, dur = media_of(clip, src)
    return offset, offset + dur


# Ajustes que no necesitan re-render del vídeo (la portada se rehace aparte).
NO_RENDER_KEYS = {"cover_t", "cover_text"}


def current_state(clip: dict, src: dict) -> dict:
    st = {k: v for k, v in (clip.get("settings") or {}).items() if k not in NO_RENDER_KEYS}
    return {"start": clip["start"], "end": clip["end"], "hook": clip.get("hook") or "",
            "settings": st, "preset": src.get("preset") or {}}


def is_dirty(clip: dict, src: dict) -> bool:
    r = dict(clip.get("rendered") or {})
    if not r:
        return clip["status"] != "ready"
    r["settings"] = {k: v for k, v in (r.get("settings") or {}).items() if k not in NO_RENDER_KEYS}
    cur = current_state(clip, src)
    return any(json.dumps(cur[k], sort_keys=True) != json.dumps(r.get(k), sort_keys=True) for k in cur)


@router.get("/api/editor/options")
def options() -> dict:
    return {"layouts": [{"id": k, "label": v, "available": True} for k, v in LAYOUT_LABELS.items()],
            "styles": [{"id": k, "label": v} for k, v in STYLE_LABELS.items()],
            "music": [t for t in music_library.tracks() if t["allowed"]],
            "licenses": ALLOWED_LICENSES,
            "clip_min": CLIP_MIN_S, "clip_max": CLIP_MAX_S}


class MusicImportIn(BaseModel):
    path: str
    license: str
    title: str = ""
    author: str = ""
    source: str = ""


@router.post("/api/music/import")
def music_import(body: MusicImportIn) -> dict:
    try:
        return music_library.import_track(body.path, body.license, body.title, body.author, body.source)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/api/clips/{clip_id}/detail")
def clip_detail(clip_id: int) -> dict:
    clip = _clip_or_404(clip_id)
    src = db.get("sources", clip["source_id"])
    out_dir = common.clip_dir(src["id"], clip_id)
    transcript = common.load_transcript(src["id"])
    lo, hi = limits(clip, src)
    margin = 30.0
    words = [{"t": round(w.start, 3), "e": round(w.end, 3), "w": w.text}
             for w in (transcript.words if transcript else []) if clip["start"] - margin <= w.start <= clip["end"] + margin]
    plan_p = out_dir / "plan.json"
    sfx_p = out_dir / "sfx.json"
    wave_p = Path((clip.get("files") or {}).get("wave") or out_dir / "wave.json")
    return {
        "clip": clip,
        "source": {k: src.get(k) for k in ("id", "kind", "title", "channel", "url", "platform", "preset")},
        "words": words,
        "plan": json.loads(plan_p.read_text(encoding="utf-8")) if plan_p.exists() else None,
        "wave": json.loads(wave_p.read_text(encoding="utf-8")) if wave_p.exists() else [],
        "audio": json.loads(sfx_p.read_text(encoding="utf-8")) if sfx_p.exists() else None,
        "crop": crop_settings(clip, src),
        "language": (transcript.language if transcript else None) or "es",
        "limits": {"min": lo, "max": hi},
        "dirty": is_dirty(clip, src),
    }


class CropIn(BaseModel):
    mode: Literal["auto", "manual"] = "auto"
    layout: Literal["follow", "facecam", "blur", "split2", "split3", "group", "speaker"] | None = None
    boxes: list[dict] = Field(default_factory=list)
    tracking: dict = Field(default_factory=dict)


class SettingsIn(BaseModel):
    style: str | None = None
    sub_lang: Literal["orig", "es", "en", "both"] | None = None
    cover_t: float | None = Field(default=None, ge=0)
    cover_text: str | None = Field(default=None, max_length=80)
    cover_auto: bool = False  # volver a la portada elegida automáticamente
    sfx: Literal["none", "auto", "subtle", "meme"] | None = None
    fx: Literal["none", "subtle", "dynamic"] | None = None
    zoom: bool | None = None
    music: str | None = None  # id de la pista; "" = sin música
    music_gain: float | None = Field(default=None, ge=-40, le=-6)
    hook_seconds: float | None = Field(default=None, ge=0.5, le=6)
    crop: CropIn | None = None
    clear_crop: bool = False  # volver a usar el preset del directo / automático


class ClipPatch(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    title_en: str | None = Field(default=None, max_length=120)
    description_es: str | None = Field(default=None, max_length=400)
    description_en: str | None = Field(default=None, max_length=400)
    hashtags: list[str] | None = None
    hook: str | None = Field(default=None, max_length=80)
    start: float | None = None
    end: float | None = None
    settings: SettingsIn | None = None


def _validate_box(b: dict) -> dict:
    try:
        x, y, w, h = (float(b[k]) for k in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "Caja de recorte inválida") from None
    if not (0 <= x <= 1 and 0 <= y <= 1 and 0.01 <= w <= 1 and 0.01 <= h <= 1):
        raise HTTPException(400, "Caja de recorte fuera del frame")
    return {"id": b.get("id"), "x": x, "y": y, "w": min(w, 1 - x), "h": min(h, 1 - y),
            "include": bool(b.get("include", True))}


@router.patch("/api/clips/{clip_id}")
def patch_clip(clip_id: int, body: ClipPatch) -> dict:
    clip = _clip_or_404(clip_id)
    src = db.get("sources", clip["source_id"])
    if clip["status"] in ("pending", "rendering"):
        raise HTTPException(409, "El clip se está renderizando; espera a que termine")
    if not clip.get("rendered") and clip["status"] == "ready":
        # Clip renderizado antes de que existiera el registro: lo actual es lo renderizado.
        db.update("clips", clip_id, rendered=current_state(clip, src))
    fields: dict = {}
    if body.title is not None:
        if not body.title.strip():
            raise HTTPException(400, "El título no puede estar vacío")
        fields["title"] = body.title.strip()
    if body.hashtags is not None:
        tags = []
        for t in body.hashtags:
            t = str(t).strip().lstrip("#").replace(" ", "").lower()
            if t and t not in tags:
                tags.append(t)
        fields["hashtags"] = tags[:15]
    if body.hook is not None:
        fields["hook"] = body.hook.strip().upper()
    meta_edits = {k: getattr(body, k).strip() for k in ("title_en", "description_es", "description_en")
                  if getattr(body, k) is not None}
    if meta_edits or body.hashtags is not None:
        from app.shared.metadata.generator import platform_hashtags

        meta = dict(clip.get("meta") or {})
        meta.update(meta_edits)
        if body.hashtags is not None:
            meta["hashtags"] = fields["hashtags"]
            meta["platforms"] = platform_hashtags(fields["hashtags"])
        fields["meta"] = meta
    if body.start is not None or body.end is not None:
        start = body.start if body.start is not None else clip["start"]
        end = body.end if body.end is not None else clip["end"]
        lo, hi = limits(clip, src)
        if start < lo - 0.01 or end > hi + 0.01:
            raise HTTPException(400, f"El clip debe quedar entre {lo:.1f} y {hi:.1f} s del vídeo")
        if not (CLIP_MIN_S <= end - start <= CLIP_MAX_S):
            raise HTTPException(400, f"La duración debe estar entre {CLIP_MIN_S:.0f} s y {CLIP_MAX_S / 60:.0f} min")
        fields["start"], fields["end"] = round(start, 3), round(end, 3)
        fields["peak"] = min(max(clip["peak"], start), end)
    if body.settings is not None:
        st = dict(clip.get("settings") or {})
        if body.settings.style is not None:
            if body.settings.style not in STYLE_LABELS:
                raise HTTPException(400, "Estilo de subtítulos desconocido")
            st["style"] = body.settings.style
        if body.settings.hook_seconds is not None:
            st["hook_seconds"] = body.settings.hook_seconds
        if body.settings.sub_lang is not None:
            st["sub_lang"] = body.settings.sub_lang
        if body.settings.cover_text is not None:
            st["cover_text"] = body.settings.cover_text.strip()
        if body.settings.cover_t is not None:
            st["cover_t"] = min(body.settings.cover_t, clip["end"] - clip["start"])
        if body.settings.cover_auto:
            st.pop("cover_t", None)
        if body.settings.sfx is not None:
            st["sfx"] = body.settings.sfx
        if body.settings.zoom is not None:
            st["zoom"] = body.settings.zoom
        if body.settings.fx is not None:
            st["fx"] = body.settings.fx
        if body.settings.music_gain is not None:
            st["music_gain"] = body.settings.music_gain
        if body.settings.music is not None:
            if body.settings.music == "":
                st.pop("music", None)
            else:
                from app.shared.music import library

                if library.track_path(body.settings.music) is None:
                    raise HTTPException(400, "Esa pista no está en la biblioteca o su licencia no está permitida")
                st["music"] = body.settings.music
        if body.settings.clear_crop:
            st.pop("crop", None)
        elif body.settings.crop is not None:
            c = body.settings.crop.model_dump()
            c["boxes"] = [_validate_box(b) for b in c["boxes"]]
            if c["layout"] and c["layout"] not in LAYOUTS:
                raise HTTPException(400, "Diseño desconocido")
            st["crop"] = c
        fields["settings"] = st
    if fields:
        db.update("clips", clip_id, **fields)
        clip_changed(clip_id)
        if clip["status"] == "ready" and ({"title", "hashtags", "meta"} & fields.keys()):
            # Los textos de subida no necesitan re-render: se reescribe el .txt al momento.
            from app.clipper.pipeline import write_clip_txt

            write_clip_txt(db.get("clips", clip_id), src, common.clip_dir(src["id"], clip_id))
    return clip_detail(clip_id)


@router.post("/api/clips/{clip_id}/cover")
async def regenerate_cover(clip_id: int) -> dict:
    """Rehace solo la portada (fotograma y texto actuales), sin re-renderizar el vídeo."""
    import asyncio

    from app.clipper.pipeline import make_cover

    clip = _clip_or_404(clip_id)
    if clip["status"] in ("pending", "rendering"):
        raise HTTPException(409, "El clip se está renderizando")
    src = db.get("sources", clip["source_id"])
    media, offset, _ = media_of(clip, src)
    out_dir = common.clip_dir(src["id"], clip_id)
    plan_p = out_dir / "plan.json"
    plan = json.loads(plan_p.read_text(encoding="utf-8")) if plan_p.exists() else None
    crop = (plan or {}).get("crop")
    info = ffmpeg.probe(media)
    sw, sh = (crop[0], crop[1]) if crop else (info.width, info.height)
    await asyncio.to_thread(make_cover, clip, src, media, clip["start"] - offset, clip["end"] - offset, plan, sw, sh,
                            info.fps, out_dir)
    files = dict(clip.get("files") or {})
    files.update({"png": str(out_dir / "cover.png"), "thumb": str(out_dir / "thumb.jpg")})
    rendered = dict(clip.get("rendered") or {})
    rendered["cover_at"] = __import__("time").time()
    db.update("clips", clip_id, files=files, rendered=rendered)
    clip_changed(clip_id)
    return clip_detail(clip_id)


@router.post("/api/clips/{clip_id}/metadata")
def regenerate_metadata(clip_id: int) -> dict:
    _clip_or_404(clip_id)
    from app.workers.tasks import metadata_task

    metadata_task(clip_id)
    return {"ok": True}


@router.post("/api/clips/{clip_id}/render")
def rerender(clip_id: int) -> dict:
    clip = _clip_or_404(clip_id)
    if clip["status"] in ("pending", "rendering"):
        raise HTTPException(409, "Ya se está renderizando")
    from app.workers.tasks import render_clip_task

    db.update("clips", clip_id, status="pending", progress=0)
    clip_changed(clip_id)
    render_clip_task(clip_id)
    return {"ok": True}


@router.get("/api/clips/{clip_id}/frame")
def clip_frame(clip_id: int, t: float = 0.0):
    """Frame de la fuente (sin bandas negras si las hay) a `t` segundos desde el inicio del clip."""
    clip = _clip_or_404(clip_id)
    src = db.get("sources", clip["source_id"])
    path, offset, dur = media_of(clip, src)
    at = min(max(0.0, clip["start"] - offset + t), max(0.0, dur - 0.1))
    out_dir = common.clip_dir(src["id"], clip_id)
    plan_p = out_dir / "plan.json"
    crop = None
    if plan_p.exists():
        crop = json.loads(plan_p.read_text(encoding="utf-8")).get("crop")
    out = out_dir / f"frame_{int(at * 10)}.jpg"
    if not out.exists():
        for old in out_dir.glob("frame_*.jpg"):
            old.unlink(missing_ok=True)
        vf = (f"crop={crop[0]}:{crop[1]}:{crop[2]}:{crop[3]}," if crop else "") + "scale=1280:-2,format=yuvj420p"
        ffmpeg.run(["-ss", f"{at:.2f}", "-i", str(path), "-frames:v", "1", "-vf", vf, "-q:v", "3", str(out)])
    return FileResponse(out, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


class PresetIn(BaseModel):
    crop: CropIn
    apply_all: bool = False
    from_clip: int | None = None  # el clip desde el que se guardó (deja de tener recorte propio)


@router.post("/api/sources/{source_id}/preset")
def save_preset(source_id: int, body: PresetIn) -> dict:
    src = db.get("sources", source_id)
    if not src:
        raise HTTPException(404, "Fuente no encontrada")
    c = body.crop.model_dump()
    c["boxes"] = [_validate_box(b) for b in c["boxes"]]
    db.update("sources", source_id, preset=c)
    rerendered = 0
    if body.apply_all or body.from_clip:
        from app.workers.tasks import render_clip_task

        targets = db.query("SELECT * FROM clips WHERE source_id=?", (source_id,)) if body.apply_all else \
            [db.get("clips", body.from_clip)]
        for clip in targets:
            if not clip or clip["status"] in ("pending", "rendering"):
                continue
            st = dict(clip.get("settings") or {})
            st.pop("crop", None)  # el preset manda
            db.update("clips", clip["id"], settings=st, status="pending", progress=0)
            clip_changed(clip["id"])
            render_clip_task(clip["id"])
            rerendered += 1
    log_event(source_id, f"Preset de recorte guardado ({c['mode']}, {c.get('layout') or 'automático'})"
              + (f"; re-renderizando {rerendered} clips" if rerendered else ""))
    return {"ok": True, "rerendered": rerendered}
