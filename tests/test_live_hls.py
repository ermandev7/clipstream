"""Monitor del directo: lista HLS con los últimos trozos del búfer y sin acceso fuera de él."""
import pytest
from fastapi.testclient import TestClient

from app.clipper import live
from app.clipper.ingest.buffer import SegmentBuffer


@pytest.fixture
def client(tmp_path, monkeypatch):
    buf = SegmentBuffer(tmp_path / "buffer", 10, 15)
    lines = []
    for i in range(8):
        (buf.dir / f"seg_{i:06d}.ts").write_bytes(b"ts" * 10)
        lines.append(f"seg_{i:06d}.ts,{i * 10.02:.3f},{(i + 1) * 10.02:.3f}")
    buf.list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (tmp_path / "secreto.txt").write_text("no")

    class FakeSession:
        buffer = buf

    monkeypatch.setattr(live, "_sessions", {7: FakeSession()})
    from app.main import app

    return TestClient(app)


def test_playlist_lists_last_segments(client):
    r = client.get("/api/sources/7/live.m3u8")
    assert r.status_code == 200 and "mpegurl" in r.headers["content-type"]
    body = r.text
    assert body.startswith("#EXTM3U") and "#EXT-X-MEDIA-SEQUENCE:2" in body  # los 6 últimos de 8
    assert "#EXT-X-TARGETDURATION:11" in body and body.count("#EXTINF:10.020,") == 6
    assert "seg/seg_000007.ts" in body and "#EXT-X-ENDLIST" not in body  # directo: sin fin


def test_segment_served_and_nothing_outside(client):
    assert client.get("/api/sources/7/seg/seg_000003.ts").status_code == 200
    r = client.get("/api/sources/7/seg/..%2Fsecreto.txt")  # no encaja con la ruta: la recoge la interfaz (index.html)
    assert r.text != "no" and "mp2t" not in r.headers.get("content-type", "")
    assert client.get("/api/sources/7/seg/secreto.txt").status_code == 404
    assert client.get("/api/sources/99/live.m3u8").status_code == 404
