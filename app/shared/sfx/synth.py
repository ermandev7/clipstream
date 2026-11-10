"""Efectos de sonido generados por código (sin muestras de terceros: CC0 de ClipStream).

    .venv\\Scripts\\python -m app.shared.sfx.synth   -> escribe assets/sfx/*.wav y los registra
"""
from __future__ import annotations

import json
import wave
from pathlib import Path

import numpy as np
from config.settings import ASSETS_DIR

RATE = 48000
SFX_DIR = ASSETS_DIR / "sfx"
rng = np.random.default_rng(7)  # determinista: mismos archivos en cada generación


def _t(seconds: float) -> np.ndarray:
    return np.arange(int(seconds * RATE)) / RATE


def _bandpass_sweep(noise: np.ndarray, f0: np.ndarray, q: float = 1.2) -> np.ndarray:
    """Filtro paso banda (biquad) con frecuencia central variable por muestra."""
    out = np.zeros_like(noise)
    x1 = x2 = y1 = y2 = 0.0
    for i, x in enumerate(noise):
        w0 = 2 * np.pi * f0[i] / RATE
        alpha = np.sin(w0) / (2 * q)
        b0, b2 = alpha, -alpha
        a0, a1, a2 = 1 + alpha, -2 * np.cos(w0), 1 - alpha
        y = (b0 * x + b2 * x2 - a1 * y1 - a2 * y2) / a0
        x2, x1, y2, y1 = x1, x, y1, y
        out[i] = y
    return out


def _env(t: np.ndarray, attack: float, release: float, total: float) -> np.ndarray:
    e = np.minimum(1.0, t / max(attack, 1e-4))
    tail = np.clip((total - t) / max(release, 1e-4), 0, 1)
    return e * tail


def whoosh(dur: float = 0.55, f_lo: float = 300, f_hi: float = 3200) -> np.ndarray:
    t = _t(dur)
    sweep = f_lo * (f_hi / f_lo) ** (t / dur)
    sig = _bandpass_sweep(rng.normal(0, 1, len(t)), sweep, q=1.6)
    return sig * np.sin(np.pi * t / dur) ** 1.5


def pop() -> np.ndarray:
    t = _t(0.09)
    f = 900 * np.exp(-t * 30) + 180
    phase = 2 * np.pi * np.cumsum(f) / RATE
    return np.sin(phase) * np.exp(-t * 45)


def ding() -> np.ndarray:
    t = _t(1.3)
    tone = (np.sin(2 * np.pi * 1318.5 * t) + 0.45 * np.sin(2 * np.pi * 2637 * t) + 0.2 * np.sin(2 * np.pi * 3955 * t))
    return tone * np.exp(-t * 3.2) * np.minimum(1, t / 0.004)


def boom() -> np.ndarray:
    t = _t(1.4)
    f = 38 + 50 * np.exp(-t * 6)
    body = np.sin(2 * np.pi * np.cumsum(f) / RATE) * np.exp(-t * 2.4)
    click = rng.normal(0, 1, len(t)) * np.exp(-t * 60) * 0.5
    return np.tanh((body + click) * 1.6)


def riser(dur: float = 1.5) -> np.ndarray:
    t = _t(dur)
    sweep = 200 * (10 ** (t / dur))
    noise = _bandpass_sweep(rng.normal(0, 1, len(t)), sweep, q=2.5)
    tone = 0.3 * np.sin(2 * np.pi * np.cumsum(sweep) / RATE)
    return (noise + tone) * (t / dur) ** 2


def swipe() -> np.ndarray:
    return whoosh(0.28, 900, 5000)


def scratch() -> np.ndarray:
    """Disco rayado: dos pasadas de ruido filtrado con el tono subiendo y bajando."""
    t = _t(0.5)
    f = 400 + 2600 * np.abs(np.sin(2 * np.pi * 2.2 * t))
    sig = _bandpass_sweep(rng.normal(0, 1, len(t)), f, q=3.0)
    return sig * _env(t, 0.005, 0.12, 0.5)


def fail() -> np.ndarray:
    """Trombón triste: cuatro notas que bajan, la última larga y con vibrato («wah wah wah waaah»)."""
    notes = [(311.1, 0.38), (293.7, 0.38), (277.2, 0.38), (261.6, 1.1)]
    out = []
    for i, (f0, d) in enumerate(notes):
        t = _t(d)
        vib = 1 + (0.012 * np.sin(2 * np.pi * 6 * t) if i == 3 else 0)
        bend = 1 - (0.06 * (t / d) if i == 3 else 0.015 * (t / d))
        ph = 2 * np.pi * np.cumsum(f0 * vib * bend) / RATE
        tone = sum(np.sin(k * ph) / k ** 1.3 for k in range(1, 7))
        out.append(tone * _env(t, 0.03, 0.12 if i < 3 else 0.5, d))
    return np.concatenate(out)


def crickets() -> np.ndarray:
    """Grillos: trinos agudos en grupos (silencio incómodo)."""
    t = _t(1.8)
    carrier = np.sin(2 * np.pi * 4300 * t)
    chirp = (np.sin(2 * np.pi * 30 * t) > 0.2).astype(float)  # pulsos rápidos
    groups = ((t % 0.6) < 0.22).astype(float)  # grupos de trinos
    smooth = np.convolve(chirp * groups, np.ones(80) / 80, mode="same")
    return carrier * smooth * 0.8


def cash() -> np.ndarray:
    """Caja registradora («ka-ching»): golpe metálico y campana aguda."""
    t1 = _t(0.08)
    click = rng.normal(0, 1, len(t1)) * np.exp(-t1 * 70)
    t2 = _t(1.0)
    bell = (np.sin(2 * np.pi * 2093 * t2) + 0.6 * np.sin(2 * np.pi * 3136 * t2) + 0.3 * np.sin(2 * np.pi * 4186 * t2))
    bell *= np.exp(-t2 * 3.5)
    gap = np.zeros(int(0.06 * RATE))
    return np.concatenate([click, gap, bell * 0.8])


def boing() -> np.ndarray:
    """Muelle: tono con oscilación de frecuencia que se apaga."""
    t = _t(0.7)
    f = 180 + 90 * np.sin(2 * np.pi * 14 * t) * np.exp(-t * 4) + 120 * np.exp(-t * 6)
    return np.sin(2 * np.pi * np.cumsum(f) / RATE) * np.exp(-t * 4.5)


SOUNDS = {
    "whoosh": ("Whoosh", whoosh),
    "swipe": ("Barrido corto", swipe),
    "pop": ("Pop", pop),
    "ding": ("Ding", ding),
    "boom": ("Impacto grave", boom),
    "riser": ("Subida", riser),
    "scratch": ("Disco rayado", scratch),
    "fail": ("Trombón triste", fail),
    "crickets": ("Grillos", crickets),
    "cash": ("Caja registradora", cash),
    "boing": ("Muelle", boing),
}


def write_wav(path: Path, x: np.ndarray, peak_db: float = -1.0) -> None:
    x = x / (np.max(np.abs(x)) or 1.0) * 10 ** (peak_db / 20)
    pcm = (np.clip(x, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())


def generate_all() -> list[Path]:
    SFX_DIR.mkdir(parents=True, exist_ok=True)
    reg_path = ASSETS_DIR / "licenses.json"
    reg = json.loads(reg_path.read_text(encoding="utf-8"))
    out = []
    for name, (title, fn) in SOUNDS.items():
        p = SFX_DIR / f"{name}.wav"
        write_wav(p, fn())
        reg["files"][f"sfx/{name}.wav"] = {"license": "CC0", "title": title, "author": "ClipStream (generado por código)",
                                           "source": "app/shared/sfx/synth.py"}
        out.append(p)
    reg_path.write_text(json.dumps(reg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out


if __name__ == "__main__":
    for p in generate_all():
        print(p.name, p.stat().st_size)
