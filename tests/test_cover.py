from app.shared.thumbnail.cover import cover_text_markup, plan_at, sharpness, text_position

import numpy as np


def test_text_position_stacked_layouts_use_seam():
    assert text_position({"layout": "facecam"}) == r"{\an5\pos(540,768)}"
    assert text_position({"layout": "split2"}) == r"{\an5\pos(540,960)}"
    assert text_position({"layout": "follow"}) == ""
    assert "\a" not in text_position({"layout": "facecam"})  # sin caracteres de control


def test_markup_highlights_longest_word():
    m = cover_text_markup("mitos y mentiras")
    assert "MENTIRAS" in m and m.index("&H0047D5F5") < m.index("MENTIRAS")


def test_plan_at_slices_path():
    plan = {"layout": "follow", "shots": [{"start": 0, "end": 5, "layout": "follow", "path": [0.1, 0.2, 0.3, 0.4], "path_fps": 1},
                                          {"start": 5, "end": 10, "layout": "blur"}]}
    p = plan_at(plan, 2.0)
    assert p["shots"][0]["path"] == [0.3, 0.4] and p["shots"][0]["start"] == 0
    assert plan_at(plan, 7.0)["shots"][0]["layout"] == "blur"


def test_sharpness_prefers_detail():
    flat = np.full((50, 50), 128.0)
    noisy = np.random.default_rng(0).integers(0, 255, (50, 50)).astype(float)
    assert sharpness(noisy) > sharpness(flat)
