"""Publicaciones: validar según las normas de TikTok, encolar (ahora o programado) y seguir su estado."""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from app.core import db
from app.core.events import bus
from app.publish import tiktok

log = logging.getLogger(__name__)
ACTIVE = ("scheduled", "queued", "uploading", "processing")


class PostError(ValueError):
    pass


def notify(post_id: int) -> None:
    bus.publish("post", post=db.get("posts", post_id))


def clip_video(clip: dict) -> Path:
    p = Path((clip.get("files") or {}).get("mp4") or "")
    if clip.get("status") != "ready" or not p.is_file():
        raise PostError("El clip aún no está generado")
    return p


def published_last_24h() -> int:
    since = time.time() - 86400
    return db.query("SELECT COUNT(*) AS n FROM posts WHERE platform='tiktok' AND status IN "
                    "('queued','uploading','processing','published') AND COALESCE(started_at, created_at) >= ?",
                    (since,))[0]["n"]


def validate(clip: dict, s: dict, creator: dict) -> dict:
    """Comprueba la petición contra creator_info y las normas de contenido de TikTok. Devuelve post_info."""
    if not s.get("consent"):
        raise PostError("Tienes que aceptar la declaración de TikTok antes de publicar")
    options = creator.get("privacy_level_options") or []
    privacy = s.get("privacy_level")
    if privacy not in options:
        raise PostError("Elige quién puede ver el vídeo (opción no disponible para esta cuenta)")
    commercial = bool(s.get("commercial"))
    organic, branded = bool(s.get("brand_organic")), bool(s.get("brand_content"))
    if commercial and not (organic or branded):
        raise PostError("Indica si el contenido promociona tu marca, a un tercero o ambos")
    if branded and privacy == "SELF_ONLY":
        raise PostError("El contenido de marca no puede ser privado")
    duration = float(clip["end"]) - float(clip["start"])
    max_d = creator.get("max_video_post_duration_sec")
    if max_d and duration > float(max_d) + 0.5:
        raise PostError(f"El clip dura {duration:.0f} s y esta cuenta solo puede publicar hasta {max_d} s")
    title = str(s.get("title") or "").strip()
    if len(title.encode("utf-16-le")) // 2 > 2200:
        raise PostError("El texto supera los 2200 caracteres que admite TikTok")
    return {
        "title": title, "privacy_level": privacy,
        # Las interacciones vienen desmarcadas por defecto; si la cuenta las tiene desactivadas, no se pueden activar.
        "disable_comment": not s.get("allow_comment") or bool(creator.get("comment_disabled")),
        "disable_duet": not s.get("allow_duet") or bool(creator.get("duet_disabled")),
        "disable_stitch": not s.get("allow_stitch") or bool(creator.get("stitch_disabled")),
        "brand_organic_toggle": commercial and organic, "brand_content_toggle": commercial and branded,
        "is_aigc": bool(s.get("is_aigc")),
        "video_cover_timestamp_ms": int(max(0.0, float(s.get("cover_ms") or 0))),
    }


def create_post(clip_id: int, s: dict, scheduled_at: float | None) -> dict:
    clip = db.get("clips", clip_id)
    if not clip:
        raise PostError("Clip no encontrado")
    clip_video(clip)
    creator = tiktok.creator_info()  # al momento: si la cuenta no puede publicar, TikTok lo dice aquí
    post_info = validate(clip, s, creator)
    pending = db.query("SELECT COUNT(*) AS n FROM posts WHERE platform='tiktok' AND status IN "
                       "('scheduled','queued','uploading','processing')", ())[0]["n"]
    if scheduled_at is None and published_last_24h() + 1 > tiktok.DAILY_CAP:
        raise PostError(f"Ya hay {tiktok.DAILY_CAP} publicaciones en las últimas 24 h (límite de TikTok): prográmala para más tarde")
    now = time.time()
    when = float(scheduled_at) if scheduled_at else None
    if when is not None and when < now - 60:
        raise PostError("La hora programada ya ha pasado")
    pid = db.insert("posts", clip_id=clip_id, platform="tiktok", status="scheduled" if when else "queued",
                    settings={**post_info, "account": creator.get("creator_nickname", "")},
                    scheduled_at=when, created_at=now, updated_at=now)
    if not when:
        from app.workers.tasks import publish_post_task

        publish_post_task(pid)
    notify(pid)
    log.info("Publicación %s creada (clip %s, %s, pendientes %s)", pid, clip_id, "programada" if when else "ahora", pending)
    return db.get("posts", pid)


def cancel(post_id: int) -> None:
    p = db.get("posts", post_id)
    if not p:
        raise PostError("Publicación no encontrada")
    if p["status"] not in ("scheduled", "queued"):
        raise PostError("Ya se ha empezado a subir: no se puede cancelar desde aquí")
    db.update("posts", post_id, status="cancelled", updated_at=time.time())
    notify(post_id)


def run_post(post_id: int) -> None:
    """Sube y sigue la publicación hasta PUBLISH_COMPLETE o FAILED."""
    p = db.get("posts", post_id)
    if not p or p["status"] not in ("queued", "scheduled"):
        return
    clip = db.get("clips", p["clip_id"])
    info = dict(p.get("settings") or {})
    info.pop("account", None)

    def set_(**f):
        db.update("posts", post_id, updated_at=time.time(), **f)
        notify(post_id)

    try:
        if published_last_24h() >= tiktok.DAILY_CAP:
            raise PostError(f"Límite de {tiktok.DAILY_CAP} publicaciones en 24 h alcanzado: reprográmala")
        mp4 = clip_video(clip)
        creator = tiktok.creator_info()  # la cuenta puede haber cambiado desde que se programó
        if info["privacy_level"] not in (creator.get("privacy_level_options") or []):
            raise PostError("La privacidad elegida ya no está disponible para esta cuenta")
        set_(status="uploading", progress=0.0, started_at=time.time(), error=None)
        last = [0.0]

        def prog(f: float) -> None:
            if f - last[0] >= 0.1 or f >= 1:
                last[0] = f
                set_(progress=round(f, 3))

        publish_id = tiktok.upload_video(mp4, info, prog)
        set_(status="processing", publish_id=publish_id, progress=1.0)
        deadline = time.time() + 15 * 60
        while time.time() < deadline:
            time.sleep(5)
            st = tiktok.post_status(publish_id)
            status = st.get("status")
            if status == "PUBLISH_COMPLETE":
                ids = st.get("publicaly_available_post_id") or []
                set_(status="published", post_id=str(ids[0]) if ids else None, published_at=time.time())
                return
            if status == "FAILED":
                raise PostError(f"TikTok rechazó el vídeo: {st.get('fail_reason') or 'motivo desconocido'}")
        set_(status="processing", error="TikTok sigue procesando el vídeo; revisa tu perfil en unos minutos")
    except (PostError, tiktok.TikTokError) as e:
        log.warning("Publicación %s: %s", post_id, e)
        set_(status="failed", error=str(e)[:400])
    except Exception as e:  # noqa: BLE001
        log.exception("Publicación %s fallida", post_id)
        set_(status="failed", error=f"Error inesperado: {e}"[:400])


# ---- programador: revisa cada 20 s si toca publicar algo ------------------------------------------
_stop = threading.Event()
_thread: threading.Thread | None = None


def due_posts(now: float | None = None) -> list[int]:
    now = now or time.time()
    return [r["id"] for r in db.query("SELECT id FROM posts WHERE status='scheduled' AND scheduled_at <= ? "
                                      "ORDER BY scheduled_at", (now,))]


def _loop() -> None:
    from app.workers.tasks import publish_post_task

    while not _stop.wait(20):
        try:
            for pid in due_posts():
                db.update("posts", pid, status="queued", updated_at=time.time())
                notify(pid)
                publish_post_task(pid)
        except Exception:  # noqa: BLE001
            log.exception("Programador de publicaciones")


def start_scheduler() -> None:
    global _thread
    # Lo que quedó a medias al cerrar la app se marca como fallido (no se sabe si llegó a TikTok).
    db.conn().execute("UPDATE posts SET status='failed', error='La aplicación se cerró durante la subida' "
                      "WHERE status IN ('uploading')")
    for r in db.query("SELECT id FROM posts WHERE status='queued'"):
        db.update("posts", r["id"], status="scheduled", scheduled_at=time.time())
    if _thread is None:
        _stop.clear()
        _thread = threading.Thread(target=_loop, name="publicador", daemon=True)
        _thread.start()


def stop_scheduler() -> None:
    _stop.set()
