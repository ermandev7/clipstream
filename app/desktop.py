"""Ventana propia de ClipStream (pywebview + WebView2). El motor FastAPI corre en un hilo."""
from __future__ import annotations

import logging
import sys
import threading
import time

import httpx

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from config.settings import settings  # noqa: E402

log = logging.getLogger("clipstream.desktop")


class JsApi:
    """Expuesto a la interfaz como window.pywebview.api."""

    def __init__(self) -> None:
        # Con "_" pywebview no lo expone a JS; si no, recorre el objeto nativo .NET hasta reventar la pila.
        self._window = None

    def pick_file(self, kind: str = "video") -> str | None:
        from app.api.system import AUDIO_TYPES, VIDEO_TYPES

        return native_dialog(self._window, AUDIO_TYPES if kind == "audio" else VIDEO_TYPES)


def native_dialog(window, file_types=("Vídeo (*.mp4;*.mov;*.mkv;*.webm;*.m4v)", "Todos los archivos (*.*)")) -> str | None:
    import webview

    if window is None:
        return None
    res = window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=False, file_types=tuple(file_types))
    return res[0] if res else None


def folder_dialog(window, start: str | None = None) -> str | None:
    import webview

    if window is None:
        return None
    res = window.create_file_dialog(webview.FileDialog.FOLDER, directory=start or "")
    if not res:
        return None
    return res[0] if isinstance(res, (list, tuple)) else res


def _serve(server) -> None:
    server.run()


def main() -> None:
    import uvicorn
    import webview

    from app import main as backend
    from app.api import system

    host = "0.0.0.0" if settings.lan_access else settings.host
    # Al cerrar la ventana no se espera indefinidamente a conexiones abiertas (WebSocket del móvil, etc.).
    config = uvicorn.Config(backend.app, host=host, port=settings.port, log_level="warning", timeout_graceful_shutdown=3)
    server = uvicorn.Server(config)
    t = threading.Thread(target=_serve, args=(server,), daemon=True, name="api")
    t.start()

    url = f"http://127.0.0.1:{settings.port}/"
    for _ in range(150):
        try:
            if httpx.get(url + "api/system/status", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    else:
        log.error("El motor no arrancó")

    if settings.lan_access and (ip := backend.lan_ip()):
        log.info("Acceso desde el móvil: http://%s:%d", ip, settings.port)

    # pywebview bloquea por defecto las descargas del navegador; los botones usan el diálogo de carpeta,
    # pero así cualquier enlace de descarga también funciona (WebView2 muestra su barra de descargas).
    webview.settings["ALLOW_DOWNLOADS"] = True
    api = JsApi()
    window = webview.create_window("ClipStream", url, js_api=api, width=1440, height=900, min_size=(960, 640),
                                   background_color="#141415")
    api._window = window
    system.native_file_dialog = lambda types: native_dialog(window, types)
    system.native_folder_dialog = lambda start=None: folder_dialog(window, start)
    try:
        webview.start(gui="edgechromium", private_mode=False)
    finally:
        server.should_exit = True
        t.join(timeout=15)


if __name__ == "__main__":
    main()
