"""Официальная выгрузка результатов для организаторов (CQA_Student_Guide_Appendix_Results, формат JSON 1.0).

results/dev/results.json и results/prod/results.json:
- criterion_id — коды contact, discovery, identification, consultation, empathy, dialogue, closure, critical;
- value: true = «Да», false = «Нет», null = N/A; шкала — число-ступень до учёта веса;
- evidence_turns — номера реплик С 1 по исходному массиву транскрипта (у нас индекс с 0 → +1);
- score.final_score — итог 0–100 (2 знака), score.critical_flag = value критерия critical;
- не удалось оценить → status "error", criteria [], score null, причина в error.
Выгружается результат системы (ИИ-оценка), а не решения по апелляциям.
"""
from __future__ import annotations

import re
from typing import Any

from . import checklists as checklist_store
from . import conversations as conversation_store

SCHEMA_VERSION = "1.0"
OFFICIAL = {  # код → (допустимые типы value, разрешён ли null)
    "contact": ("bool", False), "discovery": ("bool", True), "identification": ("bool", True),
    "consultation": ("scale", True), "empathy": ("bool", False), "dialogue": ("scale", False),
    "closure": ("bool", True), "critical": ("bool", False),
}
OFFICIAL_SCALES = {"consultation": {0, 5, 10, 15, 20}, "dialogue": {0, 4, 7, 10}}


def _value(criterion: dict[str, Any], answer: Any):
    if answer == "na":
        return None
    if criterion["type"] == "scale":
        return answer
    return answer == "yes"


def _evidence_turns(row: dict[str, Any], transcript: list[dict[str, Any]]) -> list[int]:
    idx = {e["index"] for e in row.get("evidence") or [] if isinstance(e.get("index"), int)}
    idx |= set(row.get("issue_turns") or [])
    if not idx and row.get("expected_range"):
        idx.add(row["expected_range"]["from"])
    if not idx:  # например, «грубых ошибок нет» — подтверждают все реплики оператора
        idx = {t["index"] for t in transcript if t["speaker"] == "operator" and not t.get("on_hold")}
    return sorted(i + 1 for i in idx if 0 <= i < len(transcript))


_TURN_REF = re.compile(r"\[([^\]]*#\d[^\]]*)\]")


def to_one_based_refs(text: str) -> str:
    """Ссылки на реплики в тексте («[#9]», «[#9, #11]», «[#12–14]») — в нумерацию с 1, как evidence_turns."""
    def fix(m: re.Match) -> str:
        inner = re.sub(r"(#?)(\d+)", lambda d: f"{d.group(1)}{int(d.group(2)) + 1}", m.group(1))
        return f"[{inner}]"
    return _TURN_REF.sub(fix, text)


def _rationale(row: dict[str, Any]) -> str:
    text = (row.get("explanation") or "").strip()
    if row.get("answer") == "na" and row.get("na_reason") and row["na_reason"] not in text:
        text = f"{text} N/A: {row['na_reason']}".strip()
    return to_one_based_refs(text)


def build_entry(conversation_id: str, checklist: dict[str, Any], use_final: bool = False) -> dict[str, Any]:
    conv = conversation_store.get(conversation_id)
    ev = conversation_store.latest_evaluation(conversation_id)
    if not conv or not ev or ev.get("status") != "done":
        reason = (ev or {}).get("error") or ("Диалог не оценён" if conv else "Диалог не загружен")
        return {"conversation_id": conversation_id, "status": "error", "criteria": [], "score": None, "error": reason}
    crit = {c["id"]: c for c in checklist["criteria"]}
    final_answers = {c["criterion_id"]: c["answer"] for c in (ev.get("final") or {}).get("criteria", [])}
    criteria, critical = [], None
    for row in ev["criteria"]:
        c = crit.get(row["criterion_id"])
        if not c:
            continue
        answer = final_answers.get(row["criterion_id"], row["answer"]) if use_final else row["answer"]
        code = c.get("code") or c["id"]
        value = _value(c, answer)
        if c["type"] == "flag":
            critical = bool(value)
        criteria.append({"criterion_id": code, "value": value, "rationale": _rationale(row),
                         "evidence_turns": _evidence_turns(row, conv["transcript"])})
    total = ev["final"]["total"] if use_final else ev["total"]
    return {"conversation_id": conversation_id, "status": "ok", "criteria": criteria,
            "score": {"final_score": total, "critical_flag": bool(critical)}}


def build(conversation_ids: list[str], team_name: str, model_name: str, checklist_id: str = "default",
          version: int | None = None, use_final: bool = False) -> dict[str, Any]:
    checklist = checklist_store.get(checklist_id, version)
    return {
        "schema_version": SCHEMA_VERSION,
        "team_name": team_name,
        "model": {"mode": "external_api", "name": model_name},
        "results": [build_entry(cid, checklist, use_final) for cid in conversation_ids],
    }


def validate(doc: dict[str, Any], expected_ids: list[str] | None = None, expected_count: int | None = None,
             transcripts: dict[str, int] | None = None) -> list[str]:
    """Проверки из «Правил заполнения». Возвращает список проблем (пустой — можно сдавать)."""
    errors: list[str] = []
    if doc.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version должен быть \"1.0\"")
    if not doc.get("team_name"):
        errors.append("пустой team_name")
    m = doc.get("model") or {}
    if m.get("mode") not in ("local", "external_api", "hybrid") or not m.get("name"):
        errors.append("model.mode — local/external_api/hybrid, model.name обязателен")
    results = doc.get("results") or []
    ids = [r.get("conversation_id") for r in results]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        errors.append(f"повторяющиеся conversation_id: {dup}")
    if expected_ids is not None and set(ids) != set(expected_ids):
        errors.append(f"ID не совпадают с набором: нет {sorted(set(expected_ids) - set(ids))[:5]}, "
                      f"лишние {sorted(set(ids) - set(expected_ids))[:5]}")
    if expected_count is not None and len(results) != expected_count:
        errors.append(f"записей {len(results)}, ожидается {expected_count}")
    for r in results:
        cid = r.get("conversation_id")
        if r.get("status") == "error":
            if r.get("criteria") != [] or r.get("score") is not None or not r.get("error"):
                errors.append(f"{cid}: для error нужны criteria [], score null и причина в error")
            continue
        if r.get("status") != "ok":
            errors.append(f"{cid}: status — ok или error")
            continue
        codes = [c.get("criterion_id") for c in r.get("criteria") or []]
        if sorted(codes) != sorted(OFFICIAL):
            errors.append(f"{cid}: нужны все 8 критериев ровно по разу, есть {codes}")
        for c in r.get("criteria") or []:
            code, v = c.get("criterion_id"), c.get("value")
            kind, nullable = OFFICIAL.get(code, (None, False))
            if v is None:
                if not nullable:
                    errors.append(f"{cid}.{code}: null (N/A) не допускается")
            elif kind == "bool" and not isinstance(v, bool):
                errors.append(f"{cid}.{code}: ожидается true/false, получено {v!r}")
            elif kind == "scale" and (isinstance(v, bool) or v not in OFFICIAL_SCALES[code]):
                errors.append(f"{cid}.{code}: ступень {v!r} не из {sorted(OFFICIAL_SCALES[code])}")
            turns = c.get("evidence_turns")
            if not isinstance(turns, list) or not all(isinstance(t, int) and t >= 1 for t in turns):
                errors.append(f"{cid}.{code}: evidence_turns — список номеров с 1")
            elif transcripts and cid in transcripts and any(t > transcripts[cid] for t in turns):
                errors.append(f"{cid}.{code}: номер реплики больше длины транскрипта")
            if not isinstance(c.get("rationale"), str) or not c["rationale"].strip():
                errors.append(f"{cid}.{code}: пустой rationale")
        score = r.get("score") or {}
        fs = score.get("final_score")
        if fs is not None and (not isinstance(fs, (int, float)) or not 0 <= fs <= 100 or round(fs, 2) != fs):
            errors.append(f"{cid}: final_score вне 0–100 или больше 2 знаков")
        crit = next((c.get("value") for c in r.get("criteria") or [] if c.get("criterion_id") == "critical"), None)
        if score.get("critical_flag") is not crit:
            errors.append(f"{cid}: critical_flag должен совпадать с value критерия critical")
    return errors
