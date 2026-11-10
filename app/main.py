"""Servidor FastAPI de ClipStream: API, WebSocket y la interfaz compilada."""
from __future__ import annotations

import asyncio
import logging
import socket
from contextlib import asynccontextmanager
from pathlib import Path

from config.settings import ROOT, settings
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import clips, editor, publish, setup, sources, stories, system, ws
from app.core import db
from app.core.events import bus

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("faster_whisper").setLevel(logging.WARNING)
log = logging.getLogger("clipstream")
FRONTEND = ROOT / "frontend" / "dist"


def _recover_interrupted() -> None:
    """Fuentes/clips que quedaron a medias si la app se cerró en mitad de un trabajo."""
    db.conn().execute("UPDATE sources SET status='stopped', stage='Interrumpido' WHERE status IN ('live')")
    db.conn().execute("UPDATE sources SET status='error', error='La aplicación se cerró durante el análisis' "
                      "WHERE status IN ('downloading','analyzing')")
    db.conn().execute("UPDATE clips SET status='pending', progress=0 WHERE status='rendering'")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    bus.bind_loop(asyncio.get_running_loop())
    _recover_interrupted()
    from app.shared.llm import ollama

    await asyncio.to_thread(ollama.unload_all)  # VRAM limpia aunque la sesión anterior muriera
    from app.core.setup import auto_install_small

    auto_install_small()  # modelos pequeños nuevos tras una actualización: sin bloquear la interfaz
    from app.core.queue import huey, start_consumer, stop_consumer
    from app.workers.tasks import analyze_media_task, render_clip_task

    # La base de datos manda: se vacía la cola y se reencola lo pendiente (sin duplicados).
    huey.flush()
    for s in db.query("SELECT id FROM sources WHERE status='pending' AND kind IN ('vod','local')"):
        analyze_media_task(s["id"])
    for c in db.query("SELECT id FROM clips WHERE status='pending'"):
        render_clip_task(c["id"])
    start_consumer()
    from app.publish.service import start_scheduler, stop_scheduler

    start_scheduler()  # publicaciones programadas
    yield
    stop_scheduler()
    from app.clipper.live import stop_all
    from app.core.gpu import gpu

    stop_all()
    stop_consumer()
    gpu.release()


app = FastAPI(title="ClipStream", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(sources.router)
app.include_router(clips.router)
app.include_router(editor.router)
app.include_router(setup.router)
app.include_router(publish.router)
def _stories_enabled() -> None:
    """Microseries desactivadas: la API existe pero responde 404 (se reactivan con CLIPSTREAM_STORIES_ENABLED=1)."""
    if not settings.stories_enabled:
        raise HTTPException(404, "Las microseries están desactivadas")


app.include_router(stories.router, dependencies=[Depends(_stories_enabled)])
app.include_router(system.router)
app.include_router(ws.router)


@app.exception_handler(Exception)
async def unhandled(_req, exc: Exception):
    log.exception("Error no controlado")
    return JSONResponse({"detail": f"Error interno: {exc}"}, status_code=500)


if FRONTEND.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        f = FRONTEND / full_path
        if full_path and f.is_file() and FRONTEND in f.resolve().parents:
            return FileResponse(f)
        # Sin caché: tras recompilar la interfaz, la ventana carga siempre los assets nuevos.
        return FileResponse(FRONTEND / "index.html", headers={"Cache-Control": "no-cache"})


def lan_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return None


def run(host: str | None = None, port: int | None = None) -> None:
    import uvicorn

    host = host or ("0.0.0.0" if settings.lan_access else settings.host)
    port = port or settings.port
    if settings.lan_access and (ip := lan_ip()):
        log.info("Acceso desde el móvil: http://%s:%d", ip, port)
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    run()
