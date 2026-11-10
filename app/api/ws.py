from __future__ import annotations

import asyncio
import contextlib

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.events import bus
from app.core.gpu import gpu

router = APIRouter()


@router.websocket("/ws")
async def ws(socket: WebSocket) -> None:
    await socket.accept()
    q = bus.subscribe()

    async def gpu_ticker() -> None:
        while True:
            await socket.send_json({"type": "gpu", "gpu": await asyncio.to_thread(gpu.status)})
            await asyncio.sleep(2)

    ticker = asyncio.create_task(gpu_ticker())
    try:
        while True:
            msg = await q.get()
            await socket.send_json(msg)
    except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):  # cancelación = cierre de la app
        pass
    finally:
        ticker.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await ticker
        bus.unsubscribe(q)
