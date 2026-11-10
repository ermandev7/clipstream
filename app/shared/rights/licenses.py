"""Control de licencias de assets/: un archivo sin licencia permitida no se usa."""
from __future__ import annotations

import json
from functools import cache
from pathlib import Path

from config.settings import ALLOWED_LICENSES, ASSETS_DIR

ASSETS = ASSETS_DIR


@cache
def _registry() -> dict:
    return json.loads((ASSETS / "licenses.json").read_text(encoding="utf-8")).get("files", {})


def license_of(path: Path) -> str | None:
    rel = path.resolve().relative_to(ASSETS.resolve()).as_posix()
    entry = _registry().get(rel)
    return entry.get("license") if entry else None


def is_allowed(path: Path) -> bool:
    return license_of(path) in ALLOWED_LICENSES


def credit_line(path: Path) -> str | None:
    """Crédito para la descripción si la licencia es CC-BY."""
    rel = path.resolve().relative_to(ASSETS.resolve()).as_posix()
    e = _registry().get(rel) or {}
    if e.get("license") == "CC-BY":
        return f"{e.get('title', path.stem)} — {e.get('author', '')} (CC-BY) {e.get('source', '')}".strip()
    return None


def entry(path: Path) -> dict | None:
    """Registro completo del archivo (título, autor, licencia, fuente)."""
    try:
        rel = path.resolve().relative_to(ASSETS.resolve()).as_posix()
    except ValueError:
        return None
    return _registry().get(rel)
