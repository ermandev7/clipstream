"""Ajusta inicio y fin del candidato a frases completas (3 s – 3 min)."""
from __future__ import annotations

from config.settings import CLIP_MAX_S, CLIP_MIN_S

from app.clipper.detection.types import Candidate
from app.shared.transcription.types import Transcript, Word

PAUSE_S = 0.7  # una pausa así también cierra frase si Whisper no puso puntuación
LEAD_IN_S = 0.15
TAIL_S = 0.35
NO_SPEECH_PRE = 12.0  # tramo sin habla: segundos antes y después del pico
NO_SPEECH_POST = 8.0


def sentence_starts(words: list[Word]) -> list[float]:
    """Instantes donde empieza una frase."""
    starts: list[float] = []
    for i, w in enumerate(words):
        if i == 0:
            starts.append(w.start)
            continue
        prev = words[i - 1]
        if prev.ends_sentence or (w.start - prev.end) >= PAUSE_S:
            starts.append(w.start)
    return starts


def sentence_ends(words: list[Word]) -> list[float]:
    ends: list[float] = []
    for i, w in enumerate(words):
        nxt = words[i + 1] if i + 1 < len(words) else None
        if w.ends_sentence or nxt is None or (nxt.start - w.end) >= PAUSE_S:
            ends.append(w.end)
    return ends


def adjust(c: Candidate, transcript: Transcript | None, media_duration: float,
           min_s: float = CLIP_MIN_S, max_s: float = CLIP_MAX_S) -> Candidate:
    start, end = c.start, c.end

    if transcript is not None and len(transcript.slice(start, end)) < 4:
        # Sin habla en el tramo (música, juego, silencio): no hay frases a las que ajustarse, así
        # que nada de estirar hasta el máximo; un momento corto alrededor del pico.
        start = max(0.0, max(start, c.peak - NO_SPEECH_PRE))
        end = min(media_duration, min(end, c.peak + NO_SPEECH_POST))
        if end - start < min_s:
            end = min(media_duration, start + min_s)
        c.start, c.end = round(start, 2), round(end, 2)
        return c

    if transcript is not None and transcript.words:
        words = transcript.words
        starts = sentence_starts(words)
        ends = sentence_ends(words)
        # Inicio: la frase que empieza más cerca antes del inicio propuesto (o la que lo contiene).
        before = [s for s in starts if s <= start + 1.0]
        if before:
            start = before[-1]
        else:
            after = [s for s in starts if s >= start]
            if after and after[0] < c.peak:
                start = after[0]
        # Fin: primera frase que termina después del fin propuesto (sin pasar mucho).
        after_end = [e for e in ends if e >= end - 1.0]
        if after_end and after_end[0] - start <= max_s:
            end = after_end[0]
        else:
            fitting = [e for e in ends if start + min_s <= e <= start + max_s]
            if fitting:
                end = fitting[-1]
        start = max(0.0, start - LEAD_IN_S)
        end = end + TAIL_S

    end = min(end, media_duration)
    start = max(0.0, start)

    if end - start > max_s:
        # Recortar alrededor del pico, prefiriendo conservar lo anterior (el contexto).
        start = max(start, c.peak - max_s * 0.7)
        end = start + max_s
        if transcript is not None and transcript.words:
            fitting = [e for e in sentence_ends(transcript.words) if start + min_s <= e <= end]
            if fitting:
                end = fitting[-1] + TAIL_S
    if end - start < min_s:
        pad = (min_s - (end - start)) / 2
        start = max(0.0, start - pad)
        end = min(media_duration, start + min_s)
        start = max(0.0, end - min_s)

    c.start, c.end = round(start, 2), round(end, 2)
    return c
