"""Настройки оценки, которые можно менять из UI (GET/PUT /api/settings) поверх значений из .env."""
from __future__ import annotations

from typing import Any

from .. import db
from ..config import get_settings

EFFORTS = ("low", "medium", "high", "xhigh", "max")
EDITABLE = {"model", "reasoning_effort", "self_consistency", "llm_concurrency"}


class SettingsError(ValueError):
    pass


def _stored() -> dict[str, Any]:
    with db.connect() as conn:
        return {r["key"]: db.loads(r["value"]) for r in conn.execute("SELECT key, value FROM settings")}


def runtime_settings() -> dict[str, Any]:
    s = get_settings()
    values = {"model": s.openai_model, "reasoning_effort": s.reasoning_effort,
              "self_consistency": s.self_consistency, "llm_concurrency": s.llm_concurrency}
    values.update({k: v for k, v in _stored().items() if k in EDITABLE})
    return values


def public_settings() -> dict[str, Any]:
    s = get_settings()
    return {**runtime_settings(), "provider": "openai", "llm_mode": s.llm_mode,
            "prices_per_1m": {"input": s.price_input_per_1m, "cached": s.price_cached_per_1m,
                              "output": s.price_output_per_1m}}


def update(values: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for k, v in values.items():
        if k not in EDITABLE:
            continue
        if k == "reasoning_effort":
            if v not in EFFORTS:
                raise SettingsError(f"reasoning_effort: одно из {', '.join(EFFORTS)}")
        elif k in ("self_consistency", "llm_concurrency"):
            try:
                v = int(v)
            except (TypeError, ValueError):
                raise SettingsError(f"{k}: целое число")
            if not 1 <= v <= (7 if k == "self_consistency" else 16):
                raise SettingsError(f"{k}: вне допустимого диапазона")
        elif k == "model":
            v = str(v).strip()
            if not v:
                raise SettingsError("model: пустое значение")
        clean[k] = v
    with db.connect() as conn:
        for k, v in clean.items():
            conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                         "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, db.dumps(v)))
    return public_settings()
