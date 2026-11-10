"""API del editor sobre una fuente local sintética (sin GPU: no se renderiza nada)."""
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SAMPLE = Path("storage/testdata/sample.mp4")


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    if not SAMPLE.exists():
        pytest.skip("Falta storage/testdata/sample.mp4 (vídeo de prueba de 17 s)")
    from config.settings import settings

    from app.core import db

    original = settings.storage_dir
    settings.storage_dir = tmp_path_factory.mktemp("storage")
    db._local.conn = None
    db._initialized = False
    video = settings.storage_dir / "v.mp4"
    shutil.copy(SAMPLE, video)
    sid = db.insert("sources", kind="local", path=str(video), title="v", status="done", duration=17.0)
    cid = db.insert("clips", source_id=sid, start=2.0, end=12.0, peak=6.0, title="Clip", status="ready",
                    files={}, warnings=[])
    from app.main import app

    with TestClient(app) as c:
        yield c, sid, cid
    settings.storage_dir = original
    db._local.conn = None
    db._initialized = False


def test_detail(env):
    c, sid, cid = env
    d = c.get(f"/api/clips/{cid}/detail").json()
    assert d["clip"]["id"] == cid and d["crop"] == {"mode": "auto"}
    assert d["limits"]["min"] == 0 and 16.5 < d["limits"]["max"] < 17.5


def test_trim_validation(env):
    c, _, cid = env
    assert c.patch(f"/api/clips/{cid}", json={"start": 2, "end": 3}).status_code == 400  # < 3 s
    assert c.patch(f"/api/clips/{cid}", json={"start": 10, "end": 30}).status_code == 400  # fuera del vídeo
    r = c.patch(f"/api/clips/{cid}", json={"start": 1.0, "end": 9.5})
    assert r.status_code == 200 and r.json()["clip"]["end"] == 9.5


def test_dirty_after_edit_and_undo_values(env):
    c, _, cid = env
    d = c.patch(f"/api/clips/{cid}", json={"hook": "mira", "settings": {"style": "impacto"}}).json()
    assert d["clip"]["hook"] == "MIRA" and d["clip"]["settings"]["style"] == "impacto"
    assert d["dirty"] is True


def test_hashtags_are_normalized(env):
    c, _, cid = env
    d = c.patch(f"/api/clips/{cid}", json={"hashtags": ["#Humor", "humor", " viral clip "]}).json()
    assert d["clip"]["hashtags"] == ["humor", "viralclip"]


def test_crop_settings_and_clear(env):
    c, _, cid = env
    crop = {"mode": "manual", "layout": "split2", "boxes": [{"x": 0.1, "y": 0.1, "w": 0.3, "h": 0.6}, {"x": 0.6, "y": 0.1, "w": 0.3, "h": 0.6}]}
    d = c.patch(f"/api/clips/{cid}", json={"settings": {"crop": crop}}).json()
    assert d["crop"]["mode"] == "manual" and len(d["crop"]["boxes"]) == 2
    assert c.patch(f"/api/clips/{cid}", json={"settings": {"crop": {"mode": "auto", "layout": "nope"}}}).status_code == 422
    d = c.patch(f"/api/clips/{cid}", json={"settings": {"clear_crop": True}}).json()
    assert d["crop"] == {"mode": "auto"}


def test_preset_is_used_when_clip_has_no_crop(env):
    c, sid, cid = env
    r = c.post(f"/api/sources/{sid}/preset", json={"crop": {"mode": "auto", "layout": "blur"}})
    assert r.status_code == 200 and r.json()["rerendered"] == 0
    assert c.get(f"/api/clips/{cid}/detail").json()["crop"]["layout"] == "blur"


def test_frame(env):
    c, _, cid = env
    r = c.get(f"/api/clips/{cid}/frame?t=1")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and len(r.content) > 1000


def test_options(env):
    c, _, _ = env
    o = c.get("/api/editor/options").json()
    assert {s["id"] for s in o["styles"]} == {"clasico", "impacto", "limpio"}
    assert next(l for l in o["layouts"] if l["id"] == "speaker")["available"] is True
