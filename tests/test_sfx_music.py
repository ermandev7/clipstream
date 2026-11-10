import json

import pytest

from app.shared.sfx.planner import plan_sfx, sound_path


def test_none_mode_is_silent():
    assert plan_sfx("none", 30, 10, True, []) == []


def test_subtle():
    ev = plan_sfx("subtle", 30, 10, True, [])
    assert [(e.sound, e.t) for e in ev] == [("whoosh", 0.0), ("pop", 9.95)]
    assert all(e.gain_db <= -16 for e in ev)


def test_meme_spacing_and_cap():
    words = [(t, "¡no!") for t in range(1, 30)]
    ev = plan_sfx("meme", 30, 15, True, words)
    names = [e.sound for e in ev]
    assert names.count("boom") == 1 and "riser" in names and names[0] == "whoosh"
    pops = [e.t for e in ev if e.sound == "pop"]
    assert len(pops) <= 4
    assert all(b - a >= 3 for a, b in zip(pops, pops[1:]))
    assert all(abs(p - 15) >= 2 for p in pops)


def test_sound_files_exist_and_are_licensed():
    for s in ("whoosh", "pop", "boom", "riser", "ding", "swipe"):
        assert sound_path(s) is not None


def test_import_rejects_bad_license(tmp_path, monkeypatch):
    from app.shared.music import library

    f = tmp_path / "pista.mp3"
    f.write_bytes(b"x")
    with pytest.raises(ValueError):
        library.import_track(f, "Copyright", "t", "a", "")
    with pytest.raises(ValueError):
        library.import_track(f, "CC-BY", "t", "", "")  # CC-BY necesita autor


def test_import_registers_license(tmp_path, monkeypatch):
    from app.shared.music import library
    from app.shared.rights import licenses

    root = tmp_path / "assets"
    (root / "music").mkdir(parents=True)
    reg = root / "licenses.json"
    reg.write_text(json.dumps({"files": {}}))
    monkeypatch.setattr(library, "MUSIC_DIR", root / "music")
    monkeypatch.setattr(library, "REGISTRY", reg)
    monkeypatch.setattr(licenses, "ASSETS", root)
    licenses._registry.cache_clear()
    monkeypatch.setattr(licenses, "_registry", lambda: json.loads(reg.read_text())["files"])
    f = tmp_path / "Mi pista (final).mp3"
    f.write_bytes(b"x")
    t = library.import_track(f, "CC-BY", "Mi pista", "Autora", "https://ejemplo.org")
    assert t["allowed"] and t["license"] == "CC-BY" and t["id"] == "Mi_pista_final.mp3"
    assert library.track_path("../../secreto.mp3") is None
