"""Piezas compartidas por el análisis completo y el directo."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from config.settings import settings

from app.clipper.detection import llm_judge
from app.clipper.detection.types import Candidate
from app.core import db
from app.shared.transcription.types import Transcript


def source_dir(source_id: int) -> Path:
    d = settings.storage_dir / "sources" / str(source_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def clip_dir(source_id: int, clip_id: int) -> Path:
    d = source_dir(source_id) / "clips" / str(clip_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_transcript(source_id: int, t: Transcript) -> None:
    (source_dir(source_id) / "transcript.json").write_text(json.dumps(t.to_dict(), ensure_ascii=False), encoding="utf-8")


def load_transcript(source_id: int) -> Transcript | None:
    p = source_dir(source_id) / "transcript.json"
    return Transcript.from_dict(json.loads(p.read_text(encoding="utf-8"))) if p.exists() else None


def save_events(source_id: int, events: dict[str, np.ndarray]) -> None:
    np.savez_compressed(source_dir(source_id) / "events.npz", **events)


def load_events(source_id: int) -> dict[str, np.ndarray] | None:
    p = settings.storage_dir / "sources" / str(source_id) / "events.npz"
    if not p.exists():
        return None
    with np.load(p) as z:
        return {k: z[k] for k in z.files}


def mmss(t: float) -> str:
    t = max(0, int(round(t)))
    return f"{t // 60}:{t % 60:02d}"


def binned(arr: np.ndarray | None, bins: int, how: str = "mean") -> list[float] | None:
    if arr is None or len(arr) == 0:
        return None
    chunks = np.array_split(np.asarray(arr, dtype=float), min(bins, len(arr)))
    f = np.max if how == "max" else np.mean
    return [round(float(f(c)), 3) for c in chunks]


def save_signals(source_id: int, duration: float, curve: np.ndarray, audio_db: np.ndarray | None,
                 chat_rate: np.ndarray | None, bins: int = 240, offset: float = 0.0,
                 laughter: np.ndarray | None = None, viewers: np.ndarray | None = None) -> dict:
    act = np.clip(curve, 0, None)
    peak = float(act.max()) if len(act) else 0.0
    data = {
        "duration": round(float(duration), 2),
        "offset": round(offset, 2),
        "bins": bins,
        "activity": binned(act / peak if peak > 0 else act, bins, "max"),
        "audio_db": binned(audio_db, bins),
        "chat_rate": binned(chat_rate, bins) if chat_rate is not None and float(np.sum(chat_rate)) > 0 else None,
        "laughter": binned(laughter, bins, "max") if laughter is not None and len(laughter) else None,
        "viewers": binned(viewers, bins) if viewers is not None and len(viewers) and float(np.max(viewers)) > 0 else None,
    }
    (source_dir(source_id) / "signals.json").write_text(json.dumps(data), encoding="utf-8")
    return data


def load_signals(source_id: int) -> dict | None:
    p = settings.storage_dir / "sources" / str(source_id) / "signals.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def rights_warnings(source: dict, start: float | None = None, end: float | None = None) -> list[dict]:
    """Política 'warn' del clipper: avisos, nunca bloqueo ni modificación del clip."""
    from app.clipper.signals.audio_events import ranges

    warns: list[dict] = []
    events = load_events(source["id"]) if start is not None else None
    wav = settings.storage_dir / "sources" / str(source["id"]) / "audio.wav"
    if events is None and start is not None and wav.exists():
        # Fuente analizada antes de que existiera la detección de música: se calcula ahora (segundos).
        from app.clipper.signals.audio_events import classify_wav

        events = classify_wav(wav)
        save_events(source["id"], events)
    if events is not None and "music" in events:
        for a, b in ranges(events["music"], start, end):
            warns.append({"kind": "music", "message": f"Música detectada {mmss(a - start)}–{mmss(b - start)}",
                          "start": round(a - start, 1), "end": round(b - start, 1)})
    if source["kind"] in ("live", "vod"):
        perm = db.channel_permission(source.get("platform") or "youtube", source.get("channel") or "")
        if perm not in ("yes", "program"):
            warns.append({"kind": "permission", "message": "Permiso del streamer no marcado"})
    return warns


def store_clip(source: dict, c: Candidate, transcript: Transcript | None, files: dict | None = None,
               meta: dict | None = None) -> int:
    title = (meta or {}).get("title_es") or c.title or llm_judge.fallback_title(c, transcript)
    return db.insert(
        "clips", source_id=source["id"], start=c.start, end=c.end, peak=c.peak, score=c.score,
        signal_score=c.signal_score, llm_score=c.llm_score, reason=c.llm_reason, title=title, hook=c.hook,
        hashtags=c.hashtags, signals=c.signals, signals_label=c.signals_label(),
        warnings=rights_warnings(source, c.start, c.end), status="pending", files=files or {},
        meta=meta or {},
    )


def clip_metadata(c: Candidate, transcript: Transcript | None, source_title: str) -> dict:
    """Título EN, descripciones y hashtags por plataforma (una llamada al LLM por clip)."""
    from app.shared.metadata.generator import generate

    text = transcript.text_between(c.start, c.end) if transcript else ""
    lang = transcript.language if transcript else "es"
    return generate(text, source_title, c.title or llm_judge.fallback_title(c, transcript), c.duration, lang, c.hashtags)
