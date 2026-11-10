from app.shared.metadata.generator import clean_tags, platform_hashtags, strip_decoration
from app.shared.subtitles.translate import Sentence, sentences, to_words, translate_sentences
from app.shared.transcription.types import Word


def test_strip_decoration():
    s = "¿Vudú o bromas? Descubre los mitos. 🧐 #Parque #Amigos"
    assert strip_decoration(s) == "¿Vudú o bromas? Descubre los mitos."
    assert strip_decoration("Mira esto 🔥 !") == "Mira esto!"


def test_platform_hashtags():
    tags = clean_tags(["#Humor", "humor", "viral", "españa", "comedia", "risa", "clip", "monologo", "fun", "x"])
    p = platform_hashtags(tags)
    assert p["tiktok"] == ["humor", "viral", "españa", "comedia", "risa"]
    assert p["youtube"] == ["humor", "viral", "shorts"]
    assert len(p["instagram"]) == 8 and len(p["facebook"]) == 3


def test_sentences_split_on_punctuation_and_pause():
    w = [Word(0, .3, "Hola"), Word(.3, .6, "amigos."), Word(.7, 1, "Esto"), Word(1, 1.3, "es"),
         Word(2.5, 2.8, "otra"), Word(2.8, 3.1, "cosa")]
    ss = sentences(w, 0, 10)
    assert [s.text for s in ss] == ["Hola amigos.", "Esto es", "otra cosa"]


def test_to_words_spans_sentence():
    out = to_words([(Sentence(10.0, 12.0, "x"), "Hello my friends")])
    assert out[0].start == 10.0 and out[-1].end <= 12.0 and [w.text for w in out] == ["Hello", "my", "friends"]


def test_same_language_is_identity():
    s = [Sentence(0, 1, "hola")]
    assert translate_sentences(s, "es", "es") == [(s[0], "hola")]
