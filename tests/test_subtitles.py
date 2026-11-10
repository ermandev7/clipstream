from app.shared.subtitles.ass import ass_color, ass_time, build_ass, build_srt, group_words
from app.shared.transcription.types import Word


def words():
    return [Word(10.0, 10.3, "Hola"), Word(10.35, 10.7, "a"), Word(10.75, 11.1, "todos."),
            Word(12.0, 12.4, "Esto"), Word(12.45, 12.8, "es"), Word(12.85, 13.5, "increíble")]


def test_ass_color_is_bgr():
    assert ass_color("#F5D547") == "&H0047D5F5"
    assert ass_color("#FFFFFF", 0x80) == "&H80FFFFFF"


def test_ass_time_format():
    assert ass_time(0) == "0:00:00.00"
    assert ass_time(61.257) == "0:01:01.26"
    assert ass_time(3725.5) == "1:02:05.50"


def test_group_words_splits_on_sentence_end_and_pause():
    groups = group_words(words(), max_words=3, max_chars=30)
    assert [[w.text for w in g] for g in groups] == [["Hola", "a", "todos."], ["Esto", "es", "increíble"]]


def test_build_ass_one_event_per_word_relative_times():
    ass = build_ass(words(), clip_start=10.0, clip_end=14.0, hook="MIRA ESTO")
    events = [line for line in ass.splitlines() if line.startswith("Dialogue: 0")]
    assert len(events) == 6
    assert events[0].startswith("Dialogue: 0,0:00:00.00,")
    assert "{\\c&H0047D5F5}HOLA{\\c&H00FFFFFF} A TODOS." in events[0]
    assert "Hook,,0,0,0,,MIRA ESTO" in ass
    # La palabra activa se mantiene hasta que empieza la siguiente: sin parpadeo.
    assert events[0].split(",")[2] == "0:00:00.35"


def test_build_ass_excludes_words_outside_clip():
    ass = build_ass(words(), clip_start=11.5, clip_end=14.0)
    assert "HOLA" not in ass and "INCREÍBLE" in ass


def test_overlapping_whisper_words_never_show_two_lines():
    ws = [Word(5.0, 5.9, "no"), Word(5.2, 5.5, "dejéis"), Word(5.5, 5.94, "muchas."), Word(5.69, 5.89, "cosas")]
    ass = build_ass(ws, 0, 10)
    events = [line.split(",")[1:3] for line in ass.splitlines() if line.startswith("Dialogue: 0")]
    secs = [(sum(float(x) * m for x, m in zip(a.split(":"), (3600, 60, 1))),
             sum(float(x) * m for x, m in zip(b.split(":"), (3600, 60, 1)))) for a, b in events]
    for (s1, e1), (s2, _e2) in zip(secs, secs[1:]):
        assert e1 <= s2 + 1e-9


def test_braces_are_escaped():
    ass = build_ass([Word(0, 1, "{\\b1}hack")], 0, 2)
    assert "{\\b1}" not in ass.split("[Events]")[1].replace("{\\c", "")


def test_srt():
    srt = build_srt(words(), 10.0, 14.0)
    assert srt.startswith("1\n00:00:00,000 --> 00:00:01,100\nHola a todos.")


def test_styles():
    from app.shared.subtitles.ass import STYLES, style_by_name

    assert style_by_name("nope") is STYLES["clasico"]
    imp = build_ass(words(), 10.0, 14.0, style=STYLES["impacto"])
    assert r"\fscx112" in imp and "Montserrat Black" in imp
    limpio = build_ass(words(), 10.0, 14.0, style=STYLES["limpio"])
    assert ",3,14.0,0.0,2," in limpio  # caja opaca
    assert "Hola" in limpio and "HOLA" not in limpio  # sin mayúsculas forzadas


def test_bilingual_secondary_line():
    ass = build_ass(words(), 10.0, 14.0, secondary=[(10.0, 11.1, "Hello everyone."), (12.0, 13.5, "This is amazing")])
    sec = [line for line in ass.splitlines() if ",Sub2," in line and line.startswith("Dialogue")]
    assert len(sec) == 2 and sec[0].endswith("Hello everyone.")
    assert sec[0].split(",")[1] == "0:00:00.00"
    assert "Style: Sub2," in ass
