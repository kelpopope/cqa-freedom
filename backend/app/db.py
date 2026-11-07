"""SQLite-хранилище. Сущности хранятся целиком как JSON (`data`) + индексируемые колонки для фильтров."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .config import CHECKLISTS_DIR, get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY,
    group_id        TEXT NOT NULL,
    channel         TEXT,
    language        TEXT,
    data            TEXT NOT NULL,          -- Conversation (API_CONTRACT §1)
    imported_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_conv_group ON conversations(group_id);

CREATE TABLE IF NOT EXISTS checklists (
    checklist_id TEXT NOT NULL,
    version      INTEGER NOT NULL,
    is_active    INTEGER NOT NULL DEFAULT 0,
    data         TEXT NOT NULL,             -- Checklist
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (checklist_id, version)
);

CREATE TABLE IF NOT EXISTS evaluations (
    evaluation_id     TEXT PRIMARY KEY,
    conversation_id   TEXT NOT NULL,
    checklist_id      TEXT NOT NULL,
    checklist_version INTEGER NOT NULL,
    status            TEXT NOT NULL,
    data              TEXT NOT NULL,        -- Evaluation
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_eval_conv ON evaluations(conversation_id, created_at);

CREATE TABLE IF NOT EXISTS appeals (
    appeal_id       TEXT PRIMARY KEY,
    evaluation_id   TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    criterion_id    TEXT NOT NULL,
    status          TEXT NOT NULL,
    data            TEXT NOT NULL,          -- Appeal
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_appeal_eval ON appeals(evaluation_id);

CREATE TABLE IF NOT EXISTS jobs (
    job_id     TEXT PRIMARY KEY,
    status     TEXT NOT NULL,
    total      INTEGER NOT NULL DEFAULT 0,
    done       INTEGER NOT NULL DEFAULT 0,
    failed     INTEGER NOT NULL DEFAULT 0,
    data       TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _db_path() -> Path:
    return get_settings().db_path


@contextmanager
def connect(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """Короткоживущее соединение на операцию: безопасно для потоков FastAPI и фоновых задач."""
    conn = sqlite3.connect(path or _db_path(), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def loads(text: str | None) -> Any:
    return None if text is None else json.loads(text)


def init_db(path: Path | None = None) -> None:
    with connect(path) as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
    seed_default_checklist(path)


def seed_default_checklist(path: Path | None = None) -> None:
    """Если чек-листов нет — загрузить data/checklists/default.json как версию 1 (активную)."""
    src = CHECKLISTS_DIR / "default.json"
    if not src.exists():
        return
    with connect(path) as conn:
        if conn.execute("SELECT 1 FROM checklists LIMIT 1").fetchone():
            return
        data = json.loads(src.read_text(encoding="utf-8"))
        data["version"] = 1
        data["is_active"] = True
        conn.execute(
            "INSERT INTO checklists (checklist_id, version, is_active, data) VALUES (?, 1, 1, ?)",
            (data["checklist_id"], dumps(data)),
        )
