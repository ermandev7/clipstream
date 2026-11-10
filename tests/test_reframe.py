from app.clipper.reframe.detector import Box, FrameDet
from app.clipper.reframe.planner import Tracking, interpolate_path, plan_layout, smooth_path, window_width

W, H = 1920, 1080


def frames(per_frame, n=40):
    return [FrameDet(i / 4, per_frame(i)) for i in range(n)]


def face(cx, cy, size=0.12, score=0.9):
    return Box(cx - size / 2, cy - size / 2, size, size * 16 / 9, score, "face")


def test_window_width_16_9():
    assert abs(window_width(W, H) - 0.3164) < 1e-3


def test_no_people_is_blur():
    p = plan_layout(frames(lambda i: []), W, H)
    assert p.layout == "blur"


def test_single_person_follow_stays_within_frame():
    p = plan_layout(frames(lambda i: [face(0.2 + 0.015 * i, 0.4)]), W, H)
    assert p.layout == "follow"
    lo, hi = p.window_w / 2, 1 - p.window_w / 2
    assert all(lo - 1e-6 <= x <= hi + 1e-6 for x in p.path)
    assert p.path[-1] > p.path[0]  # la cámara acompaña el desplazamiento


def test_dead_zone_ignores_small_moves():
    targets = [0.5 + (0.01 if i % 2 else -0.01) for i in range(40)]
    path = smooth_path(targets, 4, 0.316, Tracking(dead_zone=0.12))
    assert max(path) - min(path) < 1e-6


def test_motion_is_speed_limited():
    path = smooth_path([0.2] * 2 + [0.8] * 20, 4, 0.316, Tracking(max_speed=0.35))
    steps = [abs(b - a) for a, b in zip(path, path[1:])]
    assert max(steps) <= 0.35 / 4 + 1e-9


def test_cut_mode_jumps():
    path = smooth_path([0.2] * 2 + [0.8] * 3, 4, 0.316, Tracking(cut=True))
    assert path[2] == 0.8 or abs(path[2] - (1 - 0.316 / 2)) < 1e-6


def test_lost_target_returns_to_center():
    path = smooth_path([0.2] * 4 + [None] * 60, 4, 0.316, Tracking())
    assert abs(path[-1] - 0.5) < 0.05


def test_two_people_close_is_group():
    p = plan_layout(frames(lambda i: [face(0.45, 0.4, 0.06), face(0.55, 0.4, 0.06)]), W, H)
    assert p.layout == "group"


def test_two_people_apart_is_split():
    p = plan_layout(frames(lambda i: [face(0.2, 0.4), face(0.8, 0.4)]), W, H)
    assert p.layout == "split2"
    assert len(p.regions) == 2 and p.regions[0].x < p.regions[1].x
    for r in p.regions:  # proporción 1080x960 en píxeles
        assert abs((r.w * W) / (r.h * H) - 1080 / 960) < 0.02


def test_three_people_apart_is_split3():
    p = plan_layout(frames(lambda i: [face(0.15, 0.4), face(0.5, 0.4), face(0.85, 0.4)]), W, H)
    assert p.layout == "split3" and len(p.regions) == 3


def test_facecam_in_corner_with_game():
    p = plan_layout(frames(lambda i: [face(0.88, 0.82, 0.07)]), W, H)
    assert p.layout == "facecam"
    fc, game = p.regions
    assert fc.x > 0.6 and fc.y > 0.4
    assert abs((game.w * W) / (game.h * H) - 1080 / 1152) < 0.02


def test_force_layout():
    p = plan_layout(frames(lambda i: [face(0.5, 0.4)]), W, H, force="blur")
    assert p.layout == "blur"


def test_interpolate_path():
    out = interpolate_path([0.4, 0.6], 4, 8, 0.5)
    assert out == [0.4, 0.5, 0.6, 0.6]


def test_seated_person_in_wide_shot_is_not_facecam():
    from app.clipper.reframe.planner import plan_clip

    # Plano general 5-12 s con una cabeza pequeña cerca del borde izquierdo; primeros planos el resto.
    def boxes(i):
        t = i / 4
        if 5 <= t < 12:
            return [Box(0.18, 0.27, 0.05, 0.09, 0.9, "pose")]
        return [face(0.5, 0.4, 0.2)]

    p = plan_clip(frames(boxes, 120), [5.0, 12.0], 30.0, W, H)
    assert all(s.plan.layout != "facecam" for s in p.shots)
    assert p.shots[1].plan.layout == "follow"
