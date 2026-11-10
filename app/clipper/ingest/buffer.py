"""Buffer circular de segmentos de 10 s para directos."""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from app.shared.render import ffmpeg

log = logging.getLogger(__name__)
SEG_RE = re.compile(r"seg_(\d{6})\.ts$")


@dataclass
class Segment:
    index: int
    path: Path
    start: float  # tiempo de stream (s desde el inicio de la captura)
    duration: float


class SegmentBuffer:
    def __init__(self, directory: Path, segment_seconds: int = 10, max_minutes: int = 15) -> None:
        self.dir = directory
        self.dir.mkdir(parents=True, exist_ok=True)
        self.seg_s = segment_seconds
        self.max_segments = max(3, max_minutes * 60 // segment_seconds)
        self._lock = threading.Lock()
        self.wall_start: float | None = None  # hora de pared del segundo 0

    @property
    def pattern(self) -> str:
        return str(self.dir / "seg_%06d.ts")

    @property
    def list_path(self) -> Path:
        """ffmpeg escribe aquí 'archivo,inicio,fin' al cerrar cada segmento (tiempos reales:
        con -c copy los cortes caen en keyframes y no duran exactamente 10 s)."""
        return self.dir / "segments.csv"

    def _all(self) -> list[Segment]:
        segs: dict[int, Segment] = {}
        if self.list_path.exists():
            for line in self.list_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                parts = line.strip().split(",")
                if len(parts) < 3:
                    continue
                m = SEG_RE.search(parts[0])
                p = self.dir / parts[0]
                if m and p.exists():
                    try:
                        s, e = float(parts[1]), float(parts[2])
                    except ValueError:
                        continue
                    segs[int(m.group(1))] = Segment(int(m.group(1)), p, s, e - s)
        return sorted(segs.values(), key=lambda s: s.index)

    def complete(self) -> list[Segment]:
        """Segmentos cerrados (solo los que ffmpeg ya apuntó en la lista)."""
        return self._all()

    def head_time(self) -> float:
        segs = self.complete()
        return segs[-1].start + segs[-1].duration if segs else 0.0

    def prune(self, keep_from: float | None = None) -> None:
        """Borra segmentos fuera de la ventana; `keep_from` protege lo que aún se va a renderizar."""
        with self._lock:
            segs = self._all()
            excess = len(segs) - self.max_segments
            for s in segs[: max(0, excess)]:
                if keep_from is not None and s.start + s.duration > keep_from:
                    break
                try:
                    s.path.unlink()
                except OSError:
                    pass

    def range(self, start: float, end: float) -> list[Segment]:
        return [s for s in self.complete() if s.start + s.duration > start and s.start < end]

    def export(self, start: float, end: float, out: Path) -> tuple[Path, float]:
        """Une los segmentos que cubren [start, end] en un .ts. Devuelve (ruta, tiempo de stream del archivo)."""
        segs = self.range(start, end)
        if not segs:
            raise ValueError(f"El buffer no contiene {start:.0f}-{end:.0f} s")
        lst = out.with_suffix(".txt")
        lst.write_text("".join(f"file '{s.path.resolve().as_posix()}'\n" for s in segs), encoding="utf-8")
        ffmpeg.run(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)])
        lst.unlink(missing_ok=True)
        return out, segs[0].start

    def wait_for(self, t: float, timeout: float = 120.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.head_time() >= t:
                return True
            time.sleep(1.0)
        return False
