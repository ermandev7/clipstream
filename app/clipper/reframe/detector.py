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


TILES = (3, 2)  # columnas x filas del mosaico para caras pequeñas
TILE_OVERLAP = 0.25
TILE_MIN_SCORE = 0.75  # en trozos pequeños el detector ve más cosas raras: se le exige más


def _tiled_faces(rgb: np.ndarray, det) -> list[Box]:
    """Caras pequeñas (p. ej. dentro de un vídeo insertado en el directo): el detector reduce la imagen
    entera a 192 px y una cara de 40 px se queda en nada. Mirando por trozos, cada uno se reduce mucho menos."""
    import mediapipe as mp

    h, w = rgb.shape[:2]
    cols, rows = TILES
    tw = int(w / (cols - (cols - 1) * TILE_OVERLAP))
    th = int(h / (rows - (rows - 1) * TILE_OVERLAP))
    out: list[Box] = []
    for r in range(rows):
        for c in range(cols):
            x0 = min(int(c * tw * (1 - TILE_OVERLAP)), w - tw)
            y0 = min(int(r * th * (1 - TILE_OVERLAP)), h - th)
            tile = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb[y0:y0 + th, x0:x0 + tw]))
            for d in det.detect(tile).detections:
                if d.categories[0].score < TILE_MIN_SCORE:
                    continue
                bb = d.bounding_box
                out.append(Box((x0 + bb.origin_x) / w, (y0 + bb.origin_y) / h, bb.width / w, bb.height / h,
                               d.categories[0].score, "face"))
    return out


def detect_image(rgb: np.ndarray, tiles: bool = False) -> list[Box]:
    import mediapipe as mp

    h, w = rgb.shape[:2]
    img = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
    short, full, person = _detectors()
    faces: list[Box] = []
    for det in (short, full):
        for d in det.detect(img).detections:
            bb = d.bounding_box
            faces.append(Box(bb.origin_x / w, bb.origin_y / h, bb.width / w, bb.height / h, d.categories[0].score, "face"))
    if tiles:
        # Las caras grandes ya las ve la pasada completa; del mosaico solo interesan las pequeñas
        # (una cara grande partida entre dos trozos daría cajas falsas).
        faces += [f for f in _tiled_faces(rgb, full) if f.w < 0.1]
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


DETECT_WIDTH = 1280  # a 640 px las caras pequeñas no llegan al detector ni troceando
GRID = (64, 36)  # celdas del mapa de actividad (columnas, filas)


def _cells(rgb: np.ndarray) -> np.ndarray:
    """Brillo medio por celda de la rejilla GRID."""
    gw, gh = GRID
    h, w = rgb.shape[:2]
    ch, cw = h // gh, w // gw
    g = rgb[:ch * gh, :cw * gw].mean(axis=2)
    return g.reshape(gh, ch, gw, cw).mean(axis=(1, 3))


def detect_range(src: str | Path, start: float, end: float, fps: float = 4.0,
                 crop: tuple[int, int, int, int] | None = None) -> list[FrameDet]:
    return analyze_range(src, start, end, fps, crop)[0]


def analyze_range(src: str | Path, start: float, end: float, fps: float = 4.0,
                  crop: tuple[int, int, int, int] | None = None) -> tuple[list[FrameDet], np.ndarray | None]:
    """Caras por fotograma y mapa de actividad (desviación del brillo de cada celda a lo largo del tramo):
    con él se localiza un vídeo insertado en el directo (ver find_window)."""
    frames: list[FrameDet] = []
    s = s2 = None
    for t, rgb in sample_frames(src, start, end, fps, width=DETECT_WIDTH, crop=crop):
        frames.append(FrameDet(t, detect_image(rgb, tiles=True)))
        c = _cells(rgb)
        s, s2 = (c, c * c) if s is None else (s + c, s2 + c * c)
    if s is None or len(frames) < 8:
        return frames, None
    n = len(frames)
    return frames, np.sqrt(np.maximum(s2 / n - (s / n) ** 2, 0.0))


WIN_ACTIVE = 6.0  # desviación de brillo (0-255) a partir de la cual una celda «se mueve»
WIN_FILL = 0.75  # parte de la ventana que debe moverse
WIN_QUIET = 0.75  # parte del borde exterior que debe estar quieto
WIN_AREA = (0.03, 0.55)  # fracción del fotograma
WIN_ASPECT = (1.15, 2.4)  # ancho/alto en píxeles: vídeos apaisados (16:9 = 1,78)


def find_window(activity: np.ndarray, avoid: list[tuple[float, float, float, float]], src_w: int = 16,
                src_h: int = 9, need: list[tuple[float, float]] | None = None) -> tuple[float, float, float, float] | None:
    """Vídeo insertado en la emisión (reacciones): el rectángulo apaisado más grande que se mueve por dentro
    y está quieto alrededor (marco, fondo).
    - `avoid`: zonas (x0, y0, x1, y1, 0-1) de las personas principales; la ventana no puede tocarlas (si no,
      la propia webcam del streamer parecería un vídeo).
    - `need`: si se da, la ventana debe contener alguno de estos puntos (caras vistas dentro del vídeo): así el
      chat, que también se mueve, no pasa por vídeo.
    Devuelve (x, y, w, h) 0-1."""
    gh, gw = activity.shape
    act = activity > WIN_ACTIVE
    quiet = (~act).astype(np.int32)
    a = np.zeros((gh + 1, gw + 1), np.int32)
    a[1:, 1:] = act.astype(np.int32).cumsum(0).cumsum(1)
    rowq = np.zeros((gh, gw + 1), np.int32)
    rowq[:, 1:] = quiet.cumsum(1)
    colq = np.zeros((gh + 1, gw), np.int32)
    colq[1:, :] = quiet.cumsum(0)
    lft = np.arange(gw)[:, None]  # columna inicial
    rgt = np.arange(1, gw + 1)[None, :]  # columna final (exclusiva)
    width = (rgt - lft).astype(float)
    valid_lr = rgt > lft
    cell_aspect = (src_w / gw) / (src_h / gh)
    avoid_c = [(x0 * gw, y0 * gh, x1 * gw, y1 * gh) for x0, y0, x1, y1 in avoid]
    need_c = [(cx * gw, cy * gh) for cx, cy in need or []]
    best, best_score = None, 0.0
    for t in range(gh):
        for b in range(t + 2, gh + 1):
            h = b - t
            area = width * h
            inside = a[b, rgt] - a[t, rgt] - a[b, lft] + a[t, lft]
            fill = np.where(valid_lr, inside / np.maximum(area, 1), 0)
            q = np.zeros_like(area)
            nring = np.zeros_like(area)
            if t > 0:
                q += rowq[t - 1, rgt] - rowq[t - 1, lft]
                nring += width
            if b < gh:
                q += rowq[b, rgt] - rowq[b, lft]
                nring += width
            left_ok = lft > 0
            lcol = np.clip(lft - 1, 0, gw - 1)
            q += np.where(left_ok, colq[b, lcol] - colq[t, lcol], 0)
            nring += np.where(left_ok, h, 0)
            right_ok = rgt < gw
            rcol = np.clip(rgt, 0, gw - 1)
            q += np.where(right_ok, colq[b, rcol] - colq[t, rcol], 0)
            nring += np.where(right_ok, h, 0)
            with np.errstate(divide="ignore", invalid="ignore"):
                quiet_frac = np.where(nring > 0, q / nring, 0)
            aspect = width / h * cell_aspect
            ok = (valid_lr & (fill >= WIN_FILL) & (quiet_frac >= WIN_QUIET) & (nring >= 0.4 * 2 * (width + h))
                  & (area >= WIN_AREA[0] * gw * gh) & (area <= WIN_AREA[1] * gw * gh)
                  & (aspect >= WIN_ASPECT[0]) & (aspect <= WIN_ASPECT[1]))
            for x0, y0, x1, y1 in avoid_c:
                if t < y1 and b > y0:
                    ok &= ~((lft < x1) & (rgt > x0))
            if need is not None:
                has = np.zeros_like(ok)
                for cx, cy in need_c:
                    if t <= cy < b:
                        has |= (lft <= cx) & (cx < rgt)
                ok &= has
            if not ok.any():
                continue
            score = np.where(ok, area * fill ** 4 * quiet_frac, 0)
            i = np.unravel_index(int(score.argmax()), score.shape)
            if score[i] > best_score:
                best_score, best = float(score[i]), (int(i[0]), t, int(i[1]) + 1, b)
    if best is None:
        return None
    l, t, r, b = best
    # Se recortan los bordes casi quietos que el margen de tolerancia dejó dentro (franjas negras, rótulos fijos).
    edge = 0.5
    while b - t > 2 and act[t, l:r].mean() < edge:
        t += 1
    while b - t > 2 and act[b - 1, l:r].mean() < edge:
        b -= 1
    while r - l > 2 and act[t:b, l].mean() < edge:
        l += 1
    while r - l > 2 and act[t:b, r - 1].mean() < edge:
        r -= 1
    return l / gw, t / gh, (r - l) / gw, (b - t) / gh


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
