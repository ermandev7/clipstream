"""Exportar: guardar clips en una carpeta y quitarlos de la lista (sin borrar nada)."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def setup(tmp_path, monkeypatch):
    from config.settings import settings

    from app.core import db

    monkeypatch.setattr(settings, "storage_dir", tmp_path / "storage")
    db._local.conn = None
    db._initialized = False
    files = {}
    for k, ext in (("mp4", ".mp4"), ("png", ".png"), ("txt", ".txt")):
        p = tmp_path / f"f{ext}"
        p.write_bytes(b"x" * 10)
        files[k] = str(p)
    sid = db.insert("sources", kind="local", title="s", status="done")
    ready = db.insert("clips", source_id=sid, start=0.0, end=10.0, peak=5.0, title="Mi clip: ¿bueno?", status="ready", files=files)
    pending = db.insert("clips", source_id=sid, start=20.0, end=30.0, peak=25.0, title="Otro", status="pending")
    from app.main import app

    yield TestClient(app), tmp_path, ready, pending
    db._local.conn = None
    db._initialized = False


def test_save_only_video_is_flat(setup):
    client, tmp, ready, pending = setup
    dest = tmp / "destino"
    dest.mkdir()
    r = client.post("/api/clips/save", json={"ids": [ready, pending], "dest": str(dest), "mp4": True, "png": False,
                                             "txt": False, "srt": False}).json()
    assert r["clips"] == 1 and r["files"] == 1  # el pendiente se salta
    assert [p.name for p in dest.iterdir()] == [f"{ready:04d}_Mi_clip_¿bueno.mp4"]
    # Otra vez: no se sobrescribe, se numera.
    client.post("/api/clips/save", json={"ids": [ready], "dest": str(dest), "png": False, "txt": False})
    assert len(list(dest.iterdir())) == 2


def test_save_with_extras_uses_subfolder(setup):
    client, tmp, ready, _ = setup
    dest = tmp / "d2"
    dest.mkdir()
    client.post("/api/clips/save", json={"ids": [ready], "dest": str(dest)})
    sub = dest / f"{ready:04d}_Mi_clip_¿bueno"
    assert sorted(p.suffix for p in sub.iterdir()) == [".mp4", ".png", ".txt"]


def test_save_rejects_missing_folder(setup):
    client, tmp, ready, _ = setup
    assert client.post("/api/clips/save", json={"ids": [ready], "dest": str(tmp / "no")}).status_code == 400


def test_hide_and_restore_keeps_files(setup):
    from app.core import db

    client, _tmp, ready, _ = setup
    client.post("/api/clips/hide", json={"ids": [ready], "hidden": True})
    clip = next(c for c in client.get("/api/clips").json() if c["id"] == ready)
    assert clip["hidden"] == 1 and clip["files"]["mp4"]
    client.post("/api/clips/hide", json={"ids": [ready], "hidden": False})
    assert db.get("clips", ready)["hidden"] == 0
