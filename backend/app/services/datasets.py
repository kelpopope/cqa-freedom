"""Загрузка и выгрузка наборов диалогов помимо API организаторов.

Источники: файл JSON, вставленный текст, любой адрес (свой API). Форматы на входе:
- ответ API организаторов: {"isSuccess": true, "data": {"dataset_group_id": ..., "records": [...]}};
- {"records": [...]}, просто массив диалогов или один диалог с полем transcript.
Диалог: {conversation_id?, channel?, language?, transcript: [{timestamp?, speaker, text}], audio_url?}.
Недостающее дополняется: id — по порядку, язык — по тексту, канал — «agent chat».
Если id уже занят диалогом другого набора, к нему добавляется префикс набора (набор не перетирает чужие диалоги).

Выгрузка: набор — в том же формате, что API организаторов (можно загрузить обратно);
результаты — CSV для Excel (официальный results.json — results_export).
"""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime
from typing import Any

import httpx

from .. import db
from . import checklists as checklist_store
from . import conversations as conversation_store
from .fetcher import FetchError, normalize_conversation

MAX_BYTES = 25 * 1024 * 1024
_KZ = re.compile(r"[әғқңөұүһі]", re.I)
_CYR = re.compile(r"[а-яё]", re.I)
_LAT = re.compile(r"[a-z]", re.I)


def detect_language(transcript: list[dict[str, Any]]) -> str:
    text = " ".join(str(t.get("text") or "") for t in transcript
                    if str(t.get("speaker") or "").lower() in ("client", "operator", "клиент", "оператор"))
    if len(_KZ.findall(text)) >= 3:
        return "KZ"
    return "EN" if len(_LAT.findall(text)) > len(_CYR.findall(text)) else "RU"


def parse_payload(payload: Any) -> tuple[list[Any], str | None]:
    """Список записей и group_id из ответа, если он там есть."""
    if isinstance(payload, (str, bytes)):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as e:
            raise FetchError(f"Это не JSON: {e.msg}, строка {e.lineno}") from None
    if isinstance(payload, list):
        return payload, None
    if not isinstance(payload, dict):
        raise FetchError("Ожидается JSON-объект или массив диалогов")
    if payload.get("isSuccess") is False:
        raise FetchError(f"Источник вернул ошибку: {payload.get('error') or 'isSuccess = false'}")
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("records"), list):
        return data["records"], data.get("dataset_group_id")
    if isinstance(payload.get("records"), list):
        return payload["records"], payload.get("dataset_group_id") or payload.get("group_id")
    if isinstance(payload.get("transcript"), list):
        return [payload], payload.get("group_id")
    raise FetchError("Не найден список диалогов: нужен data.records, records, массив диалогов или диалог с transcript")


def _existing_groups(ids: list[str]) -> dict[str, str]:
    if not ids:
        return {}
    with db.connect() as conn:
        rows = conn.execute(f"SELECT conversation_id, group_id FROM conversations WHERE conversation_id IN "
                            f"({','.join('?' * len(ids))})", ids).fetchall()
    return {r["conversation_id"]: r["group_id"] for r in rows}


def clean_group_id(group_id: str | None) -> str:
    g = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(group_id or "").strip()).strip("-")
    return g[:40] or f"UPLOAD-{datetime.now().strftime('%m%d-%H%M')}"


def import_payload(payload: Any, group_id: str | None = None) -> dict[str, Any]:
    records, found_group = parse_payload(payload)
    group = clean_group_id(group_id or found_group)
    if not records:
        raise FetchError("В наборе нет ни одного диалога")
    convs, skipped = [], []
    raw_ids = [str(r.get("conversation_id") or "").strip() for r in records if isinstance(r, dict)]
    taken = _existing_groups([i for i in raw_ids if i])
    renamed = []
    for n, rec in enumerate(records, 1):
        if not isinstance(rec, dict):
            skipped.append({"position": n, "error": "запись не объект"})
            continue
        turns = rec.get("transcript")
        if not isinstance(turns, list) or not any(isinstance(t, dict) and str(t.get("text") or "").strip() for t in turns):
            skipped.append({"position": n, "conversation_id": rec.get("conversation_id"), "error": "пустой transcript"})
            continue
        rec = dict(rec)
        cid = str(rec.get("conversation_id") or "").strip() or f"{group.lower()}-{n:03d}"
        if taken.get(cid) not in (None, group):
            new = f"{group.lower()}-{cid}"
            renamed.append({"from": cid, "to": new})
            cid = new
        rec["conversation_id"] = cid
        try:
            conv = normalize_conversation(rec, group)
        except FetchError as e:
            skipped.append({"position": n, "conversation_id": cid, "error": str(e)})
            continue
        conv["language"] = conv["language"] or detect_language(conv["transcript"])
        convs.append(conv)
    if not convs:
        raise FetchError(f"Ни один диалог не загружен: {skipped[0]['error'] if skipped else 'нет данных'}")
    conversation_store.upsert_many(convs)
    return {"group_id": group, "imported": len(convs), "skipped": skipped, "renamed": renamed,
            "languages": sorted({c["language"] for c in convs if c["language"]})}


def fetch_url(url: str, timeout: float = 60) -> Any:
    """Скачать JSON по адресу (свой API или файл в сети). Только http/https, ответ до 25 МБ."""
    url = (url or "").strip()
    if not re.match(r"^https?://", url, re.I):
        raise FetchError("Адрес должен начинаться с http:// или https://")
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url, headers={"Accept": "application/json"})
    except httpx.HTTPError as e:
        raise FetchError(f"Не удалось получить данные: {type(e).__name__}") from None
    if resp.status_code != 200:
        raise FetchError(f"Источник ответил HTTP {resp.status_code}")
    if len(resp.content) > MAX_BYTES:
        raise FetchError("Ответ больше 25 МБ")
    try:
        return resp.json()
    except ValueError:
        raise FetchError("Источник вернул не JSON") from None


def export_dataset(group_id: str) -> dict[str, Any]:
    """Набор в формате API организаторов — его можно загрузить обратно в эту или другую систему."""
    records = []
    for item in conversation_store.list_items(group_id=group_id):
        c = conversation_store.get(item["conversation_id"])
        records.append({"conversation_id": c["conversation_id"], "channel": c["channel"], "language": c["language"],
                        "transcript": [{"timestamp": t["timestamp"], "speaker": t["speaker"], "text": t["text"]}
                                       for t in c["transcript"]],
                        "audio_url": c.get("audio_url")})
    return {"error": "", "isSuccess": True,
            "data": {"dataset_group_id": group_id, "total_records": len(records), "records": records}}


def results_csv(group_id: str) -> str:
    """Итоги набора для Excel: по строке на диалог, по колонке на ответ и баллы каждого критерия."""
    cl = checklist_store.get("default")
    crit = [c for c in cl["criteria"]]
    head = ["conversation_id", "group_id", "language", "channel", "operator", "status", "total_ai", "total_final",
            "gross_error", "gross_types", "needs_review", "reviewed_by", "checklist_version", "headline"]
    for c in crit:
        head += [f"{c['id']} {c['title']}", f"{c['id']} баллы"]
    out = io.StringIO()
    out.write("﻿")  # Excel открывает UTF-8 с кириллицей корректно
    w = csv.writer(out, delimiter=";")
    w.writerow(head)
    for item in conversation_store.list_items(group_id=group_id):
        ev = conversation_store.latest_evaluation(item["conversation_id"])
        row = [item["conversation_id"], item["group_id"], item["language"], item["channel"], item.get("operator_name") or ""]
        if not ev or ev.get("status") != "done":
            w.writerow(row + ["не оценён"] + [""] * (len(head) - len(row) - 1))
            continue
        fin = {r["criterion_id"]: r for r in (ev.get("final") or {}).get("criteria") or ev["criteria"]}
        g = ev.get("gross_error") or {}
        fg = (ev.get("final") or {}).get("gross_error")
        gross = fg if isinstance(fg, bool) else bool(g.get("flag"))
        row += ["оценён", _num(ev.get("total")), _num((ev.get("final") or {}).get("total")),
                "да" if gross else "нет", ", ".join(g.get("types") or []),
                "да" if ev.get("needs_review") else "нет", (ev.get("review") or {}).get("by") or "",
                ev.get("checklist_version"), ev.get("headline") or ""]
        for c in crit:
            r = fin.get(c["id"]) or {}
            a = r.get("answer")
            row += [{"yes": "Да", "no": "Нет", "na": "N/A"}.get(a, a if a is not None else ""), _num(r.get("raw_points"))]
        w.writerow(row)
    return out.getvalue()


def _num(x: Any) -> str:
    return "" if x is None else str(x).replace(".", ",")
