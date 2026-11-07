"""Апелляции: оператор оспаривает критерий → проверяющий подтверждает или меняет → пересчёт финала.

ИИ-оценка (evaluation.criteria / total) не меняется никогда — изменения живут в evaluation.final.overrides,
поэтому всегда видны «исходная оценка → решение → финал».
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from .. import db
from . import checklists as checklist_store
from . import conversations as conversation_store
from .evaluator import ASTANA, get_evaluation, recompute_final, save
from .scoring import ScoringError, normalize_answer

CONTEXT_PAD = 2  # реплик вокруг доказательства во фрагменте для проверяющего


class AppealError(ValueError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _now() -> str:
    return datetime.now(ASTANA).isoformat(timespec="seconds")


def _save(appeal: dict[str, Any]) -> None:
    with db.connect() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO appeals (appeal_id, evaluation_id, conversation_id, criterion_id, status, data)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (appeal["appeal_id"], appeal["evaluation_id"], appeal["conversation_id"], appeal["criterion_id"],
             appeal["status"], db.dumps(appeal)))


def get(appeal_id: str) -> dict[str, Any] | None:
    with db.connect() as conn:
        row = conn.execute("SELECT data FROM appeals WHERE appeal_id=?", (appeal_id,)).fetchone()
    return db.loads(row["data"]) if row else None


def _criterion_row(evaluation: dict[str, Any], criterion_id: str) -> dict[str, Any] | None:
    return next((c for c in evaluation["criteria"] if c["criterion_id"] == criterion_id), None)


def _context(appeal: dict[str, Any]) -> list[dict[str, Any]]:
    """Фрагмент диалога для проверяющего: реплики-доказательства и «где ожидалось» ± CONTEXT_PAD."""
    conv = conversation_store.get(appeal["conversation_id"])
    ev = get_evaluation(appeal["evaluation_id"])
    if not conv or not ev:
        return []
    row = _criterion_row(ev, appeal["criterion_id"]) or {}
    idx = [e["index"] for e in row.get("evidence") or [] if e.get("index") is not None]
    rng = row.get("expected_range")
    if rng:
        idx += [rng["from"], rng["to"]]
    tr = conv["transcript"]
    if not idx:
        return tr[-6:]
    lo, hi = max(min(idx) - CONTEXT_PAD, 0), min(max(idx) + CONTEXT_PAD, len(tr) - 1)
    return tr[lo:hi + 1]


DEFAULT_REVIEWER = "Проверяющий"


def view(appeal: dict[str, Any]) -> dict[str, Any]:
    """Апелляция для экранов: + фрагмент диалога, строка критерия из оценки, данные диалога, текущий итог."""
    a = dict(appeal)
    conv = conversation_store.get(a["conversation_id"]) or {}
    ev = get_evaluation(a["evaluation_id"]) or {}
    a["context"] = _context(a)
    a["criterion_result"] = next((c for c in (ev.get("final") or {}).get("criteria", ev.get("criteria", []))
                                  if c["criterion_id"] == a["criterion_id"]), None)
    a["conversation"] = {k: conv.get(k) for k in ("conversation_id", "operator_name", "channel", "language")}
    a["checklist_version"] = ev.get("checklist_version")
    a["current_total"] = (ev.get("final") or {}).get("total", ev.get("total"))
    return a


def create(evaluation_id: str, criterion_id: str, comment: str, author_role: str = "operator",
           author_name: str | None = None, proposed_answer: Any = None) -> dict[str, Any]:
    ev = get_evaluation(evaluation_id)
    if ev is None:
        raise AppealError(f"Оценка {evaluation_id} не найдена", 404)
    if ev.get("status") != "done":
        raise AppealError("Оспорить можно только завершённую оценку")
    row = _criterion_row(ev, criterion_id)
    if row is None:
        raise AppealError(f"Критерия {criterion_id} нет в этой оценке", 404)
    comment = (comment or "").strip()
    if not comment:
        raise AppealError("Нужен комментарий: что именно оспаривается")
    with db.connect() as conn:
        if conn.execute("SELECT 1 FROM appeals WHERE evaluation_id=? AND criterion_id=? AND status='open'",
                        (evaluation_id, criterion_id)).fetchone():
            raise AppealError(f"По критерию {criterion_id} уже есть открытая апелляция", 409)

    final_row = next((c for c in ev["final"].get("criteria", []) if c["criterion_id"] == criterion_id), row)
    if not author_name:
        conv = conversation_store.get(ev["conversation_id"]) or {}
        author_name = conv.get("operator_name") if author_role == "operator" else DEFAULT_REVIEWER
    appeal = {
        "appeal_id": f"ap_{uuid.uuid4().hex[:10]}",
        "evaluation_id": evaluation_id,
        "conversation_id": ev["conversation_id"],
        "criterion_id": criterion_id,
        "criterion_title": row.get("title"),
        "comment": comment,
        "author_role": author_role or "operator",
        "author_name": author_name,
        "proposed_answer": proposed_answer,
        "created_at": _now(),
        "status": "open",
        "original": {"answer": final_row["answer"], "points": final_row.get("points"),
                     "raw_points": final_row.get("raw_points"), "total": ev["final"]["total"],
                     "ai_answer": row["answer"], "ai_total": ev["total"],
                     "explanation": row.get("explanation"), "evidence": row.get("evidence")},
        "decision": None,
    }
    _save(appeal)
    return appeal


def list_appeals(status: str | None = None, conversation_id: str | None = None,
                 with_context: bool = True) -> list[dict[str, Any]]:
    sql, args = "SELECT data FROM appeals WHERE 1=1", []
    if status:
        sql += " AND status=?"; args.append(status)
    if conversation_id:
        sql += " AND conversation_id=?"; args.append(conversation_id)
    sql += " ORDER BY created_at DESC, rowid DESC"
    with db.connect() as conn:
        items = [db.loads(r["data"]) for r in conn.execute(sql, args)]
    return [view(a) for a in items] if with_context else items


def decide(appeal_id: str, action: str, reason: str, new_answer: Any = None,
           reviewer_role: str = "reviewer", reviewer: str | None = None) -> dict[str, Any]:
    appeal = get(appeal_id)
    if appeal is None:
        raise AppealError(f"Апелляция {appeal_id} не найдена", 404)
    if appeal["status"] != "open":
        raise AppealError("Решение по этой апелляции уже принято", 409)
    if action not in ("confirm", "change"):
        raise AppealError("action: confirm или change")
    reason = (reason or "").strip()
    if not reason:
        raise AppealError("Нужна причина решения")

    ev = get_evaluation(appeal["evaluation_id"])
    checklist = checklist_store.get(ev["checklist_id"], ev["checklist_version"])
    criterion = next(c for c in checklist["criteria"] if c["id"] == appeal["criterion_id"])

    decision = {"action": action, "reason": reason, "reviewer_role": reviewer_role or "reviewer",
                "reviewer": reviewer or DEFAULT_REVIEWER, "decided_at": _now(),
                "total_before": ev["final"]["total"], "new_answer": appeal["original"]["answer"]}
    if action == "change":
        try:
            new = normalize_answer(criterion, new_answer)
        except ScoringError as e:
            raise AppealError(str(e))
        current = appeal["original"]["answer"]
        if new == current:
            raise AppealError("Новый ответ совпадает с текущим — используйте confirm")
        overrides = [o for o in ev["final"].get("overrides", []) if o["criterion_id"] != criterion["id"]]
        overrides.append({"criterion_id": criterion["id"], "from": current, "to": new, "appeal_id": appeal_id,
                          "reason": reason, "decided_at": decision["decided_at"], "by": appeal.get("author_role", "operator")})
        ev["final"]["overrides"] = overrides
        decision["new_answer"] = new
    recompute_final(ev, checklist)
    save(ev)

    decision["new_total"] = ev["final"]["total"]
    appeal["decision"] = decision
    appeal["status"] = "resolved"
    _save(appeal)
    return view(appeal)


def override(evaluation_id: str, criterion_id: str, new_answer: Any, reason: str, reviewer: str | None = None) -> dict[str, Any]:
    """Правка оценки проверяющим = апелляция от проверяющего, сразу решённая (история сохраняется так же)."""
    ap = create(evaluation_id, criterion_id, reason, author_role="reviewer", author_name=reviewer or DEFAULT_REVIEWER,
                proposed_answer=new_answer)
    try:
        return decide(ap["appeal_id"], "change", reason, new_answer, reviewer=reviewer)
    except AppealError:
        with db.connect() as conn:                 # не оставляем «висящую» апелляцию, если правка недопустима
            conn.execute("DELETE FROM appeals WHERE appeal_id=?", (ap["appeal_id"],))
        raise
