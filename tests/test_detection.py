import numpy as np

from app.clipper.detection import boundaries, dedup, scorer
from app.clipper.detection.types import Candidate
from app.shared.transcription.types import Transcript, Word


def make_words(sentences: list[tuple[float, str]], wps: float = 3.0) -> Transcript:
    words: list[Word] = []
    for start, sentence in sentences:
        t = start
        for tok in sentence.split():
            words.append(Word(t, t + 1 / wps * 0.9, tok))
            t += 1 / wps
    return Transcript("es", words)


# ---- scorer -------------------------------------------------------------

def test_scorer_finds_audio_and_chat_peaks():
    rng = np.random.default_rng(0)
    n = 900
    audio = -30 + rng.normal(0, 1.0, n)
    audio[300:310] += 15
    audio[700:705] += 12
    chat = np.abs(rng.normal(1.0, 0.3, n))
    chat[306:320] += 8  # el chat reacciona con retraso
    sig = scorer.SignalSet(duration_s=n, audio_db=audio, chat_rate=chat)
    cands = scorer.score_signals(sig, max_candidates=5)
    peaks = sorted(c.peak for c in cands[:2])
    assert abs(peaks[0] - 305) <= 8
    assert abs(peaks[1] - 702) <= 8
    best = max(cands, key=lambda c: c.signal_score)
    assert best.signals.get("chat_x", 0) > 3
    assert "Chat" in best.signals_label()


def test_scorer_flat_signal_gives_no_strong_candidates():
    n = 600
    sig = scorer.SignalSet(duration_s=n, audio_db=np.full(n, -25.0))
    cands = scorer.score_signals(sig, max_candidates=5)
    assert all(c.signal_score < 50 for c in cands)


def test_find_peaks_respects_min_distance():
    curve = np.zeros(200)
    curve[50] = 3
    curve[60] = 2
    curve[150] = 2.5
    assert scorer.find_peaks(curve, 30, 0.5) == [50, 150]


def test_target_clip_count_bounds():
    assert scorer.target_clip_count(60) == 3
    assert scorer.target_clip_count(15 * 60) == 5
    assert scorer.target_clip_count(10 * 3600) == 20


def test_keywords_detected_in_transcript():
    t = make_words([(10, "esto es normal"), (40, "no puede ser qué locura")])
    _, kw = scorer.transcript_series(t, 60)
    assert kw[40:45].max() > 0
    assert kw[10:15].max() == 0


# ---- boundaries ---------------------------------------------------------

def test_boundaries_snap_to_sentences():
    t = make_words([(0, "Hola a todos."), (5, "Hoy vamos a probar algo nuevo."), (12, "Y mirad lo que pasa aquí."),
                    (20, "No puede ser."), (25, "Seguimos con otra cosa.")])
    c = Candidate(start=6.0, end=21.0, peak=20.0, signal_score=80)
    boundaries.adjust(c, t, media_duration=60)
    assert abs(c.start - (5 - boundaries.LEAD_IN_S)) < 0.05  # empieza en la frase de las 5 s
    first_word_end = t.words[[w.text for w in t.words].index("ser.")].end
    assert abs(c.end - (first_word_end + boundaries.TAIL_S)) < 0.05


def test_boundaries_enforce_min_duration():
    c = Candidate(start=10.0, end=11.0, peak=10.5, signal_score=50)
    boundaries.adjust(c, None, media_duration=100)
    assert c.duration >= 3.0


def test_boundaries_enforce_max_duration():
    c = Candidate(start=0.0, end=500.0, peak=300.0, signal_score=50)
    boundaries.adjust(c, None, media_duration=600)
    assert c.duration <= 180.0
    assert c.start <= 300.0 <= c.end


def test_boundaries_clamp_to_media():
    c = Candidate(start=55.0, end=70.0, peak=58.0, signal_score=50)
    boundaries.adjust(c, None, media_duration=60)
    assert c.end <= 60 and c.duration >= 3


# ---- dedup --------------------------------------------------------------

def test_dedup_merges_overlapping():
    a = Candidate(10, 40, 30, 90, {"audio": 0.9})
    b = Candidate(30, 60, 50, 70, {"chat": 0.8})
    c = Candidate(200, 230, 220, 60)
    out = dedup.merge([a, b, c])
    assert len(out) == 2
    assert out[0].start == 10 and out[0].end == 60
    assert out[0].signals == {"audio": 0.9, "chat": 0.8}


def test_dedup_drops_when_union_too_long():
    a = Candidate(0, 170, 100, 90)
    b = Candidate(100, 290, 200, 50)
    out = dedup.merge([a, b])
    assert len(out) == 1 and out[0].end == 170


def test_dedup_rechecks_after_growing():
    # c (alta puntuación) se acepta primero; a crece al fusionarse con b y entonces solapa con c.
    a = Candidate(72, 100, 90, 90)
    c = Candidate(133, 180, 150, 80)
    b = Candidate(80, 164, 120, 70)
    out = dedup.merge([a, b, c])
    assert len(out) == 1 and out[0].start == 72 and out[0].end == 180


def test_select_diverse_never_fills_with_overlaps():
    a = Candidate(70, 165, 100, 90)
    b = Candidate(133, 180, 150, 60)
    assert len(dedup.select_diverse([a, b], 2)) == 1


def test_select_diverse_skips_adjacent_clips():
    a = Candidate(900, 930, 915, 90)
    b = Candidate(931, 960, 945, 88)  # pegado a "a"
    c = Candidate(100, 130, 115, 60)
    d = Candidate(400, 430, 415, 50)
    out = dedup.select_diverse([a, b, c, d], 3)
    assert [x.start for x in out] == [100, 400, 900]
    # Si no hay suficientes separados, rellena con los pegados.
    assert len(dedup.select_diverse([a, b], 2)) == 2


def test_normalize_title():
    from app.clipper.detection.llm_judge import normalize_title

    assert normalize_title("LEVántATE CON CARIÑO") == "Levántate con cariño"
    assert normalize_title("EXTIENDA BRAZOS Y AVANCE") == "Extienda brazos y avance"
    assert normalize_title("Cómo motivar a tu equipo en Madrid") == "Cómo motivar a tu equipo en Madrid"
    assert normalize_title('"Hola."') == "Hola"
    assert normalize_title("Cómo Desarrollar Tu Talento") == "Cómo desarrollar tu talento"
    assert normalize_title("¡dale un masaje bien dado!") == "¡Dale un masaje bien dado!"


def test_combined_score_uses_llm():
    c = Candidate(0, 10, 5, signal_score=50, llm_score=100)
    assert c.score == 80.0


def test_laughter_event_creates_candidate():
    n = 600
    audio = np.full(n, -25.0)
    lau = np.zeros(n)
    lau[300:306] = 0.9
    sig = scorer.SignalSet(duration_s=n, audio_db=audio, events={"laughter": lau})
    cands = scorer.score_signals(sig, max_candidates=3)
    best = max(cands, key=lambda c: c.signal_score)
    assert abs(best.peak - 303) <= 5
    assert "risa" in best.signals_label()


def test_donation_and_viewer_rise_create_candidates():
    n = 900
    audio = np.full(n, -25.0)
    don = np.zeros(n)
    don[400] = 2
    viewers = np.full(n, 1000.0)
    viewers[700:] = 1600.0
    sig = scorer.SignalSet(duration_s=n, audio_db=audio, events={"donation": don, "viewers": viewers})
    cands = scorer.score_signals(sig, max_candidates=4)
    peaks = sorted(c.peak for c in cands[:2])
    assert any(abs(p - (400 - scorer.CHAT_DELAY_S)) <= 8 for p in peaks)
    labels = " ".join(c.signals_label() for c in cands)
    assert "donaciones" in labels


def test_no_speech_candidate_is_short():
    t = make_words([(5, "Hola a todos y bienvenidos.")])  # habla solo al principio
    c = Candidate(start=100.0, end=280.0, peak=200.0, signal_score=60)
    boundaries.adjust(c, t, media_duration=600)
    assert c.start == 200 - boundaries.NO_SPEECH_PRE and c.end == 200 + boundaries.NO_SPEECH_POST


def test_music_only_is_penalized_not_removed():
    music = np.zeros(300)
    music[100:200] = 0.95
    a = Candidate(110, 140, 120, 80.0)
    b = Candidate(10, 40, 20, 60.0)
    t = make_words([(12, "Esto es una frase con varias palabras.")])
    out = scorer.penalize_music_only([a, b], {"music": music}, t)
    assert len(out) == 2 and a.signal_score == 40.0 and b.signal_score == 60.0


def test_drop_weak_keeps_minimum():
    cs = [Candidate(i * 100, i * 100 + 30, i * 100 + 10, s) for i, s in enumerate([80, 60, 40, 14, 12])]
    assert [c.signal_score for c in dedup.drop_weak(cs)] == [80, 60, 40]
    few = [Candidate(0, 30, 10, 50), Candidate(100, 130, 110, 10), Candidate(200, 230, 210, 9), Candidate(300, 330, 310, 5)]
    assert [c.signal_score for c in dedup.drop_weak(few)] == [50, 10, 9]


def test_fallback_title_without_speech():
    from app.clipper.detection.llm_judge import fallback_title

    assert fallback_title(Candidate(30, 50, 95, 10), None) == "Momento 1:35"
