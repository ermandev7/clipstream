"""Transcripción con faster-whisper (tiempos por palabra), cargada vía gestor de GPU."""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Callable

from config.settings import settings

from app.core.gpu import gpu
from app.shared.transcription.types import Transcript, Word

log = logging.getLogger(__name__)
_dll_dirs_added = False


def _add_cuda_dll_dirs() -> None:
    """Los wheels nvidia-cublas-cu12 / nvidia-cudnn-cu12 dejan sus DLL en site-packages/nvidia/*/bin."""
    global _dll_dirs_added
    if _dll_dirs_added or sys.platform != "win32":
        return
    from app.core.setup import cuda_dirs  # venv en desarrollo; %LOCALAPPDATA%\ClipStream\runtime\cuda instalado

    for bin_dir in cuda_dirs():
        os.add_dll_directory(str(bin_dir))
        os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
    _dll_dirs_added = True


def _load():
    _add_cuda_dll_dirs()
    from faster_whisper import WhisperModel

    kwargs = dict(device="cuda", compute_type=settings.whisper_compute_type, download_root=str(settings.models_dir))
    from app.core.setup import whisper_dir

    if (whisper_dir() / "model.bin").exists():  # descargado por la preparación del primer arranque
        kwargs.pop("download_root")
        return WhisperModel(str(whisper_dir()), **kwargs)
    try:
        # Sin red: si ya está descargado, no se consulta HuggingFace en cada carga.
        return WhisperModel(settings.whisper_model, local_files_only=True, **kwargs)
    except Exception:  # noqa: BLE001 - primera vez: descargar
        log.info("Descargando modelo Whisper %s (~1,6 GB)", settings.whisper_model)
        return WhisperModel(settings.whisper_model, **kwargs)


def _unload(model) -> None:
    # ctranslate2 libera la VRAM al destruir el modelo; unload_model lo hace explícito.
    try:
        model.model.unload_model()
    except Exception:  # noqa: BLE001
        pass


gpu.register("whisper", _load, _unload)


def transcribe(audio_path: str | Path, language: str | None = "es", offset: float = 0.0,
               progress: Callable[[float], None] | None = None) -> Transcript:
    """Transcribe un archivo de audio. `offset` se suma a todos los tiempos."""
    with gpu.use("whisper") as model:
        segments, info = model.transcribe(
            str(audio_path),
            language=language,
            word_timestamps=True,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            beam_size=5,  # con 1 (greedy) a veces corta tras la primera frase y pierde el resto
            condition_on_previous_text=False,
        )
        words: list[Word] = []
        total = float(info.duration or 0)
        for seg in segments:
            if progress and total > 0:
                progress(min(1.0, seg.end / total))
            for w in seg.words or []:
                text = w.word.strip()
                if text:
                    words.append(Word(round(w.start + offset, 3), round(w.end + offset, 3), text, round(w.probability, 3)))
    return Transcript(info.language or (language or "es"), words)
