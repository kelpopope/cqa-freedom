"""Сравнение оценок системы с нашим эталоном (data/gt/<group>_labels.json).

Метрики:
- overall — доля совпавших ячеек «диалог × критерий» (точное совпадение ответа/ступени);
- overall_confident — то же без ячеек, помеченных в эталоне как спорные (uncertain);
- per_criterion, per_language — точность по разрезам;
- total_mae — средняя абсолютная ошибка итогового балла (эталонный итог считает тот же scoring.py);
- gross — precision/recall флага грубой ошибки.
"""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from .. import db
from ..config import DATA_DIR
from . import checklists as checklist_store
from .scoring import normalize_answer, score

GT_DIR = DATA_DIR / "gt"


def load_gt(group_id: str = "DEV") -> dict[str, Any] | None:
    path = GT_DIR / f"{group_id.lower()}_labels.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def latest_evaluations(conversation_ids: list[str], checklist_id: str, version: int | None) -> dict[str, dict]:
    """Последняя успешная оценка каждого диалога по указанному чек-листу (и версии, если задана)."""
    sql = "SELECT conversation_id, data FROM evaluations WHERE status='done' AND checklist_id=?"
    args: list[Any] = [checklist_id]
    if version is not None:
        sql += " AND checklist_version=?"
        args.append(version)
    sql += " ORDER BY created_at, rowid"
    result = {}
    wanted = set(conversation_ids)
    with db.connect() as conn:
        for r in conn.execute(sql, args):
            if r["conversation_id"] in wanted:
                result[r["conversation_id"]] = db.loads(r["data"])  # последняя перезапишет предыдущие
    return result


def evidence_report(evaluations: dict[str, dict], checklist: dict[str, Any]) -> dict[str, Any]:
    """Насколько точно система указывает реплики (без эталона реплик — проверяемые свойства):
    - valid: цитата найдена в указанной реплике;
    - bad_located: каждый нарушенный критерий привязан к реплике оператора (или к месту, где действие ожидалось);
    - turn_review_coverage: доля реплик оператора с разбором;
    - consistent: в разборе нет ошибок по критериям, которые в итоге выполнены."""
    from . import conversations as conversation_store
    from .evaluator import criterion_status
    crit = {c["id"]: c for c in checklist["criteria"]}
    n_items = n_valid = n_bad = n_bad_loc = n_turns = n_noted = n_issues = n_incons = 0
    for cid, ev in evaluations.items():
        conv = conversation_store.get(cid)
        if not conv:
            continue
        roles = {t["index"]: t["speaker"] for t in conv["transcript"]}
        for row in ev["criteria"]:
            c = crit.get(row["criterion_id"])
            if not c:
                continue
            n_items += 1
            n_valid += bool(row.get("evidence_valid"))
            if criterion_status(c, row["answer"]) == "bad":
                n_bad += 1
                op_ev = any(roles.get(e["index"]) == "operator" for e in row.get("evidence") or [])
                n_bad_loc += bool(row.get("issue_turns") or op_ev or row.get("expected_range"))
        status = {r["criterion_id"]: criterion_status(crit[r["criterion_id"]], r["answer"])
                  for r in ev["criteria"] if r["criterion_id"] in crit}
        for t in ev.get("turn_review") or []:
            n_turns += 1
            n_noted += bool(t.get("note"))
            for iss in t.get("issues") or []:
                n_issues += 1
                n_incons += status.get(iss["criterion_id"]) != "bad"
    r = lambda a, b: round(a / b, 4) if b else None  # noqa: E731
    return {"valid": r(n_valid, n_items), "bad_located": r(n_bad_loc, n_bad), "bad_criteria": n_bad,
            "turn_review_coverage": r(n_noted, n_turns), "turn_issues": n_issues,
            "consistent": r(n_issues - n_incons, n_issues)}


def _language(cid: str, ev: dict | None) -> str:
    parts = cid.split("-")
    return parts[1].upper() if len(parts) > 2 else "?"


def compute(group_id: str = "DEV", evaluations: dict[str, dict] | None = None) -> dict[str, Any]:
    gt = load_gt(group_id)
    if gt is None:
        return {"error": f"Нет эталона для {group_id}", "n": 0}
    checklist = checklist_store.get(gt["checklist_id"], gt.get("checklist_version"))
    crit = {c["id"]: c for c in checklist["criteria"]}
    labels = gt["labels"]
    if evaluations is None:
        evaluations = latest_evaluations(list(labels), gt["checklist_id"], gt.get("checklist_version"))

    cells = defaultdict(lambda: [0, 0])          # criterion -> [hit, total]
    cells_conf = defaultdict(lambda: [0, 0])
    langs = defaultdict(lambda: [0, 0])
    mismatches, abs_err = [], []
    tp = fp = fn = 0
    for cid, lab in labels.items():
        ev = evaluations.get(cid)
        if not ev:
            continue
        pred = {c["criterion_id"]: c["answer"] for c in ev["criteria"]}
        for k, expected in lab["answers"].items():
            if k not in crit or k not in pred:
                continue
            exp = normalize_answer(crit[k], expected)
            got = pred[k]
            hit = got == exp
            cells[k][0] += hit; cells[k][1] += 1
            langs[_language(cid, ev)][0] += hit; langs[_language(cid, ev)][1] += 1
            if k not in lab.get("uncertain", []):
                cells_conf[k][0] += hit; cells_conf[k][1] += 1
            if not hit:
                mismatches.append({"conversation_id": cid, "criterion_id": k, "expected": exp, "got": got,
                                   "uncertain": k in lab.get("uncertain", [])})
        gt_total = score(checklist, lab["answers"])["total"]
        if gt_total is not None and ev.get("total") is not None:
            abs_err.append(abs(gt_total - ev["total"]))
        gt_flag = any(crit[k]["type"] == "flag" and lab["answers"].get(k) == "yes" for k in crit)
        got_flag = bool((ev.get("gross_error") or {}).get("flag"))
        tp += gt_flag and got_flag; fp += got_flag and not gt_flag; fn += gt_flag and not got_flag

    def ratio(pair):
        return round(pair[0] / pair[1], 4) if pair[1] else None

    total_pair = [sum(v[0] for v in cells.values()), sum(v[1] for v in cells.values())]
    conf_pair = [sum(v[0] for v in cells_conf.values()), sum(v[1] for v in cells_conf.values())]
    return {
        "group_id": group_id,
        "n": len([c for c in labels if c in evaluations]),
        "labeled": len(labels),
        "overall": ratio(total_pair),
        "overall_confident": ratio(conf_pair),
        "per_criterion": {k: ratio(v) for k, v in sorted(cells.items())},
        "per_criterion_confident": {k: ratio(v) for k, v in sorted(cells_conf.items())},
        "per_language": {k: ratio(v) for k, v in sorted(langs.items())},
        "total_mae": round(sum(abs_err) / len(abs_err), 2) if abs_err else None,
        "gross": {"tp": tp, "fp": fp, "fn": fn,
                  "precision": round(tp / (tp + fp), 4) if tp + fp else None,
                  "recall": round(tp / (tp + fn), 4) if tp + fn else None},
        "evidence": evidence_report({c: e for c, e in evaluations.items() if c in labels}, checklist),
        "mismatches": mismatches,
    }
