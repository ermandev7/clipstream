"""Captura de un directo de YouTube: streamlink -> ffmpeg (copia) -> segmentos de 10 s."""
from __future__ import annotations

import logging
import subprocess
import threading
import time
from pathlib import Path

from app.clipper.ingest.base import StreamSource
from app.clipper.ingest.buffer import SegmentBuffer
from app.shared.render.ffmpeg import NO_WINDOW, ffmpeg_bin

log = logging.getLogger(__name__)


def live_info(url: str) -> dict:
    """Título/canal y si está en directo (vía yt-dlp, sin descargar)."""
    from app.clipper.ingest.vod import fetch_info

    info = fetch_info(url)
    return {
        "title": info.get("title") or "",
        "channel": info.get("channel") or info.get("uploader") or "",
        "is_live": bool(info.get("is_live")),
        "id": info.get("id"),
    }


class YouTubeLive(StreamSource):
    def __init__(self, url: str, buffer: SegmentBuffer, quality: str = "1080p,720p,best") -> None:
        self.url = url
        self.buffer = buffer
        self.quality = quality
        self._proc: subprocess.Popen | None = None
        self._stream_fd = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.error: str | None = None

    RECONNECT_TRIES = 6
    RECONNECT_WAIT_S = 10

    def _open_stream(self):
        from streamlink import Streamlink

        session = Streamlink()
        # Por defecto streamlink se rinde si YouTube tarda unos segundos en publicar el siguiente
        # segmento; en un directo eso pasa a menudo. 0 = no cortar por ese motivo.
        session.set_option("stream-segmented-queue-deadline", 0)
        session.set_option("stream-timeout", 60.0)
        streams = session.streams(self.url)
        if not streams:
            return None
        stream = next((streams[q] for q in self.quality.split(",") if q in streams), streams.get("best"))
        return stream.open()

    def start(self) -> None:
        self._stream_fd = self._open_stream()
        if self._stream_fd is None:
            raise ValueError("No se encontró un directo activo en ese link.")
        log.info("Capturando %s", self.url)
        self._proc = subprocess.Popen(
            [ffmpeg_bin(), "-hide_banner", "-nostdin", "-loglevel", "error", "-i", "pipe:0",
             "-map", "0:v:0", "-map", "0:a:0", "-c", "copy",
             "-f", "segment", "-segment_time", str(self.buffer.seg_s), "-reset_timestamps", "0",
             "-segment_format", "mpegts", "-segment_list", str(self.buffer.list_path),
             "-segment_list_type", "csv", "-segment_list_flags", "+live", self.buffer.pattern],
            stdin=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=NO_WINDOW,
        )
        self.buffer.wall_start = time.time()
        self._thread = threading.Thread(target=self._pump, daemon=True, name="live-pump")
        self._thread.start()

    def _pump(self) -> None:
        assert self._proc and self._proc.stdin and self._stream_fd
        self.reconnects = 0
        try:
            while not self._stop.is_set():
                try:
                    data = self._stream_fd.read(64 * 1024)
                except Exception as e:  # noqa: BLE001
                    log.warning("Lectura del directo interrumpida: %s", e)
                    data = b""
                if data:
                    self._proc.stdin.write(data)
                    continue
                # Sin datos: o el directo terminó o fue un corte de red. Reintentar antes de rendirse.
                if not self._reconnect():
                    log.info("El directo terminó")
                    break
        except Exception as e:  # noqa: BLE001
            if not self._stop.is_set():
                self.error = str(e)
                log.warning("Captura interrumpida: %s", e)
        finally:
            self._close()

    def _reconnect(self) -> bool:
        try:
            self._stream_fd.close()
        except Exception:  # noqa: BLE001
            pass
        for attempt in range(1, self.RECONNECT_TRIES + 1):
            if self._stop.wait(self.RECONNECT_WAIT_S):
                return False
            try:
                fd = self._open_stream()
            except Exception as e:  # noqa: BLE001
                log.warning("Reconexión %d/%d falló: %s", attempt, self.RECONNECT_TRIES, e)
                continue
            if fd is None:  # ya no hay directo
                return False
            self._stream_fd = fd
            self.reconnects += 1
            log.info("Reconectado al directo (intento %d)", attempt)
            return True
        return False

    def _close(self) -> None:
        try:
            if self._stream_fd:
                self._stream_fd.close()
        except Exception:  # noqa: BLE001
            pass
        if self._proc:
            try:
                if self._proc.stdin:
                    self._proc.stdin.close()
                self._proc.wait(timeout=10)
            except Exception:  # noqa: BLE001
                self._proc.kill()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=15)

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())


def ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p
