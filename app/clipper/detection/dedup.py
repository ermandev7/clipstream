"""Fusiona candidatos que se solapan."""
from __future__ import annotations

from config.settings import CLIP_MAX_S

from app.clipper.detection.types import Candidate


def overlap(a: Candidate, b: Candidate) -> float:
    """Fracción del clip más corto que queda cubierta por el otro."""
    inter = max(0.0, min(a.end, b.end) - max(a.start, b.start))
    shorter = min(a.duration, b.duration)
    return inter / shorter if shorter > 0 else 0.0


def merge(cands: list[Candidate], threshold: float = 0.3, max_s: float = CLIP_MAX_S) -> list[Candidate]:
    """Recorre de mayor a menor puntuación; si un candidato se solapa con uno ya
    aceptado se fusiona en él (unión de rangos si cabe en max_s, si no se descarta).
    Se repite hasta que no cambie nada: al crecer, un clip fusionado puede pasar a
    solaparse con otro que se aceptó antes."""
    current = list(cands)
    while True:
        kept: list[Candidate] = []
        for c in sorted(current, key=lambda x: x.score, reverse=True):
            target = next((k for k in kept if overlap(k, c) >= threshold), None)
            if target is None:
                kept.append(c)
                continue
            new_start, new_end = min(target.start, c.start), max(target.end, c.end)
            if new_end - new_start <= max_s:
                target.start, target.end = new_start, new_end
                for key, val in c.signals.items():
                    target.signals[key] = max(target.signals.get(key, 0.0), val)
        if len(kept) == len(current):
            return sorted(kept, key=lambda x: x.start)
        current = kept


def gap(a: Candidate, b: Candidate) -> float:
    """Segundos entre dos clips (0 si se tocan o se solapan)."""
    return max(0.0, max(a.start, b.start) - min(a.end, b.end))


def select_diverse(cands: list[Candidate], count: int, min_gap: float = 45.0) -> list[Candidate]:
    """Los mejores `count`, evitando clips pegados entre sí (p. ej. varios seguidos
    durante un aplauso final). Si no hay suficientes separados, se rellena con el resto,
    pero nunca con uno que se solape con otro ya elegido (sería contenido repetido)."""
    ranked = sorted(cands, key=lambda c: c.score, reverse=True)
    chosen: list[Candidate] = []
    for c in ranked:
        if len(chosen) >= count:
            break
        if all(gap(c, k) >= min_gap for k in chosen):
            chosen.append(c)
    for c in ranked:
        if len(chosen) >= count:
            break
        if c not in chosen and all(overlap(c, k) < 0.15 for k in chosen):
            chosen.append(c)
    return sorted(chosen, key=lambda c: c.start)


MIN_SCORE = 25.0
MIN_CLIPS = 3


def drop_weak(cands: list[Candidate], min_score: float = MIN_SCORE, keep_at_least: int = MIN_CLIPS) -> list[Candidate]:
    """Quita candidatos flojos para no rellenar con clips sin interés; si quedarían menos de
    `keep_at_least`, se conservan los mejores aunque no lleguen al mínimo."""
    good = [c for c in cands if c.score >= min_score]
    if len(good) >= keep_at_least:
        return good
    return sorted(cands, key=lambda c: c.score, reverse=True)[:max(keep_at_least, len(good))]
