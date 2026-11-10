"""Título, descripción y hashtags en español e inglés, y su reparto por plataforma."""
from __future__ import annotations

import logging
import re
import unicodedata

from app.clipper.detection.llm_judge import normalize_title
from app.shared.llm import ollama

log = logging.getLogger(__name__)

SYSTEM = """Eres community manager de vídeos cortos (TikTok, Reels, Facebook, YouTube Shorts).
Escribes títulos y descripciones naturales, sin clickbait engañoso, sin emojis y sin inventar datos.
Respondes SOLO con JSON válido."""

PROMPT = """Vídeo de origen: "{source}". Fragmento ({duration:.0f} s), transcripción en {lang_name}:
\"\"\"{text}\"\"\"
Título provisional: "{title}"

Devuelve JSON:
{{"title_es": "máx. 60 caracteres, mayúscula de frase",
 "title_en": "max 60 characters, sentence case, natural English",
 "description_es": "1-2 frases que inviten a ver el clip, máx. 180 caracteres",
 "description_en": "1-2 sentences, max 180 characters",
 "hashtags": ["8 hashtags relevantes en minúscula sin #, del más al menos importante"]}}"""

LANG_NAMES = {"es": "español", "en": "inglés"}

# Cuántos hashtags usa cada plataforma (más no ayuda y a veces penaliza).
PLATFORM_LIMITS = {"tiktok": 5, "instagram": 8, "facebook": 3, "youtube": 3}
PLATFORM_EXTRA = {"youtube": ["shorts"]}


def clean_tags(tags) -> list[str]:
    out: list[str] = []
    for t in tags if isinstance(tags, list) else []:
        t = re.sub(r"[^\wáéíóúñü]", "", str(t).lower().lstrip("#"))
        if t and t not in out and len(t) <= 30:
            out.append(t)
    return out


def platform_hashtags(tags: list[str]) -> dict[str, list[str]]:
    out = {}
    for p, n in PLATFORM_LIMITS.items():
        extra = PLATFORM_EXTRA.get(p, [])
        base = [t for t in tags if t not in extra][: n - len(extra) if extra else n]
        out[p] = base + extra
    return out


def strip_decoration(s: str) -> str:
    """Quita emojis y hashtags incrustados: la descripción va limpia y los hashtags aparte."""
    s = re.sub(r"#\w+", "", str(s or ""))
    s = "".join(ch for ch in s if unicodedata.category(ch) not in ("So", "Sk", "Cs", "Co") and ch not in "️‍")
    return re.sub(r"\s+([.,;:!?])", r"\1", re.sub(r"\s+", " ", s)).strip()


def _short(s: str, n: int) -> str:
    s = re.sub(r"\s+", " ", strip_decoration(str(s or ""))).strip().strip('"')
    return s if len(s) <= n else s[: n - 1].rsplit(" ", 1)[0] + "…"


def generate(text: str, source_title: str, title: str, duration: float, lang: str = "es",
             fallback_tags: list[str] | None = None) -> dict:
    """Metadatos del clip. Si el LLM falla, devuelve lo mínimo a partir de lo que ya hay."""
    base_tags = clean_tags(fallback_tags or [])
    meta = {"title_en": "", "description_es": "", "description_en": "", "hashtags": base_tags,
            "platforms": platform_hashtags(base_tags)}
    if len(text.split()) < 4:
        return meta
    try:
        res = ollama.generate_json(SYSTEM, PROMPT.format(source=source_title[:80], duration=duration,
                                                          lang_name=LANG_NAMES.get(lang, lang), text=text[:3000],
                                                          title=title))
    except Exception as e:  # noqa: BLE001
        log.warning("Metadatos sin LLM: %s", e)
        return meta
    tags = clean_tags(res.get("hashtags")) or base_tags
    meta.update({
        "title_es": normalize_title(str(res.get("title_es") or title)),
        "title_en": normalize_title(_short(res.get("title_en"), 80)),
        "description_es": _short(res.get("description_es"), 220),
        "description_en": _short(res.get("description_en"), 220),
        "hashtags": tags[:10],
        "platforms": platform_hashtags(tags),
    })
    return meta
