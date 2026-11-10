"""Decide qué efectos suenan y cuándo, según el modo elegido (ninguno / sutil / meme)."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from config.settings import ASSETS_DIR

from app.shared.rights.licenses import is_allowed

SFX_DIR = ASSETS_DIR / "sfx"
MODES = ("none", "subtle", "meme")
EMPHASIS_RE = re.compile(r"[!¡?¿]|\b(no puede ser|incre[ií]ble|brutal|qu[eé] locura|wow|madre m[ií]a|nunca|jam[aá]s)\b", re.I)


@dataclass
class SfxEvent:
    t: float  # segundos desde el inicio del clip
    sound: str
    gain_db: float

    def to_dict(self) -> dict:
        return asdict(self)


def sound_path(name: str):
    p = SFX_DIR / f"{name}.wav"
    if not p.exists():
        from app.shared.sfx.synth import generate_all

        generate_all()
    if not is_allowed(p):  # un efecto sin licencia permitida no se usa
        return None
    return p


def plan_sfx(mode: str, duration: float, peak: float, hook: bool, words: list[tuple[float, str]],
             zoom: bool = True) -> list[SfxEvent]:
    """`peak` y los tiempos de `words` son relativos al inicio del clip."""
    if mode not in ("subtle", "meme") or duration <= 0:
        return []
    ev: list[SfxEvent] = []
    peak = min(max(peak, 0.0), duration)
    if mode == "subtle":
        if hook:
            ev.append(SfxEvent(0.0, "whoosh", -20))
        if zoom and peak > 0.8:
            ev.append(SfxEvent(max(0.0, peak - 0.05), "pop", -16))
        return ev
    # meme: más presencia, pero sin saturar (mínimo 3 s entre golpes)
    if hook:
        ev.append(SfxEvent(0.0, "whoosh", -12))
    if peak > 2.0:
        ev.append(SfxEvent(max(0.0, peak - 1.5), "riser", -18))
    if peak > 0.5:
        ev.append(SfxEvent(peak, "boom", -9))
    last = -10.0
    used = 0
    for t, text in words:
        if used >= 4:
            break
        if not EMPHASIS_RE.search(text) or abs(t - peak) < 2.0 or t - last < 3.0 or t < 1.0 or t > duration - 0.3:
            continue
        ev.append(SfxEvent(t, "pop", -13))
        last, used = t, used + 1
    return sorted(ev, key=lambda e: e.t)
