"""Preparación del primer arranque: comprueba y descarga lo que el instalador no incluye.

El instalador solo lleva el programa. La primera vez se descargan, con barra de progreso:
  - runtime CUDA (cuBLAS + cuDNN) desde PyPI          ~1,3 GB
  - ffmpeg (compilación «essentials» de gyan.dev)     ~0,1 GB
  - Whisper large-v3-turbo desde Hugging Face         ~1,6 GB
  - modelos de visión y audio de MediaPipe            ~0,02 GB
  - el LLM en Ollama (Ollama lo instala el usuario)   ~4,7 GB
En modo desarrollo casi todo ya está (venv, winget), así que suele salir todo «listo».
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx
from config.settings import DATA_DIR, FROZEN, settings

log = logging.getLogger(__name__)
RUNTIME_DIR = DATA_DIR / "runtime"
CUDA_DIR = RUNTIME_DIR / "cuda"
FFMPEG_DIR = RUNTIME_DIR / "ffmpeg"
CUDA_WHEELS = [("nvidia-cublas-cu12", "12.9.2.10"), ("nvidia-cudnn-cu12", "9.28.1.4"), ("nvidia-cuda-nvrtc-cu12", "12.9.86")]
CUDA_DLLS = ["cublas64_12.dll", "cublasLt64_12.dll", "cudnn64_9.dll", "cudnn_ops64_9.dll", "cudnn_cnn64_9.dll"]
FFMPEG_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
OLLAMA_DOWNLOAD = "https://ollama.com/download/windows"

Progress = Callable[[float, str], None]


@dataclass
class Component:
    id: str
    label: str
    size_mb: int
    required: bool
    check: Callable[[], tuple[bool, str]]
    install: Callable[[Progress], None] | None
    help_url: str | None = None


# ---- descargas ---------------------------------------------------------------

def download(url: str, dest: Path, progress: Progress, label: str, start: float = 0.0, span: float = 1.0) -> Path:
    """Descarga a .part y renombra al terminar (una descarga cortada no deja un archivo «bueno» a medias)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=httpx.Timeout(60, connect=20),
                      headers={"User-Agent": "ClipStream/0.1"}) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        done = 0
        last = 0.0
        with part.open("wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
                done += len(chunk)
                now = time.monotonic()
                if total and now - last > 0.3:
                    last = now
                    progress(start + span * done / total, f"{label}: {done / 1e6:.0f} / {total / 1e6:.0f} MB")
    part.replace(dest)
    return dest


# ---- CUDA --------------------------------------------------------------------

def cuda_dirs() -> list[Path]:
    """Carpetas con las DLL de CUDA: las descargadas, las que trae PyTorch (motor de imágenes: cuBLAS 12 y
    cuDNN 9, compatibles con ctranslate2) y, en desarrollo, las del venv."""
    from app.core.pylib import PYLIB

    dirs = [CUDA_DIR] if CUDA_DIR.exists() else []
    torch_lib = PYLIB / "torch" / "lib"
    if FROZEN and torch_lib.is_dir():
        dirs.append(torch_lib)
    if not FROZEN:
        for base in map(Path, sys.path):
            nv = base / "nvidia"
            if nv.is_dir():
                dirs += [p for p in nv.glob("*/bin") if p.is_dir()]
    return dirs


def check_cuda() -> tuple[bool, str]:
    found = {f.name.lower() for d in cuda_dirs() for f in d.glob("*.dll")}
    missing = [d for d in CUDA_DLLS if d.lower() not in found]
    return (not missing, "Listo" if not missing else f"Faltan {len(missing)} bibliotecas")


def install_cuda(progress: Progress) -> None:
    CUDA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = RUNTIME_DIR / "tmp"
    n = len(CUDA_WHEELS)
    for i, (pkg, ver) in enumerate(CUDA_WHEELS):
        meta = httpx.get(f"https://pypi.org/pypi/{pkg}/{ver}/json", timeout=30).raise_for_status().json()
        wheel = next(u for u in meta["urls"] if u["filename"].endswith("win_amd64.whl"))
        whl = download(wheel["url"], tmp / wheel["filename"], progress, pkg, i / n, 0.9 / n)
        progress((i + 0.9) / n, f"{pkg}: extrayendo")
        with zipfile.ZipFile(whl) as z:
            for name in z.namelist():
                if name.lower().endswith(".dll") and "/bin/" in name:
                    with z.open(name) as src, (CUDA_DIR / Path(name).name).open("wb") as dst:
                        shutil.copyfileobj(src, dst)
        whl.unlink(missing_ok=True)
    shutil.rmtree(tmp, ignore_errors=True)


# ---- ffmpeg ------------------------------------------------------------------

def check_ffmpeg() -> tuple[bool, str]:
    from app.shared.render import ffmpeg

    ffmpeg._resolve.cache_clear()
    try:
        exe = ffmpeg.ffmpeg_bin()
    except FileNotFoundError:
        return False, "No encontrado"
    try:
        enc = subprocess.run([exe, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=20,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        return ("h264_nvenc" in enc, "Listo" if "h264_nvenc" in enc else "Sin NVENC (se usará CPU)")
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def install_ffmpeg(progress: Progress) -> None:
    tmp = RUNTIME_DIR / "tmp"
    zp = download(FFMPEG_URL, tmp / "ffmpeg.zip", progress, "ffmpeg", 0.0, 0.9)
    FFMPEG_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zp) as z:
        for name in z.namelist():
            base = Path(name).name.lower()
            if base in ("ffmpeg.exe", "ffprobe.exe"):
                with z.open(name) as src, (FFMPEG_DIR / base).open("wb") as dst:
                    shutil.copyfileobj(src, dst)
            elif name.endswith("LICENSE") or base in ("license", "readme.txt"):
                with z.open(name) as src, (FFMPEG_DIR / f"ffmpeg_{base or 'LICENSE'}").open("wb") as dst:
                    shutil.copyfileobj(src, dst)
    shutil.rmtree(tmp, ignore_errors=True)
    from app.shared.render import ffmpeg

    ffmpeg._resolve.cache_clear()


# ---- Whisper -----------------------------------------------------------------

def whisper_dir() -> Path:
    return settings.models_dir / f"whisper-{settings.whisper_model}"


def check_whisper() -> tuple[bool, str]:
    if (whisper_dir() / "model.bin").exists():
        return True, "Listo"
    try:  # descargado antes con faster-whisper (caché de Hugging Face)
        from faster_whisper.utils import download_model

        download_model(settings.whisper_model, local_files_only=True, cache_dir=str(settings.models_dir))
        return True, "Listo"
    except Exception:  # noqa: BLE001
        return False, "No descargado"


def install_whisper(progress: Progress) -> None:
    from faster_whisper.utils import _MODELS

    repo = _MODELS.get(settings.whisper_model, settings.whisper_model)
    info = httpx.get(f"https://huggingface.co/api/models/{repo}", follow_redirects=True, timeout=30).raise_for_status().json()
    repo = info.get("id") or repo  # el repositorio puede haberse movido
    files = [s["rfilename"] for s in info.get("siblings", [])
             if s["rfilename"] in ("config.json", "model.bin", "tokenizer.json", "vocabulary.json", "vocabulary.txt",
                                   "preprocessor_config.json")]
    dest = whisper_dir()
    for i, f in enumerate(files):
        span = 0.9 if f == "model.bin" else 0.1 / max(1, len(files) - 1)
        start = sum(0.9 if g == "model.bin" else 0.1 / max(1, len(files) - 1) for g in files[:i])
        download(f"https://huggingface.co/{repo}/resolve/main/{f}", dest / f, progress, f"Whisper {f}", start, span)


# ---- MediaPipe ---------------------------------------------------------------

def check_vision() -> tuple[bool, str]:
    from app.clipper.reframe.detector import MODEL_URLS, MODELS

    names = [*MODEL_URLS, "yamnet.tflite"]
    missing = [n for n in names if not (MODELS / n).exists()]
    return (not missing, "Listo" if not missing else f"Faltan {len(missing)} modelos")


def install_vision(progress: Progress) -> None:
    from app.clipper.reframe.detector import ensure_models
    from app.clipper.signals.audio_events import ensure_model

    progress(0.1, "Modelos de visión")
    ensure_models()
    progress(0.8, "Modelo de audio")
    ensure_model()


# ---- Ollama y LLM ------------------------------------------------------------

def check_ollama() -> tuple[bool, str]:
    try:
        httpx.get(f"{settings.ollama_url}/api/version", timeout=3).raise_for_status()
        return True, "En marcha"
    except Exception:  # noqa: BLE001
        return False, "No instalado o no está en marcha"


def check_llm() -> tuple[bool, str]:
    try:
        tags = httpx.get(f"{settings.ollama_url}/api/tags", timeout=3).json()
    except Exception:  # noqa: BLE001
        return False, "Necesita Ollama"
    ok = any(m["name"].startswith(settings.llm_model) for m in tags.get("models", []))
    return ok, "Listo" if ok else "No descargado"


def install_llm(progress: Progress) -> None:
    import json

    if not check_ollama()[0]:
        raise RuntimeError("Instala Ollama primero (ollama.com) y vuelve a intentarlo")
    with httpx.stream("POST", f"{settings.ollama_url}/api/pull", json={"model": settings.llm_model, "stream": True},
                      timeout=httpx.Timeout(None, connect=10)) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line:
                continue
            d = json.loads(line)
            if d.get("error"):
                raise RuntimeError(d["error"])
            total, done = d.get("total"), d.get("completed")
            if total and done:
                progress(done / total, f"{settings.llm_model}: {done / 1e9:.1f} / {total / 1e9:.1f} GB")
            else:
                progress(0.0, d.get("status", ""))


# ---- Voz (microseries) -------------------------------------------------------

def check_voice() -> tuple[bool, str]:
    from app.stories import voices

    return (True, "Listo") if voices.available() else (False, "No descargado (solo para microseries)")


def install_voice(progress: Progress) -> None:
    from app.stories import voices

    download(voices.MODEL_URL + voices.MODEL, voices.KOKORO_DIR / voices.MODEL, progress, "Kokoro", 0.0, 0.92)
    download(voices.MODEL_URL + voices.VOICES_BIN, voices.KOKORO_DIR / voices.VOICES_BIN, progress, "Voces", 0.92, 0.08)


# ---- Imágenes (microseries) ---------------------------------------------------

HF = "https://huggingface.co/{repo}/resolve/main/{file}"
IMAGE_FILES = [  # (repo, archivo, carpeta destino, MB aprox.)
    ("stabilityai/stable-diffusion-xl-base-1.0", "unet/diffusion_pytorch_model.fp16.safetensors", "sdxl-base", 5140),
    ("stabilityai/stable-diffusion-xl-base-1.0", "text_encoder_2/model.fp16.safetensors", "sdxl-base", 1390),
    ("stabilityai/stable-diffusion-xl-base-1.0", "text_encoder/model.fp16.safetensors", "sdxl-base", 246),
    *[("stabilityai/stable-diffusion-xl-base-1.0", f, "sdxl-base", 1) for f in (
        "model_index.json", "LICENSE.md", "scheduler/scheduler_config.json", "unet/config.json",
        "text_encoder/config.json", "text_encoder_2/config.json",
        "tokenizer/merges.txt", "tokenizer/special_tokens_map.json", "tokenizer/tokenizer_config.json", "tokenizer/vocab.json",
        "tokenizer_2/merges.txt", "tokenizer_2/special_tokens_map.json", "tokenizer_2/tokenizer_config.json",
        "tokenizer_2/vocab.json")],
    ("ByteDance/SDXL-Lightning", "sdxl_lightning_4step_lora.safetensors", "sdxl-lightning", 394),
    ("madebyollin/sdxl-vae-fp16-fix", "diffusion_pytorch_model.safetensors", "sdxl-vae-fp16-fix", 335),
    ("madebyollin/sdxl-vae-fp16-fix", "config.json", "sdxl-vae-fp16-fix", 1),
    ("openai/clip-vit-large-patch14", "model.safetensors", "clip-vit-large-patch14", 1710),
    *[("openai/clip-vit-large-patch14", f, "clip-vit-large-patch14", 1) for f in (
        "config.json", "preprocessor_config.json", "tokenizer.json", "tokenizer_config.json", "vocab.json",
        "merges.txt", "special_tokens_map.json")],
]


def check_engine() -> tuple[bool, str]:
    from app.core import pylib

    if pylib.activate():
        return True, "Listo"
    return False, "No descargado (solo para microseries)"


def install_engine(progress: Progress) -> None:
    from app.core import pylib

    pylib.install(progress, download)


def check_images() -> tuple[bool, str]:
    from app.shared.rights import image_guard
    from app.stories import images

    if images.models_present() and image_guard.available():
        return True, "Listo"
    return False, "No descargado (solo para microseries)"


def install_images(progress: Progress) -> None:
    total = sum(mb for *_, mb in IMAGE_FILES)
    done = 0
    for repo, file, folder, mb in IMAGE_FILES:
        dest = settings.models_dir / folder / file
        if not dest.exists():
            download(HF.format(repo=repo, file=file), dest, progress, Path(file).name, done / total, mb / total)
        done += mb


STORIES_COMPONENTS = {"voice", "engine", "images"}

COMPONENTS = [
    Component("cuda", "Runtime de NVIDIA CUDA (cuBLAS, cuDNN)", 1300, True, check_cuda, install_cuda),
    Component("ffmpeg", "ffmpeg con NVENC", 100, True, check_ffmpeg, install_ffmpeg),
    Component("whisper", f"Transcripción · Whisper {settings.whisper_model}", 1600, True, check_whisper, install_whisper),
    Component("vision", "Detección de caras, pose y audio (MediaPipe)", 20, True, check_vision, install_vision),
    Component("ollama", "Ollama (motor del LLM)", 0, False, check_ollama, None, OLLAMA_DOWNLOAD),
    Component("llm", f"LLM · {settings.llm_model}", 4700, False, check_llm, install_llm),
    Component("voice", "Voz narradora · Kokoro (microseries)", 350, False, check_voice, install_voice),
    Component("engine", "Motor de imágenes · PyTorch CUDA (microseries)", 2790, False, check_engine, install_engine),
    Component("images", "Imágenes · SDXL + Lightning + revisión CLIP (microseries)", 9300, False, check_images,
              install_images),
]
if not settings.stories_enabled:
    COMPONENTS = [c for c in COMPONENTS if c.id not in STORIES_COMPONENTS]


class SetupManager:
    def __init__(self) -> None:
        self.running: str | None = None
        self.progress: dict[str, dict] = {}
        self._thread: threading.Thread | None = None

    def status(self) -> dict:
        items = []
        for c in COMPONENTS:
            ok, detail = c.check()
            p = self.progress.get(c.id, {})
            items.append({"id": c.id, "label": c.label, "size_mb": c.size_mb, "required": c.required, "ok": ok,
                          "detail": p.get("message") if self.running == c.id else (p.get("error") or detail),
                          "installable": c.install is not None, "help_url": c.help_url,
                          "progress": p.get("value") if self.running == c.id else None,
                          "error": p.get("error")})
        return {"items": items, "running": self.running,
                "ready": all(i["ok"] for i in items if i["required"])}

    def install(self, ids: list[str]) -> None:
        if self._thread and self._thread.is_alive():
            raise RuntimeError("Ya hay una descarga en curso")
        todo = [c for c in COMPONENTS if c.id in ids and c.install is not None]
        self._thread = threading.Thread(target=self._run, args=(todo,), daemon=True, name="setup")
        self._thread.start()

    def _run(self, todo: list[Component]) -> None:
        from app.core.events import bus

        for c in todo:
            if c.check()[0]:
                continue
            self.running = c.id
            self.progress[c.id] = {"value": 0.0, "message": "Empezando…"}

            def cb(v: float, msg: str, cid=c.id) -> None:
                self.progress[cid] = {"value": round(min(1.0, v), 3), "message": msg}
                bus.publish("setup", id=cid, progress=self.progress[cid]["value"], message=msg)

            try:
                log.info("Preparación: instalando %s", c.id)
                c.install(cb)
                self.progress[c.id] = {"value": 1.0, "message": "Listo"}
            except Exception as e:  # noqa: BLE001
                log.exception("Preparación fallida: %s", c.id)
                self.progress[c.id] = {"value": None, "error": f"Error: {e}"}
            bus.publish("setup", id=c.id, done=True)
        self.running = None
        bus.publish("setup", done=True)


manager = SetupManager()


AUTO_INSTALL_MAX_MB = 50


def auto_install_small() -> None:
    """Al arrancar, descarga sola lo pequeño que falte (p. ej. un modelo nuevo de 4 MB tras actualizar).
    Lo grande (CUDA, Whisper, LLM) siempre lo decide el usuario en «Preparar ClipStream»."""
    def run() -> None:
        try:
            small = [c.id for c in COMPONENTS
                     if c.install is not None and c.size_mb <= AUTO_INSTALL_MAX_MB and not c.check()[0]]
            if small:
                log.info("Descarga automática de componentes pequeños: %s", small)
                manager.install(small)
        except Exception:  # noqa: BLE001
            log.exception("Descarga automática fallida")

    threading.Thread(target=run, daemon=True, name="setup-auto").start()
