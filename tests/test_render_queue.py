"""Los clips se renderizan de uno en uno y en el orden del vídeo, aunque Huey tenga varios hilos."""
import threading
import time

from app.clipper import pipeline


def test_renders_are_serial_and_in_video_order(monkeypatch):
    clips = {1: {"id": 1, "source_id": 5, "start": 300.0}, 2: {"id": 2, "source_id": 5, "start": 20.0},
             3: {"id": 3, "source_id": 5, "start": 150.0}}
    order, active, overlap = [], [0], []

    monkeypatch.setattr(pipeline.db, "get", lambda table, cid: clips.get(cid))

    def fake_render(cid):
        active[0] += 1
        overlap.append(active[0])
        time.sleep(0.05)
        order.append(cid)
        active[0] -= 1

    monkeypatch.setattr(pipeline, "_render_clip", fake_render)
    # Ocupa la cola un momento para que los tres hilos lleguen a esperar a la vez.
    gate = {"id": 99, "source_id": 1, "start": 0.0}
    pipeline._render_turn(gate)
    threads = [threading.Thread(target=pipeline.render_clip, args=(cid,)) for cid in (1, 2, 3)]
    for t in threads:
        t.start()
    time.sleep(0.2)
    pipeline._render_done()
    for t in threads:
        t.join(5)
    assert order == [2, 3, 1]  # por segundo de inicio, no por orden de llegada
    assert max(overlap) == 1  # nunca dos a la vez
