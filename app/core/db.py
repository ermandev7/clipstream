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

-- Microseries (Fase 3) ------------------------------------------------------
CREATE TABLE IF NOT EXISTS series (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    template TEXT NOT NULL,            -- biblicas | mitologia | fabulas | leyendas | frutinovela
    mode TEXT NOT NULL DEFAULT 'narrated',  -- narrated | dramatized (Fase 4)
    languages TEXT DEFAULT '["es"]',
    story_id TEXT,                     -- entrada del catálogo de dominio público (None en frutinovela)
    synopsis TEXT DEFAULT '',
    bible TEXT DEFAULT '{}',           -- estilo visual, tono, personajes clave
    rights TEXT DEFAULT '{}',          -- resultado del guardián a nivel de serie
    status TEXT NOT NULL DEFAULT 'draft',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS characters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    series_id INTEGER NOT NULL REFERENCES series(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    role TEXT DEFAULT '',
    description TEXT DEFAULT '',
    visual TEXT DEFAULT '',            -- descripción visual fija (consistencia entre imágenes)
    voice TEXT DEFAULT '',
    files TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_characters_series ON characters(series_id);
CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    series_id INTEGER NOT NULL REFERENCES series(id) ON DELETE CASCADE,
    number INTEGER NOT NULL,
    title TEXT DEFAULT '',
    summary TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft',   -- draft | scripting | script_ready | blocked | generating | ready | error
    stage TEXT DEFAULT '',
    phases TEXT DEFAULT '{}',          -- guion / personajes / imágenes / voz / montaje -> estado
    rights TEXT DEFAULT '{}',          -- comprobaciones del guardián
    files TEXT DEFAULT '{}',
    error TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_episodes_series ON episodes(series_id, number);
CREATE TABLE IF NOT EXISTS scenes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    idx INTEGER NOT NULL,
    narration_es TEXT DEFAULT '',
    narration_en TEXT DEFAULT '',
    image_prompt TEXT DEFAULT '',
    characters TEXT DEFAULT '[]',
    duration REAL DEFAULT 6,
    status TEXT NOT NULL DEFAULT 'pending', -- pending | generating | review | approved | blocked
    block_reason TEXT,
    suggestion TEXT,
    files TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_scenes_episode ON scenes(episode_id, idx);
"""

JSON_FIELDS = {"hashtags", "signals", "warnings", "files", "languages", "bible", "rights", "phases", "characters"}
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
    ("characters", "kind", "TEXT DEFAULT ''"),        # qué es, en inglés (lion, young man): para SDXL y CLIP
    ("sources", "stage_progress", "REAL DEFAULT 0"),  # % de la fase actual (la interfaz lo muestra por pasos)
    ("clips", "hidden", "INTEGER DEFAULT 0"),         # quitado de la lista de Exportar (no se borra nada)
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
    if table in ("sources", "series"):
        fields.setdefault("created_at", now)
        fields.setdefault("updated_at", now)
    elif table in ("clips", "episodes"):
        fields.setdefault("created_at", now)
    cols = ", ".join(fields)
    qs = ", ".join("?" for _ in fields)
    cur = conn().execute(f"INSERT INTO {table} ({cols}) VALUES ({qs})", [_enc(v) for v in fields.values()])
    return int(cur.lastrowid)


def update(table: str, id_: int, **fields) -> None:
    if table in ("sources", "series"):
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
