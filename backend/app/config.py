"""Настройки из .env (корень репозитория). Секреты — только здесь, во фронт не попадают."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
FRONTEND_DIR = ROOT_DIR / "frontend"
DATA_DIR = ROOT_DIR / "data"
CHECKLISTS_DIR = DATA_DIR / "checklists"

load_dotenv(ROOT_DIR / ".env")


def _path(value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else ROOT_DIR / p


@dataclass(frozen=True)
class Settings:
    openai_api_key: str
    openai_model: str
    org_api_url: str
    db_path: Path
    reasoning_effort: str      # low | medium | high | xhigh | max (gpt-6.1-sol не поддерживает temperature)
    llm_concurrency: int       # одновременных запросов к OpenAI при пакетной оценке
    price_input_per_1m: float  # $ за 1 млн входных токенов
    price_cached_per_1m: float
    price_output_per_1m: float

    @property
    def llm_mode(self) -> str:
        return "cloud" if self.openai_api_key else "no_key"


def get_settings() -> Settings:
    return Settings(
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-6.1-sol").strip() or "gpt-6.1-sol",
        org_api_url=os.getenv("ORG_API_URL", "").strip(),
        db_path=_path(os.getenv("DB_PATH", "cqa.db").strip() or "cqa.db"),
        reasoning_effort=os.getenv("OPENAI_REASONING_EFFORT", "medium").strip() or "medium",
        llm_concurrency=int(os.getenv("LLM_CONCURRENCY", "6") or 6),
        price_input_per_1m=float(os.getenv("OPENAI_PRICE_INPUT_PER_1M", "2.0") or 2.0),
        price_cached_per_1m=float(os.getenv("OPENAI_PRICE_CACHED_PER_1M", "0.10") or 0.10),
        price_output_per_1m=float(os.getenv("OPENAI_PRICE_OUTPUT_PER_1M", "10.0") or 10.0),
    )
