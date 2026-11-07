"""Вызов OpenAI (Responses API + Structured Outputs). Возвращает разобранный JSON и usage со стоимостью."""
from __future__ import annotations

import json
import time
from typing import Any

from openai import OpenAI

from ..config import get_settings


class LLMError(RuntimeError):
    pass


_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    s = get_settings()
    if not s.openai_api_key:
        raise LLMError("OPENAI_API_KEY не задан в .env")
    if _client is None:
        _client = OpenAI(api_key=s.openai_api_key, timeout=180, max_retries=2)
    return _client


def cost_usd(input_tokens: int, cached_tokens: int, output_tokens: int) -> float:
    s = get_settings()
    fresh = max(input_tokens - cached_tokens, 0)
    return round((fresh * s.price_input_per_1m + cached_tokens * s.price_cached_per_1m
                  + output_tokens * s.price_output_per_1m) / 1_000_000, 6)


def complete_json(messages: list[dict[str, str]], schema: dict[str, Any], name: str = "evaluation",
                  reasoning_effort: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    s = get_settings()
    started = time.monotonic()
    try:
        resp = _get_client().responses.create(
            model=s.openai_model,
            input=messages,
            reasoning={"effort": reasoning_effort or s.reasoning_effort},
            text={"format": {"type": "json_schema", "name": name, "schema": schema, "strict": True}},
        )
    except LLMError:
        raise
    except Exception as e:  # сеть, лимиты, 4xx/5xx
        raise LLMError(f"OpenAI: {type(e).__name__}: {str(e)[:300]}") from e

    text = resp.output_text or ""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise LLMError(f"Модель вернула не JSON: {text[:200]!r}") from e

    u = resp.usage
    input_tokens = getattr(u, "input_tokens", 0) or 0
    output_tokens = getattr(u, "output_tokens", 0) or 0
    cached = getattr(getattr(u, "input_tokens_details", None), "cached_tokens", 0) or 0
    reasoning = getattr(getattr(u, "output_tokens_details", None), "reasoning_tokens", 0) or 0
    usage = {
        "input_tokens": input_tokens,
        "cached_tokens": cached,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning,
        "cost_usd": cost_usd(input_tokens, cached, output_tokens),
        "duration_ms": int((time.monotonic() - started) * 1000),
    }
    return data, usage
