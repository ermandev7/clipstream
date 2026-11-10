"""Borrar vídeos de verdad (también sus archivos) y «empezar limpio» al analizar uno nuevo."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def env(tmp_path, monkeypatch):
    from config.settings import settings

    from app.core import db

    monkeypatch.setattr(settings, "storage_dir", tmp_path / "storage")
    db._local.conn = None
    db._initialized = False
    import app.workers.tasks as tasks

    monkeypatch.setattr(tasks, "analyze_media_task", lambda sid: None)  # sin análisis real
    from app.main import app

    yield TestClient(app), tmp_path
    db._local.conn = None
    db._initialized = False


def make_source(tmp_path, status="done", mb=1):
    from app.clipper import cleanup
    from app.core import db

    sid = db.insert("sources", kind="local", title=f"v{status}", status=status, path=str(tmp_path / "mi_video.mp4"))
    d = cleanup.folder(sid)
    (d / "clips" / "1").mkdir(parents=True)
    (d / "audio.wav").write_bytes(b"a" * mb * 1_000_000)
    (d / "clips" / "1" / "clip.mp4").write_bytes(b"c" * 500_000)
    db.insert("clips", source_id=sid, start=0.0, end=10.0, peak=5.0, title="c", status="ready")
    return sid


def test_storage_report_splits_original_and_clips(env):
    client, tmp = env
    make_source(tmp, mb=2)
    r = client.get("/api/sources/storage").json()
    s = r["sources"][0]
    assert s["clips"] == 1 and s["clips_bytes"] == 500_000 and s["original_bytes"] == 2_000_000
    assert r["total_bytes"] == 2_500_000


def test_delete_frees_disk_but_never_touches_user_file(env):
    from app.clipper import cleanup
    from app.core import db

    client, tmp = env
    original = tmp / "mi_video.mp4"
    original.write_bytes(b"video del usuario")
    sid = make_source(tmp)
    r = client.delete(f"/api/sources/{sid}").json()
    assert r["freed_bytes"] == 1_500_000
    assert not cleanup.folder(sid).exists() and db.get("sources", sid) is None
    assert not db.query("SELECT 1 FROM clips WHERE source_id=?", (sid,))
    assert original.read_bytes() == b"video del usuario"


def test_busy_source_is_not_deleted(env):
    client, tmp = env
    sid = make_source(tmp, status="analyzing")
    assert client.delete(f"/api/sources/{sid}").status_code == 409


def test_start_clean_deletes_previous_but_not_busy(env):
    from app.core import db

    client, tmp = env
    old1, old2 = make_source(tmp), make_source(tmp)
    running = make_source(tmp, status="rendering")
    new_file = tmp / "nuevo.mp4"
    new_file.write_bytes(b"x")
    new = client.post("/api/sources", json={"kind": "local", "path": str(new_file), "clean_previous": True}).json()
    ids = {r["id"] for r in db.query("SELECT id FROM sources")}
    assert ids == {running, new["id"]} and old1 not in ids and old2 not in ids


def test_keep_previous_by_default(env):
    from app.core import db

    client, tmp = env
    old = make_source(tmp)
    f = tmp / "otro.mp4"
    f.write_bytes(b"x")
    client.post("/api/sources", json={"kind": "local", "path": str(f)})
    assert db.get("sources", old) is not None


def test_bulk_cleanup_keep_list(env):
    from app.core import db

    client, tmp = env
    a, b, c = make_source(tmp), make_source(tmp), make_source(tmp)
    r = client.post("/api/sources/cleanup", json={"keep": [c]}).json()
    assert r["deleted"] == 2 and {s["id"] for s in db.query("SELECT id FROM sources")} == {c}
    assert a != b
