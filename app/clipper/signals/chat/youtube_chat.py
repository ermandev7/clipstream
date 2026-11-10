"""Chat de YouTube (en vivo y repetición) usando el descargador de chat de yt-dlp.

chat-downloader quedó desactualizado frente a YouTube ("Unable to parse initial video
data"); yt-dlp mantiene su extractor al día y escribe el chat como líneas JSON.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import tempfile
import threading
import time
from collections.abc import Iterable
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

HIT_RE = re.compile(r"(j[aes]j[aes]|jsjs|\bx+d+\b|\blol\b|\blmao\b|\bkek|\bclip\b|\bomg\b|\bwtf\b|\bno+\b|😂|🤣|💀|😱|🔥|:\w*(?:lul|kek|laugh|joy)\w*:)",
                    re.IGNORECASE)
MESSAGE_RENDERERS = ("liveChatTextMessageRenderer", "liveChatPaidMessageRenderer")


def is_hit(text: str) -> bool:
    return bool(HIT_RE.search(text or ""))


def _runs_text(message: dict) -> str:
    out: list[str] = []
    for run in message.get("runs", []):
        if "text" in run:
            out.append(run["text"])
        elif "emoji" in run:
            emo = run["emoji"]
            out.append(emo.get("emojiId") if not emo.get("isCustomEmoji") else (emo.get("shortcuts") or [""])[0])
    return "".join(x for x in out if x)


def parse_chat_line(line: str) -> list[tuple[float | None, float | None, str]]:
    """Una línea de *.live_chat.json -> [(segundo_del_vídeo | None, hora_unix | None, texto)]."""
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return []
    wrapper = obj.get("replayChatItemAction", obj)
    offset_ms = wrapper.get("videoOffsetTimeMsec")
    offset = int(offset_ms) / 1000 if offset_ms not in (None, "") else None
    out: list[tuple[float | None, float | None, str]] = []
    for action in wrapper.get("actions", []):
        item = (action.get("addChatItemAction") or {}).get("item") or {}
        for key in MESSAGE_RENDERERS:
            r = item.get(key)
            if r is None:
                continue
            ts = r.get("timestampUsec")
            out.append((offset, int(ts) / 1e6 if ts else None, _runs_text(r.get("message") or {})))
    return out


# Eventos del chat que indican un momento fuerte (el streamer suele reaccionar).
EVENT_RENDERERS = {
    "liveChatPaidMessageRenderer": "donation",       # Super Chat
    "liveChatPaidStickerRenderer": "donation",       # Super Sticker
    "liveChatMembershipItemRenderer": "sub",         # nuevo miembro / hito
    "liveChatSponsorshipsGiftPurchaseAnnouncementRenderer": "sub",  # membresías regaladas
}
EVENT_KINDS = ("donation", "sub")


def parse_chat_events(line: str) -> list[tuple[float | None, float | None, str]]:
    """Una línea de *.live_chat.json -> [(segundo_del_vídeo | None, hora_unix | None, tipo)]."""
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return []
    wrapper = obj.get("replayChatItemAction", obj)
    offset_ms = wrapper.get("videoOffsetTimeMsec")
    offset = int(offset_ms) / 1000 if offset_ms not in (None, "") else None
    out = []
    for action in wrapper.get("actions", []):
        item = (action.get("addChatItemAction") or {}).get("item") or {}
        for key, kind in EVENT_RENDERERS.items():
            r = item.get(key)
            if r is not None:
                ts = r.get("timestampUsec")
                out.append((offset, int(ts) / 1e6 if ts else None, kind))
    return out


def event_series(events, duration_s: int) -> dict[str, np.ndarray]:
    """[(segundo, tipo)] -> {tipo: eventos por segundo}."""
    out = {k: np.zeros(duration_s) for k in EVENT_KINDS}
    for t, kind in events:
        i = int(t)
        if 0 <= i < duration_s and kind in out:
            out[kind][i] += 1
    return out


def series_from_times(times: Iterable[tuple[float, bool]], duration_s: int) -> tuple[np.ndarray, np.ndarray]:
    """[(segundo, es_hit)] -> (mensajes/s, hits/s)."""
    rate = np.zeros(duration_s)
    hits = np.zeros(duration_s)
    for t, hit in times:
        i = int(t)
        if 0 <= i < duration_s:
            rate[i] += 1
            if hit:
                hits[i] += 1
    return rate, hits


class _Stop(Exception):
    pass


def _download_chat(url: str, out_dir: Path, stop: threading.Event | None = None) -> None:
    import yt_dlp

    def hook(_d: dict) -> None:
        if stop is not None and stop.is_set():
            raise _Stop()

    opts = {
        "skip_download": True,
        "writesubtitles": True,
        "subtitleslangs": ["live_chat"],
        "outtmpl": str(out_dir / "chat.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "progress_hooks": [hook],
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])


def _chat_files(out_dir: Path) -> list[Path]:
    """El archivo final, o mientras descarga, el .part (se va completando por fragmentos)."""
    final = sorted(out_dir.glob("*.live_chat.json"))
    return final or sorted(p for p in out_dir.glob("*.live_chat.json.part") if p.is_file())


def replay_series(url: str, duration_s: int, max_seconds: float = 600) -> dict[str, np.ndarray] | None:
    """Repetición del chat de un directo terminado -> {rate, hits, donation, sub}. None si no hay chat."""
    tmp = Path(tempfile.mkdtemp(prefix="clipstream_chat_"))
    stop = threading.Event()
    err: list[Exception] = []

    def run() -> None:
        try:
            _download_chat(url, tmp, stop)
        except _Stop:
            pass
        except Exception as e:  # noqa: BLE001
            err.append(e)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(max_seconds)
    if t.is_alive():
        log.warning("Repetición de chat cortada tras %.0f s", max_seconds)
        stop.set()
        t.join(30)
    try:
        times: list[tuple[float, bool]] = []
        events: list[tuple[float, str]] = []
        for f in _chat_files(tmp):
            for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
                for offset, _ts, text in parse_chat_line(line):
                    if offset is not None:
                        times.append((offset, is_hit(text)))
                for offset, _ts, kind in parse_chat_events(line):
                    if offset is not None:
                        events.append((offset, kind))
        if not times and not events:
            if err:
                log.info("Sin repetición de chat: %s", err[0])
            return None
        log.info("Chat: %d mensajes y %d eventos en la repetición", len(times), len(events))
        rate, hits = series_from_times(times, duration_s)
        return {"rate": rate, "hits": hits, **event_series(events, duration_s)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


class LiveChat:
    """Descarga el chat en vivo en un hilo y lo lee por hora de pared."""

    def __init__(self, url: str, work_dir: Path | None = None) -> None:
        self.url = url
        self.dir = Path(work_dir or tempfile.mkdtemp(prefix="clipstream_live_chat_"))
        self.dir.mkdir(parents=True, exist_ok=True)
        self._msgs: list[tuple[float, bool]] = []
        self._events: list[tuple[float, str]] = []
        self._read_pos: dict[Path, int] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="youtube-chat")
        self.error: str | None = None

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        try:
            _download_chat(self.url, self.dir, self._stop)
        except _Stop:
            pass
        except Exception as e:  # noqa: BLE001
            self.error = str(e)
            log.warning("Chat en vivo no disponible: %s", e)

    def _ingest(self) -> None:
        """Lee las líneas nuevas que yt-dlp añadió desde la última vez."""
        for f in _chat_files(self.dir):
            pos = self._read_pos.get(f, 0)
            try:
                with f.open("rb") as fh:
                    fh.seek(pos)
                    data = fh.read()
            except OSError:
                continue
            end = data.rfind(b"\n")
            if end < 0:
                continue
            self._read_pos[f] = pos + end + 1
            for line in data[: end + 1].decode("utf-8", errors="ignore").splitlines():
                for _off, ts, text in parse_chat_line(line):
                    if ts is not None:
                        self._msgs.append((ts, is_hit(text)))
                for _off, ts, kind in parse_chat_events(line):
                    if ts is not None:
                        self._events.append((ts, kind))
        if len(self._msgs) > 200_000:
            self._msgs = self._msgs[-100_000:]

    def series(self, wall_start: float, duration_s: int) -> tuple[np.ndarray, np.ndarray]:
        """Series por segundo desde `wall_start` (hora de pared del segundo 0 de la captura)."""
        with self._lock:
            self._ingest()
            times = [(t - wall_start, h) for t, h in self._msgs if t >= wall_start]
        return series_from_times(times, duration_s)

    def events(self, wall_start: float, duration_s: int) -> dict[str, np.ndarray]:
        with self._lock:
            self._ingest()
            evs = [(t - wall_start, k) for t, k in self._events if t >= wall_start]
        return event_series(evs, duration_s)

    @property
    def message_count(self) -> int:
        with self._lock:
            self._ingest()
            return len(self._msgs)
