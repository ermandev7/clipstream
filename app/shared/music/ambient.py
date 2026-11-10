"""Música de fondo generada por código (CC0, autoría propia): un colchón en bucle por ambiente.

La biblioteca viene vacía y solo admite pistas con licencia registrada; estas bases garantizan que
una microserie siempre tenga música segura. Si el usuario importa pistas (Pixabay, YouTube Audio
Library, CC0), puede elegirlas en su lugar.

Síntesis aditiva con numpy: acordes con armónicos suaves, bajo, arpegios y percusión grave según el
ambiente. Todo se suma en un búfer circular, así el bucle no tiene costura.
"""
from __future__ import annotations

import json
import wave

import numpy as np
from config.settings import ASSETS_DIR

RATE = 48000
MUSIC_DIR = ASSETS_DIR / "music"
A4 = 440.0

# Acordes como semitonos sobre La 4 (raíz, tercera, quinta[, séptima]); 4 compases.
MOODS = {
    "solemne": {"title": "Base solemne", "bpm": 60, "beats": 4, "brightness": 0.55,
                "chords": [[-12, -9, -5, 0], [-16, -12, -9, -4], [-19, -15, -12, -7], [-14, -10, -7, -2]],
                "arp": False, "drum": False, "tremolo": 0.0},
    "epico": {"title": "Base épica", "bpm": 76, "beats": 4, "brightness": 0.7,
              "chords": [[-12, -9, -5], [-16, -12, -9], [-19, -15, -12], [-14, -10, -7]],
              "arp": False, "drum": True, "tremolo": 0.0},
    "alegre": {"title": "Base alegre", "bpm": 96, "beats": 4, "brightness": 0.8,
               "chords": [[-9, -5, -2], [-4, 0, 3], [-2, 2, 5], [-4, 0, 3]],
               "arp": True, "drum": False, "tremolo": 0.0},
    "misterio": {"title": "Base de misterio", "bpm": 56, "beats": 4, "brightness": 0.45,
                 "chords": [[-12, -9, -5], [-11, -8, -4], [-12, -9, -5], [-13, -10, -6]],
                 "arp": True, "drum": False, "tremolo": 0.25},
    "telenovela": {"title": "Base de telenovela", "bpm": 72, "beats": 4, "brightness": 0.75,
                   "chords": [[-9, -5, -2, 2], [-14, -10, -7, -3], [-11, -7, -4, 0], [-16, -12, -9, -5]],
                   "arp": True, "drum": False, "tremolo": 0.0},
}
TEMPLATE_MOOD = {"biblicas": "solemne", "mitologia": "epico", "fabulas": "alegre", "leyendas": "misterio",
                 "frutinovela": "telenovela"}
LOOPS = 4  # repeticiones de la progresión en el archivo (~1 min)


def _freq(semi: float) -> float:
    return A4 * 2 ** (semi / 12)


def _add(buf: np.ndarray, start: int, sig: np.ndarray) -> None:
    """Suma circular: lo que sobra al final entra por el principio (bucle sin costura)."""
    n = len(buf)
    idx = (start + np.arange(len(sig))) % n
    np.add.at(buf, idx, sig)


def _pad(freq: float, dur: float, brightness: float, rng: np.random.Generator) -> np.ndarray:
    t = np.arange(int(dur * RATE)) / RATE
    sig = np.zeros_like(t)
    for detune in (-0.12, 0.0, 0.12):  # coro: tres voces ligeramente desafinadas
        f = freq * 2 ** (detune / 12)
        ph = rng.uniform(0, 2 * np.pi)
        for h in range(1, 7):
            sig += (brightness ** (h - 1)) / h * np.sin(2 * np.pi * f * h * t + ph * h)
    env = np.minimum(1, t / 1.2) * np.clip((dur - t) / 1.6, 0, 1)  # entrada y salida lentas, solapan
    return sig * env / 6


def _pluck(freq: float, dur: float = 1.2) -> np.ndarray:
    t = np.arange(int(dur * RATE)) / RATE
    sig = np.sin(2 * np.pi * freq * t) + 0.3 * np.sin(2 * np.pi * 2 * freq * t) + 0.1 * np.sin(2 * np.pi * 3 * freq * t)
    return sig * np.exp(-t * 4.5) * np.minimum(1, t / 0.004)


def _drum(dur: float = 0.9) -> np.ndarray:
    t = np.arange(int(dur * RATE)) / RATE
    f = 38 + 60 * np.exp(-t * 18)
    return np.sin(2 * np.pi * np.cumsum(f) / RATE) * np.exp(-t * 5)


def synth(mood: str) -> np.ndarray:
    cfg = MOODS[mood]
    rng = np.random.default_rng(sum(map(ord, mood)))  # determinista
    beat = 60 / cfg["bpm"]
    bar = beat * cfg["beats"]
    total = bar * len(cfg["chords"]) * LOOPS
    buf = np.zeros(int(total * RATE))
    for loop in range(LOOPS):
        for c, chord in enumerate(cfg["chords"]):
            t0 = (loop * len(cfg["chords"]) + c) * bar
            s0 = int(t0 * RATE)
            for semi in chord:
                _add(buf, s0, 0.55 * _pad(_freq(semi), bar + 1.6, cfg["brightness"], rng))
            _add(buf, s0, 0.9 * _pad(_freq(chord[0] - 12), bar + 1.6, 0.35, rng))  # bajo
            if cfg["arp"]:
                notes = [*chord, chord[0] + 12, *reversed(chord[1:])]
                step = beat / 2
                for k in range(int(bar / step)):
                    _add(buf, int((t0 + k * step) * RATE), 0.22 * _pluck(_freq(notes[k % len(notes)] + 12)))
            if cfg["drum"]:
                for k in range(cfg["beats"]):
                    if k % 2 == 0:
                        _add(buf, int((t0 + k * beat) * RATE), 0.8 * _drum())
    if cfg["tremolo"]:
        t = np.arange(len(buf)) / RATE
        buf *= 1 - cfg["tremolo"] * (0.5 + 0.5 * np.sin(2 * np.pi * (1 / beat) * t))
    # Reverberación barata: ecos circulares decrecientes (también sin costura).
    wet = np.zeros_like(buf)
    for delay, gain in ((0.113, 0.35), (0.197, 0.28), (0.293, 0.2), (0.411, 0.14), (0.587, 0.09)):
        wet += gain * np.roll(buf, int(delay * RATE))
    out = buf + wet
    return out / (np.max(np.abs(out)) or 1) * 10 ** (-3 / 20)


def write_wav(path, x: np.ndarray) -> None:
    pcm = (np.clip(np.column_stack([x, np.roll(x, int(0.011 * RATE))]), -1, 1) * 32767).astype(np.int16)  # estéreo
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())


def track_id(mood: str) -> str:
    return f"ambient_{mood}.m4a"


def _encode(x: np.ndarray, dest) -> None:
    """WAV temporal -> AAC 160 kbps (~1 MB por pista en vez de 10 MB)."""
    import subprocess
    import tempfile
    from pathlib import Path

    from app.shared.render.ffmpeg import NO_WINDOW, ffmpeg_bin

    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "a.wav"
        write_wav(wav, x)
        subprocess.run([ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(wav), "-c:a", "aac",
                        "-b:a", "160k", str(dest)], check=True, creationflags=NO_WINDOW)


def generate_all() -> list[str]:
    MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    reg_path = ASSETS_DIR / "licenses.json"
    reg = json.loads(reg_path.read_text(encoding="utf-8"))
    out = []
    for mood, cfg in MOODS.items():
        _encode(synth(mood), MUSIC_DIR / track_id(mood))
        reg["files"][f"music/{track_id(mood)}"] = {"license": "CC0", "title": cfg["title"],
                                                  "author": "ClipStream (generado por código)",
                                                  "source": "app/shared/music/ambient.py", "mood": mood}
        out.append(track_id(mood))
    reg_path.write_text(json.dumps(reg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    from app.shared.rights import licenses

    licenses._registry.cache_clear()
    return out


if __name__ == "__main__":
    print(generate_all())
