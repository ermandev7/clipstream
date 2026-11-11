"""SQLite: un archivo, una conexión por hilo, WAL para lecturas concurrentes."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any

from config.settings import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,                -- live | vod | local
    platform TEXT,                     -- youtube | kick | NULL
    url TEXT,
    path TEXT,
    title TEXT DEFAULT '',
    channel TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',  -- pending|downloading|analyzing|live|rendering|done|stopped|error
    progress REAL DEFAULT 0,
    stage TEXT DEFAULT '',
    duration REAL DEFAULT 0,
    was_live INTEGER DEFAULT 0,
    has_chat INTEGER DEFAULT 0,
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS clips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    start REAL NOT NULL,
    end REAL NOT NULL,
    peak REAL NOT NULL,
    score REAL DEFAULT 0,
    signal_score REAL DEFAULT 0,
    llm_score REAL,
    reason TEXT DEFAULT '',
    title TEXT DEFAULT '',
    hook TEXT DEFAULT '',
    hashtags TEXT DEFAULT '[]',
    signals TEXT DEFAULT '{}',
    signals_label TEXT DEFAULT '',
    warnings TEXT DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'pending',  -- pending|rendering|ready|error
    progress REAL DEFAULT 0,
    error TEXT,
    files TEXT DEFAULT '{}',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_clips_source ON clips(source_id);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER,
    ts REAL NOT NULL,
    level TEXT NOT NULL DEFAULT 'info',
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_events_source ON events(source_id, id);
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    clip_id INTEGER REFERENCES clips(id) ON DELETE SET NULL,
    platform TEXT NOT NULL DEFAULT 'tiktok',
    status TEXT NOT NULL DEFAULT 'queued',   -- scheduled | queued | uploading | processing | published | failed | cancelled
    settings TEXT DEFAULT '{}',              -- post_info enviado a TikTok + cuenta
    scheduled_at REAL,
    started_at REAL,
    published_at REAL,
    progress REAL DEFAULT 0,
    publish_id TEXT,
    post_id TEXT,
    error TEXT,
    created_at REAL,
    updated_at REAL
);
CREATE INDEX IF NOT EXISTS ix_posts_status ON posts(status, scheduled_at);
CREATE TABLE IF NOT EXISTS channels (
    key TEXT PRIMARY KEY,              -- plataforma:canal
    permission TEXT DEFAULT 'unknown'  -- yes | no | program | unknown
);
"""

JSON_FIELDS = {"hashtags", "signals", "warnings", "files"}
_local = threading.local()
_init_lock = threading.Lock()
_initialized = False


def conn() -> sqlite3.Connection:
    global _initialized
    c = getattr(_local, "conn", None)
    if c is None:
        settings.storage_dir.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(settings.db_path, timeout=30, isolation_level=None, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA synchronous=NORMAL")
        _local.conn = c
    if not _initialized:
        with _init_lock:
            if not _initialized:
                c.executescript(SCHEMA)
                _migrate(c)
                _initialized = True
    return c


# Columnas añadidas después de la Fase 1: (tabla, columna, definición).
MIGRATIONS = [
    ("clips", "layout", "TEXT DEFAULT ''"),           # diseño aplicado (follow, facecam, ...)
    ("clips", "layout_override", "TEXT"),            # diseño elegido a mano (None = automático)
    ("clips", "settings", "TEXT DEFAULT '{}'"),       # ajustes de edición del clip (Fase 2)
    ("sources", "preset", "TEXT DEFAULT '{}'"),       # preset de recorte del directo/canal
    ("clips", "rendered", "TEXT DEFAULT '{}'"),       # ajustes con los que se hizo el último render
    ("clips", "meta", "TEXT DEFAULT '{}'"),           # título EN, descripciones, hashtags por plataforma
    ("sources", "parent_id", "INTEGER"),              # repetición: id del directo del que sale
    ("sources", "stage_progress", "REAL DEFAULT 0"),  # % de la fase actual (la interfaz lo muestra por pasos)
    ("clips", "hidden", "INTEGER DEFAULT 0"),         # quitado de la lista de Exportar (no se borra nada)
    ("sources", "auto_publish", "INTEGER DEFAULT 0"), # modo Automático: al terminar, tanda lista para aprobar
]
JSON_FIELDS |= {"settings", "preset", "rendered", "meta"}


def _migrate(c: sqlite3.Connection) -> None:
    for table, col, ddl in MIGRATIONS:
        cols = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")


def row_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for k in JSON_FIELDS & d.keys():
        try:
            d[k] = json.loads(d[k]) if d[k] else ([] if k in ("hashtags", "warnings") else {})
        except (TypeError, json.JSONDecodeError):
            pass
    return d


def _enc(v: Any) -> Any:
    return json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v


def insert(table: str, **fields) -> int:
    now = time.time()
    if table == "sources":
        fields.setdefault("created_at", now)
        fields.setdefault("updated_at", now)
    elif table == "clips":
        fields.setdefault("created_at", now)
    cols = ", ".join(fields)
    qs = ", ".join("?" for _ in fields)
    cur = conn().execute(f"INSERT INTO {table} ({cols}) VALUES ({qs})", [_enc(v) for v in fields.values()])
    return int(cur.lastrowid)


def update(table: str, id_: int, **fields) -> None:
    if table == "sources":
        fields["updated_at"] = time.time()
    sets = ", ".join(f"{k}=?" for k in fields)
    conn().execute(f"UPDATE {table} SET {sets} WHERE id=?", [*(_enc(v) for v in fields.values()), id_])


def get(table: str, id_: int) -> dict | None:
    return row_dict(conn().execute(f"SELECT * FROM {table} WHERE id=?", (id_,)).fetchone())


def query(sql: str, params: tuple = ()) -> list[dict]:
    return [row_dict(r) for r in conn().execute(sql, params).fetchall()]


def channel_permission(platform: str, channel: str) -> str:
    r = conn().execute("SELECT permission FROM channels WHERE key=?", (f"{platform}:{channel}",)).fetchone()
    return r["permission"] if r else "unknown"


def set_channel_permission(platform: str, channel: str, permission: str) -> None:
    conn().execute("INSERT INTO channels(key, permission) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET permission=excluded.permission",
                   (f"{platform}:{channel}", permission))
