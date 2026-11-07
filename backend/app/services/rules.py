"""Радар комплаенса: детерминированные сигналы грубых ошибок без LLM (RU / KZ / EN).

Это второй, независимый от модели сигнал. Совпадение с LLM повышает доверие (source = "llm+rules"),
расхождение отправляет оценку на проверку человеку (needs_review). Правила намеренно узкие:
ловят явные формулировки, а не перефразы — перефразы ловит модель.

Учитываются только реплики оператора вне удержания (во время hold клиент не слышит).
"""
from __future__ import annotations

import re
from typing import Any

FLAGS = ("insult", "private_service", "confidential_disclosure")

_I = re.I | re.U

PRIVATE_SERVICE = [
    # RU
    r"скин\w*\s+(?:мне\s+)?деньги", r"переве\w*\s+(?:деньги\s+)?мне(?:\s+на)?", r"(?:мне\s+на|на\s+мою)\s+карт[уы]",
    r"лично\s+предлагаю", r"через\s+компанию\s+(?:оформлять\s+)?не\s+надо", r"личн\w+\s+номер",
    r"частн\w+\s+консультац", r"за\s+плату\s+(?:объясню|расскажу|подскажу|помогу)", r"сам\s+(?:их\s+)?вложу",
    # KZ
    r"жеке\s+нөмір", r"ақылы\s+түрде", r"картама", r"маған\s+(?:ақша\s+)?аудар",
    # EN
    r"personal\s+(?:phone\s+)?number", r"\bI\s+charge\b", r"private\s+(?:advice|consultation)",
    r"(?:send|transfer)\s+(?:the\s+)?money\s+to\s+(?:me|my)", r"\bmy\s+(?:personal\s+)?card\b",
]
INSULT = [
    r"читать\s+умеете", r"вы\s+что,?\s+не\s+понимаете", r"\bтуп\w*\b", r"\bидиот\w*", r"\bдебил\w*", r"бестолков\w*",
    r"оқи\s+алмайсыз\s+ба", r"\bақымақ\w*",
    r"can'?t\s+you\s+read", r"are\s+you\s+(?:stupid|dumb|blind)", r"\bidiot\w*",
]
# Сведения о конкретном счёте клиента: «вижу ограничение на вашем счёте», «I can see … on your account»
ACCOUNT_DATA = [
    r"(?:на|в)\s+ваш\w*\s+сч[её]т\w*\s+(?:есть|стоит|установлен|наложен|висит|числится|отображается\s+ограничен)",
    r"(?:вижу|посмотрел\w*|проверил\w*)[^.?!]{0,60}ваш\w*\s+сч[её]т",
    r"ваш\w*\s+сч[её]т\w*[^.?!]{0,40}(?:ограничен|заблокир|арест)",
    r"шотыңызда[^.?!]{0,80}(?:көріп\s+тұрмын|қойылған|бар)",
    r"\bI\s+(?:can\s+)?see\b[^.?!]{0,60}\byour\s+account\b", r"\byour\s+account\s+(?:has|is|shows)\b[^.?!]{0,40}(?:restrict|block|limit|frozen)",
]
# Отказ от идентификации перед раскрытием: «подтверждение не нужно», «we can skip that»
IDENTITY_SKIP = [
    r"подтверждени\w*\s+(?:пока\s+)?не\s+нужн", r"можно\s+без\s+(?:проверки|подтверждения)",
    r"растаудың\s+қажеті\s+жоқ", r"\bwe\s+can\s+skip\s+(?:that|it|verification)\b", r"no\s+need\s+to\s+verify",
]
IDENTITY_VERIFIED = re.compile(r"личность клиента подтверждена|identity verified|жеке басы расталды", _I)

_COMPILED = {
    "private_service": [re.compile(p, _I) for p in PRIVATE_SERVICE],
    "insult": [re.compile(p, _I) for p in INSULT],
    "account_data": [re.compile(p, _I) for p in ACCOUNT_DATA],
    "identity_skip": [re.compile(p, _I) for p in IDENTITY_SKIP],
}


def _first_match(kind: str, text: str) -> re.Match | None:
    for rx in _COMPILED[kind]:
        m = rx.search(text)
        if m:
            return m
    return None


def _quote(text: str, m: re.Match, pad: int = 40) -> str:
    a, b = max(m.start() - pad, 0), min(m.end() + pad, len(text))
    a = text.rfind(" ", 0, a) + 1 if a > 0 else 0
    b_space = text.find(" ", b)
    b = b_space if b_space != -1 else len(text)
    return text[a:b].strip()


def analyze(transcript: list[dict[str, Any]]) -> dict[str, Any]:
    """Сигналы по диалогу: {"signals": [...], "types": [...], "identity_verified_at": idx|None,
    "first_disclosure": idx|None}. Сигнал: {type, index, quote, rule}."""
    verified_at = next((t["index"] for t in transcript
                        if t["speaker"] == "system" and IDENTITY_VERIFIED.search(t["text"])), None)
    signals: list[dict[str, Any]] = []
    for t in transcript:
        if t["speaker"] != "operator" or t.get("on_hold"):
            continue
        text = t["text"]
        for kind in ("private_service", "insult"):
            m = _first_match(kind, text)
            if m:
                signals.append({"type": kind, "index": t["index"], "quote": _quote(text, m), "rule": m.re.pattern})
        before_verification = verified_at is None or t["index"] < verified_at
        if before_verification:
            m = _first_match("account_data", text)
            if m:
                signals.append({"type": "confidential_disclosure", "index": t["index"], "quote": _quote(text, m),
                                "rule": m.re.pattern})
            m = _first_match("identity_skip", text)
            if m:
                signals.append({"type": "identity_skip", "index": t["index"], "quote": _quote(text, m),
                                "rule": m.re.pattern})
    types = sorted({s["type"] for s in signals if s["type"] in FLAGS})
    first_disclosure = next((s["index"] for s in signals if s["type"] == "confidential_disclosure"), None)
    return {"signals": signals, "types": types, "identity_verified_at": verified_at,
            "first_disclosure": first_disclosure}
