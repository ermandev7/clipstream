"""Efectos según el contenido del clip: qué suena, qué se ve y que el render con efectos funciona."""
import subprocess

import numpy as np

from app.shared.fx import planner as fx
from app.shared.render import vertical
from app.shared.render.ffmpeg import ffmpeg_bin, probe


def ctx(**kw):
    base = dict(duration=30.0, peak=12.0, hook=True, words=[], events={}, cuts=[])
    base.update(kw)
    return fx.Context(**base)


def series(n=30, **on):
    """Serie por segundo con 1.0 en los segundos indicados: series(scream=[5, 6])."""
    out = {}
    for k, secs in on.items():
        s = np.zeros(n)
        s[list(secs)] = 1.0
        out[k] = s
    return out


def names(sounds):
    return [(round(s.t, 2), s.sound) for s in sounds]


def test_none_modes_do_nothing():
    c = ctx(events=series(scream=[5]))
    assert fx.plan_sounds(c, "none") == []
    assert fx.plan_visuals(c, "none") == (0.0, [])


def test_auto_reacts_to_content():
    c = ctx(events=series(scream=[5, 6], donation=[20], applause=[25, 26]),
            words=[(8.0, 8.5, "¡no puede ser!"), (16.0, 16.4, "falló")])
    got = names(fx.plan_sounds(c, "auto"))
    assert (0.0, "whoosh") in got and (5.0, "boom") in got and (20.0, "cash") in got
    assert any(s == "fail" for _, s in got)
    assert all(b - a >= 3.0 for (a, _), (b, _) in zip(got, got[1:]))  # nunca amontonados
    assert len(got) <= 30 // 9 + 1  # tope por duración


def test_quiet_clip_gets_almost_nothing():
    """Un clip sin nada especial no se llena de efectos: gancho y momento cumbre como mucho."""
    got = names(fx.plan_sounds(ctx(hook=False), "auto"))
    assert got == [(12.0, "pop")]


def test_laughter_and_music_reduce_sounds():
    words = [(t, t + 0.3, "wow") for t in (4.0, 9.0, 15.0, 22.0)]
    laughing = ctx(hook=False, words=words, events={"laughter": np.ones(30)})
    assert all(s.sound in ("cash", "boom") for s in fx.plan_sounds(laughing, "auto"))
    music = ctx(words=words, events={"music": np.ones(30)})
    assert len(fx.plan_sounds(music, "auto")) <= 2


def test_meme_adds_crickets_on_awkward_silence():
    words = [(1.0, 1.5, "bueno"), (1.6, 2.0, "pues"), (6.0, 6.4, "nada")]
    got = names(fx.plan_sounds(ctx(hook=False, words=words, peak=20.0), "meme"))
    assert any(s == "crickets" for _, s in got)
    assert not any(s == "crickets" for _, s in names(fx.plan_sounds(ctx(hook=False, words=words, peak=20.0), "auto")))


def test_visuals_follow_events():
    c = ctx(events=series(scream=[5], laughter=[18, 19], applause=[25, 26]))
    push, ev = fx.plan_visuals(c, "dynamic", fx.plan_sounds(c, "auto"))
    kinds = {(e.kind, round(e.t)) for e in ev}
    assert push > 0 and ("punch", 12) in kinds and ("shake", 5) in kinds and ("flash", 25) in kinds
    assert ("pulse", 18) in kinds
    sub_push, sub = fx.plan_visuals(c, "subtle")
    assert [e.kind for e in sub] == ["punch"] and sub_push < push


def test_clip_events_slice():
    ev = fx.clip_events({"scream": np.arange(100.0)}, 40.2, 45.0)
    assert list(ev["scream"]) == [40, 41, 42, 43, 44]


def test_render_with_all_effects(tmp_path):
    src = tmp_path / "src.mp4"
    subprocess.run([ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=1280x720:rate=30:duration=6", "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
                    "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(src)], check=True)
    out = tmp_path / "out.mp4"
    spec = vertical.RenderSpec(src=src, start=0.0, end=6.0, ass_path=None, out=out, use_nvenc=False, src_w=1280, src_h=720,
                               fx={"push": 0.06, "events": [{"t": 1.0, "kind": "shake"}, {"t": 2.5, "kind": "punch"},
                                                            {"t": 4.0, "kind": "pulse"}, {"t": 4.8, "kind": "flash"}]})
    vertical.render(spec)
    info = probe(out)
    assert info.width == 1080 and info.height == 1920 and abs(info.duration - 6.0) < 0.2
