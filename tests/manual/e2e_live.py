"""Prueba manual de directo: captura N segundos, mide VRAM y clips, y detiene.

    .venv\\Scripts\\python tests\\manual\\e2e_live.py https://www.youtube.com/watch?v=... 240
"""
import sys
import time

import httpx

API = "http://127.0.0.1:8765"
url, seconds = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 240
t0 = time.time()
r = httpx.post(f"{API}/api/sources", json={"kind": "live", "platform": "youtube", "url": url}, timeout=90)
if r.status_code != 200:
    sys.exit(f"Error {r.status_code}: {r.text}")
sid = r.json()["id"]
print(f"Fuente {sid}: {r.json()['title']}")
peak = 0.0
seen_events = 0
while time.time() - t0 < seconds:
    s = httpx.get(f"{API}/api/sources/{sid}", timeout=10).json()
    g = httpx.get(f"{API}/api/system/status", timeout=10).json()["gpu"]
    peak = max(peak, g["used_gb"])
    for e in s["events"][seen_events:]:
        print(f"  [{time.time() - t0:5.0f}s] {e['level']:<5} {e['message'][:140]}")
    seen_events = len(s["events"])
    sig = s.get("signals") or {}
    chat = sig.get("chat_rate")
    print(f"{time.time() - t0:5.0f}s  {s['status']:<8} capturado {s['duration']:5.0f}s  chat {'sí' if chat else 'no'}"
          f"  clips {sum(c['status'] == 'ready' for c in s['clips'])}/{len(s['clips'])}  VRAM {g['used_gb']:.1f} GB ({g['active_model']})", flush=True)
    time.sleep(10)
httpx.post(f"{API}/api/sources/{sid}/stop", timeout=30)
time.sleep(8)
s = httpx.get(f"{API}/api/sources/{sid}", timeout=10).json()
print(f"\nEstado final {s['status']} · capturado {s['duration']:.0f}s · pico VRAM {peak:.2f} GB")
for c in s["clips"]:
    print(f"- [{c['status']}] {c['start']:6.1f}-{c['end']:6.1f} ({c['end'] - c['start']:4.1f}s) {c['score']:.0f} {c['title']} · {c['signals_label']}")
