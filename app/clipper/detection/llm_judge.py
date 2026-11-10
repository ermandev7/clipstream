"""El LLM evalúa el texto de cada candidato: gancho, se entiende solo, emoción."""
from __future__ import annotations

import logging
import re

from app.clipper.detection.types import Candidate
from app.shared.llm import ollama
from app.shared.transcription.types import Transcript

log = logging.getLogger(__name__)

SYSTEM = """Eres editor de vídeos cortos virales (TikTok, Reels, Shorts).
Evalúas fragmentos transcritos de directos y vídeos. Respondes SOLO con JSON válido."""

PROMPT = """Fragmento de "{source}" ({duration:.0f} s). Señales: {signals}.
Transcripción:
\"\"\"{text}\"\"\"

Evalúa si funciona como clip vertical independiente:
- gancho: ¿los primeros segundos atrapan?
- autonomia: ¿se entiende sin contexto previo?
- emocion: humor, sorpresa, tensión, polémica o información valiosa.

Sé exigente y usa toda la escala: un fragmento corriente vale 30-50, uno bueno 60-75,
y solo un momento excepcional (remate claro, giro, frase memorable) supera 85.
El título va en minúsculas normales (solo mayúscula inicial y nombres propios) y describe
lo que pasa en ESTE fragmento concreto; no repitas el título del vídeo.

Devuelve JSON:
{{"score": 0-100, "reason": "máx. 20 palabras en español",
 "title": "título en español, máx. 60 caracteres, sin emojis, sin comillas",
 "hook": "texto gancho en pantalla, máx. 6 palabras, MAYÚSCULAS",
 "hashtags": ["5 hashtags en minúscula sin #"]}}"""


def normalize_title(title: str, max_len: int = 80) -> str:
    """El LLM a veces devuelve TODO EN MAYÚSCULAS o mezclas raras ('LEVántATE'): frase normal."""
    t = re.sub(r"\s+", " ", title.strip().strip('"«»').strip())
    letters = [ch for ch in t if ch.isalpha()]
    upper = sum(ch.isupper() for ch in letters)
    words = [w for w in t.split() if any(ch.isalpha() for ch in w)]
    mixed_word = any(any(ch.isupper() for ch in w[1:]) and any(ch.islower() for ch in w) for w in words)
    # "Cómo Desarrollar Tu Talento": en español los títulos van en mayúscula de frase.
    capitalized = sum(1 for w in words if w.lstrip("¡¿\"'(")[:1].isupper())
    title_case = len(words) >= 3 and capitalized / len(words) > 0.6
    if letters and (upper / len(letters) > 0.5 or mixed_word or title_case):
        t = t.lower()
    # Primera letra en mayúscula, aunque empiece por ¡ o ¿.
    m = re.search(r"[^\W\d_]", t)
    if m:
        t = t[: m.start()] + t[m.start()].upper() + t[m.start() + 1 :]
    return t[:max_len].rstrip(" .,;:")


def _clean_tags(tags) -> list[str]:
    out: list[str] = []
    for t in tags if isinstance(tags, list) else []:
        t = re.sub(r"[^\wáéíóúñü]", "", str(t).lower().lstrip("#"))
        if t and t not in out:
            out.append(t)
    return out[:8]


def judge(c: Candidate, transcript: Transcript | None, source_title: str) -> Candidate:
    text = transcript.text_between(c.start, c.end) if transcript else ""
    if len(text.split()) < 4:
        # Sin habla suficiente: el LLM no puede opinar; nos quedamos con las señales.
        c.llm_score = None
        c.llm_reason = "Sin diálogo suficiente para evaluar"
        return c
    try:
        res = ollama.generate_json(SYSTEM, PROMPT.format(
            source=source_title[:80], duration=c.duration, signals=c.signals_label(), text=text[:3000]))
    except Exception as e:  # noqa: BLE001
        log.warning("LLM no disponible para juzgar: %s", e)
        return c
    try:
        c.llm_score = float(max(0, min(100, float(res.get("score", 0)))))
    except (TypeError, ValueError):
        c.llm_score = None
    c.llm_reason = str(res.get("reason", ""))[:200]
    c.title = normalize_title(str(res.get("title", "")))
    c.hook = str(res.get("hook", "")).strip().strip('"').upper()[:60]
    c.hashtags = _clean_tags(res.get("hashtags"))
    return c


def fallback_title(c: Candidate, transcript: Transcript | None) -> str:
    text = transcript.text_between(c.start, c.end) if transcript else ""
    words = text.split()
    if words:
        return " ".join(words[:8]) + ("…" if len(words) > 8 else "")
    t = int(c.peak)
    return f"Momento {t // 60}:{t % 60:02d}"
