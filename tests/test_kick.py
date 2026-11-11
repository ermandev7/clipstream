import pytest

from app.clipper.ingest.kick import is_kick, parse


def test_parse_vod_urls():
    u = "e91df1b7-163c-4a53-93cd-09cc0b98507e"
    assert parse(f"https://kick.com/westcol/videos/{u}") == {"kind": "vod", "uuid": u, "slug": "westcol"}
    assert parse(f"https://kick.com/video/{u}")["uuid"] == u


def test_parse_channel():
    assert parse("https://kick.com/Spreen") == {"kind": "channel", "slug": "spreen"}
    assert parse("https://www.kick.com/coscu?ref=x") == {"kind": "channel", "slug": "coscu"}


def test_parse_rejects_other():
    assert not is_kick("https://www.youtube.com/watch?v=x")
    with pytest.raises(ValueError):
        parse("https://youtube.com/x")
    with pytest.raises(ValueError):
        parse("https://kick.com/browse")


def test_new_style_vod_link_uses_page_and_channel_list(monkeypatch):
    """Enlaces nuevos (01…): la API antigua da 404; la fuente sale de og:video y los datos de la lista del canal."""
    from app.clipper.ingest import kick

    src = "https://stream.kick.com/x/ivs/v1/1/c/2026/11/8/1/21/abc/media/hls/master.m3u8"
    page = (f'<meta property="og:video" content="{src}"/>'
            '<meta property="og:title" content="baile - ermandev7 VOD on KICK"/>')

    def fake_get(path):
        if path.startswith("/api/v1/video/"):
            raise ValueError("Kick no encuentra ese canal o vídeo")
        return [{"source": "otra", "session_title": "no"},
                {"source": src, "session_title": "Mi directo", "channel_id": 7, "duration": 120000}]

    class Resp:
        status_code = 200
        text = page

        def raise_for_status(self):
            pass

    monkeypatch.setattr(kick, "_get", fake_get)
    monkeypatch.setattr(kick.httpx, "get", lambda *a, **k: Resp())
    i = kick.vod_info("01a1b8be-f9f0-710f-b711-b57db83ebf1b", "ermandev7")
    assert i["source"] == src and i["title"] == "Mi directo" and i["duration"] == 120 and i["channel"] == "ermandev7"
