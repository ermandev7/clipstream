"""Montaje: piezas puras y un render real pequeño con libx264 (sin GPU)."""
import wave

import numpy as np
import pytest

from app.shared.render.ffmpeg import probe
from app.stories import montage, report


def test_timeline_and_alternating_motion():
    starts, total = montage.timeline([5.0, 4.5, 6.2])
    assert starts == [0.0, 5.0, 9.5] and total == 15.7
    a, b = montage.motion_for(0), montage.motion_for(1)
    assert a.z1 > a.z0 and b.z1 < b.z0  # acercar y luego alejar


def test_kenburns_never_shows_borders(tmp_path):
    import cv2

    png = tmp_path / "a.png"
    cv2.imwrite(str(png), np.full((1344, 768, 3), 200, np.uint8))
    for idx in range(6):
        shot = montage.Shot(png, montage.motion_for(idx), 5.0)
        for t in (0.0, 2.5, 5.0):
            f = shot.frame(t)
            assert f.shape == (1920, 1080, 3) and f.min() >= 195  # imagen lisa: un borde reflejado o negro se notaría


def _wav(path, seconds, rate=24000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.sin(np.arange(int(seconds * rate)) / 10) * 8000).astype(np.int16).tobytes())
    return path


def test_voice_track_places_each_scene(tmp_path):
    out = montage.voice_track([_wav(tmp_path / "1.wav", 1.0), None, _wav(tmp_path / "3.wav", 0.5)], [0.0, 1.5, 3.0], 4.0,
                              tmp_path / "v.wav")
    with wave.open(str(out)) as w:
        pcm = np.frombuffer(w.readframes(w.getnframes()), np.int16)
    sr = 24000
    assert abs(len(pcm) / sr - 4.0) < 0.01
    assert np.abs(pcm[int(0.5 * sr)]) > 0 or np.abs(pcm[int(0.5 * sr) + 1]) > 0
    assert not pcm[int(1.6 * sr): int(2.9 * sr)].any()  # escena sin voz: silencio
    assert np.abs(pcm[int(3.2 * sr): int(3.4 * sr)]).max() > 1000


def test_subtitles_have_title_label_and_offset_words():
    ass = montage.build_subtitles([[{"start": 0.1, "end": 0.5, "text": "Jonás"}], [{"start": 0.2, "end": 0.6, "text": "huyó"}]],
                                  [0.0, 5.0], 8.0, "Episodio 1 · La huida", "es")
    assert "Style: Label" in ass and "Contenido generado con IA" in ass and "Episodio 1" in ass
    line = next(x for x in ass.splitlines() if x.startswith("Dialogue: 0") and "huyó" in x.lower())
    assert "0:00:05.20" in line


def test_report_text_groups_checks():
    r = {"generado": "x", "serie": "S", "episodio": 1, "titulo": "T",
         "historia": {"titulo": "Jonás", "fuente": "Jonás 1-4", "base_legal": "RV1909", "evitar": []},
         "texto": [{"revision": "citas", "ok": True, "detalle": "Sin citas", "donde": "Escena 1"},
                   {"revision": "citas", "ok": False, "detalle": "Cita inventada", "donde": "Escena 2"}],
         "imagenes": [{"escena": 1, "ok": True, "parecido_max": "x", "probabilidad": 0.1}],
         "musica": None, "efectos": None, "modelos": [], "ia": "IA", "videos": {}, "bloqueos": [2]}
    txt = report.to_text(r)
    assert "[BLOQUEO] Citas textuales: 1/2" in txt and "Escena 2: Cita inventada" in txt and "no debe publicarse" in txt


def test_real_small_render(tmp_path):
    import cv2

    pngs = []
    for i, color in enumerate(((40, 80, 160), (160, 80, 40))):
        p = tmp_path / f"{i}.png"
        cv2.imwrite(str(p), np.full((1344, 768, 3), color, np.uint8))
        pngs.append(p)
    base = montage.render_base(pngs, [1.0, 1.0], tmp_path / "base.mp4", nvenc=False)
    voice = montage.voice_track([_wav(tmp_path / "a.wav", 0.6), _wav(tmp_path / "b.wav", 0.6)], [0.0, 1.0], 2.0,
                                tmp_path / "v.wav")
    ass = montage.build_subtitles([[{"start": 0, "end": 0.5, "text": "hola"}], []], [0.0, 1.0], 2.0, "Ep 1", "es")
    out = montage.render_language(base, voice, ass, 2.0, [0.0, 1.0], None, None, tmp_path / "ep.mp4", nvenc=False)
    info = probe(out)
    assert info.width == 1080 and info.height == 1920 and info.has_audio and abs(info.duration - 2.0) < 0.15
