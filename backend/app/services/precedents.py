"""Прецеденты: решения проверяющего по апелляциям становятся примерами для модели.

Так система «учится» трактовке критериев у людей (калибровка LLM-судьи по исправлениям экспертов):
по каждому критерию в промпт попадают последние решения — фрагмент диалога, ответ ИИ, решение человека и причина.
Правила:
- берутся только решённые апелляции, сначала изменения (там, где ИИ ошибся), затем подтверждения;
- только если критерий в текущей версии чек-листа не изменился по сути (иначе решение относится к старой формулировке);
- прецедент из оцениваемого диалога не используется (нет утечки ответа в его же переоценку);
- не больше MAX_PER_CRITERION на критерий, фрагмент — короткий.
"""
from __future__ import annotations

from typing import Any

from .. import db
from . import checklists as checklist_store

MAX_PER_CRITERION = 3
FRAGMENT_PAD = 1
MAX_FRAGMENT_TURNS = 6
SAME_KEYS = ("type", "title", "description", "scale", "na_allowed", "na_condition")


def _fragment(transcript: list[dict[str, Any]], evaluation: dict[str, Any], criterion_id: str) -> list[dict[str, Any]]:
    row = next((c for c in evaluation.get("criteria", []) if c["criterion_id"] == criterion_id), {})
    idx = [e["index"] for e in row.get("evidence") or [] if e.get("index") is not None]
    idx += row.get("issue_turns") or []
    rng = row.get("expected_range")
    if rng:
        idx += [rng["from"], rng["to"]]
    if not idx:
        idx = [t["index"] for t in transcript if t["speaker"] == "operator"][:2]
    lo, hi = max(min(idx) - FRAGMENT_PAD, 0), min(max(idx) + FRAGMENT_PAD, len(transcript) - 1)
    turns = [t for t in transcript[lo:hi + 1] if t["speaker"] != "bot"]
    return turns[:MAX_FRAGMENT_TURNS]


def collect(checklist: dict[str, Any], exclude_conversation_id: str | None = None) -> dict[str, list[dict[str, Any]]]:
    """Прецеденты по каждому критерию текущего чек-листа: {criterion_id: [{...}]}."""
    current = {c["id"]: c for c in checklist["criteria"]}
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT a.data AS appeal, e.data AS evaluation, c.data AS conversation
               FROM appeals a JOIN evaluations e ON e.evaluation_id = a.evaluation_id
               JOIN conversations c ON c.conversation_id = a.conversation_id
               WHERE a.status = 'resolved' ORDER BY a.created_at DESC, a.rowid DESC""").fetchall()
    cache: dict[tuple, dict] = {}
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        ap, ev, conv = db.loads(r["appeal"]), db.loads(r["evaluation"]), db.loads(r["conversation"])
        cid = ap["criterion_id"]
        if cid not in current or ap["conversation_id"] == exclude_conversation_id or not ap.get("decision"):
            continue
        key = (ev["checklist_id"], ev["checklist_version"])
        if key not in cache:
            cache[key] = {c["id"]: c for c in (checklist_store.get(*key) or {"criteria": []})["criteria"]}
        old = cache[key].get(cid)
        if not old or any(old.get(k) != current[cid].get(k) for k in SAME_KEYS):
            continue
        d = ap["decision"]
        out.setdefault(cid, []).append({
            "appeal_id": ap["appeal_id"],
            "conversation_id": ap["conversation_id"],
            "action": d["action"],
            "ai_answer": ap["original"].get("ai_answer", ap["original"]["answer"]),
            "human_answer": d.get("new_answer", ap["original"]["answer"]),
            "reason": d.get("reason", ""),
            "operator_comment": ap.get("comment", ""),
            "fragment": _fragment(conv["transcript"], ev, cid),
        })
    for cid, items in out.items():
        items.sort(key=lambda p: p["action"] != "change")        # исправления ИИ — самые ценные примеры
        out[cid] = items[:MAX_PER_CRITERION]
    return out


def format_block(items: list[dict[str, Any]]) -> str:
    lines = ["Прецеденты — решения проверяющего по похожим случаям (следуй этой трактовке критерия):"]
    for p in items:
        frag = " / ".join(f"[{t['index']}] {t['speaker'].upper()}: {t['text'][:160]}" for t in p["fragment"])
        verdict = (f"ИИ ответил «{p['ai_answer']}», проверяющий исправил на «{p['human_answer']}»"
                   if p["action"] == "change" else f"ИИ ответил «{p['ai_answer']}», проверяющий подтвердил")
        lines.append(f"  - {frag}\n    → {verdict}. Причина: {p['reason']}")
    return "\n".join(lines)
