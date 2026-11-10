"""Publicación en TikTok sin red: reglas de las normas de TikTok, PKCE, trozos y ciclo de una publicación."""
import hashlib
import time
from urllib.parse import parse_qs, urlparse

import pytest

from app.publish import service, tiktok

CREATOR = {"creator_nickname": "erman", "privacy_level_options": ["PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"],
           "comment_disabled": False, "duet_disabled": True, "stitch_disabled": False, "max_video_post_duration_sec": 180}
CLIP = {"start": 10.0, "end": 40.0}


def ok(**kw):
    return {"consent": True, "privacy_level": "PUBLIC_TO_EVERYONE", "title": "Hola #clip", **kw}


def test_privacy_has_no_default_and_must_be_an_option():
    with pytest.raises(service.PostError):
        service.validate(CLIP, ok(privacy_level=None), CREATOR)
    with pytest.raises(service.PostError):
        service.validate(CLIP, ok(privacy_level="FOLLOWER_OF_CREATOR"), CREATOR)


def test_consent_is_required():
    with pytest.raises(service.PostError, match="declaración"):
        service.validate(CLIP, ok(consent=False), CREATOR)


def test_interactions_off_by_default_and_creator_settings_win():
    info = service.validate(CLIP, ok(allow_comment=True, allow_duet=True), CREATOR)
    assert info["disable_comment"] is False
    assert info["disable_duet"] is True  # la cuenta tiene los dúos desactivados
    assert info["disable_stitch"] is True  # no se marcó
    assert info["brand_content_toggle"] is False and info["brand_organic_toggle"] is False


def test_commercial_content_rules():
    with pytest.raises(service.PostError, match="marca"):
        service.validate(CLIP, ok(commercial=True), CREATOR)
    with pytest.raises(service.PostError, match="privado"):
        service.validate(CLIP, ok(commercial=True, brand_content=True, privacy_level="SELF_ONLY"), CREATOR)
    info = service.validate(CLIP, ok(commercial=True, brand_organic=True), CREATOR)
    assert info["brand_organic_toggle"] and not info["brand_content_toggle"]


def test_duration_limit_from_creator_info():
    with pytest.raises(service.PostError, match="180"):
        service.validate({"start": 0, "end": 200}, ok(), CREATOR)


def test_chunk_plan_follows_media_transfer_guide():
    assert tiktok.chunk_plan(3_000_000) == (3_000_000, 1)
    assert tiktok.chunk_plan(60 * 1024 * 1024) == (60 * 1024 * 1024, 1)
    size = 150 * 1024 * 1024 + 123
    chunk, n = tiktok.chunk_plan(size)
    assert chunk == tiktok.CHUNK and n == size // chunk
    last = size - (n - 1) * chunk
    assert 5 * 1024 * 1024 <= chunk <= 64 * 1024 * 1024 and last <= 128 * 1024 * 1024


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    from config.settings import settings

    from app.core import db
    from app.publish.vault import Vault

    monkeypatch.setattr(settings, "storage_dir", tmp_path)
    monkeypatch.setattr(tiktok, "vault", Vault(tmp_path / "tiktok.vault"))
    db._local.conn = None
    db._initialized = False
    yield tmp_path
    db._local.conn = None
    db._initialized = False


def test_pkce_uses_hex_sha256_and_state_is_checked(isolated):
    tiktok.set_keys("awkey1234567", "secret")
    q = parse_qs(urlparse(tiktok.authorize_url()).query)
    assert q["redirect_uri"][0] == "http://127.0.0.1:8765/api/publish/tiktok/callback/"
    assert q["code_challenge_method"] == ["S256"] and q["scope"] == ["user.info.basic,video.publish"]
    verifier = tiktok._pending[q["state"][0]][0]
    assert q["code_challenge"][0] == hashlib.sha256(verifier.encode()).hexdigest()
    with pytest.raises(tiktok.TikTokError, match="state"):
        tiktok.handle_callback("code", "otro-state", None, None)


def test_tokens_refresh_and_rotate(isolated, monkeypatch):
    tiktok.set_keys("awkey1234567", "secret")
    tiktok.vault.update(tokens={"access_token": "old", "refresh_token": "r1", "expires_at": time.time() - 10,
                                "refresh_expires_at": time.time() + 1000})
    sent = {}

    def fake_post(url, data):
        sent.update(data)
        return {"access_token": "new", "refresh_token": "r2", "expires_in": 7200, "refresh_expires_in": 100000,
                "open_id": "x", "scope": "video.publish"}

    monkeypatch.setattr(tiktok, "_post_form", fake_post)
    assert tiktok.access_token() == "new" and sent["refresh_token"] == "r1"
    assert tiktok.vault.load()["tokens"]["refresh_token"] == "r2"  # se guarda el nuevo (el viejo ya no vale)
    assert tiktok.vault.path.read_text().startswith(("dpapi:", "plain:"))  # nunca en claro en Windows


def test_post_lifecycle_and_schedule(isolated, monkeypatch):
    from app.core import db

    mp4 = isolated / "clip.mp4"
    mp4.write_bytes(b"0" * 1000)
    sid = db.insert("sources", kind="local", title="s", status="done")
    cid = db.insert("clips", source_id=sid, start=0.0, end=30.0, peak=5.0, title="Clip", status="ready", files={"mp4": str(mp4)})
    monkeypatch.setattr(tiktok, "creator_info", lambda: CREATOR)
    monkeypatch.setattr(tiktok, "upload_video", lambda p, info, prog=None: (prog and prog(1.0)) or "pub_1")
    monkeypatch.setattr(tiktok, "post_status", lambda pid: {"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": [77]})
    monkeypatch.setattr(service.time, "sleep", lambda s: None)
    queued = []
    import app.workers.tasks as tasks

    monkeypatch.setattr(tasks, "publish_post_task", lambda pid: queued.append(pid))
    p = service.create_post(cid, ok(), None)
    assert p["status"] == "queued" and queued == [p["id"]]
    service.run_post(p["id"])
    done = db.get("posts", p["id"])
    assert done["status"] == "published" and done["post_id"] == "77" and done["publish_id"] == "pub_1"
    # Programada: no se encola hasta su hora.
    later = service.create_post(cid, ok(), time.time() + 3600)
    assert later["status"] == "scheduled" and len(queued) == 1
    assert later["id"] not in service.due_posts() and later["id"] in service.due_posts(time.time() + 3601)
    service.cancel(later["id"])
    assert db.get("posts", later["id"])["status"] == "cancelled"


def test_daily_cap(isolated, monkeypatch):
    from app.core import db

    now = time.time()
    for _ in range(tiktok.DAILY_CAP):
        db.insert("posts", clip_id=None, platform="tiktok", status="published", created_at=now, started_at=now)
    mp4 = isolated / "c.mp4"
    mp4.write_bytes(b"0")
    sid = db.insert("sources", kind="local", title="s", status="done")
    cid = db.insert("clips", source_id=sid, start=0.0, end=10.0, peak=5.0, title="C", status="ready", files={"mp4": str(mp4)})
    monkeypatch.setattr(tiktok, "creator_info", lambda: CREATOR)
    with pytest.raises(service.PostError, match="15"):
        service.create_post(cid, ok(), None)
