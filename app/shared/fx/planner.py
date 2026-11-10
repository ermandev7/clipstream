"""Efectos de cada clip según lo que pasa en el vídeo: visuales (zoom, temblor, destello, pulsos)
y de sonido (solo cuando aportan algo).

Señales que se usan (todas ya detectadas en el análisis):
- Eventos de audio por segundo (YAMNet): risas, aplausos, gritos, música, voz.
- Chat (directos): donaciones y suscripciones por segundo.
- Transcripción: frases de sorpresa y de fallo, silencios incómodos.
- El momento cumbre del clip, el texto gancho y los cambios de plano del encuadre.

«Solo cuando hace falta»: cada efecto candidato tiene una prioridad; se eligen los más importantes
respetando una separación mínima y un tope según la duración. Con música de fondo o durante las risas
se reducen (el audio original ya aporta).
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

import numpy as np

VISUAL_MODES = ("none", "subtle", "dynamic")
SOUND_MODES = ("none", "auto", "subtle", "meme")

SURPRISE_RE = re.compile(
    r"\b(no puede ser|qu[eé] (?:dices|locura|fuerte)|en serio|incre[ií]ble|madre m[ií]a|wow|ostras|hostia|"
    r"no me lo creo|what|oh my god|no way)\b", re.I)
EMPHASIS_RE = re.compile(r"\b(mira|miren|ojo|atenci[oó]n|brutal|locura|loco|nunca|jam[aá]s|bestial|tremendo|"
                         r"look|crazy|insane|never)\b", re.I)
# Umbrales medidos con YAMNet en clips reales: las risas de conversación dan 0,15-0,3 (no 0,6);
# los aplausos de público, 0,85-0,97.
LAUGH, APPLAUSE, SCREAM = 0.15, 0.5, 0.3
FAIL_RE = re.compile(r"\b(fall[oó]|fallado|error|perd[ií]|perdimos|la cagu[eé]|no funciona|se rompi[oó]|"
                     r"ups|uy no|qu[eé] desastre|fail|lost|broke)\b", re.I)


@dataclass
class FxEvent:
    t: float  # segundos desde el inicio del clip
    kind: str  # punch | shake | flash | pulse
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Sound:
    t: float
    sound: str
    gain_db: float
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Context:
    duration: float
    peak: float  # relativo al inicio del clip
    hook: bool = False
    words: list[tuple[float, float, str]] = field(default_factory=list)  # (inicio, fin, texto), relativos
    events: dict[str, np.ndarray] = field(default_factory=dict)  # series por segundo; índice 0 = 1.er segundo del clip
    cuts: list[float] = field(default_factory=list)  # cambios de plano del encuadre


def starts(series: np.ndarray | None, thr: float, min_len: int = 1) -> list[float]:
    """Segundos donde la serie empieza a superar el umbral (tramos de al menos `min_len` s)."""
    if series is None or not len(series):
        return []
    on = np.asarray(series) >= thr
    out, i = [], 0
    while i < len(on):
        if on[i]:
            j = i
            while j < len(on) and on[j]:
                j += 1
            if j - i >= min_len:
                out.append(float(i))
            i = j
        else:
            i += 1
    return out


def _at(series: np.ndarray | None, t: float) -> float:
    if series is None or not len(series):
        return 0.0
    i = int(min(max(t, 0), len(series) - 1))
    return float(series[i])


def _near(series: np.ndarray | None, t: float, radius: float, thr: float) -> bool:
    if series is None or not len(series):
        return False
    lo, hi = int(max(0, t - radius)), int(min(len(series), t + radius + 1))
    return bool(hi > lo and np.max(series[lo:hi]) >= thr)


def silences(ctx: Context, min_gap: float = 1.8) -> list[float]:
    """Inicio de silencios incómodos: hueco largo entre palabras sin risas ni música."""
    out = []
    for (a0, a1, _), (b0, _b1, _bt) in zip(ctx.words, ctx.words[1:]):
        gap = b0 - a1
        if gap >= min_gap and 1.0 < a1 < ctx.duration - 2.0:
            mid = a1 + gap / 2
            if not _near(ctx.events.get("laughter"), mid, 1, LAUGH) and _at(ctx.events.get("music"), mid) < 0.4:
                out.append(a1)
    return out


# ---- sonido -----------------------------------------------------------------------------------

def _sound_candidates(ctx: Context, mode: str) -> list[tuple[int, Sound]]:
    """(prioridad, sonido). Cuanto mayor la prioridad, antes se elige."""
    meme = mode == "meme"
    g = (lambda auto, mm: mm if meme else auto)
    ev = ctx.events
    c: list[tuple[int, Sound]] = []
    if ctx.hook:
        c.append((9, Sound(0.0, "whoosh", g(-17, -12), "texto gancho")))
    for t in starts(ev.get("donation"), 0.5):
        c.append((8, Sound(t, "cash", g(-14, -10), "donación en el chat")))
    for t in starts(ev.get("sub"), 0.5):
        c.append((6, Sound(t, "ding", g(-17, -13), "suscripción en el chat")))
    peak = ctx.peak
    if 0.5 < peak < ctx.duration - 0.3:
        loud = _near(ev.get("scream"), peak, 1, SCREAM) or _near(ev.get("applause"), peak, 1, APPLAUSE)
        if meme and peak > 2.0:
            c.append((6, Sound(peak - 1.5, "riser", -18, "subida al momento cumbre")))
        # Un pico «tranquilo» cede ante un evento real del contenido (grito, fallo, donación…).
        c.append((7 if loud else 5, Sound(peak, "boom" if (loud or meme) else "pop", g(-13 if loud else -16, -9),
                                          "momento cumbre")))
    for t in starts(ev.get("scream"), SCREAM):
        c.append((7, Sound(t, "boom", g(-14, -10), "grito")))
    for t in starts(ev.get("applause"), APPLAUSE):
        c.append((5, Sound(t, "ding", g(-16, -13), "aplausos")))
    for a, b, text in ctx.words:
        if FAIL_RE.search(text):
            c.append((6, Sound(b + 0.15, "fail", g(-15, -11), f"«{text.strip()}»")))
        elif SURPRISE_RE.search(text):
            c.append((4 if meme else 3, Sound(a, "scratch" if meme else "pop", g(-17, -12), f"«{text.strip()}»")))
    if meme:
        for t in starts(ev.get("laughter"), LAUGH + 0.05):
            c.append((3, Sound(t, "boing", -14, "risas")))
        for t in silences(ctx):
            c.append((3, Sound(t + 0.3, "crickets", -15, "silencio incómodo")))
    for t in ctx.cuts:
        c.append((2, Sound(max(0.0, t - 0.15), "swipe", g(-20, -15), "cambio de plano")))
    return c


def plan_sounds(ctx: Context, mode: str) -> list[Sound]:
    if mode not in SOUND_MODES or mode == "none" or ctx.duration <= 0:
        return []
    cands = [(p, s) for p, s in _sound_candidates(ctx, mode) if 0 <= s.t <= ctx.duration - 0.3]
    if mode == "subtle":  # solo lo imprescindible, flojo
        keep = {"texto gancho", "momento cumbre", "donación en el chat"}
        cands = [(p, Sound(s.t, "pop" if s.sound == "boom" else s.sound, min(s.gain_db, -18), s.reason))
                 for p, s in cands if s.reason in keep]
    music = ctx.events.get("music")
    music_heavy = music is not None and len(music) and float(np.mean(np.asarray(music) >= 0.5)) > 0.5
    per = {"auto": 9.0, "subtle": 12.0, "meme": 5.5}[mode]
    budget = max(1, int(ctx.duration // per)) + (1 if ctx.hook else 0)
    if music_heavy and mode != "meme":
        budget = max(1, budget // 2)  # con música de fondo, solo lo esencial
    spacing = 2.0 if mode == "meme" else 3.0
    chosen: list[Sound] = []
    for prio, s in sorted(cands, key=lambda x: (-x[0], x[1].t)):
        if len(chosen) >= budget:
            break
        if music_heavy and mode != "meme" and prio < 5:
            continue
        if mode != "meme" and s.sound not in ("cash", "boom") and _at(ctx.events.get("laughter"), s.t) >= LAUGH + 0.05:
            continue  # durante las risas, el audio original ya hace el trabajo
        if any(abs(s.t - o.t) < spacing for o in chosen if not (o.sound == "riser" or s.sound == "riser")):
            continue
        chosen.append(s)
    return sorted(chosen, key=lambda s: s.t)


# ---- visuales ----------------------------------------------------------------------------------

def plan_visuals(ctx: Context, mode: str, sounds: list[Sound] | None = None) -> tuple[float, list[FxEvent]]:
    """(acercamiento lento total, eventos). El acercamiento lento da movimiento a planos estáticos."""
    if mode not in VISUAL_MODES or mode == "none" or ctx.duration <= 0:
        return 0.0, []
    ev = ctx.events
    out: list[FxEvent] = []
    if 0.3 < ctx.peak < ctx.duration - 0.3:
        out.append(FxEvent(ctx.peak, "punch", "momento cumbre"))
    if mode == "subtle":
        return 0.04, out
    for s in sounds or []:  # los golpes de sonido llevan su efecto visual
        if s.sound == "boom" and abs(s.t - ctx.peak) > 0.6:
            out.append(FxEvent(s.t, "shake", s.reason))
        elif s.sound in ("cash", "ding"):
            out.append(FxEvent(s.t, "flash", s.reason))
    for t in starts(ev.get("scream"), SCREAM):
        out.append(FxEvent(t, "shake", "grito"))
    for t in starts(ev.get("applause"), APPLAUSE):
        out.append(FxEvent(t, "flash", "aplausos"))
    if _near(ev.get("scream"), ctx.peak, 1, SCREAM) or _near(ev.get("applause"), ctx.peak, 1, APPLAUSE):
        out.append(FxEvent(ctx.peak, "flash", "momento cumbre intenso"))
    pulses = [FxEvent(t, "pulse", "risas") for t in starts(ev.get("laughter"), LAUGH)][:3]
    pulses += [FxEvent(a, "pulse", "sorpresa") for a, _b, text in ctx.words if SURPRISE_RE.search(text)][:2]
    pulses += [FxEvent(a, "pulse", f"énfasis «{text.strip()}»") for a, _b, text in ctx.words
               if EMPHASIS_RE.search(text)][:2]
    out += pulses
    # Sin amontonar: 1,2 s entre efectos del mismo tipo y nada pegado al principio o al final.
    clean: list[FxEvent] = []
    for e in sorted(out, key=lambda e: (e.t, e.kind)):
        if not 0.3 < e.t < ctx.duration - 0.4:
            continue
        if any(o.kind == e.kind and abs(o.t - e.t) < 1.2 for o in clean):
            continue
        if e.kind == "pulse" and any(o.kind == "punch" and abs(o.t - e.t) < 1.8 for o in clean):
            continue
        clean.append(e)
    return 0.06, clean


def clip_events(events: dict[str, np.ndarray] | None, start: float, end: float) -> dict[str, np.ndarray]:
    """Recorta las series por segundo de la fuente al tramo del clip (índice 0 = inicio del clip)."""
    if not events:
        return {}
    lo, hi = int(max(0, start)), int(np.ceil(end))
    return {k: np.asarray(v)[lo:hi] for k, v in events.items() if v is not None and len(v)}
