"""Punto de entrada del ejecutable (ClipStream.exe)."""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def _setup_logging() -> None:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "ClipStream" / "logs"
    base.mkdir(parents=True, exist_ok=True)
    log_file = base / "clipstream.log"
    # Sin consola (modo ventana) stdout/stderr son None y cualquier print() fallaría.
    if sys.stdout is None or sys.stderr is None:
        stream = open(base / "salida.txt", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        sys.stdout = sys.stdout or stream
        sys.stderr = sys.stderr or stream
    handler = RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler],
                        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s", force=True)
    # Si una DLL nativa (CUDA, ctranslate2...) tumba el proceso, deja la pila de Python en el disco.
    import faulthandler

    global _crash_file
    _crash_file = open(base / "fallo_nativo.txt", "a", encoding="utf-8")  # noqa: SIM115
    faulthandler.enable(_crash_file, all_threads=True)


_crash_file = None


def _selftest_stories(log) -> None:
    """Microseries: voz (empaquetada) y, si están descargados, motor e imágenes."""
    from app.stories import voices

    if voices.available():
        out = Path(os.environ.get("TEMP", ".")) / "clipstream_selftest_voz.wav"
        dur = voices.synthesize("Prueba de voz de ClipStream.", "es", None, out)
        log.info("Voz Kokoro OK (%.1f s)", dur)
    else:
        log.info("Voz Kokoro: modelo no descargado (se omite)")
    from app.core import pylib

    if not pylib.activate():
        log.info("Motor de imágenes no descargado (se omite)")
        return
    import torch

    log.info("PyTorch %s · CUDA %s", torch.__version__, torch.cuda.is_available())
    from app.stories import images

    if images.available():
        out = Path(os.environ.get("TEMP", ".")) / "clipstream_selftest.png"
        images.generate("an oil painting of a lighthouse at dusk", out, 1)
        from app.shared.rights import image_guard

        log.info("SDXL OK · revisión visual: %s", image_guard.check_image(out, "a lighthouse at dusk").message)
    else:
        log.info("Modelos de imagen no descargados (se omite)")


def selftest() -> int:
    """ClipStream.exe --selftest : comprueba GPU/Whisper/ffmpeg sin abrir la ventana (escribe en el registro)."""
    log = logging.getLogger("clipstream.selftest")
    import numpy as np

    from app.core.setup import manager
    from app.shared.render import ffmpeg

    log.info("Preparación: %s", manager.status())
    log.info("ffmpeg: %s", ffmpeg.ffmpeg_bin())
    from app.shared.transcription import whisper

    wav = Path(os.environ.get("TEMP", ".")) / "clipstream_selftest.wav"
    import wave

    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes((np.sin(np.arange(32000) / 10) * 3000).astype(np.int16).tobytes())
    t = whisper.transcribe(wav)
    log.info("Whisper OK (%d palabras)", len(t.words))
    from config.settings import settings

    if settings.stories_enabled:
        _selftest_stories(log)
    from app.core.gpu import gpu

    gpu.release()
    log.info("AUTODIAGNÓSTICO OK")
    return 0


def main() -> None:
    _setup_logging()
    logging.getLogger("clipstream").info("ClipStream arrancando (%s)", sys.executable)
    from app.core import pylib

    pylib.activate()  # motor de imágenes descargado, si lo hay (antes de importar nada que lo use)
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    try:
        from app.desktop import main as desktop_main

        desktop_main()
    except Exception:
        logging.getLogger("clipstream").exception("Error fatal")
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, "ClipStream no pudo arrancar. Revisa el registro en\n"
                                             "%LOCALAPPDATA%\\ClipStream\\logs\\clipstream.log", "ClipStream", 0x10)
        except Exception:  # noqa: BLE001
            pass
        raise


if __name__ == "__main__":
    main()
