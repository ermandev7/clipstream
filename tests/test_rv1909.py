from app.stories import rv1909


def test_codes_and_chapter():
    assert rv1909.code("Jonás 1") == ("JON", 1)
    assert rv1909.code("1 Samuel 17") == ("1SA", 17)
    assert rv1909.code("Libro Inventado 3") is None
    ch = rv1909.chapter("Génesis 1")
    assert len(ch) == 31 and ch[0][1].startswith("EN el principio crió Dios")
    assert rv1909.human_ref("JON 1:2") == "Jonás 1:2"


def test_exact_fragment_is_ok():
    c = rv1909.check_quote("Levántate, y ve á Nínive, ciudad grande", ["Jonás 1"])
    assert c.ok and c.ref == "JON 1:2"


def test_modern_spelling_is_fixed_to_1909_text():
    c = rv1909.check_quote("En el principio creó Dios los cielos y la tierra.", ["Génesis 1"])
    assert not c.ok and c.ref == "GEN 1:1"
    assert c.fixed == "EN el principio crió Dios los cielos y la tierra."


def test_invented_quote_is_not_fixable():
    c = rv1909.check_quote("Y Jonás dijo: el pez era muy simpático y me invitó a cenar en su casa.", ["Jonás 1"])
    assert not c.ok and c.fixed is None


def test_check_text_replaces_quotes():
    text, checks = rv1909.check_text('Dios dijo: «Sea la luz, y fue la luz».', ["Génesis 1"])
    assert len(checks) == 1 and checks[0].ref == "GEN 1:3"
    assert "fué la luz" in text
