"""Hablante activo: movimiento de labios (MediaPipe Face Landmarker, Apache 2.0) + voz.

Alternativa ligera a TalkNet/Light-ASD (que necesitan PyTorch, ~2,5 GB y VRAM extra, y cuyos
pesos no tienen una licencia clara). Para cada persona detectada se recorta su cabeza en alta
resolución a 8 fps y se mide cuánto varía la apertura de la mandíbula (blendshape «jawOpen»)
alrededor de cada instante. Solo cuenta mientras hay voz (palabras de Whisper): así una boca
que mastica o sonríe en silencio no «habla».

El resultado se escribe en `Box.talk` de los fotogramas de detección; el planificador lo usa
para elegir a quién encuadrar y para medir el tiempo que habla cada uno.
"""
from __future__ import annotations

import logging
from functools import cache
from pathlib import Path

import numpy as np

from app.clipper.reframe.detector import MODELS, FrameDet, ensure_models, sample_frames

log = logging.getLogger(__name__)
SAMPLE_FPS = 6.0
WINDOW_S = 0.4  # semiventana alrededor de cada fotograma de detección
SILENCE_FACTOR = 0.25


@cache
def _landmarker():
    from mediapipe.tasks.python import BaseOptions, vision

    ensure_models()
    return vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=str(MODELS / "face_landmarker.task")),
        output_face_blendshapes=True, num_faces=1))


def jaw_open(rgb: np.ndarray, box, scale: float = 1.8) -> float | None:
    """Apertura de mandíbula (0-1) de la persona de `box` en el fotograma completo `rgb`."""
    import mediapipe as mp

    h, w = rgb.shape[:2]
    side = max(box.w * w, box.h * h) * scale
    cx, cy = box.cx * w, box.cy * h
    x0, y0 = int(max(0, cx - side / 2)), int(max(0, cy - side / 2))
    x1, y1 = int(min(w, cx + side / 2)), int(min(h, cy + side / 2))
    if x1 - x0 < 24 or y1 - y0 < 24:
        return None
    crop = np.ascontiguousarray(rgb[y0:y1, x0:x1])
    res = _landmarker().detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=crop))
    if not res.face_blendshapes:
        return None
    return next((c.score for c in res.face_blendshapes[0] if c.category_name == "jawOpen"), None)


def speech_mask(words: list[tuple[float, float]], times: list[float], pad: float = 0.25) -> np.ndarray:
    """1 si hay alguna palabra cerca de cada instante (tiempos relativos al clip)."""
    out = np.zeros(len(times))
    for i, t in enumerate(times):
        if any(a - pad <= t <= b + pad for a, b in words):
            out[i] = 1.0
    return out


def annotate(frames: list[FrameDet], src: str | Path, start: float, end: float, words: list[tuple[float, float]],
             crop: tuple[int, int, int, int] | None = None) -> bool:
    """Rellena `talk` en las cajas de `frames` (solo si hay ≥2 personas en algún momento).
    `words`: (inicio, fin) de cada palabra, relativos al inicio del tramo. Devuelve si se analizó."""
    if not frames or max(len(f.boxes) for f in frames) < 2:
        return False
    det_t = np.array([f.t for f in frames])
    samples: dict[tuple[int, int], list[float]] = {}
    # Las 3 personas más grandes de cada fotograma (las demás no se van a encuadrar).
    main = [sorted(range(len(f.boxes)), key=lambda j: f.boxes[j].w * f.boxes[j].h, reverse=True)[:3] for f in frames]
    for t, rgb in sample_frames(src, start, end, SAMPLE_FPS, width=1280, crop=crop):
        if not speech_mask(words, [t], pad=WINDOW_S + 0.2)[0]:
            continue  # sin voz cerca no hace falta leer bocas (ahorra la mayor parte del tiempo)
        # Fotogramas de detección cuya ventana incluye este instante.
        for i in np.nonzero(np.abs(det_t - t) <= WINDOW_S)[0]:
            for j in main[i]:
                box = frames[i].boxes[j]
                v = jaw_open(rgb, box)
                if v is not None:
                    samples.setdefault((int(i), j), []).append(v)
    speech = speech_mask(words, list(det_t))
    for (i, j), vals in samples.items():
        if len(vals) < 2:
            continue
        arr = np.array(vals)
        # Variación de la mandíbula (hablar = abrir y cerrar) más un poco de apertura media.
        talk = float(np.std(arr) + 0.25 * np.mean(np.abs(np.diff(arr))))
        frames[i].boxes[j].talk = talk * (1.0 if speech[i] else SILENCE_FACTOR)
    return True


# ---- análisis a partir de los tracks -------------------------------------------------

def talk_matrix(tracks, n: int) -> np.ndarray:
    """(tracks × fotogramas) con la actividad de boca de cada persona."""
    m = np.zeros((len(tracks), n))
    for k, t in enumerate(tracks):
        for i, b in t.boxes.items():
            m[k, i] = getattr(b, "talk", 0.0)
    return m


def mode_filter(labels: list[int | None], width: int) -> list[int | None]:
    out = []
    for i in range(len(labels)):
        win = [x for x in labels[max(0, i - width): i + width + 1] if x is not None]
        out.append(max(set(win), key=win.count) if win else None)
    return out


def active_speakers(tracks, n: int, fps: float, min_talk: float = 0.015) -> dict:
    """Quién habla en cada fotograma, si hablan por turnos o a la vez, y tiempo de habla."""
    m = talk_matrix(tracks, n)
    if m.size == 0 or float(m.max()) < min_talk:
        return {"labels": [None] * n, "overlap": 0.0, "speaking_s": [0.0] * len(tracks), "detected": False}
    raw: list[int | None] = []
    both = talking = 0
    for i in range(n):
        col = m[:, i]
        top = float(col.max())
        if top < min_talk:
            raw.append(None)
            continue
        talking += 1
        raw.append(int(col.argmax()))
        if int(np.sum(col >= max(min_talk, 0.6 * top))) >= 2:
            both += 1
    labels = mode_filter(raw, width=max(1, int(round(0.6 * fps))))
    speaking = [round(sum(1 for x in labels if x == k) / fps, 1) for k in range(len(tracks))]
    return {"labels": labels, "overlap": both / talking if talking else 0.0, "speaking_s": speaking, "detected": True}
