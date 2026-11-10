"""API de la pantalla Microseries."""
from __future__ import annotations

from typing import Literal

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

from app.core import db
from app.stories import catalog, service

router = APIRouter(prefix="/api/stories", tags=["stories"])


@router.get("/catalog")
def get_catalog() -> dict:
    return {"templates": catalog.templates(), "stories": catalog.stories()}


@router.get("/series")
def list_series() -> list[dict]:
    rows = db.query("SELECT * FROM series ORDER BY id DESC")
    counts = {r["series_id"]: r["n"] for r in db.query("SELECT series_id, COUNT(*) AS n FROM episodes GROUP BY series_id")}
    for r in rows:
        r["episode_count"] = counts.get(r["id"], 0)
        r["planned_episodes"] = len((r.get("bible") or {}).get("episodes") or [])
    return rows


class SeriesIn(BaseModel):
    template: Literal["biblicas", "mitologia", "fabulas", "leyendas", "frutinovela"]
    story_id: str | None = None
    custom_title: str = Field(default="", max_length=120)
    name: str = Field(default="", max_length=60)
    languages: list[Literal["es", "en"]] = ["es"]
    episodes: int = Field(default=4, ge=1, le=20)


@router.post("/series")
def create_series(body: SeriesIn) -> dict:
    """Crea la serie (biblia + personajes). Puede tardar ~20 s (LLM)."""
    try:
        sid = service.create_series(body.template, body.story_id, body.custom_title, body.languages, body.episodes, body.name)
    except service.Blocked as b:
        raise HTTPException(422, {"blocked": True, "message": b.finding.message, "suggestion": b.finding.suggestion}) from b
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return service.series_detail(sid)


@router.get("/series/{series_id}")
def get_series(series_id: int) -> dict:
    s = service.series_detail(series_id)
    if not s:
        raise HTTPException(404, "Serie no encontrada")
    return s


@router.delete("/series/{series_id}")
def delete_series(series_id: int) -> dict:
    db.conn().execute("DELETE FROM series WHERE id=?", (series_id,))
    return {"ok": True}


@router.post("/series/{series_id}/episodes/{number}")
def generate_episode(series_id: int, number: int) -> dict:
    s = service.series_detail(series_id)
    if not s:
        raise HTTPException(404, "Serie no encontrada")
    planned = len((s.get("bible") or {}).get("episodes") or [])
    if not 1 <= number <= max(planned, 1):
        raise HTTPException(400, f"La serie tiene {planned} episodios")
    from app.workers.tasks import story_episode_task

    story_episode_task(series_id, number)
    return {"ok": True}


@router.post("/series/{series_id}/episodes/{number}/produce")
def produce(series_id: int, number: int) -> dict:
    """Producir el episodio completo (o continuar donde se quedó)."""
    from app.stories import images, voices

    s = service.series_detail(series_id)
    if not s:
        raise HTTPException(404, "Serie no encontrada")
    planned = len((s.get("bible") or {}).get("episodes") or [])
    if not 1 <= number <= max(planned, 1):
        raise HTTPException(400, f"La serie tiene {planned} episodios")
    if not images.available():
        raise HTTPException(409, "Faltan el motor o los modelos de imagen: descárgalos en «Preparar ClipStream»")
    if not voices.available():
        raise HTTPException(409, "Falta el modelo de voz: descárgalo en «Preparar ClipStream»")
    ep = next((e for e in s["episodes"] if e["number"] == number), None)
    if ep and db.query("SELECT 1 FROM scenes WHERE episode_id=? AND status='blocked' LIMIT 1", (ep["id"],)):
        raise HTTPException(409, "Hay escenas bloqueadas por derechos: corrígelas o regenéralas antes de continuar")
    if ep:
        db.update("episodes", ep["id"], stage="En cola", error=None)
        service.notify("episode", episode_id=ep["id"], series_id=series_id)
    from app.workers.tasks import story_produce_task

    story_produce_task(series_id, number)
    return {"ok": True}


@router.get("/episodes/{episode_id}/report", response_class=PlainTextResponse)
def episode_report(episode_id: int) -> str:
    e = db.get("episodes", episode_id)
    p = ((e or {}).get("files") or {}).get("report")
    if not p or not Path(p).is_file():
        raise HTTPException(404, "El informe se genera al montar el episodio")
    return Path(p).read_text(encoding="utf-8")


@router.get("/episodes/{episode_id}")
def get_episode(episode_id: int) -> dict:
    e = service.episode_detail(episode_id)
    if not e:
        raise HTTPException(404, "Episodio no encontrado")
    return e


@router.get("/voices")
def list_voices() -> dict:
    from app.stories import voices

    return {"available": voices.available(), "voices": voices.VOICES, "default": voices.DEFAULT_VOICE}


class VoicesIn(BaseModel):
    es: str | None = None
    en: str | None = None


@router.put("/series/{series_id}/voices")
def set_voices(series_id: int, body: VoicesIn) -> dict:
    from app.stories import voices

    s = db.get("series", series_id)
    if not s:
        raise HTTPException(404, "Serie no encontrada")
    chosen = {k: v for k, v in body.model_dump().items() if v}
    for lang, v in chosen.items():
        if v not in {x["id"] for x in voices.VOICES[lang]}:
            raise HTTPException(400, f"Voz desconocida para {lang}: {v}")
    bible = dict(s.get("bible") or {})
    bible["voices"] = {**(bible.get("voices") or {}), **chosen}
    db.update("series", series_id, bible=bible)
    return service.series_detail(series_id)


@router.post("/episodes/{episode_id}/voice")
def voice_episode(episode_id: int) -> dict:
    from app.stories import voices

    e = service.episode_detail(episode_id)
    if not e:
        raise HTTPException(404, "Episodio no encontrado")
    if not e["scenes"]:
        raise HTTPException(409, "El episodio aún no tiene guion")
    if any(s["status"] == "blocked" for s in e["scenes"]):
        raise HTTPException(409, "Hay escenas bloqueadas por derechos: corrígelas antes de generar la voz")
    if not voices.available():
        raise HTTPException(409, "Falta el modelo de voz: descárgalo en «Preparar ClipStream»")
    from app.workers.tasks import story_voice_task

    story_voice_task(episode_id)
    return {"ok": True}


@router.post("/episodes/{episode_id}/images")
def illustrate(episode_id: int) -> dict:
    from app.stories import images

    e = service.episode_detail(episode_id)
    if not e:
        raise HTTPException(404, "Episodio no encontrado")
    if not e["scenes"]:
        raise HTTPException(409, "El episodio aún no tiene guion")
    if not images.available():
        raise HTTPException(409, "Faltan el motor o los modelos de imagen: descárgalos en «Preparar ClipStream»")
    from app.workers.tasks import story_images_task

    story_images_task(episode_id)
    return {"ok": True}


@router.post("/scenes/{scene_id}/image")
def reimage_scene(scene_id: int) -> dict:
    """Otra imagen para la escena (nueva semilla)."""
    from app.stories import images

    sc = db.get("scenes", scene_id)
    if not sc:
        raise HTTPException(404, "Escena no encontrada")
    if not images.available():
        raise HTTPException(409, "Faltan el motor o los modelos de imagen: descárgalos en «Preparar ClipStream»")
    from app.workers.tasks import story_images_task

    story_images_task(sc["episode_id"], scene_id)
    return {"ok": True}


@router.post("/episodes/{episode_id}/assemble")
def assemble(episode_id: int) -> dict:
    e = service.episode_detail(episode_id)
    if not e:
        raise HTTPException(404, "Episodio no encontrado")
    sc = e["scenes"]
    if not sc:
        raise HTTPException(409, "El episodio aún no tiene guion")
    if any(s["status"] == "blocked" for s in sc):
        raise HTTPException(409, "Hay escenas bloqueadas por derechos: corrígelas antes de montar")
    if not all(((s.get("files") or {}).get("image") or {}).get("png") for s in sc):
        raise HTTPException(409, "Faltan imágenes: genéralas antes de montar")
    if not all((s.get("files") or {}).get("voice") for s in sc):
        raise HTTPException(409, "Falta la voz: genérala antes de montar")
    from app.workers.tasks import story_assemble_task

    story_assemble_task(episode_id)
    return {"ok": True}


@router.get("/episodes/{episode_id}/video/{lang}.mp4")
def episode_video(episode_id: int, lang: Literal["es", "en"]) -> FileResponse:
    from config.settings import settings

    e = db.get("episodes", episode_id)
    p = ((e or {}).get("files") or {}).get("videos", {}).get(lang)
    if not p or not Path(p).is_file():
        raise HTTPException(404, "Vídeo no disponible")
    if settings.storage_dir.resolve() not in Path(p).resolve().parents:
        raise HTTPException(403, "Ruta fuera del almacenamiento")
    return FileResponse(p, media_type="video/mp4")


class MusicIn(BaseModel):
    track_id: str | None = None  # None = base propia del ambiente


@router.put("/series/{series_id}/music")
def set_music(series_id: int, body: MusicIn) -> dict:
    from app.shared.music import library

    s = db.get("series", series_id)
    if not s:
        raise HTTPException(404, "Serie no encontrada")
    if body.track_id and not library.track_path(body.track_id):
        raise HTTPException(400, "Pista no disponible o sin licencia permitida")
    bible = dict(s.get("bible") or {})
    bible["music"] = body.track_id
    db.update("series", series_id, bible=bible)
    return service.series_detail(series_id)


def _scene_file(scene_id: int, kind: str, lang: str = "es") -> Path:
    from config.settings import settings

    sc = db.get("scenes", scene_id)
    files = (sc or {}).get("files") or {}
    p = (files.get("image") or {}).get("png") if kind == "image" else ((files.get("voice") or {}).get(lang) or {}).get("wav")
    if not p or not Path(p).is_file():
        raise HTTPException(404, "Archivo no disponible")
    p = Path(p).resolve()
    if settings.storage_dir.resolve() not in p.parents:
        raise HTTPException(403, "Ruta fuera del almacenamiento")
    return p


@router.get("/scenes/{scene_id}/image.png")
def scene_image(scene_id: int) -> FileResponse:
    return FileResponse(_scene_file(scene_id, "image"), media_type="image/png")


@router.get("/scenes/{scene_id}/voice/{lang}.wav")
def scene_voice(scene_id: int, lang: Literal["es", "en"]) -> FileResponse:
    return FileResponse(_scene_file(scene_id, "voice", lang), media_type="audio/wav")


class SceneIn(BaseModel):
    narration_es: str | None = Field(default=None, max_length=600)
    narration_en: str | None = Field(default=None, max_length=600)
    image_prompt: str | None = Field(default=None, max_length=1000)
    status: Literal["approved", "review", "pending"] | None = None


@router.patch("/scenes/{scene_id}")
def edit_scene(scene_id: int, body: SceneIn) -> dict:
    sc = db.get("scenes", scene_id)
    if not sc:
        raise HTTPException(404, "Escena no encontrada")
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if fields.get("status") == "approved" and sc["status"] == "blocked" and "narration_es" not in fields:
        raise HTTPException(409, "Una escena bloqueada no se puede aprobar: corrígela o regenérala")
    if "narration_es" in fields:
        from app.stories.script import estimate_duration

        fields["duration"] = estimate_duration(fields["narration_es"])
    db.update("scenes", scene_id, **fields)
    ep = db.get("episodes", sc["episode_id"])
    if "narration_es" in fields or "image_prompt" in fields:
        service.apply_guard(ep["id"], service.series_detail(ep["series_id"]))  # el guardián revisa cada cambio
    return service.episode_detail(ep["id"])


@router.post("/scenes/{scene_id}/regenerate")
def regenerate_scene(scene_id: int) -> dict:
    if not db.get("scenes", scene_id):
        raise HTTPException(404, "Escena no encontrada")
    from app.workers.tasks import story_scene_task

    story_scene_task(scene_id)
    return {"ok": True}
