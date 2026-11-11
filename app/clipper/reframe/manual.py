"""Plan de reencuadre manual: el usuario dibuja una caja por persona sobre un frame.

Cada caja indica qué zona de esa persona se ve. Según el diseño:
  follow/group  recorte 9:16 centrado en la caja (o en el centro de todas las incluidas)
  split2/3      un panel por caja (de izquierda a derecha)
  facecam       la primera caja es la webcam; la segunda (opcional) es la zona de juego
  react         la primera caja es el vídeo insertado (arriba, 16:9); la segunda, el streamer (abajo)
  blur          plano general
Coordenadas normalizadas sobre el área útil (sin bandas negras).
"""
from __future__ import annotations

from app.clipper.reframe.planner import (
    FACECAM_PANEL,
    GAME_PANEL,
    REACT_PANELS,
    SPLIT_PANEL,
    Plan,
    Region,
    region_with_aspect,
    video_panel_region,
    window_width,
)


def _included(manual: dict) -> list[dict]:
    return [b for b in manual.get("boxes") or [] if b.get("include", True)]


def _panel_region(b: dict, aspect_px: float, w: int, h: int) -> Region:
    cx, cy = b["x"] + b["w"] / 2, b["y"] + b["h"] / 2
    # La caja marca la altura visible; el ancho se ajusta a la proporción del panel.
    return region_with_aspect(cx, cy, max(0.1, b["h"]), aspect_px, w, h)


def manual_plan(manual: dict, src_w: int, src_h: int, duration: float) -> dict:
    layout = manual.get("layout") or "follow"
    boxes = sorted(_included(manual), key=lambda b: b["x"])
    win = window_width(src_w, src_h)
    plan = Plan(layout, "Recorte manual", window_w=win)

    if layout in ("follow", "group"):
        if not boxes:
            plan = Plan("blur", "Recorte manual sin personas", window_w=win)
        else:
            left = min(b["x"] for b in boxes)
            right = max(b["x"] + b["w"] for b in boxes)
            cx = min(max((left + right) / 2, win / 2), 1 - win / 2)
            plan.path, plan.path_fps = [cx, cx], 1.0
    elif layout in ("split2", "split3"):
        k = 2 if layout == "split2" else 3
        if len(boxes) < k:
            raise ValueError(f"El diseño dividido ×{k} necesita {k} cajas incluidas")
        aspect = SPLIT_PANEL[k][0] / SPLIT_PANEL[k][1]
        plan.regions = [_panel_region(b, aspect, src_w, src_h) for b in boxes[:k]]
    elif layout == "facecam":
        ordered = _included(manual)  # aquí importa el orden: primero la webcam
        if not ordered:
            raise ValueError("Marca la webcam con una caja")
        cam = _panel_region(ordered[0], FACECAM_PANEL[0] / FACECAM_PANEL[1], src_w, src_h)
        game = (_panel_region(ordered[1], GAME_PANEL[0] / GAME_PANEL[1], src_w, src_h) if len(ordered) > 1
                else region_with_aspect(0.5, 0.5, 1.0, GAME_PANEL[0] / GAME_PANEL[1], src_w, src_h))
        plan.regions, plan.facecam = [cam, game], cam
    elif layout == "react":
        ordered = _included(manual)  # primero el vídeo, después el streamer
        if len(ordered) < 2:
            raise ValueError("Marca el vídeo insertado con una caja y al streamer con otra")
        v = ordered[0]
        video = Region(v["x"], v["y"], v["w"], v["h"])
        plan.regions = [video_panel_region(video, src_w, src_h),
                        _panel_region(ordered[1], REACT_PANELS[1][0] / REACT_PANELS[1][1], src_w, src_h)]
        plan.video = video
    else:
        plan = Plan("blur", "Plano general", window_w=win)

    d = plan.to_dict()
    return {"layout": plan.layout, "src_w": src_w, "src_h": src_h, "manual": True,
            "shots": [{"start": 0.0, "end": duration, **d}]}
