"""Чек-листы: валидация конфигурации + хранение версий.

PUT = новая версия (старые не удаляются) и она становится активной — так замена критерия на защите
сразу применяется к следующей оценке. Активной может быть одна версия каждого checklist_id.
"""
from __future__ import annotations

import re
from typing import Any

from .. import db

TYPES = {"binary", "scale", "flag"}


class ChecklistError(ValueError):
    pass


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def validate_checklist(data: dict[str, Any]) -> dict[str, Any]:
    """Проверяет и нормализует чек-лист. Возвращает копию; при ошибке — ChecklistError с понятным текстом."""
    if not isinstance(data, dict):
        raise ChecklistError("Чек-лист должен быть JSON-объектом")
    errors: list[str] = []
    out: dict[str, Any] = {k: v for k, v in data.items() if k not in ("version", "is_active", "created_at")}

    out["name"] = _text(data.get("name")) or "Чек-лист"
    try:
        out["rounding"] = int(data.get("rounding", 2))
    except (TypeError, ValueError):
        errors.append("rounding должен быть целым числом")

    sections = []
    for s in data.get("sections") or []:
        sid = _text(s.get("id"))
        if not sid:
            errors.append("У секции нет id")
            continue
        sections.append({"id": sid, "title": _text(s.get("title")) or sid})
    section_ids = {s["id"] for s in sections}
    if len(section_ids) != len(sections):
        errors.append("id секций должны быть уникальны")
    out["sections"] = sections

    criteria_in = data.get("criteria") or []
    if not criteria_in:
        errors.append("В чек-листе нет критериев")
    criteria, seen = [], set()
    for n, c in enumerate(criteria_in, 1):
        cid = _text(c.get("id"))
        label = cid or f"критерий №{n}"
        if not cid:
            errors.append(f"{label}: нет id")
        elif cid in seen:
            errors.append(f"{label}: id повторяется")
        seen.add(cid)

        ctype = _text(c.get("type")).lower()
        if ctype not in TYPES:
            errors.append(f"{label}: тип должен быть binary, scale или flag")
        title = _text(c.get("title"))
        if not title:
            errors.append(f"{label}: нет названия")

        section_id = _text(c.get("section_id")) or None
        if section_ids and section_id not in section_ids:
            errors.append(f"{label}: секция {section_id!r} не найдена")

        try:
            weight = float(c.get("weight") or 0)
            weight = int(weight) if weight.is_integer() else weight
        except (TypeError, ValueError):
            errors.append(f"{label}: вес должен быть числом")
            weight = 0
        if weight < 0:
            errors.append(f"{label}: вес не может быть отрицательным")

        na_allowed = bool(c.get("na_allowed"))
        na_condition = _text(c.get("na_condition")) or None
        scale = None
        flag_types = None

        if ctype == "flag":
            weight, na_allowed, na_condition = 0, False, None
            flag_types = [_text(x) for x in (c.get("flag_types") or []) if _text(x)] or None
        if ctype == "scale":
            steps = []
            for st in c.get("scale") or []:
                try:
                    v = float(st.get("value"))
                except (TypeError, ValueError, AttributeError):
                    errors.append(f"{label}: ступень шкалы должна быть числом")
                    continue
                steps.append({"value": int(v) if v.is_integer() else v, "description": _text(st.get("description"))})
            values = [s["value"] for s in steps]
            if len(steps) < 2:
                errors.append(f"{label}: у шкалы должно быть минимум 2 ступени")
            elif len(set(values)) != len(values):
                errors.append(f"{label}: ступени шкалы повторяются")
            elif max(values) <= 0 or min(values) < 0:
                errors.append(f"{label}: ступени шкалы должны быть ≥ 0, максимальная > 0")
            scale = sorted(steps, key=lambda s: s["value"], reverse=True)
        if na_allowed and not na_condition:
            errors.append(f"{label}: N/A разрешён, но не указано условие N/A")

        criteria.append({
            **{k: v for k, v in c.items() if k not in ("scale", "flag_types")},
            "id": cid, "section_id": section_id, "title": title,
            "description": _text(c.get("description")), "type": ctype, "weight": weight,
            "na_allowed": na_allowed, "na_condition": na_condition, "scale": scale, "flag_types": flag_types,
        })
    out["criteria"] = criteria
    out["notes"] = [_text(x) for x in (data.get("notes") or []) if _text(x)]

    if errors:
        raise ChecklistError("; ".join(errors))
    return out


# --- хранилище ------------------------------------------------------------------------------------
def _row_to_checklist(row) -> dict[str, Any]:
    data = db.loads(row["data"])
    data["checklist_id"] = row["checklist_id"]
    data["version"] = row["version"]
    data["is_active"] = bool(row["is_active"])
    data["created_at"] = row["created_at"]
    return data


def list_latest() -> list[dict[str, Any]]:
    """Последняя версия каждого чек-листа."""
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT c.* FROM checklists c
               JOIN (SELECT checklist_id, MAX(version) v FROM checklists GROUP BY checklist_id) m
                 ON m.checklist_id = c.checklist_id AND m.v = c.version
               ORDER BY c.checklist_id"""
        ).fetchall()
    return [_row_to_checklist(r) for r in rows]


def get(checklist_id: str, version: int | None = None) -> dict[str, Any] | None:
    """Конкретная версия; без version — активная (если активной нет — последняя)."""
    with db.connect() as conn:
        if version is not None:
            row = conn.execute("SELECT * FROM checklists WHERE checklist_id=? AND version=?",
                               (checklist_id, version)).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM checklists WHERE checklist_id=? ORDER BY is_active DESC, version DESC LIMIT 1",
                (checklist_id,)).fetchone()
    return _row_to_checklist(row) if row else None


def versions(checklist_id: str) -> list[dict[str, Any]]:
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM checklists WHERE checklist_id=? ORDER BY version", (checklist_id,)).fetchall()
    result = []
    for r in rows:
        data = db.loads(r["data"])
        result.append({"version": r["version"], "is_active": bool(r["is_active"]), "created_at": r["created_at"],
                       "name": data.get("name"), "criteria": [c["id"] for c in data.get("criteria", [])]})
    return result


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "checklist"


def _insert(conn, checklist_id: str, version: int, data: dict[str, Any]) -> None:
    conn.execute("UPDATE checklists SET is_active=0 WHERE checklist_id=?", (checklist_id,))
    data = {**data, "checklist_id": checklist_id, "version": version, "is_active": True}
    conn.execute("INSERT INTO checklists (checklist_id, version, is_active, data) VALUES (?, ?, 1, ?)",
                 (checklist_id, version, db.dumps(data)))


def create(data: dict[str, Any]) -> dict[str, Any]:
    clean = validate_checklist(data)
    checklist_id = _text(data.get("checklist_id")) or _slug(clean["name"])
    with db.connect() as conn:
        if conn.execute("SELECT 1 FROM checklists WHERE checklist_id=?", (checklist_id,)).fetchone():
            raise ChecklistError(f"Чек-лист {checklist_id!r} уже существует — используйте PUT для новой версии")
        _insert(conn, checklist_id, 1, clean)
    return get(checklist_id, 1)


def update(checklist_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
    clean = validate_checklist(data)
    with db.connect() as conn:
        row = conn.execute("SELECT MAX(version) FROM checklists WHERE checklist_id=?", (checklist_id,)).fetchone()
        if row[0] is None:
            return None
        version = row[0] + 1
        _insert(conn, checklist_id, version, clean)
    return get(checklist_id, version)


def activate(checklist_id: str, version: int) -> dict[str, Any] | None:
    with db.connect() as conn:
        if not conn.execute("SELECT 1 FROM checklists WHERE checklist_id=? AND version=?",
                            (checklist_id, version)).fetchone():
            return None
        conn.execute("UPDATE checklists SET is_active = (version = ?) WHERE checklist_id=?", (version, checklist_id))
    return get(checklist_id, version)
