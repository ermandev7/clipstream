from app.shared.rights.licenses import ASSETS, is_allowed


def test_registered_fonts_are_allowed():
    for f in (ASSETS / "fonts").glob("*.ttf"):
        assert is_allowed(f), f"{f.name} no tiene licencia permitida en assets/licenses.json"


def test_unregistered_file_is_not_allowed(tmp_path):
    p = ASSETS / "sfx" / "no_registrado.wav"
    assert not is_allowed(p)
