"""Хранилище диалогов + элементы списка (ConversationListItem) с итогом последней оценки."""
from __future__ import annotations

import re
import uuid
from typing import Any

from .. import db
from . import sla
from .fetcher import normalize_conversation


def upsert_many(conversations: list[dict[str, Any]]) -> int:
    with db.connect() as conn:
        conn.executemany(
            """INSERT INTO conversations (conversation_id, group_id, channel, language, data)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(conversation_id) DO UPDATE SET
                 group_id=excluded.group_id, channel=excluded.channel, language=excluded.language,
                 data=excluded.data, imported_at=datetime('now')""",
            [(c["conversation_id"], c["group_id"], c["channel"], c["language"], db.dumps(c)) for c in conversations],
        )
    return len(conversations)


def import_records(records: list[dict[str, Any]], group_id: str) -> int:
    return upsert_many([normalize_conversation(r, group_id) for r in records])


def upload(record: dict[str, Any]) -> dict[str, Any]:
    """Свой транскрипт из UI (без index/offset_sec). group_id по умолчанию — UPLOAD."""
    record = dict(record)
    record.setdefault("conversation_id", f"upload-{uuid.uuid4().hex[:8]}")
    group_id = str(record.get("group_id") or "UPLOAD").strip()
    conv = normalize_conversation(record, group_id)
    upsert_many([conv])
    return conv


def get(conversation_id: str) -> dict[str, Any] | None:
    with db.connect() as conn:
        row = conn.execute("SELECT data FROM conversations WHERE conversation_id=?", (conversation_id,)).fetchone()
    return db.loads(row["data"]) if row else None


def latest_evaluation(conversation_id: str) -> dict[str, Any] | None:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT data FROM evaluations WHERE conversation_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (conversation_id,)).fetchone()
    return db.loads(row["data"]) if row else None


def appeals_for(evaluation_id: str) -> list[dict[str, Any]]:
    with db.connect() as conn:
        rows = conn.execute("SELECT data FROM appeals WHERE evaluation_id=? ORDER BY created_at, rowid",
                            (evaluation_id,)).fetchall()
    return [db.loads(r["data"]) for r in rows]


def _sla_brief(r: dict[str, Any] | None) -> dict[str, Any] | None:
    return {"wait_sec": r["wait_sec"], "breached": r["breached"]} if r else None


def _natural_key(cid: str):
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", cid)]


def list_items(group_id: str | None = None, language: str | None = None, channel: str | None = None,
               risk: bool | None = None, status: str | None = None, q: str | None = None,
               gross: bool | None = None, needs_review: bool | None = None) -> list[dict[str, Any]]:
    """Список для экрана диалогов.

    risk=true — грубая ошибка или needs_review; gross=true — только грубые ошибки;
    needs_review=true — только требующие проверки; status — "not_evaluated" или статус оценки (done/error/…);
    q — поиск по id, имени оператора и тексту реплик.
    """
    sql, args = "SELECT data FROM conversations WHERE 1=1", []
    if group_id:
        sql += " AND group_id=?"; args.append(group_id)
    if language:
        sql += " AND upper(language)=upper(?)"; args.append(language)
    if channel:
        sql += " AND channel=?"; args.append(channel)

    with db.connect() as conn:
        convs = [db.loads(r["data"]) for r in conn.execute(sql, args).fetchall()]
        # последняя оценка по каждому диалогу одним запросом
        evals = {}
        for r in conn.execute(
            """SELECT e.conversation_id, e.data FROM evaluations e
               JOIN (SELECT conversation_id, MAX(rowid) rid FROM evaluations GROUP BY conversation_id) m
                 ON m.rid = e.rowid"""):
            evals[r["conversation_id"]] = db.loads(r["data"])
        appeal_rows = [r for r in conn.execute("SELECT evaluation_id, status, data FROM appeals").fetchall()
                       if (db.loads(r["data"]) or {}).get("author_role", "operator") == "operator"]
    appeal_status: dict[str, str] = {}
    for r in appeal_rows:
        if r["status"] == "open" or r["evaluation_id"] not in appeal_status:
            appeal_status[r["evaluation_id"]] = r["status"]

    needle = (q or "").strip().lower()
    items = []
    for c in convs:
        ev = evals.get(c["conversation_id"])
        summary = None
        if ev:
            gross_ai = ev.get("gross_error") or {}
            final = ev.get("final") or {}
            summary = {
                "evaluation_id": ev.get("evaluation_id"),
                "total": ev.get("total"),
                "final_total": final.get("total", ev.get("total")),
                "gross_error": bool(final.get("gross_error", gross_ai.get("flag"))),
                "needs_review": bool(ev.get("needs_review")),
                "checklist_version": ev.get("checklist_version"),
                "status": ev.get("status"),
                "headline": ev.get("headline"),
                "gross_types": gross_ai.get("types") or [],
                "created_at": ev.get("created_at"),
                "review": ev.get("review"),
            }
        if risk and not (summary and (summary["gross_error"] or summary["needs_review"])):
            continue
        if gross is not None and bool(summary and summary["gross_error"]) != gross:
            continue
        if needs_review is not None and bool(summary and summary["needs_review"]) != needs_review:
            continue
        if status:
            current = summary["status"] if summary else "not_evaluated"
            if current != status:
                continue
        if needle:
            hay = " ".join([c["conversation_id"], c.get("operator_name") or ""] +
                           [t["text"] for t in c["transcript"]]).lower()
            if needle not in hay:
                continue
        items.append({
            "conversation_id": c["conversation_id"],
            "group_id": c["group_id"],
            "channel": c["channel"],
            "language": c["language"],
            "turns": len(c["transcript"]),
            "duration_sec": next((t["offset_sec"] for t in reversed(c["transcript"]) if t.get("offset_sec") is not None), 0),
            "operator_name": c.get("operator_name"),
            "sla": _sla_brief(sla.first_response(c)),
            "evaluation": summary,
            "appeal_status": appeal_status.get(summary["evaluation_id"]) if summary else None,
        })
    items.sort(key=lambda x: (x["group_id"], _natural_key(x["conversation_id"])))
    return items


def groups() -> list[dict[str, Any]]:
    with db.connect() as conn:
        rows = conn.execute("SELECT group_id, COUNT(*) n FROM conversations GROUP BY group_id ORDER BY group_id")
        return [{"group_id": r["group_id"], "count": r["n"]} for r in rows]
