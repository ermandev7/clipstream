"""Asocia detecciones entre frames para obtener personas (tracks) estables."""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from app.clipper.reframe.detector import Box, FrameDet


@dataclass
class Track:
    id: int
    boxes: dict[int, Box] = field(default_factory=dict)  # índice de frame -> caja

    @property
    def last(self) -> Box:
        return self.boxes[max(self.boxes)]

    def presence(self, n_frames: int) -> float:
        return len(self.boxes) / n_frames if n_frames else 0.0

    def median_box(self) -> Box:
        bs = list(self.boxes.values())
        return Box(statistics.median(b.x for b in bs), statistics.median(b.y for b in bs),
                   statistics.median(b.w for b in bs), statistics.median(b.h for b in bs),
                   statistics.mean(b.score for b in bs), bs[0].kind)

    def center_std(self) -> float:
        bs = list(self.boxes.values())
        if len(bs) < 2:
            return 0.0
        return max(statistics.pstdev(b.cx for b in bs), statistics.pstdev(b.cy for b in bs))

    @property
    def kind(self) -> str:
        kinds = [b.kind for b in self.boxes.values()]
        return max(set(kinds), key=kinds.count)


def build_tracks(frames: list[FrameDet], max_dist: float = 0.12) -> list[Track]:
    """Asociación voraz por cercanía al último centro conocido (las personas no
    desaparecen en un clip: un track perdido se puede retomar más tarde)."""
    tracks: list[Track] = []
    for i, f in enumerate(frames):
        used: set[int] = set()
        for b in sorted(f.boxes, key=lambda b: b.score, reverse=True):
            best, best_d = None, max_dist + b.w  # caras grandes se mueven más en píxeles
            for t in tracks:
                if t.id in used:
                    continue
                lb = t.last
                d = ((lb.cx - b.cx) ** 2 + (lb.cy - b.cy) ** 2) ** 0.5
                if d < best_d:
                    best, best_d = t, d
            if best is None:
                best = Track(len(tracks))
                tracks.append(best)
            best.boxes[i] = b
            used.add(best.id)
    return tracks


def main_tracks(tracks: list[Track], n_frames: int, min_presence: float = 0.25) -> list[Track]:
    """Personas relevantes: presentes en buena parte del clip, ordenadas por importancia."""
    keep = [t for t in tracks if t.presence(n_frames) >= min_presence]
    return sorted(keep, key=lambda t: t.presence(n_frames) * t.median_box().h, reverse=True)
