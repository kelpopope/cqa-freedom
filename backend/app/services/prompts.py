"""Промпт и JSON-схема ответа собираются ДИНАМИЧЕСКИ из конфигурации чек-листа.

Ни один критерий здесь не захардкожен: всё, что относится к конкретному критерию (суть, условие N/A,
ступени шкалы, типы флагов, уточняющие правила `guidance`), берётся из чек-листа. Здесь — только
общие правила чтения диалога (роли, удержание, маски) и формат доказательств.

Порядок сообщений: system (правила + чек-лист — стабильная часть, кэшируется OpenAI) → user (диалог).
"""
from __future__ import annotations

from typing import Any

PROMPT_VERSION = "p7"

GENERAL_RULES = """Ты — эксперт отдела контроля качества брокера Freedom Broker. Ты оцениваешь работу ОПЕРАТОРА поддержки в одном диалоге по чек-листу.

Как читать диалог:
- Каждая реплика: [номер] время РОЛЬ: текст. Роли: CLIENT — клиент, OPERATOR — оцениваемый оператор, BOT — AI-бот AIM (шаблонные ответы до подключения оператора), SYSTEM — системные события.
- Оценивается ТОЛЬКО оператор. Реплики бота и системные события — контекст: оператор видит всю переписку клиента с ботом до эскалации.
- Системные события важны: эскалация на оператора, подтверждение личности клиента, «клиент покинул чат», «соединение прервано», «оператор закрыл чат», удержание.
- Пометка [HOLD] — клиент на удержании, оператор говорит с коллегой, клиент этого НЕ слышит. Такие реплики не являются общением с клиентом.
- Пометка [DUP] — дубль реплики из-за автотранскрипции, игнорируй.
- Звонки — шумная автотранскрипция: опечатки и искажённые слова не вина оператора; интонацию и дикцию по тексту не оценивай. Маски вида «[Имя скрыто]», «[Персональные данные скрыты]», «[Данные идентификации скрыты]» заменяют реальные данные, которые были произнесены.
- Диалог может быть на русском, казахском или английском — критерии одинаковы для всех языков.

Как отвечать по критерию:
- binary: "yes" — требование выполнено; "no" — действие было нужно, но пропущено или выполнено неправильно; "na" — только если выполнено условие N/A из критерия.
- scale: выбери ровно одну ступень из списка (строкой, например "15") по описаниям ступеней; "na" — только если разрешено и выполнено условие N/A.
- flag: "yes" — нарушение есть (укажи типы), "no" — нет.
- «Нет» и N/A — разные вещи: N/A ставится ТОЛЬКО по условию неприменимости, а не потому что оценить трудно.
- Сначала ищи нарушение по критерию. Если оператор допустил ошибку, относящуюся к критерию, ответ "no" (или низкая ступень), даже если формально условие N/A тоже выполняется.
- «Правила оценки» у критерия уточняют его суть и имеют приоритет над общими соображениями.

Доказательства (evidence):
- Указывай номера реальных реплик и короткую ДОСЛОВНУЮ цитату из текста этой реплики (5–15 слов, без изменений, на языке оригинала).
- Ставь доказательство ровно на ту реплику, где произошло событие: для "yes" — где требование выполнено; для "no" — реплика оператора, в которой допущена ошибка; если действие просто пропущено — в expected_range диапазон реплик, где его ожидали, а в evidence — реплика оператора, после которой оно должно было прозвучать; для "na" — реплика или системное событие, подтверждающее условие N/A. Для шкалы ниже максимума — реплика, из-за которой снижена оценка.
- explanation — 2–3 коротких предложения ПО-РУССКИ: (1) что сделал или не сделал оператор — со ссылкой [#9]; (2) почему это даёт именно такой ответ по правилу критерия; для шкалы — почему эта ступень, а не соседняя выше. Не пересказывай весь диалог, не придумывай того, чего нет в тексте. Для N/A заполни na_reason: какое условие выполнено.
- confidence: "low", если диалог допускает разные трактовки, иначе "high".

Дополнительно верни client_request: одно предложение по-русски — с чем пришёл клиент (с учётом сказанного боту).
После оценки критериев верни headline — 5–8 слов по-русски: суть диалога и главный итог или нарушение (например, «Предложение перевести деньги лично оператору», «Вход в кабинет: решено, без представления»), и summary — 1–2 предложения по-русски: с чем пришёл клиент и как отработал оператор."""

TURN_REVIEW_RULES = """
Разбор по репликам (turn_review) — делай ПЕРЕД ответами по критериям:
- Пройди по КАЖДОЙ реплике оператора из списка в конце диалога, по порядку.
- note — одно короткое предложение по-русски: что оператор сделал в этой реплике по отношению к клиенту.
- issues — критерии, которые нарушены именно в этой реплике (id критерия + короткая причина). Если реплика — место, где ожидалось пропущенное действие (не представился, не уточнил, не попрощался), тоже укажи здесь.
- strengths — критерии, которые выполнены именно в этой реплике (только id).
- Пустые списки, если реплика нейтральна. Ответы по критериям должны быть согласованы с разбором."""


def _criterion_block(c: dict[str, Any]) -> str:
    lines = [f"### {c['id']} — {c['title']} (тип: {c['type']}" + (f", вес {c['weight']}" if c["type"] != "flag" else "") + ")"]
    if c.get("description"):
        lines.append(f"Суть: {c['description']}")
    if c["type"] == "scale":
        lines.append("Ступени:")
        for st in c.get("scale") or []:
            lines.append(f"  - {st['value']}: {st.get('description') or ''}".rstrip())
    if c["type"] == "flag" and c.get("flag_types"):
        lines.append(f"Типы нарушений: {', '.join(c['flag_types'])}")
    if c.get("na_allowed"):
        lines.append(f"N/A разрешён, условие: {c.get('na_condition')}")
    elif c["type"] != "flag":
        lines.append("N/A НЕ разрешён — ответ только yes/no или ступень шкалы.")
    if c.get("guidance"):
        lines.append(f"Правила оценки: {c['guidance']}")
    return "\n".join(lines)


KNOWLEDGE_RULES = """Справочные факты компании (база знаний) — используй, чтобы проверить корректность консультации:
- если совет оператора противоречит факту по существу (условия, сроки, что разрешено, какое поручение или документ нужны, куда обращаться) — это ошибка консультации (ключевая рекомендация ошибочна или неполна);
- если отличается только название раздела или путь в интерфейсе (у приложения и веб-версии разные меню, названия меняются), это не ошибочная ключевая рекомендация: клиент подтвердил, что нашёл или у него получилось, — не снижай за это; не подтвердил — максимум на одну ступень ниже;
- если нужного факта в базе нет — оценивай по логике диалога и не придумывай правил компании."""


def build_system_prompt(checklist: dict[str, Any], turn_review: bool = True,
                        precedents: dict[str, list[dict[str, Any]]] | None = None) -> str:
    from .precedents import format_block
    parts = [GENERAL_RULES + (TURN_REVIEW_RULES if turn_review else ""), "", f"# Чек-лист «{checklist.get('name', '')}» (версия {checklist.get('version', '?')})"]
    sections = {s["id"]: s["title"] for s in checklist.get("sections") or []}
    current = object()
    for c in checklist["criteria"]:
        sid = c.get("section_id")
        if sid != current and sid in sections:
            parts.append(f"\n## {sections[sid]}")
        current = sid
        parts.append(_criterion_block(c))
        if precedents and precedents.get(c["id"]):
            parts.append(format_block(precedents[c["id"]]))
    notes = checklist.get("notes") or []
    if notes:
        parts.append("\n# Общие правила чек-листа")
        parts.extend(f"- {n}" for n in notes)
    kb = (checklist.get("knowledge_base") or "").strip()
    if kb:
        parts.append("\n# " + KNOWLEDGE_RULES)
        parts.append(kb)
    return "\n".join(parts)


def format_transcript(conversation: dict[str, Any]) -> str:
    out = []
    for t in conversation["transcript"]:
        tags = ("[HOLD] " if t.get("on_hold") else "") + ("[DUP] " if t.get("duplicate") else "")
        ts = t["timestamp"]
        if "T" in ts and t.get("offset_sec") is not None:  # ISO у звонков — компактнее как +MM:SS
            sec = int(t["offset_sec"])
            ts = f"+{sec // 60:02d}:{sec % 60:02d}"
        out.append(f"[{t['index']}] {ts} {t['speaker'].upper()}: {tags}{t['text']}")
    return "\n".join(out)


def operator_turns(conversation: dict[str, Any]) -> list[int]:
    """Реплики оператора, которые разбираются по отдельности (без удержания и дублей)."""
    return [t["index"] for t in conversation["transcript"]
            if t["speaker"] == "operator" and not t.get("on_hold") and not t.get("duplicate")]


def build_user_prompt(conversation: dict[str, Any], turn_review: bool = True) -> str:
    tail = "Оцени оператора по каждому критерию чек-листа."
    if turn_review:
        ops = operator_turns(conversation)
        tail = (f"Реплики оператора для разбора: {', '.join(f'[{i}]' for i in ops) or 'нет'}.\n"
                "Сначала разбери их по порядку (turn_review), затем оцени оператора по каждому критерию чек-листа.")
    return (f"Диалог {conversation['conversation_id']} · канал: {conversation.get('channel')} · "
            f"язык: {conversation.get('language')}\n\n{format_transcript(conversation)}\n\n{tail}")


def build_prompt(checklist: dict[str, Any], conversation: dict[str, Any], turn_review: bool = True,
                 precedents: dict[str, list[dict[str, Any]]] | None = None) -> list[dict[str, str]]:
    return [{"role": "system", "content": build_system_prompt(checklist, turn_review, precedents)},
            {"role": "user", "content": build_user_prompt(conversation, turn_review)}]


# --- JSON-схема ответа (Structured Outputs, strict) ------------------------------------------------
def answer_options(c: dict[str, Any]) -> list[str]:
    if c["type"] == "scale":
        opts = [str(st["value"]) for st in c.get("scale") or []]
    else:
        opts = ["yes", "no"]
    if c["type"] != "flag" and c.get("na_allowed"):
        opts.append("na")
    return opts


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


def _criterion_schema(c: dict[str, Any]) -> dict[str, Any]:
    props: dict[str, Any] = {
        "evidence": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["index", "quote"],
            "properties": {"index": {"type": "integer"}, "quote": {"type": "string"}}}},
        "expected_range": _nullable({"type": "object", "additionalProperties": False, "required": ["from", "to"],
                                     "properties": {"from": {"type": "integer"}, "to": {"type": "integer"}}}),
        "explanation": {"type": "string"},
        "na_reason": _nullable({"type": "string"}),
        "answer": {"type": "string", "enum": answer_options(c)},
        "confidence": {"type": "string", "enum": ["high", "low"]},
    }
    if c["type"] == "flag":
        item = {"type": "string", "enum": c["flag_types"]} if c.get("flag_types") else {"type": "string"}
        props["flag_types"] = {"type": "array", "items": item}
    return {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}


def _turn_review_schema(checklist: dict[str, Any]) -> dict[str, Any]:
    ids = {"type": "string", "enum": [c["id"] for c in checklist["criteria"]]}
    return {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["index", "note", "issues", "strengths"],
        "properties": {
            "index": {"type": "integer"},
            "note": {"type": "string"},
            "issues": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": ["criterion_id", "note"],
                "properties": {"criterion_id": ids, "note": {"type": "string"}}}},
            "strengths": {"type": "array", "items": ids},
        }}}


def build_response_schema(checklist: dict[str, Any], turn_review: bool = True) -> dict[str, Any]:
    criteria = {c["id"]: _criterion_schema(c) for c in checklist["criteria"]}
    props: dict[str, Any] = {"client_request": {"type": "string"}}
    if turn_review:                      # разбор идёт первым: модель сначала анализирует реплики, потом выносит вердикт
        props["turn_review"] = _turn_review_schema(checklist)
    props["criteria"] = {"type": "object", "additionalProperties": False, "required": list(criteria),
                         "properties": criteria}
    props["headline"] = {"type": "string"}            # после вердиктов — заголовок и резюме для интерфейса
    props["summary"] = {"type": "string"}
    return {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}
