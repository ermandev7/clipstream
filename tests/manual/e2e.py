"""Prueba manual de extremo a extremo contra el servidor en marcha.

    .venv\\Scripts\\python tests\\manual\\e2e.py vod https://www.youtube.com/watch?v=...
    .venv\\Scripts\\python tests\\manual\\e2e.py local C:\\ruta\\video.mp4
"""
import sys
import time

import httpx

API = "http://127.0.0.1:8765"
kind, target = sys.argv[1], sys.argv[2]
body = {"kind": kind, "platform": "youtube"} | ({"url": target} if kind != "local" else {"path": target})
t0 = time.time()
src = httpx.post(f"{API}/api/sources", json=body, timeout=60).raise_for_status().json()
sid = src["id"]
print(f"Fuente {sid} creada")
last = ""
peak_vram = 0.0
while True:
    s = httpx.get(f"{API}/api/sources/{sid}", timeout=10).json()
    g = httpx.get(f"{API}/api/system/status", timeout=10).json()["gpu"]
    peak_vram = max(peak_vram, g["used_gb"])
    clips = s["clips"]
    line = f"{time.time() - t0:6.0f}s  {s['status']:<11} {s['stage']:<18} {s['progress'] * 100:5.1f}%  clips {sum(c['status'] == 'ready' for c in clips)}/{len(clips)}  VRAM {g['used_gb']:.1f} GB"
    if line[8:] != last:
        print(line, flush=True)
        last = line[8:]
    if s["status"] in ("done", "error", "stopped"):
        break
    time.sleep(2)
print(f"\nTOTAL {time.time() - t0:.0f} s  · duración vídeo {s['duration']:.0f} s · pico VRAM {peak_vram:.2f} GB")
if s.get("error"):
    print("ERROR:", s["error"])
for c in s["clips"]:
    print(f"- [{c['status']}] {c['start']:7.1f}-{c['end']:7.1f} ({c['end'] - c['start']:5.1f}s) score {c['score']:5.1f} "
          f"(señal {c['signal_score']:.0f}, IA {c['llm_score']}) {c['title']} · {c['signals_label']}")
