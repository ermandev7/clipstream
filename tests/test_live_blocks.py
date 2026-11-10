"""Directo por bloques: aunque no haya picos fuertes, cada bloque da su mejor momento."""
import numpy as np
import pytest

from app.clipper import live
from app.shared.transcription.types import Transcript, Word


def make_session(monkeypatch, every=300, score=60.0):
    s = object.__new__(live.LiveSession)  # sin captura real
    s.sid = 1
    s.every_s = every
    s.block_from = None
    s.clipped = []
    n = 700
    s.audio_db = np.full(n, -30.0)
    s.audio_db[120:135] = -14.0  # un momento algo más animado, nada extremo
    s.audio_db[430:440] = -16.0
    s.events = {"laughter": np.zeros(n)}
    s.transcript = Transcript("es", [Word(t, t + 0.4, "palabra.") for t in np.arange(0, n, 1.5)])
    emitted, logs = [], []
    monkeypatch.setattr(s, "_emit", lambda c: emitted.append(c), raising=False)
    monkeypatch.setattr(live, "log_event", lambda sid, msg, *a: logs.append(msg))
    monkeypatch.setattr(live, "source_changed", lambda sid: None)
    monkeypatch.setattr(live.db, "update", lambda *a, **k: None)
    monkeypatch.setattr(live.db, "get", lambda t, i: {"title": "Directo"})

    def judge(c, tr, title):
        c.llm_score = score
        c.llm_reason = "prueba"

    monkeypatch.setattr(live.llm_judge, "judge", judge)
    return s, emitted, logs


def test_calm_stream_still_gets_one_clip_per_block(monkeypatch):
    s, emitted, logs = make_session(monkeypatch)
    s._blocks(head=340.0, oldest=0.0, chat_rate=None, chat_hits=None)  # bloque 0-300 cerrado (+20 s de margen)
    assert len(emitted) == 1 and 0 <= emitted[0].peak < 300
    assert emitted[0].end - emitted[0].start <= 70  # alrededor del mejor momento, no el tramo entero
    assert s.block_from == 300 and any(m.startswith("Bloque 0:00–5:00") for m in logs)
    # El mismo bloque no se vuelve a analizar.
    s._blocks(head=350.0, oldest=0.0, chat_rate=None, chat_hits=None)
    assert len(emitted) == 1


def test_weak_block_only_logs(monkeypatch):
    s, emitted, logs = make_session(monkeypatch, score=30.0)
    s._blocks(head=340.0, oldest=0.0, chat_rate=None, chat_hits=None)
    assert emitted == [] and any("sin momento destacable" in m for m in logs)


def test_waits_for_full_block_and_margin(monkeypatch):
    s, emitted, _ = make_session(monkeypatch)
    s._blocks(head=310.0, oldest=0.0, chat_rate=None, chat_hits=None)  # falta el margen de 20 s
    assert emitted == [] and s.block_from == 0.0


def test_only_peaks_mode(monkeypatch):
    s, emitted, _ = make_session(monkeypatch, every=0)
    s._blocks(head=700.0, oldest=0.0, chat_rate=None, chat_hits=None)
    assert emitted == []


@pytest.mark.parametrize("every", [180, 300, 600])
def test_block_sizes(monkeypatch, every):
    s, emitted, _ = make_session(monkeypatch, every=every)
    s._blocks(head=every + live.SETTLE_S + 1, oldest=0.0, chat_rate=None, chat_hits=None)
    assert s.block_from == every
