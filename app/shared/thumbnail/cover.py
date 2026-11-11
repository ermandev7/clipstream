"""Portada del clip: mejor fotograma (nitidez, cara, cercanía al pico) en vertical + texto."""
from __future__ import annotations

import copy
import logging
from pathlib import Path

import numpy as np

from app.shared.render import ffmpeg
from app.shared.render.vertical import FONTS_DIR, RenderSpec, filter_path, render
from app.shared.subtitles.ass import ass_color, escape

log = logging.getLogger(__name__)


def sharpness(gray: np.ndarray) -> float:
    """Varianza del laplaciano (más alto = más nítido)."""
    g = gray.astype(np.float32)
    lap = -4 * g[1:-1, 1:-1] + g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:]
    return float(lap.var())


def score_frame(rgb: np.ndarray) -> tuple[float, dict]:
    from app.clipper.reframe.detector import detect_image

    gray = rgb.mean(axis=2)
    sharp = sharpness(gray)
    bright = float(gray.mean())
    faces = [b for b in detect_image(rgb) if b.kind == "face"]
    face = max((b.w * b.h * b.score for b in faces), default=0.0)
    # Penalizar fotogramas casi negros o quemados (transiciones, flashes).
    exposure = 1.0 if 40 <= bright <= 215 else 0.4
    return (np.log1p(sharp) * 0.35 + min(face * 40, 3.0) + (1.0 if faces else 0.0)) * exposure, \
        {"sharp": round(sharp, 1), "face": round(face, 4), "bright": round(bright, 1)}


def best_time(src: str | Path, start: float, end: float, peak_rel: float, crop: list | None = None,
              fps: float = 2.0) -> float:
    """Instante (relativo al clip) con mejor puntuación; leve preferencia por la zona del pico."""
    from app.clipper.reframe.detector import sample_frames

    dur = end - start
    best_t, best_s = min(max(peak_rel, 0.5), max(0.5, dur - 0.5)), -1.0
    for t, rgb in sample_frames(src, start, end, fps, width=480, crop=tuple(crop) if crop else None):
        if t < 0.4 or t > dur - 0.4:
            continue
        s, _ = score_frame(rgb)
        s *= 1.0 + 0.25 * max(0.0, 1 - abs(t - peak_rel) / 6)
        if s > best_s:
            best_t, best_s = t, s
    return round(best_t, 2)


def plan_at(plan: dict | None, t: float, length: float = 0.25) -> dict | None:
    """Plan de un solo plano que empieza en `t` (para renderizar un único fotograma)."""
    if not plan:
        return None
    shots = plan.get("shots") or []
    shot = next((s for s in shots if s["start"] <= t < s["end"]), shots[-1] if shots else None)
    if shot is None:
        return plan
    s = copy.deepcopy(shot)
    if s.get("path"):
        i = int((t - shot["start"]) * s.get("path_fps", 4.0))
        s["path"] = s["path"][min(i, len(s["path"]) - 1):] or s["path"][-1:]
    s["start"], s["end"] = 0.0, length
    return {**plan, "shots": [s]}


COVER_ASS = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cover,Montserrat Black,{size},{white},{white},{black},{shadow},-1,0,0,0,100,100,0,0,1,10,4,8,90,90,300,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:10.00,Cover,,0,0,0,,{pos}{text}
"""


def cover_text_markup(text: str) -> str:
    """Texto en mayúsculas; la palabra más larga se resalta en amarillo."""
    words = escape(text.strip().upper()).split()
    if not words:
        return ""
    key = max(range(len(words)), key=lambda i: len(words[i]))
    yellow = "{\\c" + ass_color("#F5D547") + "}"
    white = "{\\c" + ass_color("#FFFFFF") + "}"
    words[key] = yellow + words[key] + white
    return " ".join(words)


TOP_ZONE = (0.12, 0.32)  # franja vertical (fracción del alto) que ocupa el texto arriba
BOTTOM_ZONE = (0.68, 0.84)


def _overlap(a: tuple[float, float], b: tuple[float, float]) -> float:
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


def text_position(plan: dict | None, frame: Path | None = None) -> str:
    """Dónde va el texto de la portada:
    - diseños apilados (facecam, dividido): en la unión de paneles;
    - resto: arriba, salvo que ahí haya una cara/cabeza en el fotograma; entonces abajo."""
    from app.clipper.reframe.planner import FACECAM_PANEL, REACT_PANELS, SPLIT_PANEL

    layout = (plan or {}).get("layout")
    seam = {"facecam": FACECAM_PANEL[1], "split2": SPLIT_PANEL[2][1], "split3": SPLIT_PANEL[3][1],
            "react": REACT_PANELS[0][1]}.get(layout)
    if seam:
        return r"{\an5\pos(540,%d)}" % seam
    if frame is None or not frame.exists():
        return ""
    try:
        from app.clipper.reframe.detector import detect_image

        raw = ffmpeg_frame_rgb(frame, 360)
        heads = detect_image(raw)
    except Exception:  # noqa: BLE001
        return ""
    top = sum(_overlap((b.y, b.y + b.h), TOP_ZONE) for b in heads)
    bottom = sum(_overlap((b.y, b.y + b.h), BOTTOM_ZONE) for b in heads)
    if top > 0 and bottom < top:
        return r"{\an2\pos(540,%d)}" % int(1920 * BOTTOM_ZONE[1])
    return ""


def ffmpeg_frame_rgb(png: Path, width: int) -> np.ndarray:
    import subprocess

    info = ffmpeg.probe(png)
    h = int(round(width * info.height / info.width / 2) * 2)
    out = subprocess.run([ffmpeg.ffmpeg_bin(), "-loglevel", "error", "-i", str(png), "-vf", f"scale={width}:{h}",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, creationflags=ffmpeg.NO_WINDOW)
    return np.frombuffer(out.stdout, np.uint8).reshape(h, width, 3)


def render_cover(media: Path, start_abs: float, t_rel: float, plan: dict | None, src_w: int, src_h: int,
                 text: str, out_png: Path, workdir: Path, fps: float = 30.0) -> Path:
    """Fotograma vertical limpio (sin subtítulos ni gancho) + texto de portada."""
    tmp_mp4 = workdir / "cover_src.mp4"
    frame = workdir / "cover_frame.png"
    t = start_abs + t_rel
    # En trozos de directo (.ts) un salto exacto a veces no deja ningún fotograma en 0,25 s: se amplía la ventana.
    last_err: Exception | None = None
    for window in (0.25, 1.5, 4.0):
        try:
            render(RenderSpec(media, t, t + window, None, tmp_mp4, fps=fps, plan=plan_at(plan, t_rel), src_w=src_w,
                              src_h=src_h, workdir=workdir))
            ffmpeg.run(["-i", str(tmp_mp4), "-frames:v", "1", str(frame)])
            last_err = None
            break
        except RuntimeError as e:
            last_err = e
        finally:
            tmp_mp4.unlink(missing_ok=True)
    if last_err is not None:
        raise last_err
    if text.strip():
        n = len(text)
        size = 120 if n <= 24 else 104 if n <= 40 else 88
        ass = workdir / "cover.ass"
        ass.write_text(COVER_ASS.format(size=size, white=ass_color("#FFFFFF"), black=ass_color("#000000"),
                                        shadow=ass_color("#000000", 0x60), pos=text_position(plan, frame),
                                        text=cover_text_markup(text)),
                       encoding="utf-8")
        ffmpeg.run(["-i", str(frame), "-vf", f"subtitles={filter_path(ass)}:fontsdir={filter_path(FONTS_DIR)}",
                    "-frames:v", "1", str(out_png)])
    else:
        frame.replace(out_png)
    frame.unlink(missing_ok=True)
    return out_png
