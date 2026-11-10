"""Utilidades de ffmpeg/ffprobe."""
from __future__ import annotations

import functools
import glob
import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from config.settings import settings

log = logging.getLogger(__name__)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@functools.cache
def _resolve(name: str, configured: str) -> str:
    if configured and configured != name and Path(configured).exists():
        return configured
    from config.settings import DATA_DIR

    local = DATA_DIR / "runtime" / "ffmpeg" / f"{name}.exe"  # descargado en la preparación
    if local.exists():
        return str(local)
    found = shutil.which(name)
    if found:
        return found
    # winget instala en una carpeta que solo entra al PATH en shells nuevos.
    pattern = os.path.expandvars(rf"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*\*\bin\{name}.exe")
    hits = sorted(glob.glob(pattern))
    if hits:
        return hits[-1]
    raise FileNotFoundError(f"No se encontró {name}. Instálalo con: winget install Gyan.FFmpeg")


def ffmpeg_bin() -> str:
    return _resolve("ffmpeg", settings.ffmpeg_bin)


def ffprobe_bin() -> str:
    return _resolve("ffprobe", settings.ffprobe_bin)


def run(args: list[str], **kw) -> subprocess.CompletedProcess:
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-loglevel", "error", "-y", *args]
    log.debug("ffmpeg %s", " ".join(args))
    res = subprocess.run(cmd, capture_output=True, creationflags=NO_WINDOW, **kw)
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg falló: {res.stderr.decode(errors='replace')[-800:]}")
    return res


@dataclass
class MediaInfo:
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool


def probe(path: str | Path) -> MediaInfo:
    res = subprocess.run(
        [ffprobe_bin(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, creationflags=NO_WINDOW,
    )
    if res.returncode != 0:
        raise RuntimeError(f"ffprobe falló con {path}: {res.stderr.decode(errors='replace')[-400:]}")
    data = json.loads(res.stdout)
    v = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    a = any(s["codec_type"] == "audio" for s in data["streams"])
    fps = 30.0
    if v and v.get("avg_frame_rate", "0/0") != "0/0":
        num, den = v["avg_frame_rate"].split("/")
        fps = float(num) / float(den) if float(den) else 30.0
    dur = float(data["format"].get("duration") or (v or {}).get("duration") or 0)
    return MediaInfo(dur, int((v or {}).get("width", 0)), int((v or {}).get("height", 0)), fps, a)


def extract_audio(src: str | Path, out_wav: str | Path, start: float | None = None, duration: float | None = None) -> Path:
    """WAV mono 16 kHz (lo que espera Whisper)."""
    args: list[str] = []
    if start is not None:
        args += ["-ss", f"{start:.3f}"]
    args += ["-i", str(src)]
    if duration is not None:
        args += ["-t", f"{duration:.3f}"]
    args += ["-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out_wav)]
    run(args)
    return Path(out_wav)


def waveform(media: str | Path, per_second: int = 10) -> list[float]:
    """Nivel 0-1 por ventana (para dibujar la pista de audio en la timeline)."""
    rate = 8000
    db = audio_levels(media, rate=rate, window=rate // per_second)
    return [round(float(min(1.0, max(0.0, (v + 60) / 60))), 3) for v in db]


def audio_levels(wav_or_media: str | Path, rate: int = 8000, window: int | None = None) -> np.ndarray:
    """Nivel RMS en dBFS por segundo (o por `window` muestras) — suficiente para picos."""
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(wav_or_media),
           "-vn", "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"]
    res = subprocess.run(cmd, capture_output=True, creationflags=NO_WINDOW)
    if res.returncode != 0:
        raise RuntimeError(res.stderr.decode(errors="replace")[-400:])
    pcm = np.frombuffer(res.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    win = window or rate
    n = len(pcm) // win
    if n == 0:
        return np.zeros(0)
    frames = pcm[: n * win].reshape(n, win)
    rms = np.sqrt(np.mean(frames**2, axis=1))
    return 20 * np.log10(np.maximum(rms, 1e-5))


def grab_frame(src: str | Path, t: float, out_png: str | Path, width: int = 0) -> Path:
    vf = ["-vf", f"scale={width}:-2"] if width else []
    run(["-ss", f"{t:.3f}", "-i", str(src), "-frames:v", "1", *vf, str(out_png)])
    return Path(out_png)
