"""Vídeo insertado en el directo (reacciones): ventana detectada y diseño «Streamer + vídeo»."""
import numpy as np
import pytest

from app.clipper.reframe.detector import Box, FrameDet, find_window
from app.clipper.reframe.manual import manual_plan
from app.clipper.reframe.planner import REACT_PANELS, plan_clip, plan_layout

W, H = 1920, 1080
GW, GH = 64, 36


def activity(video=(37, 8, 56, 20), streamer=(8, 4, 30, 36), chat=None) -> np.ndarray:
    """Mapa sintético: vídeo (activo y con marco quieto), streamer (activo) y, si se pide, chat."""
    a = np.full((GH, GW), 1.0)
    for box, v in ((video, 30.0), (streamer, 40.0), (chat, 15.0)):
        if box:
            x0, y0, x1, y1 = box
            a[y0:y1, x0:x1] = v
    return a


def frames(n=40, small=True):
    out = []
    for i in range(n):
        boxes = [Box(0.22, 0.25, 0.17, 0.3, 0.95)]  # streamer grande a la izquierda
        if small:
            boxes.append(Box(0.70, 0.32, 0.04, 0.07, 0.85))  # cara dentro del vídeo insertado
        out.append(FrameDet(i / 4, boxes))
    return out


def test_finds_window_not_streamer():
    avoid = [(0.13, 0.07, 0.48, 1.0)]
    x, y, w, h = find_window(activity(), avoid, W, H, need=[(0.72, 0.35)])
    assert x == pytest.approx(37 / GW) and y == pytest.approx(8 / GH)
    assert w == pytest.approx(19 / GW) and h == pytest.approx(12 / GH)


def test_chat_is_not_a_video_without_faces_inside():
    a = activity(video=None, chat=(37, 24, 62, 34))
    assert find_window(a, [(0.13, 0.07, 0.48, 1.0)], W, H, need=[(0.72, 0.35)]) is None
    # Elegido a mano (sin exigir caras) sí se acepta el rectángulo.
    assert find_window(a, [(0.13, 0.07, 0.48, 1.0)], W, H, need=None) is not None


def test_whole_frame_is_not_a_window():
    a = np.full((GH, GW), 30.0)
    assert find_window(a, [], W, H, need=None) is None


def test_react_layout_video_top_streamer_bottom():
    p = plan_layout(frames(), W, H, activity=activity())
    assert p.layout == "react" and p.video is not None
    video, streamer = p.regions
    assert video.x >= 0.55 and streamer.x + streamer.w / 2 < 0.5
    # El panel del vídeo es 16:9 dentro de la ventana.
    assert video.w * W / (video.h * H) == pytest.approx(REACT_PANELS[0][0] / REACT_PANELS[0][1], rel=0.02)
    assert video.x >= p.video.x - 1e-6 and video.x + video.w <= p.video.x + p.video.w + 1e-6


def test_no_video_keeps_previous_layouts():
    assert plan_layout(frames(small=False), W, H, activity=activity()).layout == "follow"
    assert plan_layout(frames(), W, H).layout != "react"  # sin mapa de actividad (planos sueltos)


def test_react_ignores_cuts_inside_video():
    cp = plan_clip(frames(), [3.0, 6.0], 10.0, W, H, activity=activity())
    assert len(cp.shots) == 1 and cp.layout == "react"


def test_manual_react_needs_two_boxes():
    boxes = [{"x": 0.58, "y": 0.22, "w": 0.3, "h": 0.32, "include": True},
             {"x": 0.1, "y": 0.1, "w": 0.4, "h": 0.8, "include": True}]
    d = manual_plan({"layout": "react", "boxes": boxes}, W, H, 10.0)
    shot = d["shots"][0]
    assert d["layout"] == "react" and len(shot["regions"]) == 2 and shot["regions"][0]["x"] >= 0.58
    with pytest.raises(ValueError):
        manual_plan({"layout": "react", "boxes": boxes[:1]}, W, H, 10.0)
