import pytest

from app.clipper.reframe.manual import manual_plan
from app.clipper.reframe.service import plan_key
from app.clipper.reframe.planner import Tracking

W, H = 1920, 1080


def box(x, y, w, h, include=True):
    return {"x": x, "y": y, "w": w, "h": h, "include": include}


def test_follow_centers_on_box_and_clamps():
    p = manual_plan({"layout": "follow", "boxes": [box(0.0, 0.2, 0.1, 0.3)]}, W, H, 30)
    shot = p["shots"][0]
    assert p["manual"] and shot["layout"] == "follow"
    assert abs(shot["path"][0] - shot["window_w"] / 2) < 1e-6  # pegado al borde izquierdo, sin salirse


def test_group_uses_all_included():
    p = manual_plan({"layout": "group", "boxes": [box(0.4, 0.2, 0.05, 0.1), box(0.55, 0.2, 0.05, 0.1),
                                                   box(0.9, 0.2, 0.05, 0.1, include=False)]}, W, H, 30)
    assert abs(p["shots"][0]["path"][0] - 0.5) < 1e-6


def test_split_orders_left_to_right_with_panel_aspect():
    p = manual_plan({"layout": "split2", "boxes": [box(0.7, 0.1, 0.2, 0.5), box(0.1, 0.1, 0.2, 0.5)]}, W, H, 30)
    r = p["shots"][0]["regions"]
    assert r[0]["x"] < r[1]["x"]
    for reg in r:
        assert abs(reg["w"] * W / (reg["h"] * H) - 1080 / 960) < 0.02


def test_split_needs_enough_boxes():
    with pytest.raises(ValueError):
        manual_plan({"layout": "split3", "boxes": [box(0.1, 0.1, 0.2, 0.5)]}, W, H, 30)


def test_facecam_first_box_is_cam():
    p = manual_plan({"layout": "facecam", "boxes": [box(0.8, 0.75, 0.18, 0.22)]}, W, H, 30)
    cam, game = p["shots"][0]["regions"]
    assert cam["x"] > 0.6 and game["h"] == 1.0


def test_no_boxes_follow_is_blur():
    assert manual_plan({"layout": "follow", "boxes": []}, W, H, 30)["layout"] == "blur"


def test_plan_key_changes_with_inputs():
    k = plan_key(0, 30, None, Tracking(), None)
    assert k == plan_key(0, 30, None, Tracking(), None)
    assert k != plan_key(0, 31, None, Tracking(), None)
    assert k != plan_key(0, 30, "split2", Tracking(), None)
    assert k != plan_key(0, 30, None, Tracking(smoothing=1.0), None)
    assert k != plan_key(0, 30, None, Tracking(), {"layout": "follow", "boxes": []})
