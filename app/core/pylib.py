"""Motor de imágenes descargable: PyTorch CUDA + diffusers/transformers en %LOCALAPPDATA%\\ClipStream\\runtime\\pylib.

Solo en la versión instalada: en desarrollo esos paquetes están en el venv. Se activa añadiendo la carpeta
al final de sys.path (lo empaquetado en el .exe tiene prioridad; ver installer/runtime_deps.py).
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import logging
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Callable

from config.settings import DATA_DIR, FROZEN, ROOT

log = logging.getLogger(__name__)
PYLIB = DATA_DIR / "runtime" / "pylib"
WHEELS = DATA_DIR / "runtime" / "wheels"
STAMP = PYLIB / ".clipstream_complete"


def manifest() -> dict:
    for p in (ROOT / "pylib_manifest.json", ROOT / "installer" / "pylib_manifest.json"):
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    return {"wheels": [], "packages": [], "total_mb": 0}


def _signature() -> str:
    """Las versiones exactas que espera este .exe: si cambian en una actualización, se vuelve a descargar."""
    return ",".join(f"{w['name']}=={w['version']}" for w in manifest()["wheels"])


def installed() -> bool:
    if not FROZEN:
        return all(importlib.util.find_spec(m) is not None for m in ("torch", "diffusers", "transformers"))
    return STAMP.exists() and STAMP.read_text(encoding="utf-8") == _signature()


def activate() -> bool:
    """Hace importable el motor descargado. Devuelve si está disponible."""
    if not FROZEN:
        return installed()
    if not installed():
        return False
    if str(PYLIB) not in sys.path:
        sys.path.append(str(PYLIB))
        importlib.invalidate_caches()
    return True


def _extract(wheel: Path, dest: Path) -> None:
    """Una rueda es un zip: se descomprime tal cual (el contenido de *.data/purelib|platlib va a la raíz)."""
    with zipfile.ZipFile(wheel) as z:
        for info in z.infolist():
            name = info.filename
            parts = name.split("/")
            if parts[0].endswith(".data"):
                if len(parts) > 2 and parts[1] in ("purelib", "platlib"):
                    name = "/".join(parts[2:])
                else:
                    continue  # scripts y cabeceras: no hacen falta
            if name.endswith("/"):
                continue
            target = dest / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out, 1 << 20)


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def install(progress: Callable[[float, str], None], download: Callable) -> None:
    wheels = manifest()["wheels"]
    total = sum(w["size"] for w in wheels) or 1
    if PYLIB.exists():
        shutil.rmtree(PYLIB)  # versión anterior o instalación a medias: se empieza limpio
    PYLIB.mkdir(parents=True)
    done = 0
    for w in wheels:
        fn = Path(w["url"].split("/")[-1].replace("%2B", "+"))
        whl = WHEELS / fn.name
        span = w["size"] / total
        if not (whl.exists() and whl.stat().st_size == w["size"]):
            download(w["url"], whl, progress, w["name"], done / total, span * 0.92)
        if w.get("sha256") and _sha256(whl) != w["sha256"]:
            whl.unlink(missing_ok=True)
            raise RuntimeError(f"La descarga de {w['name']} está dañada (suma de comprobación): vuelve a intentarlo")
        progress(done / total + span * 0.93, f"Instalando {w['name']} {w['version']}")
        _extract(whl, PYLIB)
        whl.unlink(missing_ok=True)  # el zip ya no hace falta (2,7 GB menos en disco)
        done += w["size"]
    STAMP.write_text(_signature(), encoding="utf-8")
    shutil.rmtree(WHEELS, ignore_errors=True)
    activate()
    log.info("Motor de imágenes instalado en %s", PYLIB)
