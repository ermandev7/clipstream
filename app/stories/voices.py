"""Voz narradora con Kokoro-82M (pesos Apache 2.0) vía kokoro-onnx (MIT), en CPU.

La fonetización usa espeak-ng (GPL-3.0, vía phonemizer), estándar para español; se distribuye
con su licencia (ver installer/THIRD_PARTY.md).
"""
from __future__ import annotations

import logging
import threading
import wave
from functools import cache
from pathlib import Path

import numpy as np
from config.settings import settings

log = logging.getLogger(__name__)
KOKORO_DIR = settings.models_dir / "kokoro"
MODEL = "kokoro-v1.0.onnx"
VOICES_BIN = "voices-v1.0.bin"
MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"

VOICES = {
    "es": [{"id": "em_alex", "label": "Álex (hombre)"}, {"id": "ef_dora", "label": "Dora (mujer)"},
           {"id": "em_santa", "label": "Santa (hombre, grave)"}],
    "en": [{"id": "am_michael", "label": "Michael (man)"}, {"id": "af_heart", "label": "Heart (woman)"},
           {"id": "bm_george", "label": "George (man, British)"}, {"id": "af_bella", "label": "Bella (woman)"}],
}
DEFAULT_VOICE = {"es": "em_alex", "en": "am_michael"}
KOKORO_LANG = {"es": "es", "en": "en-us"}
_lock = threading.Lock()  # una síntesis a la vez (el modelo no es reentrante)


def available() -> bool:
    return (KOKORO_DIR / MODEL).exists() and (KOKORO_DIR / VOICES_BIN).exists()


@cache
def _engine():
    from kokoro_onnx import Kokoro

    if not available():
        raise FileNotFoundError("Falta el modelo de voz Kokoro: descárgalo en «Preparar ClipStream»")
    return Kokoro(str(KOKORO_DIR / MODEL), str(KOKORO_DIR / VOICES_BIN))


def synthesize(text: str, lang: str, voice: str | None, out_wav: Path, speed: float = 0.92) -> float:
    """Escribe el WAV (24 kHz mono) y devuelve su duración en segundos."""
    voice = voice or DEFAULT_VOICE[lang]
    with _lock:
        audio, sr = _engine().create(text, voice=voice, speed=speed, lang=KOKORO_LANG.get(lang, lang))
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return len(audio) / sr
