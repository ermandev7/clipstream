from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from app.clipper import common, live
from app.clipper.ingest.local_file import ALLOWED_EXT
from app.core import db
from app.core.events import log_event, source_changed
from app.shared.render import ffmpeg

router = APIRouter(prefix="/api/sources", tags=["sources"])
log = logging.getLogger(__name__)

YOUTUBE_RE = re.compile(r"^https?://(www\.|m\.)?(youtube\.com|youtu\.be)/", re.I)
KICK_RE = re.compile(r"^https?://(www\.)?kick\.com/", re.I)


class SourceIn(BaseModel):
    kind: Literal["live", "vod", "local"]
    platform: Literal["youtube", "kick"] | None = "youtube"
    url: str | None = None
    path: str | None = None
    # «Empezar limpio»: borra los vídeos anteriores (y sus clips) antes de empezar este.
    clean_previous: bool = False
    # Directo: además de los picos fuertes, el mejor momento de cada bloque de N segundos (0 = solo picos).
    live_every: Literal[0, 180, 300, 600] = 300
    # Modo Automático: analiza, genera y deja la tanda de TikTok preparada para aprobar (nada se publica sin aprobar).
    auto_publish: bool = False


def _validate(body: SourceIn) -> None:
    if body.kind == "local":
        if not body.path or not Path(body.path).is_file():
            raise HTTPException(400, "Elige un archivo de vídeo con «Abrir ubicación…».")
        if Path(body.path).suffix.lower() not in ALLOWED_EXT:
            raise HTTPException(400, "Formato no soportado. Elige un MP4, MOV o MKV.")
        return
    url = (body.url or "").strip()
    if not url:
        raise HTTPException(400, "Pega el link del vídeo o del directo.")
    if KICK_RE.match(url):
        from app.clipper.ingest.kick import parse

        try:
            p = parse(url)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        if body.kind == "live" and p["kind"] != "channel":
            raise HTTPException(400, "Para un directo de Kick pega el link del canal (kick.com/canal).")
        if body.kind == "vod" and p["kind"] != "vod":
            raise HTTPException(400, "Para un vídeo publicado de Kick pega su link (kick.com/canal/videos/…).")
        return
    if body.platform == "kick":
        raise HTTPException(400, "El link no parece de Kick.")
    if not YOUTUBE_RE.match(url):
        raise HTTPException(400, "El link no parece de YouTube.")


def platform_of(url: str) -> str:
    return "kick" if KICK_RE.match(url.strip()) else "youtube"


@router.get("")
def list_sources() -> list[dict]:
    rows = db.query("SELECT * FROM sources ORDER BY id DESC LIMIT 100")
    counts = {r["source_id"]: r["n"] for r in db.query("SELECT source_id, COUNT(*) AS n FROM clips GROUP BY source_id")}
    for r in rows:
        r["clip_count"] = counts.get(r["id"], 0)
    return rows


@router.post("")
def create_source(body: SourceIn) -> dict:
    _validate(body)
    from app.workers.tasks import analyze_media_task

    if body.clean_previous:
        from app.clipper import cleanup

        res = cleanup.delete_others(set())
        log.info("Empezar limpio: %s vídeos borrados, %.0f MB liberados", res["deleted"], res["freed_bytes"] / 1e6)

    if body.kind == "live":
        url = body.url.strip()
        platform = platform_of(url)
        try:
            if platform == "kick":
                from app.clipper.ingest.kick import channel_info, parse

                info = channel_info(parse(url)["slug"])
            else:
                from app.clipper.ingest.youtube_live import live_info

                info = live_info(url)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"No se pudo leer el directo: {e}") from e
        if not info["is_live"]:
            raise HTTPException(400, "Ese canal no está en directo ahora. Usa «Vídeo publicado».")
        sid = db.insert("sources", kind="live", platform=platform, url=url, title=info["title"], channel=info["channel"],
                        status="pending", auto_publish=int(body.auto_publish))
        try:
            live.start_live(sid, url, platform, body.live_every)
            mode = "solo momentos fuertes" if not body.live_every else f"un clip cada {body.live_every // 60} min como mínimo"
            log_event(sid, f"Modo: {mode} (más los picos fuertes al momento)" if body.live_every else f"Modo: {mode}")
        except Exception as e:  # noqa: BLE001
            db.update("sources", sid, status="error", error=str(e)[:500])
            raise HTTPException(400, f"No se pudo iniciar la captura: {e}") from e
    elif body.kind == "vod":
        sid = db.insert("sources", kind="vod", platform=platform_of(body.url), url=body.url.strip(), status="pending",
                        stage="En cola", auto_publish=int(body.auto_publish))
        analyze_media_task(sid)
    else:
        p = Path(body.path)
        sid = db.insert("sources", kind="local", path=str(p), title=p.stem, status="pending", stage="En cola",
                        auto_publish=int(body.auto_publish))
        analyze_media_task(sid)
    log_event(sid, "Fuente creada")
    source_changed(sid)
    return db.get("sources", sid)


@router.get("/storage")
def storage() -> dict:
    """Cuánto ocupa cada vídeo (original y audio por un lado, clips por otro)."""
    from app.clipper import cleanup

    return cleanup.storage_report()


class CleanupIn(BaseModel):
    ids: list[int] | None = None  # borrar estos
    keep: list[int] | None = None  # o borrar todos menos estos


@router.post("/cleanup")
def cleanup_sources(body: CleanupIn) -> dict:
    from app.clipper import cleanup

    if body.ids is not None:
        deleted, freed, errors = 0, 0, []
        for sid in body.ids:
            try:
                freed += cleanup.delete_source(sid)
                deleted += 1
            except cleanup.CleanupError as e:
                errors.append(f"#{sid}: {e}")
        return {"deleted": deleted, "freed_bytes": freed, "skipped": len(errors), "errors": errors}
    return cleanup.delete_others(set(body.keep or []))


@router.get("/{source_id}")
def get_source(source_id: int) -> dict:
    src = db.get("sources", source_id)
    if not src:
        raise HTTPException(404, "Fuente no encontrada")
    src["signals"] = common.load_signals(source_id)
    src["clips"] = db.query("SELECT * FROM clips WHERE source_id=? ORDER BY start", (source_id,))
    src["events"] = list(reversed(db.query("SELECT * FROM events WHERE source_id=? ORDER BY id DESC LIMIT 200", (source_id,))))
    src["permission"] = db.channel_permission(src.get("platform") or "youtube", src.get("channel") or "")
    return src


@router.get("/{source_id}/media")
def source_media(source_id: int):
    """Vídeo fuente completo para el monitor (vídeo publicado / archivo local)."""
    src = db.get("sources", source_id)
    if not src or not src.get("path") or not Path(src["path"]).is_file():
        raise HTTPException(404, "Vídeo no disponible")
    return FileResponse(src["path"], media_type="video/mp4")


@router.get("/{source_id}/frame")
def source_frame(source_id: int):
    """Último frame capturado de un directo (el monitor lo refresca cada pocos segundos)."""
    s = live.session(source_id)
    if not s:
        raise HTTPException(404, "No está capturando")
    segs = s.buffer.complete()
    if not segs:
        raise HTTPException(404, "Aún no hay vídeo")
    out = s.dir / "frame.jpg"
    if not out.exists() or out.stat().st_mtime < segs[-1].path.stat().st_mtime:
        # Primer fotograma del último trozo (empieza con fotograma clave). Con -sseof -1 a veces no había nada que
        # decodificar y ffmpeg fallaba al abrir el codificador JPG. yuvj420p: JPG en rango completo (ffmpeg 9).
        try:
            ffmpeg.run(["-i", str(segs[-1].path), "-frames:v", "1", "-vf", "scale=960:-2,format=yuvj420p",
                        "-q:v", "4", str(out)])
        except RuntimeError:
            if not out.exists():
                raise HTTPException(404, "Aún no hay imagen del directo") from None
            # Si falla, se sirve la imagen anterior en lugar de un error.
    return FileResponse(out, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


LIVE_SEGMENTS = 6  # ~60 s de directo en la lista: suficiente para arrancar sin cortes


@router.get("/{source_id}/live.m3u8")
def live_playlist(source_id: int) -> Response:
    """Lista HLS de los últimos trozos capturados: el monitor reproduce el directo fluido (con hls.js),
    exactamente lo que ClipStream está analizando (unos 10-20 s por detrás de la emisión)."""
    import math

    s = live.session(source_id)
    if not s:
        raise HTTPException(404, "No está capturando")
    segs = s.buffer.complete()[-LIVE_SEGMENTS:]
    if not segs:
        raise HTTPException(404, "Aún no hay vídeo")
    lines = ["#EXTM3U", "#EXT-X-VERSION:3",
             f"#EXT-X-TARGETDURATION:{math.ceil(max(x.duration for x in segs))}",
             f"#EXT-X-MEDIA-SEQUENCE:{segs[0].index}"]
    for x in segs:
        lines += [f"#EXTINF:{x.duration:.3f},", f"seg/{x.path.name}"]
    return Response("\n".join(lines) + "\n", media_type="application/vnd.apple.mpegurl",
                    headers={"Cache-Control": "no-store"})


@router.get("/{source_id}/seg/{name}")
def live_segment(source_id: int, name: str):
    from app.clipper.ingest.buffer import SEG_RE

    s = live.session(source_id)
    if not s or not SEG_RE.fullmatch(name):  # solo nombres seg_000123.ts: nada fuera del búfer
        raise HTTPException(404)
    p = s.buffer.dir / name
    if not p.is_file():
        raise HTTPException(404, "Ese trozo ya no está en el búfer")
    return FileResponse(p, media_type="video/mp2t", headers={"Cache-Control": "no-store"})


@router.post("/{source_id}/reprocess")
def reprocess(source_id: int) -> dict:
    from app.clipper.reprocess import NotReady, start_reprocess

    try:
        sid = start_reprocess(source_id)
    except NotReady as e:
        raise HTTPException(409, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"No se pudo localizar la repetición: {e}") from e
    return db.get("sources", sid)


@router.post("/{source_id}/stop")
def stop_source(source_id: int) -> dict:
    if not live.stop_live(source_id):
        raise HTTPException(400, "Esta fuente no está capturando")
    return {"ok": True}


class PermissionIn(BaseModel):
    permission: Literal["yes", "no", "program", "unknown"]


@router.post("/{source_id}/permission")
def set_permission(source_id: int, body: PermissionIn) -> dict:
    src = db.get("sources", source_id)
    if not src:
        raise HTTPException(404, "Fuente no encontrada")
    db.set_channel_permission(src.get("platform") or "youtube", src.get("channel") or "", body.permission)
    return {"ok": True}


@router.delete("/{source_id}")
def delete_source(source_id: int) -> dict:
    """Borra el vídeo, sus clips y sus archivos (antes solo se quitaba de la lista y el disco no se liberaba)."""
    from app.clipper import cleanup

    try:
        freed = cleanup.delete_source(source_id)
    except cleanup.CleanupError as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True, "freed_bytes": freed}
