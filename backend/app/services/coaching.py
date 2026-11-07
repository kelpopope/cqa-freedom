"""«Как надо было ответить»: пример правильной реплики оператора для нарушенного критерия.

Отдельный вызов модели по кнопке — на оценку и баллы не влияет. Ответ строится только на диалоге
и базе знаний чек-листа; если нужного факта нет, модель ставит пометку [уточнить у специалиста],
а не придумывает. Результат кэшируется в оценке (evaluation.coaching[criterion_id]) — повторный
показ бесплатный и мгновенный.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import checklists as checklist_store
from . import conversations as conversation_store
from .evaluator import EvaluationError, get_evaluation, save
from .llm_client import LLMError, complete_json
from .prompts import format_transcript
from .scoring import normalize_answer

LANG_NAME = {"RU": "русском", "KZ": "казахском", "EN": "английском"}

SYSTEM = """Ты — наставник отдела контроля качества брокера Freedom Broker. Тебе дают диалог поддержки,
критерий чек-листа, который оператор нарушил, и объяснение оценщика. Покажи оператору, как надо было ответить.

Правила:
- Перепиши одну реплику оператора (указана как целевая) так, чтобы критерий был выполнен; остальное, что в ней было верно, сохрани.
  Если нарушение — пропущенное действие (не поздоровался, не завершил, не подтвердил личность), дай реплику, которую нужно было написать в этом месте.
- Пиши на языке клиента, в стиле живого чата поддержки: вежливо, по делу, 1–4 предложения. Без канцелярита.
- Факты (пути в приложении, сроки, условия, суммы) — только из диалога и базы знаний. Чего там нет — не придумывай,
  поставь в текст пометку «[уточнить у специалиста]».
- Данные конкретного счёта — только после подтверждения личности: если её не было, реплика должна сначала запросить подтверждение.
- Никаких частных услуг, личных контактов и обещаний вне правил компании.
- what_changed — 1–3 коротких пункта ВСЕГДА на русском языке (даже если реплика на казахском или английском): что именно исправлено и почему (со ссылкой на критерий).
- Номера реплик в тексте не упоминай."""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "suggested": {"type": "string"},
        "what_changed": {"type": "array", "items": {"type": "string"}},
        "needs_specialist": {"type": "boolean"},
    },
    "required": ["suggested", "what_changed", "needs_specialist"],
}


def _failed(criterion: dict[str, Any], answer: Any) -> bool:
    a = normalize_answer(criterion, answer)
    if criterion["type"] == "flag":
        return a == "yes"
    if a == "na":
        return False
    if criterion["type"] == "binary":
        return a == "no"
    return a < max(s["value"] for s in criterion.get("scale") or [{"value": 0}])


def _target_turn(row: dict[str, Any], transcript: list[dict[str, Any]]) -> tuple[int | None, str]:
    """Какую реплику переписать: первая реплика-доказательство оператора; иначе — место, где действие ожидалось;
    иначе — последняя реплика оператора. mode: rewrite | add."""
    ops = {t["index"] for t in transcript if t["speaker"] == "operator" and not t.get("on_hold")}
    for e in row.get("evidence") or []:
        if e.get("index") in ops:
            return e["index"], "rewrite"
    exp = row.get("expected_range")
    if exp:
        cand = [i for i in sorted(ops) if exp["from"] <= i <= exp["to"]]
        if cand:
            return cand[0], "rewrite"
        return exp["from"], "add"
    return (max(ops), "rewrite") if ops else (None, "add")


def suggest(evaluation_id: str, criterion_id: str, refresh: bool = False) -> dict[str, Any]:
    ev = get_evaluation(evaluation_id)
    if not ev:
        raise EvaluationError(f"Оценка {evaluation_id} не найдена", 404)
    cached = (ev.get("coaching") or {}).get(criterion_id)
    if cached and not refresh:
        return {**cached, "cached": True}
    cl = checklist_store.get(ev["checklist_id"], ev["checklist_version"])
    crit = next((c for c in cl["criteria"] if c["id"] == criterion_id), None)
    rows = (ev.get("final") or {}).get("criteria") or ev["criteria"]
    row = next((r for r in rows if r["criterion_id"] == criterion_id), None)
    if not crit or not row:
        raise EvaluationError(f"Критерий {criterion_id} не найден в оценке", 404)
    if not _failed(crit, row["answer"]):
        raise EvaluationError("Пример нужен только для нарушенного критерия", 422)
    conv = conversation_store.get(ev["conversation_id"])
    turn, mode = _target_turn(row, conv["transcript"])
    original = conv["transcript"][turn]["text"] if turn is not None and mode == "rewrite" else None
    lang = conv.get("language") or "RU"
    task = (f"Целевая реплика оператора: [{turn}] «{original}» — перепиши её." if original is not None
            else f"Оператор пропустил действие около [{turn}] — дай реплику, которую нужно было написать.")
    user = "\n".join([
        f"Язык клиента: {lang} — пиши реплику на {LANG_NAME.get(lang, 'языке клиента')} языке.",
        f"Нарушенный критерий: {crit['title']} — {crit.get('description') or ''}",
        f"Ответ оценщика: {row['answer']}. Объяснение: {row.get('explanation') or ''}",
        task,
        "",
        "База знаний компании:",
        (cl.get("knowledge_base") or "нет").strip(),
        "",
        "Диалог:",
        format_transcript(conv),
    ])
    try:
        data, usage = complete_json([{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                                    SCHEMA, name="coaching", reasoning_effort="low")
    except LLMError as e:
        raise EvaluationError(f"Модель недоступна: {e}", 502) from e
    result = {
        "criterion_id": criterion_id,
        "turn_index": turn,
        "mode": "rewrite" if original is not None else "add",
        "original": original,
        "suggested": data["suggested"].strip(),
        "what_changed": [w.strip() for w in data.get("what_changed") or [] if w.strip()][:3],
        "needs_specialist": bool(data.get("needs_specialist")) or "уточнить у специалиста" in data["suggested"].lower(),
        "language": lang,
        "cost_usd": usage.get("cost_usd"),
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }
    ev.setdefault("coaching", {})[criterion_id] = result
    save(ev)
    return {**result, "cached": False}
