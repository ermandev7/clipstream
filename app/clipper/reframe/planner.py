"""Decide el diseño vertical de un clip y calcula el movimiento de cámara.

Diseños:
  blur     plano general con fondo desenfocado (sin personas claras)
  follow   recorte 9:16 que sigue a una persona (zona muerta + movimiento suavizado)
  group    recorte 9:16 que incluye a todos si caben
  facecam  facecam arriba, juego abajo
  split2   dos personas apiladas
  split3   tres personas apiladas
  react    vídeo insertado en la emisión arriba (16:9) y el streamer abajo
"""
from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass, field

from app.clipper.reframe.detector import FrameDet
from app.clipper.reframe.tracker import Track, build_tracks, main_tracks

LAYOUTS = ("follow", "facecam", "blur", "split2", "split3", "group", "speaker", "react")
SIMULTANEOUS = 0.35  # fracción de tiempo hablando a la vez a partir de la cual se divide la pantalla
OUT_W, OUT_H = 1080, 1920
FACECAM_PANEL = (1080, 768)  # arriba
GAME_PANEL = (1080, 1152)  # abajo
SPLIT_PANEL = {2: (1080, 960), 3: (1080, 640)}
REACT_PANELS = ((1080, 608), (1080, 1312))  # vídeo insertado (16:9) arriba, streamer abajo
MAIN_FACE_H = 0.12  # personas «principales» (no caben dentro de un vídeo insertado)
TINY_FACE = 0.3  # caras de menos de esta fracción de la mayor no cuentan para elegir el diseño


@dataclass
class Region:
    """Rectángulo de recorte normalizado (0-1) sobre el frame original."""

    x: float
    y: float
    w: float
    h: float

    def clamp(self) -> "Region":
        w, h = min(self.w, 1.0), min(self.h, 1.0)
        return Region(min(max(self.x, 0.0), 1.0 - w), min(max(self.y, 0.0), 1.0 - h), w, h)


@dataclass
class Tracking:
    smoothing: float = 0.6  # segundos (constante de tiempo); más = cámara más lenta
    dead_zone: float = 0.12  # fracción del ancho del recorte en la que la cámara no se mueve
    max_speed: float = 0.35  # anchos de frame por segundo
    cut: bool = False  # True = corte seco al cambiar de objetivo en vez de desplazamiento


@dataclass
class Person:
    id: int
    kind: str
    confidence: float
    presence: float
    box: Region
    speaking_s: float = 0.0


@dataclass
class Plan:
    layout: str
    reason: str
    path_fps: float = 4.0
    path: list[float] | None = None  # centro x del recorte (normalizado) por muestra
    regions: list[Region] = field(default_factory=list)
    people: list[Person] = field(default_factory=list)
    facecam: Region | None = None
    window_w: float = 0.0
    video: Region | None = None  # vídeo insertado detectado (diseño react)

    def to_dict(self) -> dict:
        return asdict(self)


def window_width(src_w: int, src_h: int) -> float:
    """Ancho normalizado de un recorte 9:16 a altura completa."""
    return min(1.0, (9 / 16) * src_h / src_w)


def region_with_aspect(cx: float, cy: float, h: float, aspect_px: float, src_w: int, src_h: int) -> Region:
    """Rectángulo centrado en (cx, cy) con altura h y relación ancho/alto en píxeles `aspect_px`."""
    h = min(h, 1.0)
    w = aspect_px * h * src_h / src_w
    if w > 1.0:  # no cabe a lo ancho: limitar por ancho
        w = 1.0
        h = w * src_w / (aspect_px * src_h)
    return Region(cx - w / 2, cy - h / 2, w, h).clamp()


def smooth_path(targets: list[float | None], fps: float, win_w: float, t: Tracking) -> list[float]:
    """Cámara virtual: no se mueve dentro de la zona muerta; fuera, sigue con suavizado y
    velocidad máxima. Sin objetivo más de 2 s, vuelve despacio al centro."""
    dt = 1.0 / fps
    alpha = 1.0 - math.exp(-dt / max(0.05, t.smoothing))
    max_step = t.max_speed * dt
    lo, hi = win_w / 2, 1.0 - win_w / 2
    first = next((x for x in targets if x is not None), 0.5)
    cam = min(max(first, lo), hi)
    last_seen, missing = first, 0
    out: list[float] = []
    for x in targets:
        if x is None:
            missing += 1
            tgt = last_seen if missing * dt < 2.0 else 0.5
        else:
            missing, last_seen, tgt = 0, x, x
        err = tgt - cam
        dz = t.dead_zone * win_w
        if t.cut and abs(err) > win_w * 0.5:
            cam = tgt  # corte seco a un objetivo lejano
        elif abs(err) > dz:
            move = (err - math.copysign(dz, err)) * alpha
            cam += max(-max_step, min(max_step, move))
        cam = min(max(cam, lo), hi)
        out.append(round(cam, 5))
    return out


def is_facecam(track: Track, n: int) -> bool:
    """Webcam superpuesta: cara pequeña, quieta y en una esquina. La cara de una webcam
    pequeña a veces solo la ve el detector de personas, así que se aceptan ambos tipos."""
    b = track.median_box()
    near_side = b.cx < 0.25 or b.cx > 0.75
    near_edge = b.cy < 0.35 or b.cy > 0.65
    small = b.w < 0.14 and b.h < 0.3
    # El streamer se mueve dentro de su webcam: se tolera algo de oscilación, pero no que cruce el frame.
    # Presente casi todo el tiempo: una persona sentada en un plano general no es una facecam.
    return track.presence(n) >= 0.6 and track.center_std() < 0.05 and small and near_side and near_edge


def _corner(b) -> tuple[int, int] | None:
    if b.w >= 0.14 or b.h >= 0.3:
        return None
    sx = 0 if b.cx < 0.25 else 1 if b.cx > 0.75 else None
    sy = 0 if b.cy < 0.35 else 1 if b.cy > 0.65 else None
    return None if sx is None or sy is None else (sx, sy)


def corner_tracks(tracks: list[Track]) -> list[Track]:
    """Une los tracks pequeños de una misma esquina: en una webcam puede haber dos personas,
    o el detector alterna entre cara y pose y parte la misma persona en dos tracks."""
    groups: dict[tuple[int, int], list[Track]] = {}
    for t in tracks:
        c = _corner(t.median_box())
        if c is not None:
            groups.setdefault(c, []).append(t)
    merged: list[Track] = []
    for c, ts in groups.items():
        m = Track(id=-1 - len(merged))
        for t in sorted(ts, key=lambda t: len(t.boxes)):  # el más estable gana en cada frame
            m.boxes.update(t.boxes)
        merged.append(m)
    return merged


def facecam_region(track: Track, src_w: int, src_h: int) -> Region:
    b = track.median_box()
    # Cámara típica: la cara ocupa ~1/3 del alto del recuadro de la webcam. Si solo se vio el
    # cuerpo, la "cabeza" estimada es pequeña: un recuadro de webcam rara vez baja del 25 % del alto.
    h = b.h * 3.2 if track.kind == "face" else max(b.h * 3.2, 0.26)
    return region_with_aspect(b.cx, b.cy + b.h * 0.25, h, FACECAM_PANEL[0] / FACECAM_PANEL[1], src_w, src_h)


def split_regions(tracks: list[Track], src_w: int, src_h: int) -> list[Region]:
    n = len(tracks)
    aspect = SPLIT_PANEL[n][0] / SPLIT_PANEL[n][1]
    regs = []
    for t in sorted(tracks, key=lambda t: t.median_box().cx):
        b = t.median_box()
        h = min(1.0, max(0.35, b.h * (4.5 if t.kind in ("face", "pose") else 1.6)))
        regs.append(region_with_aspect(b.cx, b.cy + h * 0.15, h, aspect, src_w, src_h))
    return regs


def _inside(b, r: Region) -> bool:
    return r.x <= b.cx <= r.x + r.w and r.y <= b.cy <= r.y + r.h


def video_panel_region(v: Region, src_w: int, src_h: int) -> Region:
    """El mayor 16:9 dentro de la ventana detectada (centrado): si sobra algo de marco, se recorta."""
    aspect = REACT_PANELS[0][0] / REACT_PANELS[0][1]
    h = min(v.h, v.w * src_w / (aspect * src_h))
    return region_with_aspect(v.x + v.w / 2, v.y + v.h / 2, h, aspect, src_w, src_h)


def streamer_panel_region(b, src_w: int, src_h: int) -> Region:
    h = min(1.0, max(0.6, b.h * 3.2))
    return region_with_aspect(b.cx, b.cy + h * 0.18, h, REACT_PANELS[1][0] / REACT_PANELS[1][1], src_w, src_h)


def plan_layout(frames: list[FrameDet], src_w: int, src_h: int, fps: float = 4.0,
                tracking: Tracking | None = None, force: str | None = None, allow_facecam: bool = True,
                activity=None) -> Plan:
    tracking = tracking or Tracking()
    n = len(frames)
    win = window_width(src_w, src_h)
    tracks = build_tracks(frames)
    people = main_tracks(tracks, n)
    persons = [Person(t.id, t.kind, round(t.median_box().score, 2), round(t.presence(n), 2),
                      Region(*(lambda b: (b.x, b.y, b.w, b.h))(t.median_box()))) for t in people]

    # Vídeo insertado (reacciones): solo con el clip completo. Se busca antes que la facecam: una cara
    # pequeña y quieta dentro del vídeo (o un falso positivo en un decorado) no es la webcam del streamer.
    video: Region | None = None
    auto_react = False
    main = [t for t in people if t.median_box().h >= MAIN_FACE_H]
    if activity is not None and main and (allow_facecam or force == "react") and force not in ("facecam", "blur"):
        from app.clipper.reframe.detector import find_window

        # Zona de cada persona principal: cara ampliada y el cuerpo hacia abajo (gorra, manos, hombros).
        avoid = [(b.x - b.w * 0.5, b.y - b.h * 0.6, b.x + b.w * 1.5, 1.0) for b in (t.median_box() for t in main)]
        # Sin elegirlo a mano, debe verse alguna cara dentro del vídeo (si no, el chat podría pasar por vídeo).
        need = None if force == "react" else [
            (t.median_box().cx, t.median_box().cy) for t in tracks
            if t.presence(n) >= 0.1 and t.median_box().h < MAIN_FACE_H]
        found = find_window(activity, avoid, src_w, src_h, need) if need is None or need else None
        if found:
            video = Region(*found)
            if force is None:
                force, auto_react = "react", True

    cam_track = None
    if allow_facecam or force == "facecam":
        cams = [t for t in corner_tracks(tracks) if not (video and _inside(t.median_box(), video))]
        cam_track = next((t for t in cams if is_facecam(t, n)), None)
    if cam_track is not None and auto_react:
        video, force = None, None  # partida con facecam: manda el diseño facecam de siempre
    cam_ids = set(cam_track.boxes) if cam_track else set()
    # Quien está dentro de la webcam no cuenta como persona del plano principal.
    others = [t for t in people if not (cam_track and _corner(t.median_box()) is not None
                                         and set(t.boxes) & cam_ids)]
    if video:
        # Las caras de dentro del vídeo se ven en su panel: no son personas del plano.
        others = [t for t in others if not _inside(t.median_box(), video)]
    if others:
        # Las caras diminutas (público al fondo, una tele en el plano) se muestran pero no deciden el diseño.
        big = max(t.median_box().h for t in others)
        others = [t for t in others if t.median_box().h >= big * TINY_FACE]

    # Hablante activo (solo hay datos si speaker.annotate analizó el clip).
    from app.clipper.reframe.speaker import active_speakers

    sp_tracks = others[:3]
    sp = active_speakers(sp_tracks, n, fps) if len(sp_tracks) >= 2 else None
    if sp and sp["detected"]:
        for t, secs in zip(sp_tracks, sp["speaking_s"]):
            for p in persons:
                if p.id == t.id:
                    p.speaking_s = secs

    def follow(ts: list[Track], layout: str, reason: str) -> Plan:
        targets: list[float | None] = []
        for i in range(n):
            xs = [t.boxes[i].cx for t in ts if i in t.boxes]
            targets.append(statistics.mean(xs) if xs else None)
        return Plan(layout, reason, fps, smooth_path(targets, fps, win, tracking), people=persons, window_w=win)

    layout = force
    if layout is None:
        if cam_track is not None and (not others or all(t.median_box().h < 0.12 for t in others)):
            layout = "facecam"
        elif not others:
            layout = "blur"
        elif len(others) == 1:
            layout = "follow"
        else:
            top = others[:3]
            span = max(t.median_box().x + t.median_box().w for t in top) - min(t.median_box().x for t in top)
            if span <= win * 0.85:
                layout = "group"  # caben todos
            elif sp and sp["detected"] and sp["overlap"] < SIMULTANEOUS:
                layout = "speaker"  # hablan por turnos
            else:
                layout = "split2" if len(top) == 2 else "split3"  # hablan a la vez (o no se sabe)

    if layout == "react":
        streamer = next((t for t in others if t.median_box().h >= MAIN_FACE_H), others[0] if others else None)
        if video is None or streamer is None:
            return plan_layout(frames, src_w, src_h, fps, tracking, None, allow_facecam)
        regions = [video_panel_region(video, src_w, src_h), streamer_panel_region(streamer.median_box(), src_w, src_h)]
        return Plan("react", "Vídeo insertado en la emisión: vídeo arriba y streamer abajo", fps, regions=regions,
                    people=persons, window_w=win, video=video)
    if layout == "speaker":
        ts = others or people
        if not ts:
            return Plan("blur", "No se detectan personas", people=persons, window_w=win)
        if len(ts) == 1 or not (sp and sp["detected"]):
            return follow([ts[0]], "follow", "Sin varias personas hablando: seguimiento de cara")
        targets: list[float | None] = []
        last: dict[int, float] = {}
        for i in range(n):
            for k, t in enumerate(sp_tracks):
                if i in t.boxes:
                    last[k] = t.boxes[i].cx
            lab = sp["labels"][i]
            targets.append(last.get(lab) if lab is not None else None)
        reason = f"Hablan por turnos ({int(sp['overlap'] * 100)} % a la vez): encuadre en quien habla"
        return Plan("speaker", reason, fps, smooth_path(targets, fps, win, tracking), people=persons, window_w=win)
    if layout == "facecam":
        if cam_track is None:
            return plan_layout(frames, src_w, src_h, fps, tracking, force=None if force else "blur")
        game = region_with_aspect(0.5, 0.5, 1.0, GAME_PANEL[0] / GAME_PANEL[1], src_w, src_h)
        fc = facecam_region(cam_track, src_w, src_h)
        return Plan("facecam", "Facecam fija detectada en una esquina", fps, regions=[fc, game],
                    people=persons, facecam=fc, window_w=win)
    if layout == "follow":
        if not others and not people:
            return Plan("blur", "No se detectan personas", people=persons, window_w=win)
        return follow([(others or people)[0]], "follow", "Una persona: seguimiento de cara")
    if layout == "group":
        ts = (others or people)[:3]
        if not ts:
            return Plan("blur", "No se detectan personas", people=persons, window_w=win)
        return follow(ts, "group", f"{len(ts)} personas caben en el encuadre")
    if layout in ("split2", "split3"):
        k = 2 if layout == "split2" else 3
        ts = (others or people)[:k]
        if len(ts) < k:
            if len(ts) == 1:
                return follow(ts, "follow", "Solo se detecta una persona")
            return plan_layout(frames, src_w, src_h, fps, tracking, force="blur")
        return Plan(layout, f"{k} personas separadas: pantalla dividida", fps,
                    regions=split_regions(ts, src_w, src_h), people=persons, window_w=win)
    return Plan("blur", "No se detectan personas claras" if not people else "Plano general elegido",
                people=persons, window_w=win)


@dataclass
class Shot:
    start: float  # segundos relativos al inicio del clip
    end: float
    plan: Plan


@dataclass
class ClipPlan:
    """Plan del clip completo: uno o varios planos (cortes de cámara), cada uno con su diseño."""

    shots: list[Shot]
    src_w: int
    src_h: int

    @property
    def layout(self) -> str:
        """Diseño principal (el que más tiempo ocupa)."""
        dur: dict[str, float] = {}
        for s in self.shots:
            dur[s.plan.layout] = dur.get(s.plan.layout, 0.0) + s.end - s.start
        return max(dur, key=dur.get) if dur else "blur"

    def to_dict(self) -> dict:
        return {"layout": self.layout, "src_w": self.src_w, "src_h": self.src_h,
                "shots": [{"start": s.start, "end": s.end, **s.plan.to_dict()} for s in self.shots]}


MIN_SHOT_FRAMES = 3


def plan_clip(frames: list[FrameDet], cuts: list[float], duration: float, src_w: int, src_h: int,
              fps: float = 4.0, tracking: Tracking | None = None, force: str | None = None,
              activity=None) -> ClipPlan:
    """Planifica por plano. Si hay una facecam fija en todo el clip (gameplay) o un vídeo insertado
    (reacciones), los cortes se ignoran: son cambios dentro de la pantalla o del vídeo, no de cámara."""
    whole = plan_layout(frames, src_w, src_h, fps, tracking, force, activity=activity)
    if whole.layout in ("facecam", "react") or not cuts or force == "blur":
        return ClipPlan([Shot(0.0, duration, whole)], src_w, src_h)
    bounds = [0.0, *[c for c in cuts if 0 < c < duration], duration]
    shots: list[Shot] = []
    for a, b in zip(bounds, bounds[1:]):
        sub = [FrameDet(f.t - a, f.boxes) for f in frames if a <= f.t < b]
        if len(sub) < MIN_SHOT_FRAMES and shots:
            prev = shots[-1].plan
            # Plano muy corto: reutilizar el diseño anterior (con la cámara quieta en su última posición).
            p = Plan(prev.layout, prev.reason, prev.path_fps, [prev.path[-1]] if prev.path else None,
                     prev.regions, prev.people, prev.facecam, prev.window_w, prev.video)
        else:
            # Dentro de un plano no se busca facecam: eso solo se decide con el clip completo.
            p = plan_layout(sub, src_w, src_h, fps, tracking, force, allow_facecam=False)
        shots.append(Shot(round(a, 3), round(b, 3), p))
    return ClipPlan(shots, src_w, src_h)


def interpolate_path(path: list[float], path_fps: float, out_fps: float, duration: float) -> list[float]:
    """Remuestrea la trayectoria a los fps de salida (interpolación lineal)."""
    if not path:
        return []
    n_out = max(1, int(math.ceil(duration * out_fps)))
    out = []
    for k in range(n_out):
        pos = k / out_fps * path_fps
        i = int(pos)
        if i >= len(path) - 1:
            out.append(path[-1])
        else:
            f = pos - i
            out.append(path[i] * (1 - f) + path[i + 1] * f)
    return out
