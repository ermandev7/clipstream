"""Motor de imágenes descargable (PyTorch + diffusers…): qué se descarga y qué va dentro del .exe.

PyTorch con CUDA pesa 2,75 GB: no cabe en el instalador. Se descarga en el primer arranque como
ruedas (wheels) exactas del entorno de desarrollo y se descomprime en %LOCALAPPDATA%\\ClipStream\\runtime\\pylib.

Una dependencia que usan a la vez el .exe y el motor descargable (numpy, huggingface_hub, PIL…) va
entera dentro del .exe (collect_all): si PyInstaller solo empaquetara los submódulos que usa la app,
el motor fallaría al importar los demás, porque el paquete empaquetado tapa al descargado.

Uso (en la compilación, con red):
    .venv\\Scripts\\python installer\\runtime_deps.py   ->  escribe installer\\pylib_manifest.json
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from importlib import metadata
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = Path(__file__).with_name("pylib_manifest.json")
RUNTIME_ROOTS = ["torch", "diffusers", "transformers", "accelerate", "peft"]
TORCH_INDEX = "https://download.pytorch.org/whl/cu128/"
TAG = f"cp{sys.version_info.major}{sys.version_info.minor}"
# Ya van dentro del .exe por otras dependencias: se empaquetan enteras en vez de descargarse.
ALSO_BUNDLED = {"pygments", "setuptools"}


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def closure(roots: list[str]) -> set[str]:
    env = default_environment()
    seen: set[str] = set()
    todo = [_norm(r) for r in roots]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        seen.add(name)
        for req in dist.requires or []:
            r = Requirement(req)
            if r.marker and not r.marker.evaluate({**env, "extra": ""}):
                continue
            todo.append(_norm(r.name))
    return seen


def app_roots() -> list[str]:
    """Dependencias directas de la app (pyproject) sin las del motor de imágenes."""
    import tomllib

    deps = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    names = [_norm(Requirement(d).name) for d in deps]
    return [n for n in names if n not in {_norm(r) for r in RUNTIME_ROOTS}]


def top_level(dist_name: str) -> list[str]:
    dist = metadata.distribution(dist_name)
    txt = dist.read_text("top_level.txt")
    if txt:
        return [x.strip() for x in txt.split() if x.strip() and not x.startswith("_")]
    names = set()
    for f in dist.files or []:
        parts = f.parts
        if len(parts) > 1 and not parts[0].endswith((".dist-info", ".data")) and parts[0] != "..":
            names.add(parts[0])
        elif len(parts) == 1 and parts[0].endswith(".py"):
            names.add(parts[0][:-3])
    return sorted(n for n in names if n.isidentifier() and not n.startswith("__"))


def split() -> tuple[list[str], list[str]]:
    """(distribuciones a descargar, distribuciones compartidas que el .exe debe llevar enteras)."""
    runtime = closure(RUNTIME_ROOTS)
    app = closure(app_roots()) | ALSO_BUNDLED
    return sorted(runtime - app), sorted(runtime & app)


def shared_packages() -> list[str]:
    _, shared = split()
    return sorted({t for d in shared for t in top_level(d)})


def runtime_packages() -> list[str]:
    down, _ = split()
    return sorted({t for d in down for t in top_level(d)})


def _wheel(name: str, version: str) -> dict:
    if _norm(name) == "torch":
        fn = f"torch-{version.replace('+', '%2B')}-{TAG}-{TAG}-win_amd64.whl"
        url = TORCH_INDEX + fn
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=30) as r:
            size = int(r.headers["Content-Length"])
        return {"name": name, "version": version, "url": url, "size": size, "sha256": None}
    with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=30) as r:
        files = json.load(r)["urls"]
    wheels = [f for f in files if f["packagetype"] == "bdist_wheel"]
    pick = next((f for f in wheels if f["filename"].endswith("-none-any.whl")), None) or \
        next((f for f in wheels if f"-{TAG}-" in f["filename"] and "win_amd64" in f["filename"]), None) or \
        next((f for f in wheels if "abi3" in f["filename"] and "win_amd64" in f["filename"]), None)
    if not pick:
        raise RuntimeError(f"Sin rueda para Windows de {name} {version}")
    return {"name": name, "version": version, "url": pick["url"], "size": pick["size"], "sha256": pick["digests"]["sha256"]}


def write_manifest() -> dict:
    down, shared = split()
    wheels = [_wheel(metadata.distribution(d).metadata["Name"], metadata.version(d)) for d in down]
    data = {"python": TAG, "wheels": wheels, "packages": runtime_packages(),
            "total_mb": round(sum(w["size"] for w in wheels) / 1e6)}
    MANIFEST.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return {"descargar": down, "compartidas": shared, "total_mb": data["total_mb"]}


if __name__ == "__main__":
    print(json.dumps(write_manifest(), indent=2))
