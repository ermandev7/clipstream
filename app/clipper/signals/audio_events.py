"""Eventos de audio con YAMNet (Apache 2.0) vía MediaPipe, en CPU: música, risas, aplausos, gritos.

Devuelve series por segundo (0-1). Se procesa el WAV por trozos para que un vídeo de
varias horas no cargue todo el audio en memoria.
"""
from __future__ import annotations

import logging
import wave
from functools import cache
from pathlib import Path

import numpy as np

from app.clipper.reframe.detector import MODELS

log = logging.getLogger(__name__)
MODEL_URL = "https://storage.googleapis.com/mediapipe-models/audio_classifier/yamnet/float32/latest/yamnet.tflite"
CHUNK_S = 600

GROUPS = {
    "music": {"Music"},
    "laughter": {"Laughter", "Giggle", "Chuckle, chortle", "Belly laugh", "Snicker", "Baby laughter"},
    "applause": {"Applause", "Cheering", "Clapping", "Crowd"},
    "scream": {"Screaming", "Shout", "Yell", "Children shouting"},
    "speech": {"Speech"},
}


def ensure_model() -> Path:
    p = MODELS / "yamnet.tflite"
    if not p.exists() or p.stat().st_size == 0:
        import httpx

        MODELS.mkdir(parents=True, exist_ok=True)
        log.info("Descargando YAMNet (4 MB)")
        p.write_bytes(httpx.get(MODEL_URL, timeout=60, follow_redirects=True).raise_for_status().content)
    return p


@cache
def _classifier():
    from mediapipe.tasks.python import BaseOptions, audio

    return audio.AudioClassifier.create_from_options(audio.AudioClassifierOptions(
        base_options=BaseOptions(model_asset_path=str(ensure_model())), max_results=-1,
        running_mode=audio.RunningMode.AUDIO_CLIPS))


def classify_samples(x: np.ndarray, rate: int = 16000, offset: float = 0.0) -> list[tuple[float, float, dict[str, float]]]:
    """[(inicio_s, fin_s, {grupo: puntuación})] por ventana de YAMNet (~0,975 s)."""
    from mediapipe.tasks.python.components.containers import audio_data as ad

    if len(x) < rate // 2:
        return []
    out = []
    for r in _classifier().classify(ad.AudioData.create_from_array(x.astype(np.float32), rate)):
        scores = {g: 0.0 for g in GROUPS}
        for c in r.classifications[0].categories:
            for g, names in GROUPS.items():
                if c.category_name in names and c.score > scores[g]:
                    scores[g] = c.score
        t = offset + r.timestamp_ms / 1000
        out.append((t, t + 0.975, scores))
    return out


def to_series(windows: list[tuple[float, float, dict[str, float]]], duration_s: int) -> dict[str, np.ndarray]:
    """Ventanas -> series por segundo (máximo de las ventanas que tocan cada segundo)."""
    series = {g: np.zeros(duration_s) for g in GROUPS}
    for a, b, sc in windows:
        lo, hi = max(0, int(a)), min(duration_s, int(np.ceil(b)))
        for g, v in sc.items():
            if hi > lo:
                series[g][lo:hi] = np.maximum(series[g][lo:hi], v)
    return series


def classify_wav(wav_path: str | Path, offset: float = 0.0) -> dict[str, np.ndarray]:
    """WAV mono 16 kHz (el que se extrae para Whisper) -> series por segundo."""
    with wave.open(str(wav_path), "rb") as w:
        rate, n = w.getframerate(), w.getnframes()
        if w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError("Se espera WAV mono de 16 bits")
        windows = []
        pos = 0
        while pos < n:
            frames = w.readframes(CHUNK_S * rate)
            x = np.frombuffer(frames, np.int16).astype(np.float32) / 32768.0
            windows += classify_samples(x, rate, offset + pos / rate)
            pos += len(x)
    return to_series(windows, int(np.ceil(offset + n / rate)))


def ranges(series: np.ndarray, start: float, end: float, threshold: float = 0.3, min_len: float = 3.0,
           max_gap: float = 2.0) -> list[tuple[float, float]]:
    """Tramos [a, b) dentro de [start, end) donde la serie supera el umbral (huecos cortos se unen)."""
    lo, hi = max(0, int(start)), min(len(series), int(np.ceil(end)))
    out: list[list[float]] = []
    for i in range(lo, hi):
        if series[i] >= threshold:
            if out and i - out[-1][1] <= max_gap:
                out[-1][1] = i + 1
            else:
                out.append([i, i + 1])
    return [(max(a, start), min(b, end)) for a, b in out if min(b, end) - max(a, start) >= min_len]
