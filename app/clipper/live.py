"""Directo: captura continua con buffer circular y detección en tiempo real.

Cada ciclo (~30 s):
  1. transcribe y mide audio de los segmentos nuevos del buffer,
  2. puntúa los últimos minutos contra la línea base de todo lo capturado,
  3. los candidatos cuyo pico quedó al menos 20 s atrás se ajustan a frases,
     pasan por el LLM y, si superan el umbral, se exportan del buffer y se renderizan.
"""
from __future__ import annotations

import logging
import threading
import time

import numpy as np
from config.settings import settings

from app.clipper import common
from app.clipper.detection import boundaries, dedup, llm_judge, scorer
from app.clipper.ingest.buffer import SegmentBuffer
from app.clipper.ingest.youtube_live import YouTubeLive
from app.clipper.signals.chat.youtube_chat import LiveChat
from app.core import db
from app.core.events import clip_changed, log_event, source_changed
from app.shared.render import ffmpeg
from app.shared.transcription.types import Transcript

log = logging.getLogger(__name__)

CYCLE_S = 30
SETTLE_S = 20  # el pico tiene que quedar atrás para ver la reacción completa
WINDOW_S = 180  # ventana analizada en cada ciclo
MIN_SIGNAL = 55.0
MIN_FINAL = 55.0
# Modo por bloques: además de los picos fuertes (arriba), cada `every_s` segundos de directo se analiza ese bloque
# como un vídeo grabado y se saca su mejor momento. Así un directo tranquilo (charla) también da clips.
BLOCK_DEFAULT_S = 300
BLOCK_CHOICES = (0, 180, 300, 600)  # 0 = solo picos fuertes
BLOCK_MIN_SCORE = 45.0  # el mejor del bloque necesita al menos esto (IA + señales)
BLOCK_SECOND = 70.0  # un segundo clip del mismo bloque solo si es muy bueno
BLOCK_JUDGE = 3  # candidatos por bloque que evalúa la IA (cada uno ~2 s)
BLOCK_PRE_S, BLOCK_POST_S = 30.0, 25.0  # clip de bloque: ~20-60 s alrededor del mejor momento
BLOCK_LLM_VETO = 35.0  # dentro del bloque la señal siempre sale alta (se compara consigo mismo): la IA puede vetar


class LiveSession:
    def __init__(self, source_id: int, url: str, platform: str = "youtube", every_s: int = BLOCK_DEFAULT_S) -> None:
        self.sid = source_id
        self.every_s = every_s if every_s in BLOCK_CHOICES else BLOCK_DEFAULT_S
        self.block_from: float | None = None  # inicio del siguiente bloque por analizar
        self.url = url
        self.platform = platform
        self.dir = common.source_dir(source_id)
        self.buffer = SegmentBuffer(self.dir / "buffer", settings.segment_seconds, settings.live_buffer_minutes)
        self.capture = YouTubeLive(url, self.buffer)  # streamlink: sirve igual para YouTube y Kick
        # Chat de Kick: pendiente de decidir (WebSocket interno no oficial); de momento solo YouTube.
        self.chat = LiveChat(url, self.dir / "chat") if platform == "youtube" else None
        self.viewers = ViewerPoller(url, platform)
        self.transcript = Transcript("es", [])
        self.audio_db = np.zeros(0)
        self.events: dict[str, np.ndarray] = {}
        self.done_until = 0.0  # tiempo de stream ya transcrito
        self.clipped: list[tuple[float, float]] = []
        self._stop = threading.Event()
        self.ended_naturally = False
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"live-{source_id}")

    # ---- control --------------------------------------------------------
    def start(self) -> None:
        self.capture.start()
        if self.chat:
            self.chat.start()
        self.viewers.start()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.capture.stop()
        if self.chat:
            self.chat.stop()
        self.viewers.stop()

    @property
    def running(self) -> bool:
        return self._thread.is_alive()

    # ---- bucle ----------------------------------------------------------
    def _run(self) -> None:
        db.update("sources", self.sid, status="live", stage="Capturando")
        source_changed(self.sid)
        log_event(self.sid, "Captura iniciada")
        try:
            while not self._stop.is_set():
                t_cycle = time.monotonic()
                try:
                    self._cycle()
                except Exception as e:  # noqa: BLE001
                    log.exception("Ciclo de directo fallido")
                    log_event(self.sid, f"Error en el análisis: {e}", "error")
                if not self.capture.running and not self._stop.is_set():
                    log_event(self.sid, "El directo terminó o se cortó la captura", "warn")
                    self.ended_naturally = True
                    break
                self._stop.wait(max(1.0, CYCLE_S - (time.monotonic() - t_cycle)))
        finally:
            for part in (self.capture, self.chat, self.viewers):
                try:
                    if part is not None:
                        part.stop()
                except Exception:  # noqa: BLE001 - parar nunca debe impedir marcar la fuente como detenida
                    log.exception("Error al detener %s", type(part).__name__)
            db.update("sources", self.sid, status="stopped", stage="Detenido")
            source_changed(self.sid)
            log_event(self.sid, "Captura detenida")
            if self.ended_naturally:
                # El directo acabó: cuando la repetición esté publicada, se analiza entera.
                from app.workers.tasks import reprocess_live_task

                log_event(self.sid, "Se reprocesará el directo completo cuando la repetición esté disponible")
                reprocess_live_task(self.sid)

    def _cycle(self) -> None:
        new = [s for s in self.buffer.complete() if s.start >= self.done_until - 0.01]
        if not new:
            return
        head = new[-1].start + new[-1].duration
        # 1. Señales de los segmentos nuevos (un solo archivo para no pagar la carga de Whisper por segmento).
        chunk, t0 = self.buffer.export(new[0].start, head, self.dir / "chunk.ts")
        wav = ffmpeg.extract_audio(chunk, self.dir / "chunk.wav")
        from app.shared.transcription import whisper

        tr = whisper.transcribe(wav, language=None, offset=t0)
        self.transcript.words.extend(tr.words)
        self.transcript.language = tr.language
        levels = ffmpeg.audio_levels(wav)
        start_i = int(t0)
        need = start_i + len(levels)
        if len(self.audio_db) < need:
            self.audio_db = np.pad(self.audio_db, (0, need - len(self.audio_db)), constant_values=-60.0)
        self.audio_db[start_i:need] = levels
        from app.clipper.signals.audio_events import classify_wav

        for k, v in classify_wav(wav, offset=t0).items():
            cur = self.events.get(k, np.zeros(0))
            if len(cur) < len(v):
                cur = np.pad(cur, (0, len(v) - len(cur)))
            cur[start_i:len(v)] = v[start_i:]
            self.events[k] = cur
        common.save_events(self.sid, self.events)
        self.done_until = head
        # Recortar transcripción a lo que sigue en el buffer (más margen).
        oldest = self.buffer.complete()[0].start if self.buffer.complete() else 0.0
        self.transcript.words = [w for w in self.transcript.words if w.end >= oldest - 60]
        common.save_transcript(self.sid, self.transcript)

        # 2. Puntuar la ventana reciente contra la línea base del buffer.
        n = int(head)
        chat_rate = chat_hits = None
        if self.chat and self.buffer.wall_start:
            chat_rate, chat_hits = self.chat.series(self.buffer.wall_start, n)
            for k, v in self.chat.events(self.buffer.wall_start, n).items():
                self.events[k] = v
        if self.buffer.wall_start:
            self.events["viewers"] = self.viewers.series(self.buffer.wall_start, n)
        lo = max(int(oldest), n - WINDOW_S)
        sig = scorer.SignalSet(n - lo, self.audio_db[lo:n], None if chat_rate is None else chat_rate[lo:n],
                               None if chat_hits is None else chat_hits[lo:n], self.transcript)
        sig.transcript = Transcript(self.transcript.language, [w for w in self.transcript.words if w.start >= lo]).shifted(-lo)
        sig.events = {k: v[lo:n] for k, v in self.events.items()}
        base = scorer.SignalSet(n - int(oldest), self.audio_db[int(oldest):n],
                                None if chat_rate is None else chat_rate[int(oldest):n])
        curve, parts = scorer.combined_curve(sig, base)
        full_curve = np.zeros(n)
        full_curve[lo:n] = curve
        common.save_signals(self.sid, head, full_curve[int(oldest):], self.audio_db[int(oldest):n],
                            None if chat_rate is None else chat_rate[int(oldest):n], bins=180, offset=oldest,
                            laughter=self.events.get("laughter", np.zeros(0))[int(oldest):n],
                            viewers=self.events.get("viewers", np.zeros(0))[int(oldest):n])
        db.update("sources", self.sid, duration=head, has_chat=int(chat_rate is not None and chat_rate.sum() > 0))
        source_changed(self.sid)

        cands = scorer.candidates_from_curve(curve, parts, max_candidates=3, min_height=0.6,
                                             chat_rate=None if chat_rate is None else chat_rate[lo:n], offset=lo)
        for c in cands:
            if c.peak > head - SETTLE_S or c.signal_score < MIN_SIGNAL:
                continue
            if any(a <= c.peak <= b for a, b in self.clipped):
                continue
            boundaries.adjust(c, self.transcript, head)
            scorer.penalize_music_only([c], self.events, self.transcript)
            c.start = max(c.start, oldest)
            llm_judge.judge(c, self.transcript, db.get("sources", self.sid)["title"] or "")
            self.clipped.append((c.start, c.end))
            if c.score < MIN_FINAL:
                log_event(self.sid, f"Descartado {c.peak:.0f} s: {c.score:.0f}/100 {c.llm_reason}")
                continue
            self._emit(c)
        self._blocks(head, oldest, chat_rate, chat_hits)
        keep = [a for a, _ in self.clipped[-5:]] + ([self.block_from] if self.block_from is not None else [])
        self.buffer.prune(keep_from=min(keep, default=None))

    # ---- modo por bloques ------------------------------------------------
    def _blocks(self, head: float, oldest: float, chat_rate, chat_hits) -> None:
        if not self.every_s:
            return
        if self.block_from is None:
            self.block_from = oldest
            db.update("sources", self.sid, stage=f"Capturando · primer bloque a los {self.every_s // 60} min")
        # Se espera a tener el bloque entero y un margen detrás (para ver la reacción completa).
        while head - SETTLE_S >= self.block_from + self.every_s:
            a, b = max(self.block_from, oldest), self.block_from + self.every_s
            self.block_from = b
            if b - a >= 30:
                self._block(a, b, head, oldest, chat_rate, chat_hits)
        nxt = self.block_from + self.every_s + SETTLE_S - head
        db.update("sources", self.sid, stage=f"Capturando · próximo bloque en {max(0, int(nxt)) // 60}:{max(0, int(nxt)) % 60:02d}")
        source_changed(self.sid)

    def _block(self, a: float, b: float, head: float, oldest: float, chat_rate, chat_hits) -> None:
        """El mejor momento del bloque [a, b), comparando el bloque consigo mismo (como un vídeo grabado)."""
        lo, hi = int(a), min(int(b), len(self.audio_db))
        label = f"Bloque {common.mmss(a)}–{common.mmss(b)}"
        if hi - lo < 30:
            return
        sig = scorer.SignalSet(hi - lo, self.audio_db[lo:hi], None if chat_rate is None else chat_rate[lo:hi],
                               None if chat_hits is None else chat_hits[lo:hi], self.transcript)
        sig.transcript = Transcript(self.transcript.language,
                                    [w for w in self.transcript.words if lo <= w.start < hi]).shifted(-lo)
        sig.events = {k: v[lo:hi] for k, v in self.events.items() if len(v) > lo}
        curve, parts = scorer.combined_curve(sig)
        cands = scorer.candidates_from_curve(curve, parts, max_candidates=6, min_height=0.0,
                                             chat_rate=None if chat_rate is None else chat_rate[lo:hi], offset=lo)
        cands = [c for c in cands if not any(x <= c.peak <= y for x, y in self.clipped)]
        for c in cands:
            c.start, c.end = max(c.start, c.peak - BLOCK_PRE_S), min(c.end, c.peak + BLOCK_POST_S)
            boundaries.adjust(c, self.transcript, head)
            c.start = max(c.start, oldest)
        scorer.penalize_music_only(cands, self.events, self.transcript)
        cands = sorted(dedup.merge(cands), key=lambda c: -c.signal_score)[:BLOCK_JUDGE]
        if not cands:
            log_event(self.sid, f"{label}: sin momentos candidatos")
            return
        title = db.get("sources", self.sid)["title"] or ""
        for c in cands:
            llm_judge.judge(c, self.transcript, title)
        chosen: list = []
        for c in sorted(cands, key=lambda c: -c.score):
            if any(not (c.end <= x.start or c.start >= x.end) for x in chosen):
                continue
            if any(not (c.end <= x or c.start >= y) for x, y in self.clipped):
                continue
            if c.llm_score is not None and c.llm_score < BLOCK_LLM_VETO:
                continue  # pantalla de espera, silencio, anuncio...
            if (not chosen and c.score >= BLOCK_MIN_SCORE) or (chosen and len(chosen) < 2 and c.score >= BLOCK_SECOND):
                chosen.append(c)
        if not chosen:
            best = max(cands, key=lambda c: c.score)
            log_event(self.sid, f"{label}: sin momento destacable (el mejor, {best.score:.0f}/100: {best.llm_reason})")
            return
        for c in chosen:
            self.clipped.append((c.start, c.end))
            log_event(self.sid, f"{label}: mejor momento {c.score:.0f}/100 · {c.llm_reason}")
            self._emit(c)

    def _emit(self, c) -> None:
        src = db.get("sources", self.sid)
        n = len(db.query("SELECT id FROM clips WHERE source_id=?", (self.sid,)))
        piece, t0 = self.buffer.export(c.start, c.end, self.dir / f"piece_{n + 1}.ts")
        meta = common.clip_metadata(c, self.transcript, src["title"] or "")
        cid = common.store_clip(src, c, self.transcript, files={"src": str(piece), "src_offset": t0}, meta=meta)
        clip_changed(cid)
        log_event(self.sid, f"Momento detectado ({c.score:.0f}/100): {c.title or c.signals_label()}")
        from app.workers.tasks import render_clip_task

        render_clip_task(cid)


_sessions: dict[int, LiveSession] = {}


class ViewerPoller:
    """Espectadores del directo cada 30 s (YouTube: yt-dlp; Kick: API del canal)."""

    INTERVAL_S = 30

    def __init__(self, url: str, platform: str) -> None:
        self.url, self.platform = url, platform
        self.samples: list[tuple[float, int]] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="viewers")

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _read(self) -> int | None:
        try:
            if self.platform == "kick":
                from app.clipper.ingest.kick import parse, viewers

                return viewers(parse(self.url)["slug"])
            from app.clipper.ingest.vod import fetch_info

            return fetch_info(self.url).get("concurrent_view_count")
        except Exception:  # noqa: BLE001
            return None

    def _run(self) -> None:
        while not self._stop.is_set():
            v = self._read()
            if v is not None:
                self.samples.append((time.time(), int(v)))
            self._stop.wait(self.INTERVAL_S)

    def series(self, wall_start: float, duration_s: int) -> np.ndarray:
        """Espectadores por segundo (escalonado entre muestras)."""
        out = np.zeros(duration_s)
        pts = [(t - wall_start, v) for t, v in self.samples]
        for i, (t, v) in enumerate(pts):
            a = max(0, int(t))
            b = int(pts[i + 1][0]) if i + 1 < len(pts) else duration_s
            if a < duration_s:
                out[a:max(a, min(b, duration_s))] = v
        if pts and pts[0][0] > 0:
            out[: max(0, int(pts[0][0]))] = pts[0][1]
        return out


def start_live(source_id: int, url: str, platform: str = "youtube", every_s: int = BLOCK_DEFAULT_S) -> LiveSession:
    s = LiveSession(source_id, url, platform, every_s)
    s.start()
    _sessions[source_id] = s
    return s


def stop_live(source_id: int) -> bool:
    s = _sessions.pop(source_id, None)
    if s:
        s.stop()
        return True
    return False


def stop_all() -> None:
    for sid in list(_sessions):
        stop_live(sid)


def session(source_id: int) -> LiveSession | None:
    return _sessions.get(source_id)
