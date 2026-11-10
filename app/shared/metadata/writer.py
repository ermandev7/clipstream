"""Archivo .txt con títulos, descripciones y hashtags (ES/EN y por plataforma) para subir a mano."""
from __future__ import annotations

from pathlib import Path

PLATFORM_NAMES = {"tiktok": "TikTok", "instagram": "Instagram Reels", "facebook": "Facebook", "youtube": "YouTube Shorts"}


def _tags(tags: list[str]) -> str:
    return " ".join(f"#{t}" for t in tags)


def build_txt(title: str, hashtags: list[str], source_title: str, channel: str, url: str | None,
              warnings: list[dict], credits: list[str] | None = None, meta: dict | None = None) -> str:
    meta = meta or {}
    lines = ["TÍTULO (ES)", title]
    if meta.get("title_en"):
        lines += ["", "TITLE (EN)", meta["title_en"]]
    lines += ["", "DESCRIPCIÓN (ES)", meta.get("description_es") or title]
    if meta.get("description_en"):
        lines += ["", "DESCRIPTION (EN)", meta["description_en"]]
    src = f"Fuente: {source_title}" + (f" — {channel}" if channel else "")
    lines += ["", src]
    if url:
        lines.append(url)
    if credits:
        lines += ["", "CRÉDITOS", *credits]
    platforms = meta.get("platforms") or {}
    lines += ["", "HASHTAGS", _tags(meta.get("hashtags") or hashtags) or "#clips #viral"]
    for key, name in PLATFORM_NAMES.items():
        if platforms.get(key):
            lines.append(f"  {name}: {_tags(platforms[key])}")
    if warnings:
        lines += ["", "AVISOS DE DERECHOS (no bloquean)", *(f"- {w['message']}" for w in warnings)]
    return "\n".join(lines) + "\n"


def write_txt(path: Path, **kw) -> Path:
    path.write_text(build_txt(**kw), encoding="utf-8")
    return path
