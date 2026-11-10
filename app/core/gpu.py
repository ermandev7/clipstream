"""Gestor de VRAM.

La RTX 3060 tiene 8 GB: Whisper (~2-3 GB) y el LLM (~5 GB) no caben juntos con
holgura. Este gestor garantiza que solo un modelo pesado esté cargado a la vez y
serializa su uso. Todo el motor corre en un único proceso, así que un lock de
hilo es suficiente.

Uso:
    with gpu.use("whisper") as model:
        model.transcribe(...)
    with gpu.use("llm"):
        ollama.generate(...)
"""
from __future__ import annotations

import gc
import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator

log = logging.getLogger(__name__)


@dataclass
class VramInfo:
    name: str
    used_gb: float
    total_gb: float
    available: bool = True

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "used_gb": round(self.used_gb, 1),
            "total_gb": round(self.total_gb, 1),
            "available": self.available,
        }


@dataclass
class ModelSlot:
    """Modelo pesado gestionado: cómo cargarlo y cómo liberarlo."""

    loader: Callable[[], Any]
    unloader: Callable[[Any], None] | None = None


class GpuManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._slots: dict[str, ModelSlot] = {}
        self._active_name: str | None = None
        self._active_obj: Any = None
        self._busy_since: float | None = None
        self._nvml = None
        self._handle = None
        self._init_nvml()

    # ---- registro -------------------------------------------------------
    def register(self, name: str, loader: Callable[[], Any], unloader: Callable[[Any], None] | None = None) -> None:
        self._slots[name] = ModelSlot(loader, unloader)

    # ---- uso ------------------------------------------------------------
    @contextmanager
    def use(self, name: str) -> Iterator[Any]:
        if name not in self._slots:
            raise KeyError(f"Modelo no registrado en el gestor de GPU: {name}")
        with self._lock:
            if self._active_name != name:
                self._unload_active()
                log.info("GPU: cargando %s", name)
                t0 = time.perf_counter()
                self._active_obj = self._slots[name].loader()
                self._active_name = name
                log.info("GPU: %s cargado en %.1f s", name, time.perf_counter() - t0)
            self._busy_since = time.time()
            try:
                yield self._active_obj
            finally:
                self._busy_since = None

    def release(self) -> None:
        """Libera el modelo activo (p. ej. al cerrar la app o tras inactividad)."""
        with self._lock:
            self._unload_active()

    @property
    def active(self) -> str | None:
        return self._active_name

    def _unload_active(self) -> None:
        if self._active_name is None:
            return
        name, obj = self._active_name, self._active_obj
        log.info("GPU: liberando %s", name)
        slot = self._slots.get(name)
        if slot and slot.unloader:
            try:
                slot.unloader(obj)
            except Exception:  # noqa: BLE001 - liberar nunca debe tumbar el pipeline
                log.exception("Error liberando %s", name)
        self._active_name = None
        self._active_obj = None
        del obj
        gc.collect()

    # ---- VRAM -----------------------------------------------------------
    def _init_nvml(self) -> None:
        try:
            import pynvml

            pynvml.nvmlInit()
            self._nvml = pynvml
            self._handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        except Exception:  # noqa: BLE001
            log.warning("NVML no disponible: no se podrá leer la VRAM")

    def vram(self) -> VramInfo:
        if self._nvml is None:
            return VramInfo(name="GPU no detectada", used_gb=0.0, total_gb=0.0, available=False)
        mem = self._nvml.nvmlDeviceGetMemoryInfo(self._handle)
        name = self._nvml.nvmlDeviceGetName(self._handle)
        if isinstance(name, bytes):
            name = name.decode()
        name = name.replace("NVIDIA ", "").replace("GeForce ", "")
        return VramInfo(name=name, used_gb=mem.used / 1024**3, total_gb=mem.total / 1024**3)

    def status(self) -> dict:
        return {**self.vram().as_dict(), "active_model": self._active_name, "busy": self._busy_since is not None}


gpu = GpuManager()
