from __future__ import annotations

from pathlib import Path

from app.clipper.ingest.base import MediaSource
from app.shared.render.ffmpeg import probe

ALLOWED_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}


def open_local(path: str | Path) -> MediaSource:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"No existe el archivo: {p}")
    if p.suffix.lower() not in ALLOWED_EXT:
        raise ValueError(f"Formato no soportado: {p.suffix}. Usa MP4, MOV o MKV.")
    info = probe(p)
    if not info.has_audio:
        raise ValueError("El archivo no tiene pista de audio.")
    return MediaSource(path=p, title=p.stem, duration=info.duration)
