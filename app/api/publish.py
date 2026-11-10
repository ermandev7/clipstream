"""API de publicación en TikTok: credenciales de la app, conexión de la cuenta y publicaciones."""
from __future__ import annotations

import html
import webbrowser

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.core import db
from app.core.events import bus
from app.publish import service, tiktok

router = APIRouter(prefix="/api/publish", tags=["publish"])


@router.get("/tiktok")
def tiktok_status() -> dict:
    return {**tiktok.public_status(), "daily_cap": tiktok.DAILY_CAP, "last_24h": service.published_last_24h(),
            "music_url": tiktok.MUSIC_URL, "bc_policy_url": tiktok.BC_POLICY_URL}


class KeysIn(BaseModel):
    client_key: str = Field(min_length=4, max_length=200)
    client_secret: str = Field(min_length=4, max_length=500)


@router.put("/tiktok/keys")
def tiktok_keys(body: KeysIn) -> dict:
    tiktok.set_keys(body.client_key, body.client_secret)
    return tiktok_status()


class ConnectIn(BaseModel):
    open_browser: bool = True


@router.post("/tiktok/connect")
def tiktok_connect(body: ConnectIn) -> dict:
    try:
        url = tiktok.authorize_url()
    except tiktok.TikTokError as e:
        raise HTTPException(409, str(e)) from e
    if body.open_browser:
        webbrowser.open(url)  # el inicio de sesión de TikTok se abre en el navegador del sistema
    return {"url": url}


PAGE = """<!doctype html><html lang="es"><meta charset="utf-8"><title>ClipStream · TikTok</title>
<style>body{{background:#141415;color:#e6e6e7;font:15px/1.5 system-ui,sans-serif;display:grid;place-items:center;
height:100vh;margin:0}}div{{max-width:460px;border:1px solid #2c2c2f;background:#1a1a1c;padding:24px 28px}}
h1{{font-size:17px;margin:0 0 8px;color:{color}}}</style><div><h1>{title}</h1><p>{text}</p></div></html>"""


@router.get(tiktok.CALLBACK_PATH.removeprefix("/api/publish"), response_class=HTMLResponse)
def tiktok_callback(code: str | None = None, state: str | None = None, error: str | None = None,
                    error_description: str | None = None) -> HTMLResponse:
    try:
        tiktok.handle_callback(code, state, error, error_description)
    except (tiktok.TikTokError, KeyError) as e:
        bus.publish("tiktok", connected=False, error=str(e))
        return HTMLResponse(PAGE.format(color="#f2777b", title="No se pudo conectar con TikTok",
                                        text=html.escape(str(e))), status_code=400)
    bus.publish("tiktok", connected=True)
    return HTMLResponse(PAGE.format(color="#8fd19e", title="Cuenta de TikTok conectada",
                                    text="Ya puedes cerrar esta pestaña y volver a ClipStream."))


@router.post("/tiktok/disconnect")
def tiktok_disconnect() -> dict:
    tiktok.disconnect()
    bus.publish("tiktok", connected=False)
    return tiktok_status()


@router.get("/tiktok/creator")
def tiktok_creator() -> dict:
    """Datos de la cuenta en este momento (se piden al abrir la ventana de publicar, sin caché)."""
    try:
        return tiktok.creator_info()
    except tiktok.TikTokError as e:
        raise HTTPException(409 if e.code in ("not_connected", "refresh_expired") else 502,
                            {"code": e.code, "message": str(e)}) from e


class PostIn(BaseModel):
    clip_id: int
    title: str = Field(default="", max_length=2200)
    privacy_level: str | None = None
    allow_comment: bool = False
    allow_duet: bool = False
    allow_stitch: bool = False
    commercial: bool = False
    brand_organic: bool = False
    brand_content: bool = False
    is_aigc: bool = False
    consent: bool = False
    scheduled_at: float | None = None  # epoch (s); None = ahora


@router.post("/tiktok/posts")
def tiktok_post(body: PostIn) -> dict:
    try:
        return service.create_post(body.clip_id, body.model_dump(), body.scheduled_at)
    except service.PostError as e:
        raise HTTPException(400, str(e)) from e
    except tiktok.TikTokError as e:
        raise HTTPException(409, str(e)) from e


@router.get("/posts")
def list_posts(clip_id: int | None = Query(default=None), limit: int = Query(default=100, le=500)) -> list[dict]:
    if clip_id is not None:
        return db.query("SELECT * FROM posts WHERE clip_id=? ORDER BY id DESC LIMIT ?", (clip_id, limit))
    rows = db.query("SELECT p.*, c.title AS clip_title FROM posts p LEFT JOIN clips c ON c.id=p.clip_id "
                    "ORDER BY COALESCE(p.scheduled_at, p.created_at) DESC LIMIT ?", (limit,))
    return rows


@router.delete("/posts/{post_id}")
def cancel_post(post_id: int) -> dict:
    try:
        service.cancel(post_id)
    except service.PostError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True}
