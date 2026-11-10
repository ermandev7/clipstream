"""Narración de episodios: síntesis por escena e idioma + tiempos por palabra para subtítulos.

Los tiempos salen de Whisper sobre el audio generado, pero el texto de los subtítulos es siempre
el del guion (exacto, ya revisado por el guardián): se alinean las palabras de ambos.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from pathlib import Path

from config.settings import settings

PAD = 0.45  # silencio al final de cada escena antes del corte


def _norm(w: str) -> str:
    w = unicodedata.normalize("NFKD", w.lower())
    return re.sub(r"[^a-z0-9ñ]", "", "".join(c for c in w if not unicodedata.combining(c)))


def align_words(script_text: str, heard: list[dict], duration: float) -> list[dict]:
    """Asigna tiempos a cada palabra del guion usando las palabras oídas por Whisper.

    Las coincidentes toman su tiempo; las demás se interpolan entre vecinas conocidas.
    """
    words = script_text.split()
    if not words:
        return []
    times: list[tuple[float, float] | None] = [None] * len(words)
    a = [_norm(w) for w in words]
    b = [_norm(h["text"]) for h in heard]
    for blk in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks():
        for k in range(blk.size):
            h = heard[blk.b + k]
            times[blk.a + k] = (float(h["start"]), float(h["end"]))
    # Interpolación de huecos (palabras no reconocidas o mal oídas), proporcional a su longitud.
    i = 0
    while i < len(words):
        if times[i] is not None:
            i += 1
            continue
        j = i
        while j < len(words) and times[j] is None:
            j += 1
        t0 = times[i - 1][1] if i > 0 else 0.0
        t1 = times[j][0] if j < len(words) else max(duration, t0 + 0.3 * (j - i))
        weights = [max(len(words[k]), 2) for k in range(i, j)]
        total, acc = sum(weights), t0
        for k, wgt in zip(range(i, j), weights, strict=True):
            step = (t1 - t0) * wgt / total
            times[k] = (round(acc, 3), round(acc + step, 3))
            acc += step
        i = j
    out, last = [], 0.0
    for w, (s, e) in zip(words, times, strict=True):  # type: ignore[misc]
        s = max(s, last)
        e = max(e, s + 0.05)
        out.append({"start": round(s, 3), "end": round(e, 3), "text": w})
        last = e
    return out


def episode_dir(series_id: int, number: int) -> Path:
    return settings.storage_dir / "series" / str(series_id) / f"ep{number:02d}"


def narrate_scene(text: str, lang: str, voice: str | None, out_wav: Path) -> dict:
    """Sintetiza la escena y devuelve {"wav", "duration", "words"}."""
    from app.shared.transcription.whisper import transcribe
    from app.stories import voices

    duration = voices.synthesize(text, lang, voice, out_wav)
    heard = [{"start": w.start, "end": w.end, "text": w.text} for w in transcribe(out_wav, language=lang).words]
    return {"wav": str(out_wav), "duration": round(duration, 3), "words": align_words(text, heard, duration)}
