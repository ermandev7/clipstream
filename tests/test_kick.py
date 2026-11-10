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
