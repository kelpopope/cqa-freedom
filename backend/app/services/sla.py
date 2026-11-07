"""SLA первой реакции: оператор должен ответить в течение 3 минут после перевода чата на него.

По чек-листу порог первой реакции в баллы НЕ входит — это отдельный операционный показатель
(организаторы: «это SLA»). Считается детерминированно по меткам времени, без LLM.
"""
from __future__ import annotations

import re
from typing import Any

# Перевод чата на оператора (общий для ленты событий в evaluator и для SLA).
ESCALATION = re.compile(r"эскалац|transferred to an agent|операторға жіберілді|присоединился к разговору", re.I)

FIRST_RESPONSE_SEC = 180


def first_response(conversation: dict[str, Any], threshold_sec: int = FIRST_RESPONSE_SEC) -> dict[str, Any] | None:
    """None — не применимо (звонок или нет события перевода на оператора)."""
    if conversation.get("channel") == "voice call":
        return None
    tr = conversation.get("transcript") or []
    esc = next((t for t in tr if t["speaker"] == "system" and ESCALATION.search(t["text"])), None)
    if not esc or esc.get("offset_sec") is None:
        return None
    reply = next((t for t in tr[esc["index"] + 1:] if t["speaker"] == "operator" and not t.get("on_hold")
                  and t.get("offset_sec") is not None), None)
    base = {"threshold_sec": threshold_sec, "escalation_index": esc["index"]}
    if not reply:
        return {**base, "first_reply_index": None, "wait_sec": None, "breached": None}
    wait = max(0, reply["offset_sec"] - esc["offset_sec"])
    return {**base, "first_reply_index": reply["index"], "wait_sec": wait, "breached": wait > threshold_sec}


def summarize(results: list[dict[str, Any] | None], threshold_sec: int = FIRST_RESPONSE_SEC) -> dict[str, Any]:
    measured = [r for r in results if r and r["wait_sec"] is not None]
    waits = sorted(r["wait_sec"] for r in measured)
    return {
        "threshold_sec": threshold_sec,
        "measured": len(measured),
        "breached": sum(1 for r in measured if r["breached"]),
        "avg_wait_sec": round(sum(waits) / len(waits), 1) if waits else None,
        "max_wait_sec": waits[-1] if waits else None,
    }
