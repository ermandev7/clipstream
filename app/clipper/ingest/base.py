from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

ProgressCb = Callable[[float, str], None]  # (0-1, mensaje)


@dataclass
class MediaSource:
    """Resultado de ingerir una fuente completa (vídeo publicado o archivo local)."""

    path: Path
    title: str
    channel: str = ""
    duration: float = 0.0
    url: str | None = None
    was_live: bool = False
    meta: dict = field(default_factory=dict)


class StreamSource:
    """Interfaz para fuentes en directo: producen segmentos en un buffer circular."""

    def start(self) -> None:  # pragma: no cover - interfaz
        raise NotImplementedError

    def stop(self) -> None:  # pragma: no cover - interfaz
        raise NotImplementedError

    @property
    def running(self) -> bool:  # pragma: no cover - interfaz
        raise NotImplementedError
