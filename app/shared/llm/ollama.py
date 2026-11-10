"""Cliente mínimo de Ollama. El modelo se carga/descarga por turnos vía el gestor de GPU."""
from __future__ import annotations

import json
import logging
import re

import httpx
from config.settings import settings

from app.core.gpu import gpu

log = logging.getLogger(__name__)
TIMEOUT = httpx.Timeout(180.0, connect=5.0)


def _post(path: str, payload: dict) -> dict:
    r = httpx.post(f"{settings.ollama_url}{path}", json=payload, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


# Tiempo máximo que Ollama mantiene el modelo si ClipStream muere sin liberarlo.
# Nunca -1: si la app se cierra a la fuerza, Ollama retendría ~4,7 GB de VRAM para siempre.
KEEP_ALIVE = "5m"


def _load():
    # Petición vacía = cargar el modelo en VRAM; el gestor de GPU lo libera explícitamente.
    _post("/api/generate", {"model": settings.llm_model, "keep_alive": KEEP_ALIVE})
    return settings.llm_model


def _unload(_model) -> None:
    _post("/api/generate", {"model": settings.llm_model, "keep_alive": 0})


def unload_all() -> None:
    """Al arrancar: libera nuestro modelo si una ejecución anterior lo dejó cargado en Ollama.
    Los modelos de otras aplicaciones no se tocan; solo se avisa porque restan VRAM."""
    try:
        ps = httpx.get(f"{settings.ollama_url}/api/ps", timeout=3).json()
        for m in ps.get("models", []):
            if m["name"].startswith(settings.llm_model):
                _post("/api/generate", {"model": m["name"], "keep_alive": 0})
                log.info("Ollama: liberado %s que seguía en VRAM", m["name"])
            else:
                log.warning("Ollama tiene cargado %s (%.1f GB de VRAM) de otra aplicación",
                            m["name"], m.get("size_vram", 0) / 1024**3)
    except Exception:  # noqa: BLE001
        pass


gpu.register("llm", _load, _unload)


def available() -> bool:
    try:
        tags = httpx.get(f"{settings.ollama_url}/api/tags", timeout=3).json()
        return any(m["name"].startswith(settings.llm_model) for m in tags.get("models", []))
    except Exception:  # noqa: BLE001
        return False


def generate_json(system: str, prompt: str, temperature: float = 0.3, num_ctx: int = 4096) -> dict:
    """Genera y parsea JSON. Lanza ValueError si el modelo no devuelve JSON válido."""
    with gpu.use("llm") as model:
        data = _post("/api/generate", {
            "model": model,
            "system": system,
            "prompt": prompt,
            "format": "json",
            "stream": False,
            "keep_alive": KEEP_ALIVE,
            "options": {"temperature": temperature, "num_ctx": num_ctx},
        })
    text = data.get("response", "")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise ValueError(f"Respuesta del LLM no es JSON: {text[:200]}") from None
