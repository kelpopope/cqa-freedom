"""Оценка одного диалога: промпт из чек-листа → LLM (строгий JSON) → проверка доказательств → scoring.

LLM отвечает только «что произошло» (ответы, ступени, реплики, объяснения). Баллы считает scoring.py.
"""
from __future__ import annotations

import re
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any

from .. import db
from . import checklists as checklist_store
from . import rules
from . import conversations as conversation_store
from .llm_client import LLMError, complete_json
from .prompts import PROMPT_VERSION, build_prompt, build_response_schema
from .scoring import apply_overrides, normalize_answer, score
from .settings import runtime_settings

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
    events = [
        {"type": "escalation", "index": escalation},
        {"type": "operator_greeting", "index": greeting},
        {"type": "identity_verified", "index": identity},
        {"type": "end", "index": end_idx, "kind": end_kind},
    ]
    # Только произошедшие события: отсутствие идентификации — это отсутствие события, а не событие с null.
    return [e for e in events if e["index"] is not None]


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


_IDENTITY_CRITERION = re.compile(r"идентифик|identif|жеке бас", re.I)
FLAG_LABELS = {"insult": "оскорбление", "private_service": "услуга в обход компании",
               "confidential_disclosure": "раскрытие конфиденциальной информации"}


def apply_rules(checklist: dict[str, Any], transcript: list[dict[str, Any]], criteria: list[dict[str, Any]],
                gross: dict[str, Any], timeline: list[dict[str, Any]], review_reasons: list[str]) -> None:
    """Радар комплаенса: сверяет решение модели по грубым ошибкам с детерминированными правилами.
    Решение остаётся за моделью; совпадение → source «llm+rules», расхождение → needs_review с причиной."""
    radar = rules.analyze(transcript)
    flag_rows = [r for r, c in zip(criteria, checklist["criteria"])
                 if c["type"] == "flag" and set(c.get("flag_types") or []) & set(rules.FLAGS)]
    signals = [s for s in radar["signals"] if s["type"] in rules.FLAGS]
    gross["rules_types"] = radar["types"]
    gross["rules_signals"] = radar["signals"]
    if flag_rows:
        llm_flag, rules_flag = bool(gross["flag"]), bool(radar["types"])
        gross["rules_agree"] = llm_flag == rules_flag
        for row in flag_rows:
            row["rules_signals"] = signals
            if gross["rules_agree"] and rules_flag:
                row["source"] = "llm+rules"
        if rules_flag and not llm_flag:
            found = "; ".join(f"{FLAG_LABELS.get(s['type'], s['type'])} в реплике [#{s['index']}] «{s['quote']}»"
                              for s in signals[:3])
            review_reasons.append(f"Радар: правила нашли признаки грубой ошибки, модель — нет: {found}")
        elif llm_flag and not rules_flag:
            review_reasons.append("Радар: модель отметила грубую ошибку, явных формулировок правила не нашли — "
                                  "проверьте вручную")

    # Раскрытие данных счёта до подтверждения личности → критерий идентификации должен быть «Нет»
    if radar["first_disclosure"] is not None:
        timeline.append({"type": "data_disclosure", "index": radar["first_disclosure"]})
        timeline.sort(key=lambda e: (e["index"] if e["index"] is not None else 10 ** 6))
        for row, c in zip(criteria, checklist["criteria"]):
            if c["type"] == "binary" and _IDENTITY_CRITERION.search(c["title"]) and row["answer"] != "no":
                review_reasons.append(f"Радар: данные счёта раскрыты до подтверждения личности [#{radar['first_disclosure']}],"
                                      f" а по критерию «{c['title']}» стоит «{row['answer']}»")


def assemble(conversation: dict[str, Any], checklist: dict[str, Any], llm: dict[str, Any],
             votes: dict[str, list] | None = None) -> dict[str, Any]:
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
        if votes and len(votes.get(cid, [])) > 1:
            v = votes[cid]
            row["votes"] = v
            row["agreement"] = round(v.count(rows[cid]["answer"]) / len(v), 2)
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
    gross = {"flag": scored["gross_error"], "types": sorted(set(gross_types)), "evidence": gross_evidence,
             "rules_agree": None}
    apply_rules(checklist, transcript, criteria, gross, timeline, review_reasons)
    return {
        "criteria": criteria,
        "sections": scored["sections"],
        "total": scored["total"],
        "gross_error": gross,
        "needs_review": bool(review_reasons),
        "review_reasons": review_reasons,
        "timeline": timeline,
        "bot_context": build_bot_context(transcript, timeline, llm.get("client_request", "")),
        "client_request": llm.get("client_request", ""),
        "final": {"total": scored["total"], "overrides": []},
    }


def recompute_final(evaluation: dict[str, Any], checklist: dict[str, Any]) -> dict[str, Any]:
    """Пересчёт финала по решениям апелляций: ответы ИИ + overrides → scoring.py (п. 7 контракта)."""
    answers = {c["criterion_id"]: c["answer"] for c in evaluation["criteria"]}
    overrides = evaluation.get("final", {}).get("overrides", [])
    scored = score(checklist, apply_overrides(answers, overrides))
    overridden = {o["criterion_id"] for o in overrides}
    final = {
        "total": scored["total"],
        "overrides": overrides,
        "criteria": [{**{k: r[k] for k in ("criterion_id", "answer", "raw_points", "points", "effective_weight")},
                      "overridden": r["criterion_id"] in overridden} for r in scored["criteria"]],
        "sections": scored["sections"],
        "gross_error": scored["gross_error"],
    }
    evaluation["final"] = final
    return final


_SAME_KEYS = ("type", "title", "description", "scale", "na_allowed", "na_condition")


def carried_overrides(conversation_id: str, checklist: dict[str, Any], new_eval: dict[str, Any]) -> list[dict]:
    """Решения проверяющего — истина от человека: при переоценке они переносятся в новую оценку, если оспоренный
    критерий в новой версии чек-листа не изменился по сути (иначе решение относится к старой формулировке)."""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT data FROM evaluations WHERE conversation_id=? AND status='done' ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (conversation_id,)).fetchone()
    prev = db.loads(row["data"]) if row else None
    if not prev or not prev.get("final", {}).get("overrides"):
        return []
    old_cl = checklist_store.get(prev["checklist_id"], prev["checklist_version"])
    if not old_cl:
        return []
    old = {c["id"]: c for c in old_cl["criteria"]}
    new = {c["id"]: c for c in checklist["criteria"]}
    ai = {c["criterion_id"]: c["answer"] for c in new_eval["criteria"]}
    out = []
    for o in prev["final"]["overrides"]:
        cid = o["criterion_id"]
        if cid not in new or cid not in old or any(old[cid].get(k) != new[cid].get(k) for k in _SAME_KEYS):
            continue
        if ai.get(cid) == o["to"]:
            continue                              # модель и так согласна с решением
        out.append({**o, "from": ai.get(cid), "carried_from": prev["evaluation_id"]})
    return out


def save(evaluation: dict[str, Any]) -> None:
    _save(evaluation)


def get_evaluation(evaluation_id: str) -> dict[str, Any] | None:
    with db.connect() as conn:
        row = conn.execute("SELECT data FROM evaluations WHERE evaluation_id=?", (evaluation_id,)).fetchone()
    return db.loads(row["data"]) if row else None


def history(conversation_id: str) -> list[dict[str, Any]]:
    """Все оценки диалога (разные версии чек-листа и повторные прогоны) — кратко, новые сверху."""
    with db.connect() as conn:
        rows = conn.execute("SELECT data FROM evaluations WHERE conversation_id=? ORDER BY created_at DESC, rowid DESC",
                            (conversation_id,)).fetchall()
    out = []
    for r in rows:
        ev = db.loads(r["data"])
        out.append({k: ev.get(k) for k in ("evaluation_id", "checklist_id", "checklist_version", "status",
                                           "total", "created_at", "model", "needs_review")}
                   | {"final_total": (ev.get("final") or {}).get("total"),
                      "gross_error": bool((ev.get("gross_error") or {}).get("flag")),
                      "answers": {c["criterion_id"]: c["answer"] for c in ev.get("criteria", [])}})
    return out


def validate_llm(checklist: dict[str, Any], llm: dict[str, Any]) -> None:
    """Ответ модели допустим по чек-листу (иначе ScoringError/KeyError → повторный запрос)."""
    for c in checklist["criteria"]:
        normalize_answer(c, llm["criteria"][c["id"]]["answer"])


def vote(checklist: dict[str, Any], samples: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, list]]:
    """Self-consistency: по каждому критерию — ответ большинства. Ничья на шкале — медиана ступеней,
    иначе — ответ первого прогона. Объяснение и доказательства берутся из прогона с победившим ответом."""
    merged = {"client_request": samples[0].get("client_request", ""), "criteria": {}}
    votes: dict[str, list] = {}
    for c in checklist["criteria"]:
        cid = c["id"]
        answers = [normalize_answer(c, s["criteria"][cid]["answer"]) for s in samples]
        votes[cid] = answers
        counts = Counter(answers)
        top = max(counts.values())
        leaders = [a for a in answers if counts[a] == top]
        winner = leaders[0]
        if len(set(leaders)) > 1 and c["type"] == "scale":
            nums = sorted(a for a in answers if a != "na")
            if nums:
                winner = nums[(len(nums) - 1) // 2]
        idx = next(i for i, a in enumerate(answers) if a == winner)
        item = dict(samples[idx]["criteria"][cid])
        item["answer"] = str(winner) if c["type"] == "scale" and winner != "na" else winner
        if counts[winner] * 2 <= len(answers):          # нет строгого большинства
            item["confidence"] = "low"
        merged["criteria"][cid] = item
    return merged, votes


def evaluate(conversation_id: str, checklist_id: str = "default", version: int | None = None,
             reasoning_effort: str | None = None, samples: int | None = None) -> dict[str, Any]:
    conversation = conversation_store.get(conversation_id)
    if conversation is None:
        raise EvaluationError(f"Диалог {conversation_id} не найден", 404)
    checklist = checklist_store.get(checklist_id, version)
    if checklist is None:
        raise EvaluationError(f"Чек-лист {checklist_id} (версия {version or 'активная'}) не найден", 404)

    s = runtime_settings()
    effort = reasoning_effort or s["reasoning_effort"]
    model_name = s["model"]
    evaluation: dict[str, Any] = {
        "evaluation_id": f"ev_{uuid.uuid4().hex[:12]}",
        "conversation_id": conversation_id,
        "checklist_id": checklist["checklist_id"],
        "checklist_version": checklist["version"],
        "status": "running",
        "model": {"provider": "openai", "name": model_name, "reasoning_effort": effort,
                  "prompt_version": PROMPT_VERSION},
        "usage": None,
        "created_at": datetime.now(ASTANA).isoformat(timespec="seconds"),
    }

    messages = build_prompt(checklist, conversation)
    schema = build_response_schema(checklist)
    n = max(1, int(samples or s["self_consistency"]))
    evaluation["model"]["samples"] = n
    started = time.monotonic()

    def one_sample(_):
        usage, error = None, None
        for _attempt in range(MAX_ATTEMPTS):
            try:
                llm, u = complete_json(messages, schema, reasoning_effort=effort, model=model_name)
                usage = _sum_usage(usage, u)
                validate_llm(checklist, llm)
                return llm, usage, None
            except (LLMError, KeyError, ValueError, TypeError) as e:
                error = f"{type(e).__name__}: {e}"
        return None, usage, error

    if n == 1:
        results = [one_sample(0)]
    else:
        with ThreadPoolExecutor(n) as ex:
            results = list(ex.map(one_sample, range(n)))

    usage = None
    for _, u, _ in results:
        if u:
            usage = _sum_usage(usage, u)
    if usage:
        usage["duration_ms"] = int((time.monotonic() - started) * 1000)
    samples_ok = [llm for llm, _, _ in results if llm is not None]
    last_error = next((err for _, _, err in results if err), None)

    if samples_ok:
        merged, votes = vote(checklist, samples_ok)
        evaluation.update(assemble(conversation, checklist, merged, votes))
        evaluation["final"]["overrides"] = carried_overrides(conversation_id, checklist, evaluation)
        recompute_final(evaluation, checklist)
        evaluation["status"] = "done"
        evaluation["usage"] = usage
        _save(evaluation)
        return evaluation

    evaluation.update({"status": "error", "error": last_error, "usage": usage, "criteria": [], "sections": [],
                       "total": None, "gross_error": {"flag": False, "types": [], "evidence": [], "rules_agree": None},
                       "needs_review": True, "timeline": build_timeline(conversation["transcript"]),
                       "bot_context": None, "final": {"total": None, "overrides": []}})
    _save(evaluation)
    raise EvaluationError(f"Оценка не удалась: {last_error}", 502, evaluation)
