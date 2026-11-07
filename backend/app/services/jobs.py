"""Пакетная оценка в фоне: задача в таблице jobs, прогресс done/failed, параллельно LLM_CONCURRENCY диалогов."""
from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .. import db
from . import checklists as checklist_store
from . import conversations as conversation_store
from .evaluator import EvaluationError, evaluate
from .settings import runtime_settings


class JobError(ValueError):
    pass


def _update(job_id: str, **fields) -> None:
    sets = ", ".join(f"{k}=?" for k in fields)
    with db.connect() as conn:
        conn.execute(f"UPDATE jobs SET {sets} WHERE job_id=?", (*fields.values(), job_id))


def _increment(job_id: str, column: str) -> None:
    with db.connect() as conn:
        conn.execute(f"UPDATE jobs SET {column} = {column} + 1 WHERE job_id=?", (job_id,))


def get(job_id: str) -> dict[str, Any] | None:
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    if not row:
        return None
    data = db.loads(row["data"]) or {}
    return {"job_id": row["job_id"], "status": row["status"], "total": row["total"], "done": row["done"],
            "failed": row["failed"], "created_at": row["created_at"], **data}


def list_recent(limit: int = 20) -> list[dict[str, Any]]:
    with db.connect() as conn:
        ids = [r["job_id"] for r in conn.execute("SELECT job_id FROM jobs ORDER BY created_at DESC, rowid DESC LIMIT ?",
                                                 (limit,))]
    return [get(i) for i in ids]


def mark_interrupted() -> None:
    """После перезапуска сервера незавершённые задачи уже не выполняются."""
    with db.connect() as conn:
        conn.execute("UPDATE jobs SET status='interrupted' WHERE status IN ('queued', 'running')")


def start(checklist_id: str = "default", version: int | None = None, group_id: str | None = None,
          conversation_ids: list[str] | None = None, run_async: bool = True) -> dict[str, Any]:
    checklist = checklist_store.get(checklist_id, version)
    if checklist is None:
        raise JobError(f"Чек-лист {checklist_id} (версия {version or 'активная'}) не найден")
    if conversation_ids:
        ids = [c for c in conversation_ids if conversation_store.get(c)]
        missing = sorted(set(conversation_ids) - set(ids))
        if missing:
            raise JobError(f"Диалоги не найдены: {', '.join(missing[:10])}")
    else:
        ids = [i["conversation_id"] for i in conversation_store.list_items(group_id=group_id)]
    if not ids:
        raise JobError("Нет диалогов для оценки" + (f" в группе {group_id}" if group_id else ""))

    job_id = f"job_{uuid.uuid4().hex[:10]}"
    meta = {"group_id": group_id, "checklist_id": checklist["checklist_id"],
            "checklist_version": checklist["version"], "conversation_ids": ids, "errors": {},
            "evaluation_ids": {}, "cost_usd": 0.0}
    with db.connect() as conn:
        conn.execute("INSERT INTO jobs (job_id, status, total, data) VALUES (?, 'queued', ?, ?)",
                     (job_id, len(ids), db.dumps(meta)))

    def run():
        _update(job_id, status="running")
        lock = threading.Lock()

        def one(cid):
            try:
                ev = evaluate(cid, checklist["checklist_id"], checklist["version"])
                with lock:
                    meta["evaluation_ids"][cid] = ev["evaluation_id"]
                    meta["cost_usd"] = round(meta["cost_usd"] + (ev.get("usage") or {}).get("cost_usd", 0), 6)
                    _update(job_id, data=db.dumps(meta))
                _increment(job_id, "done")
            except EvaluationError as e:
                with lock:
                    meta["errors"][cid] = str(e)
                    _update(job_id, data=db.dumps(meta))
                _increment(job_id, "failed")

        with ThreadPoolExecutor(runtime_settings()["llm_concurrency"]) as ex:
            list(ex.map(one, ids))
        job = get(job_id)
        _update(job_id, status="done" if job["failed"] == 0 else ("error" if job["done"] == 0 else "done_with_errors"))

    if run_async:
        threading.Thread(target=run, name=job_id, daemon=True).start()
    else:
        run()
    return get(job_id)
