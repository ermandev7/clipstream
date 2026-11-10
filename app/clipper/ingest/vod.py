"""Vídeo publicado (o directo terminado) de YouTube, descargado con yt-dlp."""
from __future__ import annotations

import logging
from pathlib import Path

from app.clipper.ingest.base import MediaSource, ProgressCb
from app.shared.render.ffmpeg import ffmpeg_bin, probe

log = logging.getLogger(__name__)

FORMAT = "bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/b"


def fetch_info(url: str) -> dict:
    import yt_dlp

    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
        return ydl.extract_info(url, download=False)


def download_vod(url: str, out_dir: Path, progress: ProgressCb | None = None) -> MediaSource:
    import yt_dlp

    out_dir.mkdir(parents=True, exist_ok=True)

    def hook(d: dict) -> None:
        if progress and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if total:
                progress(min(0.99, d.get("downloaded_bytes", 0) / total), "Descargando")

    opts = {
        "format": FORMAT,
        "merge_output_format": "mp4",
        "outtmpl": str(out_dir / "source.%(ext)s"),
        "ffmpeg_location": str(Path(ffmpeg_bin()).parent),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "progress_hooks": [hook],
        "concurrent_fragment_downloads": 4,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if info.get("is_live"):
            raise ValueError("Este link es un directo en curso: usa la fuente «Directo».")
        path = Path(ydl.prepare_filename(info)).with_suffix(".mp4")
    if not path.exists():
        cands = sorted(out_dir.glob("source.*"))
        if not cands:
            raise RuntimeError("yt-dlp no produjo ningún archivo")
        path = cands[0]
    media = probe(path)
    if progress:
        progress(1.0, "Descargado")
    return MediaSource(
        path=path,
        title=info.get("title") or path.stem,
        channel=info.get("channel") or info.get("uploader") or "",
        duration=media.duration,
        url=url,
        was_live=bool(info.get("was_live")),
        meta={"id": info.get("id"), "thumbnail": info.get("thumbnail")},
    )
