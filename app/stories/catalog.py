"""Catálogo de historias de dominio público y plantillas de microseries."""
from __future__ import annotations

import json
import unicodedata
from functools import cache
from pathlib import Path

CATALOG_PATH = Path(__file__).with_name("catalog.json")


@cache
def _data() -> dict:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def templates() -> dict[str, dict]:
    return _data()["templates"]


def stories(template: str | None = None) -> list[dict]:
    return [s for s in _data()["stories"] if template is None or s["template"] == template]


def get(story_id: str | None) -> dict | None:
    return next((s for s in _data()["stories"] if s["id"] == story_id), None)


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if c.isalnum() or c == " ").strip()


def find_by_title(title: str) -> dict | None:
    """Coincidencia por título (ignorando tildes y mayúsculas)."""
    t = _norm(title)
    for s in _data()["stories"]:
        if _norm(s["title"]) == t or t and t in _norm(s["title"]):
            return s
    return None
