import datetime as dt

import pytest

from app.clipper import reprocess


class FakeResp:
    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data


def kick_src(created: dt.datetime) -> dict:
    return {"platform": "kick", "url": "https://kick.com/canal", "created_at": created.timestamp(), "kind": "live"}


def test_kick_picks_vod_that_started_before_capture(monkeypatch):
    cap = dt.datetime(2026, 11, 9, 20, 0, tzinfo=dt.UTC)
    vids = [
        {"is_live": False, "start_time": "2026-11-09 18:30:00", "video": {"uuid": "hoy"}},
        {"is_live": False, "start_time": "2026-11-07 18:00:00", "video": {"uuid": "anteayer"}},
        {"is_live": True, "start_time": "2026-11-09 21:00:00", "video": {"uuid": "en-directo"}},
    ]
    monkeypatch.setattr("httpx.get", lambda *a, **k: FakeResp(vids))
    assert reprocess.resolve_replay(kick_src(cap)) == "https://kick.com/canal/videos/hoy"


def test_kick_without_vod_is_not_ready(monkeypatch):
    monkeypatch.setattr("httpx.get", lambda *a, **k: FakeResp([]))
    with pytest.raises(reprocess.NotReady):
        reprocess.resolve_replay(kick_src(dt.datetime(2026, 11, 9, 20, 0, tzinfo=dt.UTC)))


@pytest.mark.parametrize("status, ready", [("is_live", False), ("post_live", False), ("was_live", True), ("not_live", False)])
def test_youtube_status(monkeypatch, status, ready):
    monkeypatch.setattr("app.clipper.ingest.vod.fetch_info",
                        lambda url: {"live_status": status, "is_live": status == "is_live", "webpage_url": url})
    src = {"platform": "youtube", "url": "https://www.youtube.com/watch?v=abc"}
    if ready:
        assert reprocess.resolve_replay(src) == src["url"]
    else:
        with pytest.raises(reprocess.NotReady):
            reprocess.resolve_replay(src)
