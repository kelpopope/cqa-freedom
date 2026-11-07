"""Расчёт баллов строго по API_CONTRACT §3. LLM баллы не считает — только этот модуль.

Вход: чек-лист (Checklist) + ответы по критериям {criterion_id: answer}, где answer:
  binary -> "yes" | "no" | "na";  scale -> ступень (число из scale[].value) | "na";  flag -> "yes" | "no".

1. N/A допустим, только если na_allowed.
2. binary: yes -> weight, no -> 0. scale: weight * value / max(scale.value). flag: баллов нет.
3. W = сумма weight применимых (не N/A, не flag). W == 0 -> total = None.
4. points_i = raw_i * 100 / W; effective_weight_i = weight_i * 100 / W; total = round(sum points_i).
5. Секция: сумма points / сумма effective_weight её критериев.
6. Грубая ошибка (flag = yes) — отдельная отметка, на total не влияет.
7. Апелляция подменяет ответ -> полный пересчёт (apply_overrides + score).

Арифметика точная (Fraction), округление — половина вверх (как ROUND в Excel), а не банковское.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from typing import Any, Mapping

YES, NO, NA = "yes", "no", "na"
BINARY, SCALE, FLAG = "binary", "scale", "flag"


class ScoringError(ValueError):
    """Ответ не соответствует критерию (N/A где запрещён, ступени нет в шкале и т.п.)."""


def round_half_up(value: Fraction | int | float, ndigits: int = 2) -> float:
    q = Decimal(1).scaleb(-ndigits)  # 0.01 для ndigits=2
    d = Decimal(value.numerator) / Decimal(value.denominator) if isinstance(value, Fraction) else Decimal(str(value))
    return float(d.quantize(q, rounding=ROUND_HALF_UP))


def _scale_values(criterion: Mapping[str, Any]) -> list[int | float]:
    scale = criterion.get("scale") or []
    values = [step["value"] for step in scale]
    if not values:
        raise ScoringError(f"{criterion['id']}: у критерия типа scale нет ступеней")
    if max(values) <= 0:
        raise ScoringError(f"{criterion['id']}: максимальная ступень шкалы должна быть > 0")
    return values


def _as_number(answer: Any) -> int | float | None:
    if isinstance(answer, bool):
        return None
    if isinstance(answer, (int, float)):
        return answer
    if isinstance(answer, str):
        try:
            num = float(answer.strip())
        except ValueError:
            return None
        return int(num) if num.is_integer() else num
    return None


def normalize_answer(criterion: Mapping[str, Any], answer: Any) -> str | int | float:
    """Приводит ответ к каноническому виду и проверяет его допустимость. Иначе — ScoringError."""
    cid, ctype = criterion["id"], criterion["type"]
    if isinstance(answer, str) and answer.strip().lower() in (YES, NO, NA):
        answer = answer.strip().lower()

    if answer == NA:
        if ctype == FLAG or not criterion.get("na_allowed"):
            raise ScoringError(f"{cid}: N/A не разрешён для этого критерия")
        return NA

    if ctype == BINARY or ctype == FLAG:
        if answer in (YES, NO):
            return answer
        raise ScoringError(f"{cid}: ожидается yes/no{'/na' if ctype == BINARY else ''}, получено {answer!r}")

    if ctype == SCALE:
        values = _scale_values(criterion)
        num = _as_number(answer)
        if num is None or num not in values:
            raise ScoringError(f"{cid}: ступень {answer!r} не входит в шкалу {values}")
        return num

    raise ScoringError(f"{cid}: неизвестный тип критерия {ctype!r}")


def _raw(criterion: Mapping[str, Any], answer: str | int | float) -> Fraction:
    weight = Fraction(str(criterion.get("weight") or 0))
    if criterion["type"] == BINARY:
        return weight if answer == YES else Fraction(0)
    values = _scale_values(criterion)
    return weight * Fraction(str(answer)) / Fraction(str(max(values)))


def score(checklist: Mapping[str, Any], answers: Mapping[str, Any]) -> dict[str, Any]:
    """Считает баллы по всем критериям чек-листа.

    Возвращает:
      criteria: [{criterion_id, answer, raw_points, points, weight, effective_weight}]
                (у N/A и flag: raw_points/points = None, effective_weight = 0)
      sections: [{section_id, points, max_points}] (points = None, если в секции нет применимых)
      total: float | None
      gross_error: bool, gross_criteria: [criterion_id] — флаги с ответом "yes"
    """
    ndigits = int(checklist.get("rounding", 2))
    criteria = checklist.get("criteria") or []

    missing = [c["id"] for c in criteria if c["id"] not in answers]
    if missing:
        raise ScoringError(f"Нет ответа по критериям: {', '.join(missing)}")
    unknown = sorted(set(answers) - {c["id"] for c in criteria})
    if unknown:
        raise ScoringError(f"Ответы по критериям, которых нет в чек-листе: {', '.join(unknown)}")

    normalized = {c["id"]: normalize_answer(c, answers[c["id"]]) for c in criteria}

    applicable = [c for c in criteria if c["type"] != FLAG and normalized[c["id"]] != NA]
    W = sum((Fraction(str(c.get("weight") or 0)) for c in applicable), Fraction(0))
    has_total = W > 0

    rows: list[dict[str, Any]] = []
    exact: dict[str, tuple[Fraction, Fraction]] = {}  # id -> (points, effective_weight), неокруглённые
    for c in criteria:
        cid, ans = c["id"], normalized[c["id"]]
        weight = c.get("weight") or 0
        row = {"criterion_id": cid, "answer": ans, "raw_points": None, "points": None,
               "weight": weight, "effective_weight": 0}
        if c["type"] != FLAG and ans != NA:
            raw = _raw(c, ans)
            row["raw_points"] = round_half_up(raw, ndigits)
            if has_total:
                pts = raw * 100 / W
                eff = Fraction(str(weight)) * 100 / W
                exact[cid] = (pts, eff)
                row["points"] = round_half_up(pts, ndigits)
                row["effective_weight"] = round_half_up(eff, ndigits)
            else:
                row["effective_weight"] = None
        rows.append(row)

    sections = []
    for s in checklist.get("sections") or []:
        members = [exact[c["id"]] for c in criteria if c.get("section_id") == s["id"] and c["id"] in exact]
        if members:
            sections.append({
                "section_id": s["id"],
                "points": round_half_up(sum((p for p, _ in members), Fraction(0)), ndigits),
                "max_points": round_half_up(sum((e for _, e in members), Fraction(0)), ndigits),
            })
        else:
            sections.append({"section_id": s["id"], "points": None, "max_points": 0})

    total = round_half_up(sum((p for p, _ in exact.values()), Fraction(0)), ndigits) if has_total else None
    gross = [c["id"] for c in criteria if c["type"] == FLAG and normalized[c["id"]] == YES]

    return {"criteria": rows, "sections": sections, "total": total,
            "gross_error": bool(gross), "gross_criteria": gross}


def apply_overrides(answers: Mapping[str, Any], overrides: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Решения по апелляциям ({criterion_id, to}) подменяют ответы; последнее решение побеждает."""
    result = dict(answers)
    for ov in overrides:
        result[ov["criterion_id"]] = ov["to"]
    return result
