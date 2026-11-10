"""Subtítulos traducidos: frases del clip -> LLM -> palabras con tiempos aproximados.

Se traduce por frases (no palabra a palabra) y cada palabra traducida recibe un trozo del
tiempo de su frase proporcional a su longitud. Las traducciones se cachean por fuente, así
que recortar el clip no obliga a traducir de nuevo.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from app.shared.llm import ollama
from app.shared.transcription.types import Word

log = logging.getLogger(__name__)
PAUSE_S = 0.7
MAX_SENTENCE_WORDS = 18

SYSTEM = """You are a professional subtitle translator. Translate naturally and concisely,
keeping the meaning, tone and slang. Never add explanations. Reply ONLY with valid JSON."""

PROMPT = """Translate each line from {src} to {dst}. Keep the same number of lines and order.
Lines:
{lines}

Return JSON: {{"lines": ["translation 1", "translation 2", ...]}}"""

LANG = {"es": "Spanish", "en": "English"}


@dataclass
class Sentence:
    start: float
    end: float
    text: str

    @property
    def key(self) -> str:
        return hashlib.sha1(f"{self.start:.2f}|{self.text}".encode()).hexdigest()[:16]


def sentences(words: list[Word], start: float, end: float) -> list[Sentence]:
    """Frases dentro de [start, end]: corte en puntuación final, pausa larga o longitud máxima."""
    ws = [w for w in words if w.end > start and w.start < end]
    out: list[Sentence] = []
    cur: list[Word] = []
    for i, w in enumerate(ws):
        cur.append(w)
        nxt = ws[i + 1] if i + 1 < len(ws) else None
        if w.ends_sentence or nxt is None or nxt.start - w.end >= PAUSE_S or len(cur) >= MAX_SENTENCE_WORDS:
            out.append(Sentence(cur[0].start, cur[-1].end, " ".join(x.text.strip() for x in cur)))
            cur = []
    return out


def to_words(sents: list[tuple[Sentence, str]]) -> list[Word]:
    """Reparte el tiempo de cada frase entre sus palabras traducidas según su longitud."""
    out: list[Word] = []
    for s, text in sents:
        toks = text.split()
        if not toks:
            continue
        weights = [len(t) + 2 for t in toks]
        total = sum(weights)
        t = s.start
        span = max(0.2, s.end - s.start)
        for tok, wgt in zip(toks, weights):
            d = span * wgt / total
            out.append(Word(round(t, 3), round(t + d * 0.92, 3), tok))
            t += d
    return out


class TranslationCache:
    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            self.data: dict[str, dict[str, str]] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.data = {}

    def get(self, lang: str, key: str) -> str | None:
        return self.data.get(lang, {}).get(key)

    def put(self, lang: str, key: str, text: str) -> None:
        self.data.setdefault(lang, {})[key] = text

    def save(self) -> None:
        self.path.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")


def translate_sentences(sents: list[Sentence], src: str, dst: str, cache: TranslationCache | None = None,
                        batch: int = 25) -> list[tuple[Sentence, str]]:
    if src == dst:
        return [(s, s.text) for s in sents]
    done: dict[str, str] = {}
    for s in sents:
        hit = cache.get(dst, s.key) if cache else None
        if hit is not None:
            done[s.key] = hit
    todo = [s for s in sents if s.key not in done]
    for i in range(0, len(todo), batch):
        chunk = todo[i : i + batch]
        lines = "\n".join(f"{k + 1}. {s.text}" for k, s in enumerate(chunk))
        try:
            res = ollama.generate_json(SYSTEM, PROMPT.format(src=LANG.get(src, src), dst=LANG.get(dst, dst), lines=lines),
                                       temperature=0.2)
            got = [str(x).strip() for x in res.get("lines", [])]
        except Exception as e:  # noqa: BLE001
            log.warning("Traducción fallida: %s", e)
            got = []
        if len(got) != len(chunk):
            log.warning("El LLM devolvió %d líneas para %d frases; se traducen una a una", len(got), len(chunk))
            got = []
            for s in chunk:
                try:
                    r = ollama.generate_json(SYSTEM, PROMPT.format(src=LANG.get(src, src), dst=LANG.get(dst, dst),
                                                                   lines=f"1. {s.text}"), temperature=0.2)
                    got.append(str((r.get("lines") or [s.text])[0]).strip())
                except Exception:  # noqa: BLE001
                    got.append(s.text)
        for s, t in zip(chunk, got):
            t = t.split(". ", 1)[1] if t[:3].rstrip(". ").isdigit() and ". " in t[:5] else t  # "3. texto" -> "texto"
            done[s.key] = t
            if cache:
                cache.put(dst, s.key, t)
    if cache and todo:
        cache.save()
    return [(s, done.get(s.key) or s.text) for s in sents]
