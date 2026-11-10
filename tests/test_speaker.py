from app.clipper.reframe.detector import Box, FrameDet
from app.clipper.reframe.planner import plan_layout
from app.clipper.reframe.speaker import active_speakers, mode_filter, speech_mask
from app.clipper.reframe.tracker import build_tracks

W, H = 1920, 1080


def two_people(talk_a, talk_b, n=80):
    """A a la izquierda, B a la derecha; talk_x(i) = actividad de boca en el fotograma i."""
    return [FrameDet(i / 4, [Box(0.12, 0.3, 0.08, 0.14, 0.9, "face", talk_a(i)),
                             Box(0.80, 0.3, 0.08, 0.14, 0.9, "face", talk_b(i))]) for i in range(n)]


def test_turns_choose_speaker_layout():
    fr = two_people(lambda i: 0.08 if i < 40 else 0.0, lambda i: 0.0 if i < 40 else 0.08)
    p = plan_layout(fr, W, H)
    assert p.layout == "speaker"
    assert p.path[10] < 0.4 and p.path[-1] > 0.6  # empieza en A y acaba en B
    assert sorted(x.speaking_s for x in p.people) == [10.0, 10.0]


def test_simultaneous_talk_splits():
    fr = two_people(lambda i: 0.08, lambda i: 0.07)
    assert plan_layout(fr, W, H).layout == "split2"


def test_no_talk_data_falls_back_to_split():
    fr = two_people(lambda i: 0.0, lambda i: 0.0)
    assert plan_layout(fr, W, H).layout == "split2"


def test_force_speaker_without_data_follows():
    fr = two_people(lambda i: 0.0, lambda i: 0.0)
    assert plan_layout(fr, W, H, force="speaker").layout == "follow"


def test_mode_filter_removes_flicker():
    labels = [0, 0, 0, 1, 0, 0, 0, 1, 1, 1, 1, 1]
    assert mode_filter(labels, 2)[3] == 0 and mode_filter(labels, 2)[-1] == 1


def test_speech_mask():
    assert speech_mask([(1.0, 1.5)], [0.5, 1.2, 3.0]).tolist() == [0.0, 1.0, 0.0]


def test_active_speakers_overlap():
    fr = two_people(lambda i: 0.08, lambda i: 0.075, n=20)
    tracks = build_tracks(fr)
    r = active_speakers(tracks, 20, 4.0)
    assert r["detected"] and r["overlap"] > 0.9
