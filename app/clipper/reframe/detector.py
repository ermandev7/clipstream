"""Detección de caras y personas con MediaPipe (Apache 2.0), en CPU.

- Caras: BlazeFace short-range (caras grandes, webcam) + full-range (planos más abiertos).
- Personas: EfficientDet-Lite0 como respaldo cuando no se ve ninguna cara (planos generales,
  gente de espaldas). Se usa la parte superior del cuerpo como "cabeza aproximada".
Coordenadas normalizadas 0-1 respecto al frame original.
"""
from __future__ import annotations

import logging
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

import numpy as np
from config.settings import settings

from app.shared.render.ffmpeg import NO_WINDOW, ffmpeg_bin

log = logging.getLogger(__name__)
MODELS = settings.models_dir / "mediapipe"
MODEL_URLS = {
    "blaze_face_short_range.tflite": "face_detector/blaze_face_short_range/float16/latest/blaze_face_short_range.tflite",
    "blaze_face_full_range.tflite": "face_detector/blaze_face_full_range/float16/latest/blaze_face_full_range.tflite",
    "efficientdet_lite0.tflite": "object_detector/efficientdet_lite0/float16/latest/efficientdet_lite0.tflite",
    "pose_landmarker_lite.task": "pose_landmarker/pose_landmarker_lite/float16/latest/pose_landmarker_lite.task",
    "face_landmarker.task": "face_landmarker/face_landmarker/float16/latest/face_landmarker.task",
}


@dataclass
class Box:
    x: float
    y: float
    w: float
    h: float
    score: float
    kind: str = "face"  # face | pose | person
    talk: float = 0.0  # actividad de boca mientras hay voz (ver speaker.py)

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    def iou(self, o: "Box") -> float:
        ix = max(0.0, min(self.x + self.w, o.x + o.w) - max(self.x, o.x))
        iy = max(0.0, min(self.y + self.h, o.y + o.h) - max(self.y, o.y))
        inter = ix * iy
        union = self.w * self.h + o.w * o.h - inter
        return inter / union if union > 0 else 0.0


@dataclass
class FrameDet:
    t: float  # segundos desde el inicio del tramo analizado
    boxes: list[Box] = field(default_factory=list)


def ensure_models() -> None:
    """Descarga los modelos (≈8,5 MB en total) si faltan."""
    import httpx

    MODELS.mkdir(parents=True, exist_ok=True)
    for name, rel in MODEL_URLS.items():
        p = MODELS / name
        if p.exists() and p.stat().st_size > 0:
            continue
        log.info("Descargando modelo %s", name)
        r = httpx.get(f"https://storage.googleapis.com/mediapipe-models/{rel}", timeout=60, follow_redirects=True)
        r.raise_for_status()
        p.write_bytes(r.content)


@cache
def _detectors():
    from mediapipe.tasks.python import BaseOptions, vision

    ensure_models()

    def face(name: str, conf: float):
        return vision.FaceDetector.create_from_options(vision.FaceDetectorOptions(
            base_options=BaseOptions(model_asset_path=str(MODELS / name)), min_detection_confidence=conf))

    person = vision.ObjectDetector.create_from_options(vision.ObjectDetectorOptions(
        base_options=BaseOptions(model_asset_path=str(MODELS / "efficientdet_lite0.tflite")),
        score_threshold=0.45, category_allowlist=["person"], max_results=6))
    return face("blaze_face_short_range.tflite", 0.6), face("blaze_face_full_range.tflite", 0.6), person


@cache
def _pose():
    from mediapipe.tasks.python import BaseOptions, vision

    ensure_models()
    return vision.PoseLandmarker.create_from_options(vision.PoseLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=str(MODELS / "pose_landmarker_lite.task")),
        num_poses=3, min_pose_detection_confidence=0.4))


def nms(boxes: list[Box], thr: float = 0.3) -> list[Box]:
    out: list[Box] = []
    for b in sorted(boxes, key=lambda b: b.score, reverse=True):
        if all(b.iou(k) < thr for k in out):
            out.append(b)
    return out


def detect_image(rgb: np.ndarray) -> list[Box]:
    import mediapipe as mp

    h, w = rgb.shape[:2]
    img = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
    short, full, person = _detectors()
    faces: list[Box] = []
    for det in (short, full):
        for d in det.detect(img).detections:
            bb = d.bounding_box
            faces.append(Box(bb.origin_x / w, bb.origin_y / h, bb.width / w, bb.height / h, d.categories[0].score, "face"))
    faces = [f for f in nms(faces) if f.w > 0.01 and f.h > 0.01]
    if faces:
        return faces
    # Sin caras visibles (planos abiertos, perfil): la pose da la posición real de la cabeza.
    heads: list[Box] = []
    for lm in _pose().detect(img).pose_landmarks:
        nose = lm[0]
        if nose.visibility < 0.5:
            continue
        ear_w = abs(lm[7].x - lm[8].x)
        sh_w = abs(lm[11].x - lm[12].x)
        hw = max(ear_w * 1.6, sh_w * 0.55, 0.02)
        hh = hw * w / h  # caja aproximadamente cuadrada en píxeles
        heads.append(Box(nose.x - hw / 2, nose.y - hh / 2, hw, hh, float(nose.visibility), "pose"))
    if heads:
        return nms(heads)
    persons: list[Box] = []
    for d in person.detect(img).detections:
        bb = d.bounding_box
        bw, bh = bb.width / w, bb.height / h
        if bh < 0.15:  # demasiado pequeña para encuadrarla
            continue
        # Cabeza aproximada: franja superior del cuerpo.
        persons.append(Box(bb.origin_x / w + bw * 0.25, bb.origin_y / h, bw * 0.5, min(bh * 0.25, bw * 0.6),
                           d.categories[0].score, "person"))
    return nms(persons)


def sample_frames(src: str | Path, start: float, end: float, fps: float = 4.0, width: int = 640,
                  crop: tuple[int, int, int, int] | None = None) -> Iterator[tuple[float, np.ndarray]]:
    """Frames RGB reducidos del tramo [start, end] a `fps` imágenes por segundo (tras quitar bandas)."""
    from app.shared.render.ffmpeg import probe

    info = probe(src)
    fw, fh = (crop[0], crop[1]) if crop else (info.width, info.height)
    height = int(round(width * fh / max(1, fw) / 2) * 2)
    pre = f"crop={crop[0]}:{crop[1]}:{crop[2]}:{crop[3]}," if crop else ""
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-loglevel", "error", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}",
           "-i", str(src), "-vf", f"fps={fps},{pre}scale={width}:{height}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
    assert proc.stdout
    size = width * height * 3
    i = 0
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            yield i / fps, np.frombuffer(buf, np.uint8).reshape(height, width, 3)
            i += 1
    finally:
        proc.stdout.close()
        proc.wait()


def detect_range(src: str | Path, start: float, end: float, fps: float = 4.0,
                 crop: tuple[int, int, int, int] | None = None) -> list[FrameDet]:
    return [FrameDet(t, detect_image(rgb)) for t, rgb in sample_frames(src, start, end, fps, crop=crop)]


CROP_RE = __import__("re").compile(r"crop=(\d+):(\d+):(\d+):(\d+)")


def detect_letterbox(src: str | Path, start: float, end: float, src_w: int, src_h: int) -> tuple[int, int, int, int] | None:
    """Bandas negras fijas (letterbox/pillarbox) -> (w, h, x, y) del área útil, o None."""
    from collections import Counter

    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-ss", f"{start:.3f}", "-t", f"{min(end - start, 60):.3f}", "-i", str(src),
           "-an", "-vf", "fps=2,cropdetect=limit=24:round=2:reset=1", "-f", "null", "-"]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    found = Counter(tuple(map(int, m.groups())) for m in CROP_RE.finditer(res.stderr))
    if not found:
        return None
    (w, h, x, y), count = found.most_common(1)[0]
    if count < sum(found.values()) * 0.6:  # inestable (escenas oscuras): no recortar
        return None
    if w * h <= 0 or (w >= src_w * 0.96 and h >= src_h * 0.96):
        return None
    return w, h, x, y


SCD_RE = __import__("re").compile(r"lavfi\.scd\.time:\s*([\d.]+)")


def scene_cuts(src: str | Path, start: float, end: float, threshold: float = 12.0, min_gap: float = 1.0) -> list[float]:
    """Cortes de cámara dentro del tramo (segundos relativos a `start`), con el filtro scdet de ffmpeg."""
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(src),
           "-an", "-vf", f"scale=192:-2,scdet=threshold={threshold}", "-f", "null", "-"]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    cuts: list[float] = []
    for m in SCD_RE.finditer(res.stderr):
        t = float(m.group(1))
        if t > 0.3 and (not cuts or t - cuts[-1] >= min_gap):
            cuts.append(round(t, 3))
    return cuts
