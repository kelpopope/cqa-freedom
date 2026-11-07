"""«Было → стало» между версиями чек-листа и сводная аналитика по критериям.

Аналитика — «индикатор» для заказчика: где операторы чаще проваливают критерии, какие диалоги требуют
проверки, сколько грубых ошибок — чтобы дорабатывать скрипты ответов и выбирать диалоги для апелляций.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from .. import db
from . import checklists as checklist_store


def _latest_by_conversation(checklist_id: str, version: int | None = None,
                            group_id: str | None = None) -> dict[str, dict[str, Any]]:
    sql = """SELECT e.conversation_id, e.data FROM evaluations e
             JOIN conversations c ON c.conversation_id = e.conversation_id
             WHERE e.status='done' AND e.checklist_id=?"""
    args: list[Any] = [checklist_id]
    if version is not None:
        sql += " AND e.checklist_version=?"; args.append(version)
    if group_id:
        sql += " AND c.group_id=?"; args.append(group_id)
    sql += " ORDER BY e.created_at, e.rowid"
    out: dict[str, dict] = {}
    with db.connect() as conn:
        for r in conn.execute(sql, args):
            out[r["conversation_id"]] = db.loads(r["data"])
    return out


def _answers(ev: dict[str, Any]) -> dict[str, Any]:
    final = {c["criterion_id"]: c["answer"] for c in (ev.get("final") or {}).get("criteria", [])}
    return final or {c["criterion_id"]: c["answer"] for c in ev["criteria"]}


def compare(checklist_id: str, from_version: int, to_version: int, group_id: str | None = None) -> dict[str, Any]:
    a = _latest_by_conversation(checklist_id, from_version, group_id)
    b = _latest_by_conversation(checklist_id, to_version, group_id)
    cl_a = checklist_store.get(checklist_id, from_version)
    cl_b = checklist_store.get(checklist_id, to_version)
    ids_a = {c["id"] for c in cl_a["criteria"]} if cl_a else set()
    ids_b = {c["id"] for c in cl_b["criteria"]} if cl_b else set()
    rows = []
    for cid in sorted(set(a) & set(b)):
        ans_a, ans_b = _answers(a[cid]), _answers(b[cid])
        changed = [{"criterion_id": k, "from": ans_a[k], "to": ans_b[k]}
                   for k in sorted(set(ans_a) & set(ans_b)) if ans_a[k] != ans_b[k]]
        t_a, t_b = a[cid]["final"]["total"], b[cid]["final"]["total"]
        rows.append({
            "conversation_id": cid,
            "from_total": t_a, "to_total": t_b,
            "delta": round(t_b - t_a, 2) if t_a is not None and t_b is not None else None,
            "changed_criteria": [c["criterion_id"] for c in changed] + sorted(set(ans_b) ^ set(ans_a)),
            "changes": changed,
            "added": {k: ans_b[k] for k in sorted(set(ans_b) - set(ans_a))},
            "removed": {k: ans_a[k] for k in sorted(set(ans_a) - set(ans_b))},
            "from_evaluation_id": a[cid]["evaluation_id"], "to_evaluation_id": b[cid]["evaluation_id"],
        })
    return {
        "checklist_id": checklist_id, "from_version": from_version, "to_version": to_version,
        "criteria_added": sorted(ids_b - ids_a), "criteria_removed": sorted(ids_a - ids_b),
        "only_in_from": sorted(set(a) - set(b)), "only_in_to": sorted(set(b) - set(a)),
        "items": rows,
    }


def summary(checklist_id: str = "default", version: int | None = None, group_id: str | None = None) -> dict[str, Any]:
    checklist = checklist_store.get(checklist_id, version)
    if checklist is None:
        return {"error": f"Чек-лист {checklist_id} не найден"}
    evs = _latest_by_conversation(checklist_id, checklist["version"], group_id)
    per = {c["id"]: {"criterion_id": c["id"], "title": c["title"], "type": c["type"], "answers": Counter(),
                     "failed": [], "points_share": []} for c in checklist["criteria"]}
    by_lang: dict[str, list[float]] = defaultdict(list)
    by_operator: dict[str, list[float]] = defaultdict(list)
    gross, review, appealed = [], [], 0
    with db.connect() as conn:
        names = {r["conversation_id"]: db.loads(r["data"]).get("operator_name")
                 for r in conn.execute("SELECT conversation_id, data FROM conversations")}
    for cid, ev in evs.items():
        for row in ev["final"].get("criteria", ev["criteria"]):
            p = per.get(row["criterion_id"])
            if not p:
                continue
            p["answers"][str(row["answer"])] += 1
            c = next(c for c in checklist["criteria"] if c["id"] == row["criterion_id"])
            if c["type"] == "binary" and row["answer"] == "no":
                p["failed"].append(cid)
            if c["type"] == "scale" and row["answer"] != "na":
                top = max(s["value"] for s in c["scale"])
                p["points_share"].append(row["answer"] / top)
                if row["answer"] < top:
                    p["failed"].append(cid)
            if c["type"] == "flag" and row["answer"] == "yes":
                p["failed"].append(cid)
        total = ev["final"]["total"]
        lang = cid.split("-")[1].upper() if cid.count("-") >= 2 else "?"
        if total is not None:
            by_lang[lang].append(total)
        name = names.get(cid)
        if name and total is not None:
            by_operator[name].append(total)
        if ev["final"].get("gross_error", ev["gross_error"]["flag"]):
            gross.append({"conversation_id": cid, "types": ev["gross_error"]["types"]})
        if ev.get("needs_review"):
            review.append(cid)
        appealed += bool(ev["final"].get("overrides"))

    criteria = []
    for p in per.values():
        n = sum(p["answers"].values())
        applicable = n - p["answers"].get("na", 0)
        criteria.append({
            "criterion_id": p["criterion_id"], "title": p["title"], "type": p["type"],
            "answers": dict(p["answers"]), "evaluated": n,
            "fail_rate": round(len(p["failed"]) / applicable, 3) if applicable else None,
            "avg_share": round(sum(p["points_share"]) / len(p["points_share"]), 3) if p["points_share"] else None,
            "failed_conversations": sorted(p["failed"]),
        })
    totals = [ev["final"]["total"] for ev in evs.values() if ev["final"]["total"] is not None]
    return {
        "checklist_id": checklist_id, "checklist_version": checklist["version"], "group_id": group_id,
        "evaluated": len(evs),
        "avg_total": round(sum(totals) / len(totals), 2) if totals else None,
        "criteria": sorted(criteria, key=lambda x: -(x["fail_rate"] or 0)),
        "by_language": {k: round(sum(v) / len(v), 2) for k, v in sorted(by_lang.items())},
        "by_operator": sorted(({"operator_name": k, "dialogs": len(v), "avg_total": round(sum(v) / len(v), 2)}
                               for k, v in by_operator.items()), key=lambda x: x["avg_total"]),
        "gross_errors": gross,
        "needs_review": sorted(review),
        "with_overrides": appealed,
    }


def stats(group_id: str | None = None, checklist_id: str = "default") -> dict[str, Any]:
    """Экран «Обзор» руководителя: средний балл, распределение, грубые нарушения, слабые критерии, риски."""
    from . import conversations as conversation_store
    items = conversation_store.list_items(group_id=group_id)
    evs = {}
    for it in items:
        if it["evaluation"] and it["evaluation"]["status"] == "done":
            ev = conversation_store.latest_evaluation(it["conversation_id"])
            if ev:
                evs[it["conversation_id"]] = ev
    totals = [ev["final"]["total"] for ev in evs.values() if ev["final"].get("total") is not None]
    cl = checklist_store.get(checklist_id)
    criteria = []
    for c in (cl or {"criteria": []})["criteria"]:
        if c["type"] == "flag":
            continue
        applicable = failed = na = 0
        lost = 0.0
        for ev in evs.values():
            row = next((r for r in ev["final"].get("criteria") or ev["criteria"] if r["criterion_id"] == c["id"]), None)
            if not row:
                continue
            if row["answer"] == "na":
                na += 1
                continue
            applicable += 1
            raw = row.get("raw_points") or 0
            if raw < (c.get("weight") or 0):
                failed += 1
                lost += (c.get("weight") or 0) - raw
        criteria.append({"criterion_id": c["id"], "title": c["title"], "weight": c.get("weight"),
                         "applicable": applicable, "failed": failed, "na": na,
                         "fail_rate": round(failed / applicable, 4) if applicable else 0, "lost_points": round(lost, 2)})
    criteria.sort(key=lambda x: -x["fail_rate"])
    by_lang = []
    for lang in ("RU", "KZ", "EN"):
        t = [ev["final"]["total"] for cid, ev in evs.items() if ev["final"].get("total") is not None
             and next((i["language"] for i in items if i["conversation_id"] == cid), None) == lang]
        by_lang.append({"language": lang, "n": len(t), "avg": round(sum(t) / len(t), 2) if t else None})
    with db.connect() as conn:
        appeals_open = conn.execute("SELECT COUNT(*) FROM appeals WHERE status='open'").fetchone()[0]
    gross_items = [i for i in items if i["evaluation"] and i["evaluation"]["gross_error"]]
    return {
        "total": len(items),
        "evaluated": len(evs),
        "avg": round(sum(totals) / len(totals), 2) if totals else None,
        "gross": len(gross_items),
        "needs_review": sum(1 for i in items if (i["evaluation"] or {}).get("needs_review") or i["appeal_status"] == "open"),
        "reviewed": sum(1 for ev in evs.values() if (ev.get("review") or {}).get("status") == "confirmed"),
        "appeals_open": appeals_open,
        "buckets": {"low": sum(t < 60 for t in totals), "mid": sum(60 <= t < 85 for t in totals),
                    "high": sum(t >= 85 for t in totals)},
        "by_language": by_lang,
        "criteria": criteria,
        "risky": [{"conversation_id": i["conversation_id"], "operator_name": i["operator_name"],
                   "total": i["evaluation"]["final_total"], "gross_types": i["evaluation"]["gross_types"],
                   "headline": i["evaluation"]["headline"]} for i in gross_items],
    }
