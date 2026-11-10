import numpy as np

from app.clipper.signals.audio_events import ranges, to_series


def test_to_series_takes_max_per_second():
    w = [(0.0, 0.975, {"music": 0.2, "laughter": 0.0, "applause": 0, "scream": 0, "speech": 0}),
         (0.975, 1.95, {"music": 0.8, "laughter": 0.6, "applause": 0, "scream": 0, "speech": 0})]
    s = to_series(w, 3)
    assert s["music"].tolist() == [0.8, 0.8, 0.0]
    assert s["laughter"][1] == 0.6


def test_ranges_merge_gaps_and_drop_short():
    s = np.zeros(60)
    s[10:14] = 0.9
    s[15:20] = 0.9  # hueco de 1 s: se une
    s[40:42] = 0.9  # 2 s: demasiado corto
    assert ranges(s, 0, 60) == [(10, 20)]


def test_ranges_clipped_to_window():
    s = np.zeros(60)
    s[5:30] = 0.9
    assert ranges(s, 12.5, 25.0) == [(12.5, 25.0)]
