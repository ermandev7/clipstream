"""Render 9:16: fondo desenfocado + vídeo centrado + subtítulos ASS + NVENC + -14 LUFS."""
from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from config.settings import ASSETS_DIR

from app.shared.render.ffmpeg import NO_WINDOW, ffmpeg_bin

log = logging.getLogger(__name__)
FONTS_DIR = ASSETS_DIR / "fonts"
OUT_W, OUT_H = 1080, 1920
TIME_RE = re.compile(r"out_time_us=(\d+)")


@dataclass
class RenderSpec:
    src: Path
    start: float
    end: float
    ass_path: Path | None
    out: Path
    video_bitrate: str = "12M"
    audio_bitrate: str = "192k"
    fps: float = 30.0
    use_nvenc: bool = True
    # Reencuadre (ver app/clipper/reframe/planner.py). Sin plan = fondo desenfocado.
    plan: dict | None = None
    src_w: int = 1920
    src_h: int = 1080
    workdir: Path | None = None  # dónde escribir el archivo de comandos de cámara
    # Bloque 2.3: zoom en el pico, efectos de sonido y música de fondo.
    zoom_at: float | None = None  # segundos desde el inicio del clip
    sfx: list[dict] = field(default_factory=list)  # [{"path", "t", "gain_db"}]
    # Efectos visuales según el contenido: {"push": 0.06, "events": [{"t", "kind"}]} (app/shared/fx/planner.py).
    fx: dict | None = None
    music: dict | None = None  # {"path", "gain_db"}


ZOOM_AMOUNT = 0.12  # +12 % de golpe en el pico


def filter_path(p: Path) -> str:
    """Ruta segura dentro de un filtro de ffmpeg en Windows (C:/x -> C\\:/x, entre comillas)."""
    s = p.resolve().as_posix().replace(":", "\\:").replace("'", "\\'")
    return f"'{s}'"


def _even(v: float) -> int:
    return max(2, int(round(v / 2)) * 2)


def _crop(r: dict, w: int, h: int) -> str:
    cw, ch = min(_even(r["w"] * w), w), min(_even(r["h"] * h), h)
    x = min(max(0, _even(r["x"] * w)), w - cw)
    y = min(max(0, _even(r["y"] * h)), h - ch)
    return f"crop={cw}:{ch}:{x}:{y}"


def _blur_layout(inp: str, out: str, tag: str) -> list[str]:
    return [
        f"{inp}split=2[{tag}bg][{tag}fg]",
        # Fondo: reducir antes de desenfocar es mucho más rápido y el resultado es igual de suave.
        f"[{tag}bg]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=12:2,"
        f"scale={OUT_W}:{OUT_H},eq=brightness=-0.08,setsar=1[{tag}bgb]",
        f"[{tag}fg]scale={OUT_W}:-2:flags=lanczos,setsar=1[{tag}fgs]",
        f"[{tag}bgb][{tag}fgs]overlay=(W-w)/2:(H-h)/2,setsar=1{out}",
    ]


def _follow_layout(inp: str, out: str, tag: str, shot: dict, spec: RenderSpec, fps: float, duration: float) -> list[str]:
    """Recorte 9:16 a altura completa cuya x se mueve fotograma a fotograma (sendcmd)."""
    from app.clipper.reframe.planner import interpolate_path

    w, h = spec.src_w, spec.src_h
    cw = min(_even(h * 9 / 16), w)
    xs = interpolate_path(shot.get("path") or [0.5], shot.get("path_fps", 4.0), fps, duration)
    px = [min(max(0, _even(x * w - cw / 2)), w - cw) for x in xs]
    cmd_file = (spec.workdir or spec.out.parent) / f"camera_{tag}.cmd"
    lines, last = [], None
    for k, x in enumerate(px):
        if x != last:
            lines.append(f"{k / fps:.4f} crop@{tag} x {x};")
            last = x
    cmd_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return [f"{inp}sendcmd=f={filter_path(cmd_file)},crop@{tag}={cw}:{h}:{px[0]}:0,"
            f"scale={OUT_W}:{OUT_H}:flags=lanczos,setsar=1{out}"]


def _stack_layout(inp: str, out: str, tag: str, regions: list[dict], panels: list[tuple[int, int]],
                  spec: RenderSpec) -> list[str]:
    n = len(panels)
    parts = [f"{inp}split={n}" + "".join(f"[{tag}s{i}]" for i in range(n))]
    for i, (r, (pw, ph)) in enumerate(zip(regions, panels)):
        parts.append(f"[{tag}s{i}]{_crop(r, spec.src_w, spec.src_h)},scale={pw}:{ph}:flags=lanczos,setsar=1[{tag}p{i}]")
    parts.append("".join(f"[{tag}p{i}]" for i in range(n)) + f"vstack=inputs={n}{out}")
    return parts


def _shot_chain(inp: str, out: str, tag: str, shot: dict, spec: RenderSpec, fps: float, duration: float) -> list[str]:
    from app.clipper.reframe.planner import FACECAM_PANEL, GAME_PANEL, REACT_PANELS, SPLIT_PANEL

    layout = shot.get("layout", "blur")
    regions = shot.get("regions") or []
    if layout in ("follow", "group", "speaker") and shot.get("path"):
        return _follow_layout(inp, out, tag, shot, spec, fps, duration)
    if layout == "facecam" and len(regions) == 2:
        return _stack_layout(inp, out, tag, regions, [FACECAM_PANEL, GAME_PANEL], spec)
    if layout == "react" and len(regions) == 2:
        return _stack_layout(inp, out, tag, regions, list(REACT_PANELS), spec)
    if layout in ("split2", "split3") and len(regions) in (2, 3):
        return _stack_layout(inp, out, tag, regions, [SPLIT_PANEL[len(regions)]] * len(regions), spec)
    return _blur_layout(inp, out, tag)


def plan_shots(plan: dict | None, duration: float) -> list[dict]:
    """Normaliza el plan: uno antiguo de un solo diseño o uno por planos -> lista de planos."""
    if not plan:
        return [{"start": 0.0, "end": duration, "layout": "blur"}]
    if "shots" in plan:
        return plan["shots"] or [{"start": 0.0, "end": duration, "layout": "blur"}]
    return [{"start": 0.0, "end": duration, **plan}]


def build_filter(spec: RenderSpec) -> str:
    fps = min(60.0, max(24.0, spec.fps))
    duration = spec.end - spec.start
    shots = plan_shots(spec.plan, duration)
    final = "[lay]"
    # Bandas negras detectadas: se quitan antes de encuadrar (las coordenadas del plan ya son del área útil).
    crop = (spec.plan or {}).get("crop")
    pre = f"crop={crop[0]}:{crop[1]}:{crop[2]}:{crop[3]}," if crop else ""
    if len(shots) == 1:
        parts = _shot_chain(f"[0:v]fps={fps:.3f},{pre}", final, "c0", shots[0], spec, fps, duration)
    else:
        # Un diseño por plano: se trocea el vídeo, cada trozo con su encuadre, y se vuelve a unir.
        n = len(shots)
        parts = [f"[0:v]fps={fps:.3f},{pre}split={n}" + "".join(f"[in{i}]" for i in range(n))]
        for i, s in enumerate(shots):
            a, b = float(s["start"]), min(float(s["end"]), duration)
            inp = f"[in{i}]trim=start={a:.4f}:end={b:.4f},setpts=PTS-STARTPTS,"
            parts += _shot_chain(inp, f"[sh{i}]", f"c{i}", s, spec, fps, b - a)
        parts.append("".join(f"[sh{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0{final}")
    cur = "[lay]"
    chain = fx_chain(duration, spec.fx) if spec.fx is not None else None
    if chain:
        parts.append(f"{cur}{chain}[zm]")
        cur = "[zm]"
    elif spec.fx is None and spec.zoom_at is not None and 0.3 < spec.zoom_at < duration - 0.3:
        parts.append(f"{cur}{zoom_chain(spec.zoom_at)}[zm]")
        cur = "[zm]"
    if spec.ass_path:  # los subtítulos van encima del zoom: no se mueven
        parts.append(f"{cur}subtitles={filter_path(spec.ass_path)}:fontsdir={filter_path(FONTS_DIR)}[v]")
    else:
        parts.append(f"{cur}null[v]")
    parts += audio_chain(spec, duration)
    return ";".join(parts)


PULSE_AMOUNT = 0.045  # latido de zoom (risas, sorpresa)
SHAKE_PX = 16  # amplitud del temblor en píxeles de salida
FLASH = 0.24  # subida de brillo del destello (más lava los negros)


def _env(t0: float, attack: float, hold: float, release: float) -> str:
    """Envolvente 0→1→0: sube en `attack`, se mantiene `hold` y baja en `release` (expresión de ffmpeg)."""
    a, b = t0 - attack, t0 + hold + release
    return f"clip((t-{a:.3f})/{attack:.3f},0,1)*clip(({b:.3f}-t)/{release:.3f},0,1)"


def fx_chain(duration: float, fx: dict) -> str | None:
    """Acercamiento lento + zoom de golpe + pulsos + temblor + destello en un solo paso (escala, recorte, brillo)."""
    push = float(fx.get("push") or 0.0)
    events = [e for e in fx.get("events") or [] if 0.0 < float(e["t"]) < duration]
    if push <= 0 and not events:
        return None
    zoom = [f"{push:.4f}*t/{duration:.3f}"] if push > 0 else []
    dx, dy, light = [], [], []
    for i, e in enumerate(events):
        t0, kind = float(e["t"]), e["kind"]
        if kind == "punch":
            zoom.append(f"{ZOOM_AMOUNT}*{_env(t0, 0.25, 1.25, 0.5)}")
        elif kind == "pulse":
            zoom.append(f"{PULSE_AMOUNT}*{_env(t0, 0.08, 0.0, 0.32)}")
        elif kind == "shake":
            env = _env(t0, 0.03, 0.15, 0.3)
            zoom.append(f"0.035*{env}")  # margen para que el temblor no enseñe bordes
            dx.append(f"{SHAKE_PX}*{env}*sin(2*PI*13*t+{i})")
            dy.append(f"{SHAKE_PX * 0.8:.1f}*{env}*cos(2*PI*17*t+{i})")
        elif kind == "flash":
            light.append(f"{FLASH}*{_env(t0, 0.04, 0.0, 0.26)}")
    z = "(1+" + "+".join(zoom) + ")" if zoom else "1"
    x = f"(iw-{OUT_W})/2" + ("+" + "+".join(dx) if dx else "")
    y = f"(ih-{OUT_H})*0.4" + ("+" + "+".join(dy) if dy else "")
    chain = (f"scale=w='trunc({OUT_W}*{z}/2)*2':h='trunc({OUT_H}*{z}/2)*2':eval=frame,"
             f"crop={OUT_W}:{OUT_H}:'clip({x},0,iw-{OUT_W})':'clip({y},0,ih-{OUT_H})',setsar=1")
    if light:
        chain += f",eq=brightness='{'+'.join(light)}':eval=frame"
    return chain


def zoom_chain(peak: float) -> str:
    """Acercamiento rápido (0,25 s) en el pico, se mantiene 1,25 s y vuelve en 0,5 s."""
    env = f"clip((t-{peak - 0.25:.3f})/0.25,0,1)*clip(({peak + 1.5:.3f}-t)/0.5,0,1)"
    z = f"(1+{ZOOM_AMOUNT}*{env})"
    # Centro algo por encima de la mitad: ahí suelen estar las caras.
    return (f"scale=w='trunc({OUT_W}*{z}/2)*2':h='trunc({OUT_H}*{z}/2)*2':eval=frame,"
            f"crop={OUT_W}:{OUT_H}:'(iw-{OUT_W})/2':'(ih-{OUT_H})*0.4',setsar=1")


def extra_inputs(spec: RenderSpec) -> list[list[str]]:
    """Entradas adicionales (después del vídeo, que es la 0): un archivo por efecto distinto y la música."""
    out: list[list[str]] = []
    for path in sorted({str(e["path"]) for e in spec.sfx}):
        out.append(["-i", path])
    if spec.music:
        out.append(["-stream_loop", "-1", "-i", str(spec.music["path"])])
    return out


def audio_chain(spec: RenderSpec, duration: float) -> list[str]:
    st = "aformat=sample_rates=48000:channel_layouts=stereo"
    parts = [f"[0:a]{loudnorm_filter(spec)},{st}[voice]"]
    voice = "[voice]"
    mix: list[str] = []
    files = sorted({str(e["path"]) for e in spec.sfx})
    idx = {f: i + 1 for i, f in enumerate(files)}
    if spec.sfx:
        uses: dict[str, int] = {}
        for e in spec.sfx:
            uses[str(e["path"])] = uses.get(str(e["path"]), 0) + 1
        # Cada archivo se reparte entre sus usos (asplit) y cada uso se retrasa a su instante.
        labels: dict[str, list[str]] = {}
        for f, n in uses.items():
            labels[f] = [f"[x{idx[f]}_{k}]" for k in range(n)]
            parts.append(f"[{idx[f]}:a]{st},asplit={n}" + "".join(labels[f]) if n > 1 else f"[{idx[f]}:a]{st}{labels[f][0]}")
        evs = []
        for i, e in enumerate(spec.sfx):
            lab = labels[str(e["path"])].pop(0)
            ms = int(max(0.0, float(e["t"])) * 1000)
            parts.append(f"{lab}adelay={ms}:all=1,volume={float(e['gain_db']):.1f}dB[e{i}]")
            evs.append(f"[e{i}]")
        parts.append("".join(evs) + f"amix=inputs={len(evs)}:normalize=0:duration=longest,apad[fx]")
        parts.append("[fx]asplit=2[fxa][fxsc]")
        # Ducking de la voz: baja unos dB mientras suena un efecto, para que el golpe se note.
        parts.append(f"{voice}[fxsc]sidechaincompress=threshold=0.03:ratio=3:attack=5:release=250[vduck]")
        voice = "[vduck]"
        mix.append("[fxa]")
    if spec.music:
        m = len(files) + 1
        gain = float(spec.music.get("gain_db", -20))
        fade_out = max(0.0, duration - 1.5)
        parts.append(f"[{m}:a]{st},atrim=0:{duration:.3f},volume={gain:.1f}dB,afade=t=in:d=1,"
                     f"afade=t=out:st={fade_out:.3f}:d=1.5[mus]")
        parts.append(f"{voice}asplit=2[vmain][vsc]")
        # La música se agacha bajo la voz.
        parts.append("[mus][vsc]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=400[musd]")
        voice = "[vmain]"
        mix.append("[musd]")
    if mix:
        parts.append(f"{voice}{''.join(mix)}amix=inputs={1 + len(mix)}:normalize=0:duration=first,"
                     "alimiter=limit=0.89:level=false[a]")
    else:
        parts.append(f"{voice}anull[a]")
    return parts


TARGET_I, TARGET_TP, TARGET_LRA = -14.0, -1.0, 11.0


def measure_loudness(spec: RenderSpec) -> dict | None:
    """Primera pasada de loudnorm: mide el audio del tramo."""
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-nostats", "-ss", f"{spec.start:.3f}", "-t", f"{spec.end - spec.start:.3f}",
           "-i", str(spec.src), "-vn", "-af", f"loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA={TARGET_LRA}:print_format=json",
           "-f", "null", "-"]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", res.stderr, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
        float(data["input_i"])  # "-inf" en silencio total
        return data if data["input_i"] not in ("-inf", "inf") else None
    except (ValueError, KeyError):
        return None


def loudnorm_filter(spec: RenderSpec) -> str:
    """Segunda pasada con lo medido y modo lineal: llega a −14 LUFS sin bombeo.
    (Una sola pasada dinámica se queda en ~−16 LUFS en clips cortos.)"""
    base = f"loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA={TARGET_LRA}"
    m = measure_loudness(spec)
    if not m:
        return base
    return (f"{base}:measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
            f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")


def render(spec: RenderSpec, progress: Callable[[float], None] | None = None) -> Path:
    spec.out.parent.mkdir(parents=True, exist_ok=True)
    duration = spec.end - spec.start
    venc = (["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", "21",
             "-b:v", spec.video_bitrate, "-maxrate", "16M", "-bufsize", "24M", "-profile:v", "high"]
            if spec.use_nvenc else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"])
    cmd = [
        ffmpeg_bin(), "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
        "-ss", f"{spec.start:.3f}", "-t", f"{duration:.3f}", "-i", str(spec.src),
        *[a for inp in extra_inputs(spec) for a in inp],
        "-filter_complex", build_filter(spec), "-map", "[v]", "-map", "[a]", "-t", f"{duration:.3f}",
        *venc, "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", spec.audio_bitrate, "-ar", "48000",
        "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", str(spec.out),
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=NO_WINDOW, text=True)
    assert proc.stdout
    for line in proc.stdout:
        m = TIME_RE.search(line)
        if m and progress and duration > 0:
            progress(min(0.99, int(m.group(1)) / 1e6 / duration))
    err = proc.stderr.read() if proc.stderr else ""
    if proc.wait() != 0:
        if spec.use_nvenc and "nvenc" in err.lower():
            log.warning("NVENC falló, reintentando con libx264: %s", err[-300:])
            spec.use_nvenc = False
            return render(spec, progress)
        raise RuntimeError(f"Render falló: {err[-800:]}")
    if progress:
        progress(1.0)
    return spec.out
