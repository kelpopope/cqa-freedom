from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..services import appeals as store

router = APIRouter(prefix="/api/appeals", tags=["appeals"])


class AppealRequest(BaseModel):
    evaluation_id: str
    criterion_id: str
    comment: str
    author_role: str = "operator"


class DecisionRequest(BaseModel):
    action: str                     # "confirm" | "change"
    reason: str
    new_answer: Optional[Any] = None
    reviewer_role: str = "reviewer"


@router.post("")
def create_appeal(req: AppealRequest):
    try:
        return store.create(req.evaluation_id, req.criterion_id, req.comment, req.author_role)
    except store.AppealError as e:
        raise HTTPException(e.status_code, str(e))


@router.get("")
def list_appeals(status: Optional[str] = None, conversation_id: Optional[str] = None):
    return store.list_appeals(status, conversation_id)


@router.get("/{appeal_id}")
def get_appeal(appeal_id: str):
    appeal = store.get(appeal_id)
    if appeal is None:
        raise HTTPException(404, f"Апелляция {appeal_id} не найдена")
    appeal["context"] = store._context(appeal)
    return appeal


@router.post("/{appeal_id}/decision")
def decide(appeal_id: str, req: DecisionRequest):
    try:
        return store.decide(appeal_id, req.action, req.reason, req.new_answer, req.reviewer_role)
    except store.AppealError as e:
        raise HTTPException(e.status_code, str(e))
