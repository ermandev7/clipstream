from __future__ import annotations

import asyncio
import threading

import httpx
from config.settings import settings
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.gpu import gpu

router = APIRouter(prefix="/api/system", tags=["system"])

# La ventana pywebview registra aquí su diálogo nativo (ver app/desktop.py).
native_file_dialog = None
native_folder_dialog = None  # lo pone la ventana de escritorio (app/desktop.py)
VIDEO_TYPES = ("Vídeo (*.mp4;*.mov;*.mkv;*.webm;*.m4v)", "Todos los archivos (*.*)")
AUDIO_TYPES = ("Audio (*.mp3;*.wav;*.ogg;*.m4a;*.flac)", "Todos los archivos (*.*)")


@router.get("/status")
def status() -> dict:
    ollama_ok = False
    try:
        tags = httpx.get(f"{settings.ollama_url}/api/tags", timeout=2).json()
        ollama_ok = any(m["name"].startswith(settings.llm_model) for m in tags.get("models", []))
    except Exception:  # noqa: BLE001
        pass
    return {"gpu": gpu.status(), "ollama": ollama_ok, "llm_model": settings.llm_model,
            "whisper_model": settings.whisper_model, "lan_access": settings.lan_access}


def _tk_dialog(kind: str = "video") -> str | None:
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        if kind == "audio":
            types = [("Audio", "*.mp3 *.wav *.ogg *.m4a *.flac")]
        else:
            types = [("Vídeo", "*.mp4 *.mov *.mkv *.webm *.m4v")]
        title = "Abrir audio" if kind == "audio" else "Abrir vídeo"
        return filedialog.askopenfilename(title=title, filetypes=[*types, ("Todos", "*.*")]) or None
    finally:
        root.destroy()


class FolderIn(BaseModel):
    start: str | None = None


@router.post("/pick-folder")
async def pick_folder(body: FolderIn) -> dict:
    """Diálogo «Elegir carpeta» de Windows, para guardar los clips."""
    if native_folder_dialog is not None:
        path = await asyncio.to_thread(native_folder_dialog, body.start)
    else:
        result: dict = {}

        def run() -> None:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            result["p"] = filedialog.askdirectory(initialdir=body.start or None, title="Guardar clips en…") or None
            root.destroy()

        t = threading.Thread(target=run, daemon=True)
        t.start()
        await asyncio.to_thread(t.join)
        path = result.get("p")
    return {"path": path}


class OpenFolderIn(BaseModel):
    path: str


@router.post("/open-folder")
def open_folder(body: OpenFolderIn) -> dict:
    """Abre la carpeta en el Explorador de Windows (solo carpetas que existen)."""
    import os
    import sys
    from pathlib import Path

    p = Path(body.path)
    if not p.is_dir():
        raise HTTPException(404, "La carpeta no existe")
    if sys.platform == "win32":
        os.startfile(str(p))  # noqa: S606
    return {"ok": True}


@router.post("/pick-file")
async def pick_file(kind: str = "video") -> dict:
    """Diálogo «Abrir ubicación…» de Windows (se abre en el PC donde corre ClipStream)."""
    if native_file_dialog is not None:
        path = await asyncio.to_thread(native_file_dialog, AUDIO_TYPES if kind == "audio" else VIDEO_TYPES)
    else:
        result: dict = {}
        t = threading.Thread(target=lambda: result.setdefault("p", _tk_dialog(kind)), daemon=True)
        t.start()
        await asyncio.to_thread(t.join)
        path = result.get("p")
    return {"path": path}
