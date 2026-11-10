"""Prueba manual de reencuadre: detecta, planifica, renderiza 30 s y guarda 2 frames.

    .venv\\Scripts\\python tests\\manual\\reframe_check.py video.mp4 [inicio] [layout_forzado]
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
from app.clipper.reframe.detector import detect_range  # noqa: E402
from app.clipper.reframe.detector import scene_cuts  # noqa: E402
from app.clipper.reframe.planner import plan_clip  # noqa: E402
from app.shared.render import ffmpeg  # noqa: E402
from app.shared.render.vertical import RenderSpec, render  # noqa: E402

src = Path(sys.argv[1])
start = float(sys.argv[2]) if len(sys.argv) > 2 else 10.0
force = sys.argv[3] if len(sys.argv) > 3 else None
end = start + 30
info = ffmpeg.probe(src)
t0 = time.perf_counter()
frames = detect_range(src, start, end)
t_det = time.perf_counter() - t0
plan = plan_clip(frames, scene_cuts(src, start, end), end - start, info.width, info.height, force=force)
faces = sum(len(f.boxes) for f in frames)
print(f"{src.name}: {len(frames)} frames, {faces} detecciones en {t_det:.1f}s -> {plan.layout}, {len(plan.shots)} planos")
for s in plan.shots:
    print(f"   {s.start:5.1f}-{s.end:5.1f}s {s.plan.layout:<8} {s.plan.reason}")
out_dir = Path("storage/testdata/reframe/out")
out_dir.mkdir(parents=True, exist_ok=True)
out = out_dir / f"{src.stem}_{plan.layout}.mp4"
t0 = time.perf_counter()
render(RenderSpec(src, start, end, None, out, fps=info.fps, plan=plan.to_dict(), src_w=info.width, src_h=info.height,
                  workdir=out_dir))
print(f"   render {time.perf_counter() - t0:.1f}s -> {ffmpeg.probe(out)}")
for t in (3, 20):
    ffmpeg.grab_frame(out, t, out_dir / f"{src.stem}_{plan.layout}_{t}.jpg", 360)
(out_dir / f"{src.stem}_{plan.layout}.json").write_text(json.dumps(plan.to_dict(), indent=1))
