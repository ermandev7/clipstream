import json

from app.clipper.signals.chat.youtube_chat import is_hit, parse_chat_line, series_from_times


def _line(text_runs, ts_usec="1762693200000000", offset=None, renderer="liveChatTextMessageRenderer"):
    wrapper = {"actions": [{"clickTrackingParams": "x", "addChatItemAction": {"item": {renderer: {
        "message": {"runs": text_runs}, "timestampUsec": ts_usec}}}}]}
    if offset is not None:
        wrapper["videoOffsetTimeMsec"] = str(offset)
    return json.dumps({"replayChatItemAction": wrapper})


def test_parse_live_line_uses_wall_clock():
    out = parse_chat_line(_line([{"text": "jajaja "}, {"emoji": {"emojiId": "😂", "shortcuts": [":joy:"]}}]))
    assert out == [(None, 1762693200.0, "jajaja 😂")]


def test_parse_replay_line_uses_video_offset():
    out = parse_chat_line(_line([{"text": "CLIP IT"}], offset=125500))
    assert out[0][0] == 125.5


def test_parse_custom_emoji_and_ignores_placeholders():
    line = json.dumps({"replayChatItemAction": {"actions": [
        {"addChatItemAction": {"item": {"liveChatPlaceholderItemRenderer": {"id": "a"}}}},
        {"addChatItemAction": {"item": {"liveChatTextMessageRenderer": {"message": {"runs": [
            {"emoji": {"emojiId": "UC/x", "isCustomEmoji": True, "shortcuts": [":kekw:"]}}]}, "timestampUsec": "1000000"}}}},
    ]}})
    assert parse_chat_line(line) == [(None, 1.0, ":kekw:")]


def test_parse_garbage():
    assert parse_chat_line("no json") == []


def test_hits():
    assert is_hit("JAJAJA") and is_hit("xd") and is_hit("clip!") and is_hit("😂")
    assert not is_hit("buenas tardes")


def test_series():
    rate, hits = series_from_times([(0.5, True), (0.9, False), (3.2, True), (99, True)], 5)
    assert rate.tolist() == [2, 0, 0, 1, 0]
    assert hits.tolist() == [1, 0, 0, 1, 0]


def test_parse_events_superchat_and_members():
    from app.clipper.signals.chat.youtube_chat import event_series, parse_chat_events

    line = json.dumps({"replayChatItemAction": {"videoOffsetTimeMsec": "12500", "actions": [
        {"addChatItemAction": {"item": {"liveChatPaidMessageRenderer": {"timestampUsec": "1"}}}},
        {"addChatItemAction": {"item": {"liveChatMembershipItemRenderer": {"timestampUsec": "2"}}}},
        {"addChatItemAction": {"item": {"liveChatTextMessageRenderer": {"message": {"runs": [{"text": "hola"}]}}}}},
    ]}})
    evs = parse_chat_events(line)
    assert [(o, k) for o, _t, k in evs] == [(12.5, "donation"), (12.5, "sub")]
    s = event_series([(o, k) for o, _t, k in evs], 20)
    assert s["donation"][12] == 1 and s["sub"][12] == 1


def test_viewer_poller_series_steps():
    from app.clipper.live import ViewerPoller

    p = ViewerPoller("u", "kick")
    p.samples = [(100.0, 50), (130.0, 80)]
    s = p.series(95.0, 60)
    assert s[0] == 50 and s[10] == 50 and s[40] == 80 and s[-1] == 80


def test_live_session_without_chat_stops_cleanly(tmp_path, monkeypatch):
    """Kick no tiene chat: al terminar, la sesión debe marcarse como detenida igualmente."""
    from config.settings import settings

    from app.clipper import live
    from app.core import db

    monkeypatch.setattr(settings, "storage_dir", tmp_path)
    db._local.conn = None
    db._initialized = False
    sid = db.insert("sources", kind="live", platform="kick", url="https://kick.com/x", status="live")

    class FakeCapture:
        running = False

        def stop(self):
            pass

    s = live.LiveSession(sid, "https://kick.com/x", "kick")
    assert s.chat is None
    s.capture = FakeCapture()
    s.viewers.stop()
    s._cycle = lambda: None
    s._run()
    assert db.get("sources", sid)["status"] == "stopped"
    db._local.conn = None
    db._initialized = False
