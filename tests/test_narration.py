import wave

import pytest

from app.stories import narration, voices


def test_align_exact_match_keeps_script_text():
    heard = [{"start": 0.0, "end": 0.4, "text": "Jonás"}, {"start": 0.4, "end": 0.8, "text": "huyó"},
             {"start": 0.9, "end": 1.3, "text": "lejos."}]
    out = narration.align_words("Jonás huyó lejos.", heard, 1.5)
    assert [w["text"] for w in out] == ["Jonás", "huyó", "lejos."]
    assert out[1]["start"] == 0.4 and out[2]["end"] == 1.3


def test_align_interpolates_misheard_words():
    # Whisper oyó «Tarsis» como «Tarsi» y no oyó «y»: el texto sigue siendo el del guion, sin huecos.
    heard = [{"start": 0.0, "end": 0.5, "text": "Navegó"}, {"start": 0.5, "end": 0.8, "text": "hacia"},
             {"start": 0.8, "end": 1.4, "text": "Tarsi"}, {"start": 1.6, "end": 2.0, "text": "huyó"}]
    out = narration.align_words("Navegó hacia Tarsis y huyó", heard, 2.2)
    assert [w["text"] for w in out] == ["Navegó", "hacia", "Tarsis", "y", "huyó"]
    assert 0.8 <= out[2]["start"] < out[3]["start"] < out[4]["start"] == 1.6
    assert all(b["start"] >= a["end"] for a, b in zip(out, out[1:]))


def test_align_with_nothing_heard_spreads_over_duration():
    out = narration.align_words("uno dos tres", [], 3.0)
    assert len(out) == 3 and out[0]["start"] == 0 and out[-1]["end"] == pytest.approx(3.0, abs=0.01)


@pytest.mark.skipif(not voices.available(), reason="modelo Kokoro no descargado")
def test_kokoro_synthesizes_wav(tmp_path):
    out = tmp_path / "a.wav"
    dur = voices.synthesize("Había una vez un león.", "es", None, out)
    with wave.open(str(out)) as w:
        assert w.getframerate() == 24000 and w.getnchannels() == 1
        assert abs(w.getnframes() / 24000 - dur) < 0.01
    assert 0.8 < dur < 5
