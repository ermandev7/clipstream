"""Configuración central de ClipStream."""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Empaquetado con PyInstaller: el código y los archivos de solo lectura (interfaz, fuentes,
# efectos) van en la carpeta del programa; lo que cambia (base de datos, clips, modelos,
# música importada) va en %LOCALAPPDATA%\ClipStream, que siempre es escribible.
FROZEN = bool(getattr(sys, "frozen", False))
ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
DATA_DIR = (Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "ClipStream") if FROZEN else ROOT
ASSETS_DIR = DATA_DIR / "assets"

# Licencias permitidas para archivos de assets/ (ver assets/licenses.json).
ALLOWED_LICENSES = ["CC0", "CC-BY", "Apache-2.0", "MIT", "OFL", "Pixabay", "YouTube-AudioLibrary", "Public-Domain"]

# Derechos en los clips: solo se avisa, nunca se bloquea.
RIGHTS_POLICY = {"clipper": "warn"}

# Modelos en lista blanca (nombre -> licencia verificada).
ALLOWED_MODELS = {
    "faster-whisper/large-v3-turbo": "MIT",
    "faster-whisper/medium": "MIT",
    "ollama/qwen2.5:7b-instruct": "Apache-2.0",
    "ollama/mistral:7b-instruct": "Apache-2.0",
    "mediapipe/blaze_face_short_range": "Apache-2.0",
    "mediapipe/blaze_face_full_range": "Apache-2.0",
    "mediapipe/pose_landmarker_lite": "Apache-2.0",
    "mediapipe/efficientdet_lite0": "Apache-2.0",
    "mediapipe/yamnet": "Apache-2.0",
    "mediapipe/face_landmarker": "Apache-2.0",
}

# Límites de clip (segundos).
CLIP_MIN_S = 3.0
CLIP_MAX_S = 180.0


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=DATA_DIR / ".env", env_prefix="CLIPSTREAM_", extra="ignore")

    host: str = "127.0.0.1"
    port: int = 8765
    lan_access: bool = False  # True = escuchar en 0.0.0.0 para abrir desde el móvil

    storage_dir: Path = DATA_DIR / "storage"
    models_dir: Path = DATA_DIR / "models"

    whisper_model: str = "large-v3-turbo"
    whisper_compute_type: str = "int8_float16"
    llm_model: str = "qwen2.5:7b-instruct"
    ollama_url: str = "http://127.0.0.1:11434"

    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"

    vram_total_gb: float = 8.0
    live_buffer_minutes: int = 15
    segment_seconds: int = 10

    @property
    def db_path(self) -> Path:
        return self.storage_dir / "clipstream.db"

    @property
    def queue_path(self) -> Path:
        return self.storage_dir / "queue.db"


def ensure_user_assets() -> None:
    """Versión empaquetada: copia los assets incluidos a la carpeta de datos (la primera vez) y
    añade al registro de licencias las entradas nuevas de una actualización, sin tocar la
    música que haya importado el usuario."""
    if not FROZEN:
        return
    bundled = ROOT / "assets"
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    for src in bundled.rglob("*"):
        rel = src.relative_to(bundled)
        dst = ASSETS_DIR / rel
        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
        elif rel.as_posix() != "licenses.json" and not dst.exists():
            shutil.copy2(src, dst)
    reg_dst = ASSETS_DIR / "licenses.json"
    builtin = json.loads((bundled / "licenses.json").read_text(encoding="utf-8"))
    current = json.loads(reg_dst.read_text(encoding="utf-8")) if reg_dst.exists() else {"files": {}}
    current.setdefault("files", {})
    for k, v in builtin.get("files", {}).items():
        current["files"].setdefault(k, v)
    current["_comment"] = builtin.get("_comment", "")
    reg_dst.write_text(json.dumps(current, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


ensure_user_assets()
settings = Settings()
