"""Cola de trabajos Huey con SQLite (sin Redis), consumidor en el mismo proceso."""
from __future__ import annotations

import logging

from config.settings import settings
from huey import SqliteHuey

log = logging.getLogger(__name__)

settings.storage_dir.mkdir(parents=True, exist_ok=True)
huey = SqliteHuey("clipstream", filename=str(settings.queue_path))
_consumer = None


def start_consumer(workers: int = 3) -> None:
    """Hilos: análisis (serializado de todos modos por el gestor de GPU) + renders NVENC."""
    global _consumer
    if _consumer is not None:
        return
    import app.workers.tasks  # noqa: F401  registra las tareas

    _consumer = huey.create_consumer(workers=workers, worker_type="thread", periodic=False, check_worker_health=False)
    # Huey registra manejadores de señales; fuera del hilo principal (ventana pywebview) fallaría.
    _consumer._set_signal_handlers = lambda: None
    _consumer.start()
    log.info("Cola Huey iniciada con %d hilos", workers)


def stop_consumer() -> None:
    global _consumer
    if _consumer is not None:
        _consumer.stop(graceful=False)
        _consumer = None
