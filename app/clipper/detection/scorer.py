"""Combina señales baratas (audio, chat, habla, palabras clave) en candidatos.

Todas las señales son series por segundo con la misma longitud. Cada una se
normaliza con z-score robusto (mediana / MAD) para que un streamer que siempre
grita o un chat siempre rápido no dispare candidatos todo el rato.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import numpy as np

from app.clipper.detection.types import Candidate
from app.shared.transcription.types import Transcript

WEIGHTS = {"audio": 0.30, "chat": 0.35, "chat_hits": 0.15, "keywords": 0.10, "speech": 0.10,
           "laughter": 0.20, "applause": 0.10, "scream": 0.10, "donation": 0.15, "sub": 0.10, "viewers": 0.10}
# Eventos de audio (YAMNet): probabilidades escasas; se escalan en vez de normalizarlas.
EVENT_SCALE = {"laughter": 4.0, "applause": 2.0, "scream": 2.5, "donation": 3.0, "sub": 2.0}
# Donaciones y suscripciones son recuentos (0, 1, 2...), no probabilidades: se limitan antes de escalar.
COUNT_EVENTS = {"donation", "sub"}

# El chat reacciona unos segundos después del momento.
CHAT_DELAY_S = 6

KEYWORD_PATTERNS = [
    r"\bno puede ser\b", r"\bqu[eé] (?:fuerte|locura|asco|miedo)\b", r"\bdios m[ií]o\b", r"\bmadre m[ií]a\b",
    r"\bincre[ií]ble\b", r"\bbrutal\b", r"\bjaja+\b", r"\bjeje+\b", r"\bwtf\b", r"\bnoo+\b", r"\bs[ií]+[!]+",
    r"\bomg\b", r"\boh my god\b", r"\bno way\b", r"\bwhat the\b", r"\binsane\b", r"\blet'?s go+\b",
    r"\bcuidado\b", r"\bmira\b", r"\bsecreto\b", r"\bnadie\b", r"\bnunca\b", r"\bverdad\b",
]
_KEYWORD_RE = re.compile("|".join(KEYWORD_PATTERNS), re.IGNORECASE)


@dataclass
class SignalSet:
    duration_s: int
    audio_db: np.ndarray | None = None  # dBFS por segundo
    chat_rate: np.ndarray | None = None  # mensajes por segundo
    chat_hits: np.ndarray | None = None  # "jaja", "clip", emotes... por segundo
    transcript: Transcript | None = None
    extra: dict[str, np.ndarray] = field(default_factory=dict)
    events: dict[str, np.ndarray] | None = None  # series de audio_events (risas, aplausos...)

    @property
    def has_chat(self) -> bool:
        return self.chat_rate is not None and float(np.sum(self.chat_rate)) > 0


def robust_z(x: np.ndarray, baseline: np.ndarray | None = None) -> np.ndarray:
    ref = x if baseline is None else baseline
    med = float(np.median(ref))
    mad = float(np.median(np.abs(ref - med))) * 1.4826
    if mad < 1e-6:
        std = float(np.std(ref))
        mad = std if std > 1e-6 else 1.0
    return (x - med) / mad


def smooth(x: np.ndarray, window: int) -> np.ndarray:
    if window <= 1 or len(x) == 0:
        return x.astype(float)
    # Relleno con el valor del borde: con ceros aparecerían picos falsos al inicio y al final.
    left = window // 2
    right = window - 1 - left
    padded = np.pad(x.astype(float), (left, right), mode="edge")
    return np.convolve(padded, np.ones(window) / window, mode="valid")


def shift_earlier(x: np.ndarray, seconds: int) -> np.ndarray:
    if seconds <= 0:
        return x
    out = np.zeros_like(x, dtype=float)
    out[:-seconds] = x[seconds:]
    return out


def transcript_series(t: Transcript, n: int) -> tuple[np.ndarray, np.ndarray]:
    """Palabras por segundo y aciertos de palabras clave por segundo."""
    speech = np.zeros(n)
    kw = np.zeros(n)
    for w in t.words:
        i = int(w.start)
        if 0 <= i < n:
            speech[i] += 1
            if _KEYWORD_RE.search(w.text):
                kw[i] += 1
    # Frases de varias palabras: buscar sobre ventanas de texto.
    for i in range(0, n, 5):
        text = t.text_between(i, i + 5)
        hits = len(_KEYWORD_RE.findall(text))
        if hits:
            kw[i : i + 5] = np.maximum(kw[i : i + 5], hits / 5)
    return speech, kw


def combined_curve(sig: SignalSet, baseline: SignalSet | None = None) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    n = sig.duration_s
    parts: dict[str, np.ndarray] = {}

    if sig.audio_db is not None:
        a = smooth(sig.audio_db[:n], 3)
        base = smooth(baseline.audio_db, 3) if baseline is not None and baseline.audio_db is not None else None
        parts["audio"] = robust_z(a, base)

    if sig.has_chat:
        c = shift_earlier(smooth(sig.chat_rate[:n], 5), CHAT_DELAY_S)
        base = smooth(baseline.chat_rate, 5) if baseline is not None and baseline.chat_rate is not None else None
        parts["chat"] = robust_z(c, base)
        if sig.chat_hits is not None:
            h = shift_earlier(smooth(sig.chat_hits[:n], 5), CHAT_DELAY_S)
            parts["chat_hits"] = robust_z(h, None)

    if sig.transcript is not None and sig.transcript.words:
        speech, kw = transcript_series(sig.transcript, n)
        parts["speech"] = robust_z(smooth(speech, 5))
        parts["keywords"] = smooth(kw, 5) * 3  # ya es escasa; escalar en vez de normalizar

    for name, arr in sig.extra.items():
        parts[name] = robust_z(smooth(arr[:n], 3))

    for name, scale in EVENT_SCALE.items():
        ev = (sig.events or {}).get(name)
        if ev is not None and len(ev) and float(np.max(ev[:n])) >= 0.2:
            arr = np.pad(ev[:n], (0, max(0, n - len(ev))))
            if name in COUNT_EVENTS:
                # El momento suele ser justo antes: el chat tarda en enviar el Super Chat. Un evento suelto
                # cuenta entero durante unos segundos (máximo en ventana, no media: si no, se diluye).
                arr = shift_earlier(np.minimum(arr, 2.0) / 2.0, CHAT_DELAY_S)
                padded = np.pad(arr, 4, mode="edge")
                parts[name] = np.lib.stride_tricks.sliding_window_view(padded, 9).max(axis=1) * scale
            else:
                parts[name] = smooth(arr, 5) * scale

    viewers = (sig.events or {}).get("viewers")
    if viewers is not None and len(viewers) > 30 and float(np.max(viewers)) > 0:
        # Subidas de espectadores (gente que llega por un momento compartido), no el nivel absoluto.
        v = np.pad(viewers[:n], (0, max(0, n - len(viewers))), mode="edge").astype(float)
        rise = np.clip(np.diff(smooth(v, 15), prepend=v[0]), 0, None)
        if float(rise.max()) > 0:
            parts["viewers"] = robust_z(smooth(rise, 10))

    if not parts:
        return np.zeros(n), parts

    total_w = sum(WEIGHTS.get(k, 0.1) for k in parts)
    curve = np.zeros(n)
    for k, arr in parts.items():
        arr = np.pad(arr, (0, max(0, n - len(arr))))[:n]
        parts[k] = arr
        curve += WEIGHTS.get(k, 0.1) / total_w * np.clip(arr, -1, 6)
    return smooth(curve, 5), parts


def to_score(z: float) -> float:
    """z combinado -> 0-100."""
    return round(100.0 / (1.0 + math.exp(-1.3 * (z - 1.2))), 1)


def find_peaks(curve: np.ndarray, min_distance: int, min_height: float) -> list[int]:
    """Máximos locales ordenados por altura, separados al menos `min_distance` s."""
    if len(curve) < 3:
        return []
    idx = [i for i in range(1, len(curve) - 1) if curve[i] >= curve[i - 1] and curve[i] >= curve[i + 1] and curve[i] >= min_height]
    idx.sort(key=lambda i: curve[i], reverse=True)
    chosen: list[int] = []
    for i in idx:
        if all(abs(i - j) >= min_distance for j in chosen):
            chosen.append(i)
    return chosen


def candidates_from_curve(
    curve: np.ndarray,
    parts: dict[str, np.ndarray],
    *,
    max_candidates: int,
    min_height: float = 0.3,
    min_distance: int = 30,
    pre_roll: float = 20.0,
    post_roll: float = 10.0,
    chat_rate: np.ndarray | None = None,
    offset: float = 0.0,
) -> list[Candidate]:
    n = len(curve)
    peaks = find_peaks(curve, min_distance, min_height)[:max_candidates]
    chat_median = float(np.median(chat_rate)) if chat_rate is not None and len(chat_rate) else 0.0
    out: list[Candidate] = []
    for p in peaks:
        # Extender mientras la actividad siga alta (más de la mitad del pico).
        half = curve[p] * 0.5
        lo = p
        while lo > 0 and curve[lo - 1] > half and p - lo < 90:
            lo -= 1
        hi = p
        while hi < n - 1 and curve[hi + 1] > half and hi - p < 60:
            hi += 1
        start = max(0.0, min(lo, p - pre_roll))
        end = min(float(n), max(hi, p + post_roll))
        sig = {k: float(np.clip(v[p] / 3.0, 0, 1)) for k, v in parts.items()}
        if chat_rate is not None and chat_median > 0:
            win = chat_rate[max(0, p - 3) : p + CHAT_DELAY_S + 5]
            sig["chat_x"] = float(np.max(win) / chat_median) if len(win) else 0.0
        out.append(Candidate(start + offset, end + offset, p + offset, to_score(float(curve[p])), sig))
    return out


def score_signals(sig: SignalSet, max_candidates: int, baseline: SignalSet | None = None, **kw) -> list[Candidate]:
    curve, parts = combined_curve(sig, baseline)
    return candidates_from_curve(curve, parts, max_candidates=max_candidates, chat_rate=sig.chat_rate, **kw)


def target_clip_count(duration_s: float) -> int:
    """Cuántos clips buscar en un vídeo completo: ~1 cada 3 min, mínimo 3, máximo 20."""
    return int(min(20, max(3, round(duration_s / 180))))


def penalize_music_only(cands: list[Candidate], events: dict[str, np.ndarray] | None,
                        transcript: Transcript | None, threshold: float = 0.6) -> list[Candidate]:
    """Tramos de música sin voz: contenido pobre para un clip y riesgo de derechos. Bajan en el
    ranking (puntuación a la mitad) pero no se descartan: la política del clipper es avisar."""
    music = (events or {}).get("music")
    if music is None or not len(music):
        return cands
    for c in cands:
        seg = music[int(c.start): int(c.end) + 1]
        talk = len(transcript.slice(c.start, c.end)) if transcript is not None else 0
        if len(seg) and float(seg.mean()) >= threshold and talk < 4:
            c.signal_score = round(c.signal_score * 0.5, 1)
            c.signals["music_only"] = 1.0
    return cands
