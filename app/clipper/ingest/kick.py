"""Kick: vídeo publicado (VOD) y datos de canal/directo.

- La API pública oficial (api.kick.com/public/v1) exige app OAuth y, para eventos de chat,
  webhooks hacia una URL pública: no sirve para una app local sin servidor.
- Se usan los endpoints JSON que consume la propia web (kick.com/api/v1|v2), solo lectura.
- El extractor de VOD de yt-dlp está roto con la API actual (NOT_FOUND), así que el VOD se
  descarga desde su HLS público con yt-dlp como HLS genérico.
- El directo se captura con streamlink (plugin «kick»), igual que YouTube.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import httpx

from app.clipper.ingest.base import MediaSource, ProgressCb
from app.shared.render.ffmpeg import ffmpeg_bin, probe

log = logging.getLogger(__name__)
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ClipStream", "Accept": "application/json"}
KICK_RE = re.compile(r"^https?://(?:www\.)?kick\.com/(?P<path>[^?#]+)", re.I)
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def is_kick(url: str) -> bool:
    return bool(KICK_RE.match(url.strip()))


def parse(url: str) -> dict:
    """{'kind': 'vod'|'channel', 'slug': ..., 'uuid': ...}"""
    m = KICK_RE.match(url.strip())
    if not m:
        raise ValueError("El link no es de Kick")
    parts = [p for p in m.group("path").split("/") if p]
    u = UUID_RE.search(url)
    if u and ("videos" in parts or "video" in parts):
        return {"kind": "vod", "uuid": u.group(0), "slug": parts[0] if parts[0] != "video" else None}
    if parts and parts[0] not in ("video", "categories", "browse"):
        return {"kind": "channel", "slug": parts[0].lower()}
    raise ValueError("No reconozco ese link de Kick (usa kick.com/canal o kick.com/canal/videos/…)")


def _get(path: str) -> dict:
    r = httpx.get(f"https://kick.com{path}", headers=HEADERS, timeout=20, follow_redirects=True)
    if r.status_code == 404:
        raise ValueError("Kick no encuentra ese canal o vídeo")
    r.raise_for_status()
    return r.json()


def channel_info(slug: str) -> dict:
    d = _get(f"/api/v2/channels/{slug}")
    ls = d.get("livestream") or {}
    return {
        "slug": slug, "channel_id": d.get("id"), "chatroom_id": (d.get("chatroom") or {}).get("id"),
        "title": ls.get("session_title") or "", "channel": (d.get("user") or {}).get("username") or slug,
        "is_live": bool(ls.get("is_live")), "viewers": ls.get("viewer_count"),
    }


def viewers(slug: str) -> int | None:
    try:
        return channel_info(slug)["viewers"]
    except Exception:  # noqa: BLE001
        return None


OG_VIDEO_RE = re.compile(r'property="og:video" content="(https://[^"]+\.m3u8)"')
OG_TITLE_RE = re.compile(r'property="og:title" content="([^"]*)"')


def _vod_from_page(slug: str, uuid: str) -> dict:
    """Enlaces nuevos de Kick (identificador 01…): la API antigua no los conoce (404), pero la página del vídeo
    publica su HLS en og:video. Título, duración y canal se completan con la lista de vídeos del canal."""
    import html as html_mod

    r = httpx.get(f"https://kick.com/{slug}/videos/{uuid}", headers={**HEADERS, "Accept": "text/html"},
                  timeout=20, follow_redirects=True)
    if r.status_code == 404:
        raise ValueError("Kick no encuentra ese vídeo")
    r.raise_for_status()
    m = OG_VIDEO_RE.search(r.text)
    if not m:
        raise ValueError("Ese vídeo de Kick no tiene fuente pública (¿privado o borrado?)")
    src = html_mod.unescape(m.group(1))
    t = OG_TITLE_RE.search(r.text)
    title = html_mod.unescape(t.group(1)) if t else ""
    title = re.sub(rf"\s*-\s*{re.escape(slug)} VOD on KICK$", "", title, flags=re.I) or "Vídeo de Kick"
    info = {"source": src, "title": title, "channel": slug, "channel_id": None, "duration": 0.0,
            "start_time": None, "uuid": uuid}
    try:
        for v in _get(f"/api/v2/channels/{slug}/videos"):
            if v.get("source") == src:
                info.update(title=v.get("session_title") or title, channel_id=v.get("channel_id"),
                            duration=(v.get("duration") or 0) / 1000, start_time=v.get("start_time"))
                break
    except Exception:  # noqa: BLE001 - sin la lista, basta con la fuente
        pass
    return info


def vod_info(uuid: str, slug: str | None = None) -> dict:
    try:
        d = _get(f"/api/v1/video/{uuid}")
    except ValueError:
        if not slug:
            raise
        return _vod_from_page(slug, uuid)
    ls = d.get("livestream") or {}
    src = d.get("source") or ls.get("source")
    if not src:
        raise ValueError("Ese vídeo de Kick no tiene fuente pública (¿privado o borrado?)")
    return {
        "source": src, "title": ls.get("session_title") or "Vídeo de Kick",
        "channel": (ls.get("channel") or {}).get("slug") or "", "channel_id": ls.get("channel_id"),
        "duration": (ls.get("duration") or 0) / 1000, "start_time": ls.get("start_time"), "uuid": uuid,
    }


def download_vod(url: str, out_dir: Path, progress: ProgressCb | None = None) -> MediaSource:
    import yt_dlp

    p = parse(url)
    if p["kind"] != "vod":
        raise ValueError("Ese link es un canal: para un directo usa «Directo»; para un vídeo, su link /videos/…")
    info = vod_info(p["uuid"], p.get("slug"))
    out_dir.mkdir(parents=True, exist_ok=True)

    def hook(d: dict) -> None:
        if progress and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if total:
                progress(min(0.99, d.get("downloaded_bytes", 0) / total), "Descargando")
            elif d.get("fragment_count"):
                progress(min(0.99, (d.get("fragment_index") or 0) / d["fragment_count"]), "Descargando")

    opts = {
        "format": "best[height<=1080]/best",
        "outtmpl": str(out_dir / "source.%(ext)s"),
        "merge_output_format": "mp4",
        "ffmpeg_location": str(Path(ffmpeg_bin()).parent),
        "quiet": True, "no_warnings": True, "noprogress": True,
        "progress_hooks": [hook],
        "concurrent_fragment_downloads": 8,
        "http_headers": {"User-Agent": HEADERS["User-Agent"]},
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.extract_info(info["source"], download=True)
    files = sorted(out_dir.glob("source.*"))
    if not files:
        raise RuntimeError("No se pudo descargar el vídeo de Kick")
    path = files[0]
    if path.suffix != ".mp4":  # HLS en .ts: remux sin recodificar para que el resto del pipeline sea igual
        from app.shared.render import ffmpeg

        mp4 = path.with_suffix(".mp4")
        ffmpeg.run(["-i", str(path), "-c", "copy", "-movflags", "+faststart", str(mp4)])
        path.unlink(missing_ok=True)
        path = mp4
    media = probe(path)
    if progress:
        progress(1.0, "Descargado")
    return MediaSource(path=path, title=info["title"], channel=info["channel"], duration=media.duration, url=url,
                       was_live=True, meta={"uuid": info["uuid"], "platform": "kick", "channel_id": info["channel_id"]})
