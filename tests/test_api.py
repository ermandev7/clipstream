"""Validaciones de la API sin tocar la red ni la GPU (no se crea ninguna fuente válida)."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from config.settings import settings

    from app.core import db

    original = settings.storage_dir
    settings.storage_dir = tmp_path_factory.mktemp("storage")
    db._local.conn = None
    db._initialized = False
    from app.main import app

    with TestClient(app) as c:
        yield c
    settings.storage_dir = original
    db._local.conn = None
    db._initialized = False


def test_status(client):
    r = client.get("/api/system/status")
    assert r.status_code == 200
    assert {"gpu", "ollama", "llm_model"} <= r.json().keys()


@pytest.mark.parametrize("body, msg", [
    ({"kind": "vod", "platform": "youtube", "url": ""}, "Pega el link"),
    ({"kind": "vod", "platform": "youtube", "url": "https://vimeo.com/123"}, "no parece de YouTube"),
    ({"kind": "vod", "platform": "kick", "url": "https://kick.com/canal"}, "vídeo publicado de Kick"),
    ({"kind": "live", "platform": "kick", "url": "https://kick.com/canal/videos/e91df1b7-163c-4a53-93cd-09cc0b98507e"},
     "link del canal"),
    ({"kind": "vod", "platform": "kick", "url": "https://www.youtube.com/watch?v=x"}, "no parece de Kick"),
    ({"kind": "local", "path": "C:/no/existe.mp4"}, "Abrir ubicación"),
])
def test_invalid_sources_are_rejected(client, body, msg):
    r = client.post("/api/sources", json=body)
    assert r.status_code == 400
    assert msg in r.json()["detail"]


def test_local_rejects_non_video(client, tmp_path):
    f = tmp_path / "notas.md"
    f.write_text("x")
    r = client.post("/api/sources", json={"kind": "local", "path": str(f)})
    assert r.status_code == 400 and "Formato no soportado" in r.json()["detail"]


def test_unknown_source_and_clip(client):
    assert client.get("/api/sources/999").status_code == 404
    assert client.get("/api/clips/999/file/mp4").status_code == 404
    assert client.post("/api/sources/999/stop").status_code == 400


def test_zip_requires_ready_clips(client):
    r = client.post("/api/clips/zip", json={"ids": [999]})
    assert r.status_code == 400


def test_websocket_sends_gpu(client):
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "gpu"


def test_spa_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "ClipStream" in r.text
