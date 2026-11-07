"""Адаптер API организаторов + нормализация диалогов к схеме Conversation (API_CONTRACT §1).

Нормализация:
- index = позиция в исходном массиве (порядок не меняем, даже если таймкоды звонка идут не по возрастанию);
- timestamp — исходная строка; offset_sec — секунды от начала (MM:SS у чатов, ISO 8601 у звонков);
- speaker приводится к client | operator | bot | system;
- on_hold = True для реплик между «Клиент находится на удержании» и «…больше не находится на удержании»:
  в это время оператор говорит с коллегой, клиент этого не слышит;
- duplicate = True, если та же роль + тот же текст + та же метка времени, что у предыдущей реплики (дубль транскрибации).
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx

from ..config import DATA_DIR, get_settings

RAW_DIR = DATA_DIR / "raw"  # кэш ответов API организаторов (в .gitignore)

SPEAKERS = {"client", "operator", "bot", "system"}
SPEAKER_ALIASES = {
    "customer": "client", "user": "client", "клиент": "client",
    "agent": "operator", "оператор": "operator",
    "assistant": "bot", "ivr": "bot", "бот": "bot",
}

# Удержание (hold). RU — из DEV; KZ/EN — на случай контрольного набора.
HOLD_END = re.compile(r"больше не находится на удержании|no longer on hold|taken off hold|hold ended|"
                      r"ұстаудан шықты|күту режимінен шықты", re.I)
HOLD_START = re.compile(r"находится на удержании|placed on hold|is on hold|on hold|ұстауда|күту режимінде", re.I)


class FetchError(RuntimeError):
    pass


# --- время -----------------------------------------------------------------------------------------
def _parse_mmss(ts: str) -> float | None:
    m = re.fullmatch(r"\s*(?:(\d+):)?(\d+):(\d{1,2}(?:\.\d+)?)\s*", ts)
    if not m:
        return None
    h, mm, ss = m.groups()
    return (int(h) * 3600 if h else 0) + int(mm) * 60 + float(ss)


def _parse_iso(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def compute_offsets(timestamps: list[str]) -> list[float | None]:
    """Секунды от начала диалога. Для ISO — от самой ранней метки в диалоге."""
    rel = [_parse_mmss(ts) if isinstance(ts, str) else None for ts in timestamps]
    if any(v is not None for v in rel):
        return [round(v, 2) if v is not None else None for v in rel]
    abs_ = [_parse_iso(ts) if isinstance(ts, str) else None for ts in timestamps]
    known = [d for d in abs_ if d is not None]
    if not known:
        return [None] * len(timestamps)
    start = min(known)
    return [round((d - start).total_seconds(), 2) if d is not None else None for d in abs_]


# --- реплики ---------------------------------------------------------------------------------------
def normalize_speaker(raw: Any) -> str:
    s = str(raw or "").strip().lower()
    s = SPEAKER_ALIASES.get(s, s)
    return s if s in SPEAKERS else "system"


def normalize_transcript(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    timestamps = [str(t.get("timestamp") or "") for t in turns]
    offsets = compute_offsets(timestamps)
    result, on_hold, prev = [], False, None
    for i, t in enumerate(turns):
        speaker = normalize_speaker(t.get("speaker"))
        text = str(t.get("text") or "").strip()
        hold_marker = False
        if speaker == "system":
            if HOLD_END.search(text):
                on_hold, hold_marker = False, True
            elif HOLD_START.search(text):
                on_hold, hold_marker = True, True
        turn = {
            "index": i,
            "timestamp": timestamps[i],
            "offset_sec": offsets[i],
            "speaker": speaker,
            "text": text,
            "on_hold": on_hold and not hold_marker,
            "duplicate": bool(prev and prev["speaker"] == speaker and prev["text"] == text
                              and prev["timestamp"] == timestamps[i] and text),
        }
        result.append(turn)
        prev = turn
    return result


# --- имя оператора ---------------------------------------------------------------------------------
_NAME = r"([A-ZА-ЯЁӘҒҚҢӨҰҮҺІ][a-zа-яёәғқңөұүһі]+)"
_NAME_PATTERNS = [  # (шаблон, казахский — отрезать связку «-мын/-пін»)
    (re.compile(r"меня зовут\s+" + _NAME, re.I), False),
    (re.compile(r"(?:^|[\s,.!])я\s+" + _NAME, re.I), False),
    (re.compile(r"менің атым\s+" + _NAME, re.I), False),
    (re.compile(r"(?:^|[\s,.!])мен\s+" + _NAME, re.I), True),
    (re.compile(r"my name is\s+" + _NAME, re.I), False),
    (re.compile(r"\b(?:I'm|I am|this is)\s+" + _NAME, re.I), False),
]
_KZ_COPULA = re.compile(r"(мын|мін|пын|пін|бын|бін)$")


def extract_operator_name(transcript: list[dict[str, Any]]) -> str | None:
    """Имя из первой реплики оператора («я Кирилл», «мен Нұрланмын», «I'm Maya»). Маска → None."""
    first = next((t["text"] for t in transcript if t["speaker"] == "operator" and not t.get("on_hold")), "")
    for pattern, kz in _NAME_PATTERNS:
        for m in pattern.finditer(first):
            name = m.group(1)
            if not name[0].isupper():
                continue
            if kz and len(name) > 6:
                name = _KZ_COPULA.sub("", name)
            return name
    return None


# --- диалог ----------------------------------------------------------------------------------------
def normalize_conversation(record: dict[str, Any], group_id: str) -> dict[str, Any]:
    cid = str(record.get("conversation_id") or "").strip()
    if not cid:
        raise FetchError("У диалога нет conversation_id")
    transcript = normalize_transcript(record.get("transcript") or [])
    channel = str(record.get("channel") or "agent chat").strip()
    return {
        "conversation_id": cid,
        "group_id": group_id,
        "channel": channel,
        "language": str(record.get("language") or "").strip().upper() or None,
        "audio_url": record.get("audio_url") or None,
        "operator_name": extract_operator_name(transcript),
        "transcript": transcript,
    }


# --- API организаторов -----------------------------------------------------------------------------
def _cache_path(group_id: str):
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", group_id)
    return RAW_DIR / f"{safe}.json"


def fetch_dataset(group_id: str, timeout: float = 90) -> tuple[list[dict[str, Any]], str]:
    """Скачивает набор. Возвращает (records, source), source = "api" | "cache".

    group_id добавляется к адресу строкой: httpx с params= затёр бы подпись (sig) в адресе.
    При недоступности API используется последний успешный ответ из data/raw/<group_id>.json.
    """
    group_id = group_id.strip()
    if not group_id:
        raise FetchError("Не указан group_id")
    url = get_settings().org_api_url
    error = None
    if url:
        sep = "&" if "?" in url else "?"
        try:
            resp = httpx.get(f"{url}{sep}group_id={quote(group_id, safe='')}", timeout=timeout)
            payload = resp.json()
            if resp.status_code != 200 or not payload.get("isSuccess", True):
                raise FetchError(f"API организаторов: HTTP {resp.status_code}, {payload.get('error') or 'ошибка'}")
            records = (payload.get("data") or {}).get("records")
            if records is None:
                raise FetchError("API организаторов: в ответе нет data.records")
            RAW_DIR.mkdir(parents=True, exist_ok=True)
            _cache_path(group_id).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            return records, "api"
        except (httpx.HTTPError, ValueError, FetchError) as e:
            error = e if isinstance(e, FetchError) else FetchError(f"API организаторов недоступен: {type(e).__name__}")
    else:
        error = FetchError("ORG_API_URL не задан в .env")

    cache = _cache_path(group_id)
    if not cache.exists() and group_id == "DEV" and (DATA_DIR / "dev.json").exists():
        cache = DATA_DIR / "dev.json"
    if cache.exists():
        payload = json.loads(cache.read_text(encoding="utf-8"))
        return (payload.get("data") or {}).get("records") or [], "cache"
    raise error
