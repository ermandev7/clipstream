"""API de la preparación del primer arranque."""
from __future__ import annotations

import webbrowser

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.setup import COMPONENTS, OLLAMA_DOWNLOAD, manager

router = APIRouter(prefix="/api/setup", tags=["setup"])


@router.get("")
def status() -> dict:
    return manager.status()


class InstallIn(BaseModel):
    ids: list[str]


@router.post("/install")
def install(body: InstallIn) -> dict:
    known = {c.id for c in COMPONENTS}
    if not body.ids or any(i not in known for i in body.ids):
        raise HTTPException(400, "Componente desconocido")
    try:
        manager.install(body.ids)
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True}


@router.post("/open-ollama")
def open_ollama() -> dict:
    """Abre la página oficial de descarga de Ollama en el navegador del PC."""
    webbrowser.open(OLLAMA_DOWNLOAD)
    return {"ok": True, "url": OLLAMA_DOWNLOAD}
