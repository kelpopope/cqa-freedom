"""Оценка одного диалога: промпт из чек-листа → LLM (строгий JSON) → проверка доказательств → scoring.

LLM отвечает только «что произошло» (ответы, ступени, реплики, объяснения). Баллы считает scoring.py.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from .. import db
from ..config import get_settings
from . import checklists as checklist_store
from . import conversations as conversation_store
from .llm_client import LLMError, complete_json
from .prompts import PROMPT_VERSION, build_prompt, build_response_schema
from .scoring import score

ASTANA = timezone(timedelta(hours=5))
MAX_ATTEMPTS = 3


class EvaluationError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502, evaluation: dict | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.evaluation = evaluation


# --- проверка доказательств -------------------------------------------------------------------------
_PUNCT = re.compile(r"[«»“”„\"'`’‘.,!?;:()\[\]—–-]+")


def _norm(text: str) -> str:
    text = (text or "").casefold().replace("ё", "е")
    text = _PUNCT.sub(" ", text)
    return " ".join(text.split())


def _quote_in(quote: str, text: str) -> bool:
    nt = _norm(text)
    fragments = [f for f in (_norm(p) for p in re.split(r"\.\.\.|…", quote)) if f]
    return bool(fragments) and all(f in nt for f in fragments)


def check_evidence(transcript: list[dict[str, Any]], items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    """Индекс существует и цитата есть в реплике. Если цитата найдена в другой реплике — исправляем индекс.
    Возвращает (доказательства с полем valid, все ли валидны)."""
    n = len(transcript)
    checked, all_valid = [], True
    for it in items or []:
        idx, quote = it.get("index"), (it.get("quote") or "").strip()
        valid = isinstance(idx, int) and 0 <= idx < n and (not quote or _quote_in(quote, transcript[idx]["text"]))
        if not valid and quote:
            hits = [t["index"] for t in transcript if _quote_in(quote, t["text"])]
            if hits:
                idx = min(hits, key=lambda h: abs(h - idx) if isinstance(idx, int) else h)
                valid = True
        checked.append({"index": idx if isinstance(idx, int) and 0 <= idx < n else None, "quote": quote, "valid": valid})
        all_valid &= valid
    return checked, all_valid


def _clamp_range(rng: dict[str, Any] | None, n: int) -> dict[str, int] | None:
    if not rng or n == 0:
        return None
    a = min(max(int(rng.get("from", 0)), 0), n - 1)
    b = min(max(int(rng.get("to", a)), 0), n - 1)
    return {"from": min(a, b), "to": max(a, b)}


# --- лента событий и контекст бота (базовая версия; правила — в rules.py) ----------------------------
_ESCALATION = re.compile(r"эскалац|transferred to an agent|операторға жіберілді|присоединился к разговору", re.I)
_IDENTITY = re.compile(r"личность клиента подтверждена|identity verified|жеке басы расталды", re.I)
_END = [
    ("operator_closed", re.compile(r"оператор закрыл чат|agent closed the chat|оператор чатты жапты", re.I)),
    ("connection_lost", re.compile(r"соединение с клиентом прервано|connection to customer lost|байланыс үзілді", re.I)),
    ("client_left", re.compile(r"клиент покинул чат|клиент завершил разговор|customer left|чаттан шықты", re.I)),
]


def build_timeline(transcript: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def first(pred):
        return next((t["index"] for t in transcript if pred(t)), None)

    escalation = first(lambda t: t["speaker"] == "system" and _ESCALATION.search(t["text"]))
    greeting = first(lambda t: t["speaker"] == "operator" and not t.get("on_hold"))
    identity = first(lambda t: t["speaker"] == "system" and _IDENTITY.search(t["text"]))
    end_kind, end_idx = None, None
    for t in reversed(transcript):
        if t["speaker"] == "system":
            kind = next((k for k, rx in _END if rx.search(t["text"])), None)
            if kind:
                end_kind, end_idx = kind, t["index"]
                break
    if end_kind is None:
        end_idx = next((t["index"] for t in reversed(transcript) if t["speaker"] == "operator" and not t.get("on_hold")), None)
        end_kind = "farewell" if end_idx is not None else None
    return [
        {"type": "escalation", "index": escalation},
        {"type": "operator_greeting", "index": greeting},
        {"type": "identity_verified", "index": identity},
        {"type": "data_disclosure", "index": None},
        {"type": "end", "index": end_idx, "kind": end_kind},
    ]


def build_bot_context(transcript: list[dict[str, Any]], timeline: list[dict[str, Any]], summary: str) -> dict | None:
    first_op = next((e["index"] for e in timeline if e["type"] == "operator_greeting"), None)
    escalation = next((e["index"] for e in timeline if e["type"] == "escalation"), None)
    end = escalation if escalation is not None else (first_op - 1 if first_op else None)
    if end is None or end < 0 or not any(t["speaker"] == "bot" for t in transcript[: end + 1]):
        return None
    return {"from": 0, "to": end, "summary": summary or None}


# --- оценка ---------------------------------------------------------------------------------------
def _sum_usage(total: dict[str, Any] | None, usage: dict[str, Any]) -> dict[str, Any]:
    if not total:
        return dict(usage)
    return {k: round(total.get(k, 0) + usage.get(k, 0), 6) if isinstance(usage.get(k), (int, float)) else usage[k]
            for k in usage}


def _save(evaluation: dict[str, Any]) -> None:
    with db.connect() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO evaluations
               (evaluation_id, conversation_id, checklist_id, checklist_version, status, data)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (evaluation["evaluation_id"], evaluation["conversation_id"], evaluation["checklist_id"],
             evaluation["checklist_version"], evaluation["status"], db.dumps(evaluation)))


def assemble(conversation: dict[str, Any], checklist: dict[str, Any], llm: dict[str, Any]) -> dict[str, Any]:
    """Из ответа LLM собирает Evaluation (без id/usage): проверка доказательств, баллы, флаги, needs_review."""
    transcript = conversation["transcript"]
    n = len(transcript)
    answers = {cid: v["answer"] for cid, v in llm["criteria"].items()}
    scored = score(checklist, answers)
    rows = {r["criterion_id"]: r for r in scored["criteria"]}

    criteria, review_reasons = [], []
    gross_types, gross_evidence = [], []
    for c in checklist["criteria"]:
        cid, item = c["id"], llm["criteria"][c["id"]]
        evidence, ev_valid = check_evidence(transcript, item.get("evidence") or [])
        answer = rows[cid]["answer"]
        if not evidence and not (answer == "no" and item.get("expected_range")) and c["type"] != "flag":
            ev_valid = False
        if c["type"] == "flag" and answer == "no":
            ev_valid = True
        row = {
            **rows[cid],
            "title": c["title"],
            "explanation": item.get("explanation") or "",
            "evidence": [{"index": e["index"], "quote": e["quote"]} for e in evidence if e["index"] is not None],
            "expected_range": _clamp_range(item.get("expected_range"), n) if answer == "no" else None,
            "na_reason": item.get("na_reason") if answer == "na" else None,
            "evidence_valid": ev_valid,
            "confidence": item.get("confidence", "high"),
            "source": "llm",
        }
        if c["type"] == "flag":
            row["flag_types"] = (item.get("flag_types") or []) if answer == "yes" else []
            if answer == "yes":
                gross_types += row["flag_types"]
                gross_evidence += row["evidence"]
        if not ev_valid:
            review_reasons.append(f"{cid}: доказательство не найдено в диалоге")
        if row["confidence"] == "low":
            review_reasons.append(f"{cid}: низкая уверенность модели")
        criteria.append(row)

    timeline = build_timeline(transcript)
    return {
        "criteria": criteria,
        "sections": scored["sections"],
        "total": scored["total"],
        "gross_error": {"flag": scored["gross_error"], "types": sorted(set(gross_types)),
                        "evidence": gross_evidence, "rules_agree": None},
        "needs_review": bool(review_reasons),
        "review_reasons": review_reasons,
        "timeline": timeline,
        "bot_context": build_bot_context(transcript, timeline, llm.get("client_request", "")),
        "client_request": llm.get("client_request", ""),
        "final": {"total": scored["total"], "overrides": []},
    }


def evaluate(conversation_id: str, checklist_id: str = "default", version: int | None = None,
             reasoning_effort: str | None = None) -> dict[str, Any]:
    conversation = conversation_store.get(conversation_id)
    if conversation is None:
        raise EvaluationError(f"Диалог {conversation_id} не найден", 404)
    checklist = checklist_store.get(checklist_id, version)
    if checklist is None:
        raise EvaluationError(f"Чек-лист {checklist_id} (версия {version or 'активная'}) не найден", 404)

    s = get_settings()
    effort = reasoning_effort or s.reasoning_effort
    evaluation: dict[str, Any] = {
        "evaluation_id": f"ev_{uuid.uuid4().hex[:12]}",
        "conversation_id": conversation_id,
        "checklist_id": checklist["checklist_id"],
        "checklist_version": checklist["version"],
        "status": "running",
        "model": {"provider": "openai", "name": s.openai_model, "reasoning_effort": effort,
                  "prompt_version": PROMPT_VERSION},
        "usage": None,
        "created_at": datetime.now(ASTANA).isoformat(timespec="seconds"),
    }

    messages = build_prompt(checklist, conversation)
    schema = build_response_schema(checklist)
    usage, last_error = None, None
    for _ in range(MAX_ATTEMPTS):
        try:
            llm, u = complete_json(messages, schema, reasoning_effort=effort)
            usage = _sum_usage(usage, u)
            evaluation.update(assemble(conversation, checklist, llm))
            evaluation["status"] = "done"
            evaluation["usage"] = usage
            _save(evaluation)
            return evaluation
        except (LLMError, KeyError, ValueError, TypeError) as e:
            last_error = f"{type(e).__name__}: {e}"

    evaluation.update({"status": "error", "error": last_error, "usage": usage, "criteria": [], "sections": [],
                       "total": None, "gross_error": {"flag": False, "types": [], "evidence": [], "rules_agree": None},
                       "needs_review": True, "timeline": build_timeline(conversation["transcript"]),
                       "bot_context": None, "final": {"total": None, "overrides": []}})
    _save(evaluation)
    raise EvaluationError(f"Оценка не удалась: {last_error}", 502, evaluation)
