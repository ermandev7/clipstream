"""Montaje del episodio: Ken Burns sobre cada imagen + voz + música + transiciones + subtítulos.

1. Vídeo base (sin audio, igual para todos los idiomas): cada fotograma se calcula con una
   transformación afín con precisión subpíxel (cv2.warpAffine), así el zoom y el desplazamiento
   son suaves (zoompan de ffmpeg tiembla por redondear a píxeles). Fundido de 0,4 s entre escenas.
2. Por idioma: voz colocada en su escena, música con ducking, whoosh en cada cambio, −14 LUFS en
   dos pasadas, subtítulos palabra a palabra, título del episodio y la etiqueta «Generado con IA».
"""
from __future__ import annotations

import logging
import math
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from app.shared.render.ffmpeg import NO_WINDOW, ffmpeg_bin
from app.shared.render.vertical import FONTS_DIR, RenderSpec, audio_chain, extra_inputs, filter_path
from app.shared.subtitles.ass import SubStyle, ass_color, build_ass
from app.shared.transcription.types import Word

log = logging.getLogger(__name__)
W, H, FPS = 1080, 1920, 30
XFADE = 0.4  # fundido entre escenas (s)
ZOOM = 0.12  # recorrido del zoom
PAN = 0.6  # fracción del margen disponible que se recorre al desplazar
VOICE_RATE = 24000
STORY_STYLE = SubStyle(size=68, max_words=4, max_chars=24, uppercase=False, margin_v=430, outline_w=5.0)
AI_LABEL = {"es": "Contenido generado con IA", "en": "AI-generated content"}
SFX_GAIN_DB = -16.0
MUSIC_GAIN_DB = -17.0


@dataclass
class Motion:
    z0: float
    z1: float
    p0: tuple[float, float]
    p1: tuple[float, float]


def motion_for(idx: int) -> Motion:
    """Alterna acercar/alejar y la dirección del desplazamiento: nunca dos escenas iguales seguidas."""
    zin = idx % 2 == 0
    dirs = [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, 1)]
    dx, dy = dirs[idx % len(dirs)]
    a, b = (-dx * PAN / 2, -dy * PAN / 2), (dx * PAN / 2, dy * PAN / 2)
    return Motion(1.0, 1.0 + ZOOM, a, b) if zin else Motion(1.0 + ZOOM, 1.0, a, b)


def _ease(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return 0.5 - 0.5 * math.cos(math.pi * x)


def timeline(durations: list[float]) -> tuple[list[float], float]:
    starts, t = [], 0.0
    for d in durations:
        starts.append(round(t, 3))
        t += d
    return starts, round(t, 3)


class Shot:
    """Una imagen preparada para Ken Burns (escalada una vez con Lanczos para no emborronar)."""

    def __init__(self, png: Path, motion: Motion, duration: float) -> None:
        import cv2

        img = cv2.imread(str(png), cv2.IMREAD_COLOR)
        if img is None:
            raise FileNotFoundError(f"No se puede leer la imagen {png}")
        h, w = img.shape[:2]
        cover = max(W / w, H / h) * (1.0 + ZOOM)  # tamaño máximo necesario: así luego solo se reduce
        self.img = cv2.resize(img, (round(w * cover), round(h * cover)), interpolation=cv2.INTER_LANCZOS4)
        self.h, self.w = self.img.shape[:2]
        self.base = max(W / self.w, H / self.h)
        self.motion, self.duration = motion, duration

    def frame(self, t: float) -> np.ndarray:
        import cv2

        k = _ease(t / self.duration if self.duration > 0 else 0)
        m = self.motion
        s = self.base * (m.z0 + (m.z1 - m.z0) * k)
        px = m.p0[0] + (m.p1[0] - m.p0[0]) * k
        py = m.p0[1] + (m.p1[1] - m.p0[1]) * k
        # Margen que queda fuera del encuadre a esta escala: el desplazamiento nunca enseña bordes.
        slack_x = max(0.0, (self.w - W / s) / 2)
        slack_y = max(0.0, (self.h - H / s) / 2)
        cx, cy = self.w / 2 + px * slack_x, self.h / 2 + py * slack_y
        mat = np.float32([[s, 0, W / 2 - s * cx], [0, s, H / 2 - s * cy]])
        return cv2.warpAffine(self.img, mat, (W, H), flags=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_REFLECT)


def _venc(nvenc: bool, cq: int = 20) -> list[str]:
    if nvenc:
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", str(cq), "-b:v", "12M",
                "-maxrate", "16M", "-bufsize", "24M", "-profile:v", "high"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(cq - 1)]


def render_base(pngs: list[Path], durations: list[float], out: Path, progress: Callable[[float], None] | None = None,
                nvenc: bool = True) -> Path:
    starts, total = timeline(durations)
    shots = [Shot(p, motion_for(i), d) for i, (p, d) in enumerate(zip(pngs, durations, strict=True))]
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
           "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", *_venc(nvenc, 18), "-pix_fmt", "yuv420p", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=NO_WINDOW)
    n = max(1, round(total * FPS))
    try:
        for f in range(n):
            t = f / FPS
            i = max(k for k, s in enumerate(starts) if s <= t + 1e-9)
            img = shots[i].frame(t - starts[i])
            # Fundido centrado en el corte: mitad al final de una escena, mitad al principio de la otra.
            if i + 1 < len(shots) and t > starts[i + 1] - XFADE / 2:
                a = (t - (starts[i + 1] - XFADE / 2)) / XFADE
                img = (img * (1 - a) + shots[i + 1].frame(t - starts[i + 1]) * a).astype(np.uint8)
            elif i > 0 and t < starts[i] + XFADE / 2:
                a = (t - (starts[i] - XFADE / 2)) / XFADE
                img = (shots[i - 1].frame(t - starts[i - 1]) * (1 - a) + img * a).astype(np.uint8)
            proc.stdin.write(img.tobytes())
            if progress and f % 30 == 0:
                progress(f / n)
        proc.stdin.close()
    except (BrokenPipeError, OSError):
        pass
    err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
    if proc.wait() != 0:
        if nvenc and "nvenc" in err.lower():
            log.warning("NVENC falló en el montaje, se usa libx264: %s", err[-300:])
            return render_base(pngs, durations, out, progress, nvenc=False)
        raise RuntimeError(f"Montaje falló: {err[-800:]}")
    return out


def voice_track(wavs: list[Path | None], starts: list[float], total: float, out: Path) -> Path:
    """Coloca la voz de cada escena en su sitio (24 kHz mono)."""
    buf = np.zeros(int(math.ceil(total * VOICE_RATE)) + 1, dtype=np.int16)
    for wav, st in zip(wavs, starts, strict=True):
        if not wav:
            continue
        with wave.open(str(wav)) as w:
            if w.getframerate() != VOICE_RATE or w.getnchannels() != 1:
                raise ValueError(f"Voz con formato inesperado: {wav}")
            pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        a = int(st * VOICE_RATE)
        pcm = pcm[: max(0, len(buf) - a)]
        buf[a: a + len(pcm)] = pcm
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(VOICE_RATE)
        w.writeframes(buf.tobytes())
    return out


def build_subtitles(scene_words: list[list[dict]], starts: list[float], total: float, title: str, lang: str) -> str:
    words = [Word(w["start"] + st, w["end"] + st, w["text"]) for ws, st in zip(scene_words, starts, strict=True) for w in ws]
    ass = build_ass(words, 0.0, total, STORY_STYLE, hook=title, hook_seconds=2.6)
    label_style = (f"Style: Label,Montserrat ExtraBold,30,{ass_color('#E6E6E7', 0x30)},{ass_color('#E6E6E7')},"
                   f"{ass_color('#000000', 0x40)},{ass_color('#000000', 0x80)},-1,0,0,0,100,100,0,0,1,2,0,8,60,60,150,1")
    ass = ass.replace("\n\n[Events]", f"\n{label_style}\n\n[Events]", 1)
    return ass + f"Dialogue: 2,0:00:00.00,{_ass_end(total)},Label,,0,0,0,,{AI_LABEL.get(lang, AI_LABEL['en'])}\n"


def _ass_end(total: float) -> str:
    from app.shared.subtitles.ass import ass_time

    return ass_time(total)


def render_language(base: Path, voice_wav: Path, ass_text: str, total: float, starts: list[float], music: Path | None,
                    whoosh: Path | None, out: Path, nvenc: bool = True) -> Path:
    ass_path = out.with_suffix(".ass")
    ass_path.parent.mkdir(parents=True, exist_ok=True)
    ass_path.write_text(ass_text, encoding="utf-8")
    sfx = [{"path": whoosh, "t": max(0.0, s - 0.3), "gain_db": SFX_GAIN_DB} for s in starts[1:]] if whoosh else []
    spec = RenderSpec(src=voice_wav, start=0.0, end=total, ass_path=ass_path, out=out, sfx=sfx,
                      music={"path": music, "gain_db": MUSIC_GAIN_DB} if music else None, use_nvenc=nvenc)
    inputs = [["-i", str(voice_wav)], *extra_inputs(spec), ["-i", str(base)]]
    vi = len(inputs) - 1
    graph = ";".join(audio_chain(spec, total) +
                     [f"[{vi}:v]subtitles={filter_path(ass_path)}:fontsdir={filter_path(FONTS_DIR)}[v]"])
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-loglevel", "error", "-y", *[a for i in inputs for a in i],
           "-filter_complex", graph, "-map", "[v]", "-map", "[a]", "-t", f"{total:.3f}", *_venc(nvenc),
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(out)]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    if res.returncode != 0:
        if nvenc and "nvenc" in res.stderr.lower():
            return render_language(base, voice_wav, ass_text, total, starts, music, whoosh, out, nvenc=False)
        raise RuntimeError(f"Montaje ({out.name}) falló: {res.stderr[-800:]}")
    return out
