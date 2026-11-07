from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

from ..services import checklists as store
from ..services.checklists import ChecklistError

router = APIRouter(prefix="/api/checklists", tags=["checklists"])


class ActivateRequest(BaseModel):
    version: int


@router.get("")
def list_checklists():
    return store.list_latest()


@router.get("/{checklist_id}")
def get_checklist(checklist_id: str, version: Optional[int] = None):
    cl = store.get(checklist_id, version)
    if cl is None:
        raise HTTPException(404, f"Чек-лист {checklist_id} (версия {version or 'активная'}) не найден")
    return cl


@router.get("/{checklist_id}/versions")
def list_versions(checklist_id: str):
    items = store.versions(checklist_id)
    if not items:
        raise HTTPException(404, f"Чек-лист {checklist_id} не найден")
    return items


@router.post("")
def create_checklist(data: dict[str, Any] = Body(...)):
    try:
        return store.create(data)
    except ChecklistError as e:
        raise HTTPException(422, str(e))


@router.put("/{checklist_id}")
def update_checklist(checklist_id: str, data: dict[str, Any] = Body(...)):
    try:
        cl = store.update(checklist_id, data)
    except ChecklistError as e:
        raise HTTPException(422, str(e))
    if cl is None:
        raise HTTPException(404, f"Чек-лист {checklist_id} не найден")
    return cl


@router.post("/{checklist_id}/activate")
def activate_checklist(checklist_id: str, req: ActivateRequest):
    cl = store.activate(checklist_id, req.version)
    if cl is None:
        raise HTTPException(404, f"Чек-лист {checklist_id} версии {req.version} не найден")
    return cl
