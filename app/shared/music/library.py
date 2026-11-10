"""Biblioteca de música: solo pistas de assets/music con licencia permitida en licenses.json."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from config.settings import ALLOWED_LICENSES, ASSETS_DIR

from app.shared.rights import licenses

MUSIC_DIR = ASSETS_DIR / "music"
AUDIO_EXT = {".mp3", ".wav", ".ogg", ".m4a", ".flac"}
REGISTRY = ASSETS_DIR / "licenses.json"


def tracks() -> list[dict]:
    out = []
    reg = json.loads(REGISTRY.read_text(encoding="utf-8")).get("files", {})
    for p in sorted(MUSIC_DIR.glob("*")):
        if p.suffix.lower() not in AUDIO_EXT:
            continue
        e = reg.get(f"music/{p.name}") or {}
        out.append({"id": p.name, "title": e.get("title") or p.stem, "author": e.get("author", ""),
                    "license": e.get("license"), "source": e.get("source", ""), "allowed": licenses.is_allowed(p)})
    return out


def track_path(track_id: str) -> Path | None:
    p = MUSIC_DIR / Path(track_id).name  # sin rutas: solo nombres de la biblioteca
    return p if p.is_file() and licenses.is_allowed(p) else None


def credit(track_id: str) -> str | None:
    p = track_path(track_id)
    return licenses.credit_line(p) if p else None


def import_track(src: str | Path, license_id: str, title: str, author: str, source_url: str) -> dict:
    """Copia una pista a la biblioteca y registra su licencia. Rechaza licencias no permitidas."""
    src = Path(src)
    if not src.is_file() or src.suffix.lower() not in AUDIO_EXT:
        raise ValueError("Elige un archivo de audio (MP3, WAV, OGG, M4A o FLAC)")
    if license_id not in ALLOWED_LICENSES:
        raise ValueError(f"Licencia no permitida. Solo: {', '.join(ALLOWED_LICENSES)}")
    if license_id == "CC-BY" and not author.strip():
        raise ValueError("Las pistas CC-BY necesitan el autor para el crédito")
    MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    base = re.sub(r"[^\w\-]+", "_", src.stem).strip("_")[:60] or "pista"
    dest = MUSIC_DIR / f"{base}{src.suffix.lower()}"
    n = 2
    while dest.exists():
        dest = MUSIC_DIR / f"{base}_{n}{src.suffix.lower()}"
        n += 1
    shutil.copy2(src, dest)
    reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
    reg["files"][f"music/{dest.name}"] = {"license": license_id, "title": title.strip() or src.stem,
                                          "author": author.strip(), "source": source_url.strip()}
    REGISTRY.write_text(json.dumps(reg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    getattr(licenses._registry, "cache_clear", lambda: None)()  # releer el registro
    return next(t for t in tracks() if t["id"] == dest.name)
