"""Publicación por tandas: huecos por día, preferencias y siguiente tanda."""
from datetime import datetime

import pytest

from app.publish import batch


def ts(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi).timestamp()


def hm(t):
    x = datetime.fromtimestamp(t)
    return (x.day, x.hour, x.minute)


def test_five_a_day_hourly_then_next_day():
    now = ts(2026, 11, 10, 9)
    slots = batch.next_slots(12, now, [], per_day=5, interval_min=60, start="18:00")
    assert [hm(t) for t in slots[:5]] == [(10, 18, 0), (10, 19, 0), (10, 20, 0), (10, 21, 0), (10, 22, 0)]
    assert [hm(t) for t in slots[5:10]] == [(11, 18, 0), (11, 19, 0), (11, 20, 0), (11, 21, 0), (11, 22, 0)]
    assert [hm(t) for t in slots[10:]] == [(12, 18, 0), (12, 19, 0)]


def test_past_slots_today_are_skipped():
    now = ts(2026, 11, 10, 19, 30)  # 18:00 y 19:00 ya pasaron
    slots = batch.next_slots(5, now, [], 5, 60, "18:00")
    assert [hm(t) for t in slots] == [(10, 20, 0), (10, 21, 0), (10, 22, 0), (11, 18, 0), (11, 19, 0)]


def test_day_already_full_is_skipped_and_taken_slots_avoided():
    now = ts(2026, 11, 10, 9)
    taken = [ts(2026, 11, 10, 18 + k) for k in range(5)] + [ts(2026, 11, 11, 18)]
    slots = batch.next_slots(5, now, taken, 5, 60, "18:00")
    # Día 10 lleno; el 11 ya tiene las 18:00 ocupadas: quedan 4 huecos y el quinto pasa al día 12.
    assert [hm(t) for t in slots] == [(11, 19, 0), (11, 20, 0), (11, 21, 0), (11, 22, 0), (12, 18, 0)]


def test_slots_never_cross_midnight():
    slots = batch.next_slots(3, ts(2026, 11, 10, 9), [], 5, 120, "21:00")
    assert [hm(t) for t in slots] == [(10, 21, 0), (10, 23, 0), (11, 21, 0)]


def test_prefs_are_clamped(tmp_path, monkeypatch):
    monkeypatch.setattr(batch.settings, "storage_dir", tmp_path)
    assert batch.load_prefs() == batch.DEFAULTS
    p = batch.save_prefs({"per_day": 40, "interval_min": 7, "start": "25:99"})
    assert p == {"per_day": batch.PER_DAY_MAX, "interval_min": 60, "start": "18:00"}
    assert batch.save_prefs({"per_day": 3, "interval_min": 120, "start": "9:5"}) == \
        {"per_day": 3, "interval_min": 120, "start": "09:05"}


@pytest.fixture
def clips_db(tmp_path, monkeypatch):
    from app.core import db

    monkeypatch.setattr(batch.settings, "storage_dir", tmp_path / "storage")
    db._local.conn = None
    db._initialized = False
    sid = db.insert("sources", kind="local", path="x.mp4", status="done")
    ids = [db.insert("clips", source_id=sid, start=i * 30, end=i * 30 + 20, peak=i * 30 + 10, score=s, status="ready")
           for i, s in enumerate([50, 90, 70, 60, 80, 40, 95])]
    db.update("clips", ids[0], hidden=1)
    yield db, sid, ids
    db._local.conn = None
    db._initialized = False


def test_next_batch_best_first_and_skips_scheduled(clips_db):
    db, sid, ids = clips_db
    db.insert("posts", clip_id=ids[6], platform="tiktok", status="scheduled", scheduled_at=ts(2026, 11, 10, 18),
              created_at=0, updated_at=0)
    b = batch.next_batch(sid, now=ts(2026, 11, 10, 9))
    scores = [it["clip"]["score"] for it in b["items"]]
    assert scores == [90, 80, 70, 60, 40]  # sin el oculto (50) ni el ya programado (95)
    assert b["remaining"] == 0
    # La hora de las 18:00 ya está ocupada por el programado: la tanda empieza a las 19:00.
    assert hm(b["items"][0]["scheduled_at"]) == (10, 19, 0)
