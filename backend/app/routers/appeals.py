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
    author_name: Optional[str] = None
    proposed_answer: Optional[Any] = None


class OverrideRequest(BaseModel):
    evaluation_id: str
    criterion_id: str
    new_answer: Any
    reason: str
    reviewer: Optional[str] = None


class DecisionRequest(BaseModel):
    action: str                     # "confirm" | "change"
    reason: str
    new_answer: Optional[Any] = None
    reviewer_role: str = "reviewer"
    reviewer: Optional[str] = None


@router.post("")
def create_appeal(req: AppealRequest):
    try:
        return store.view(store.create(req.evaluation_id, req.criterion_id, req.comment, req.author_role,
                                       req.author_name, req.proposed_answer))
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
    return store.view(appeal)


@router.post("/override")
def override(req: OverrideRequest):
    """Проверяющий меняет ответ по критерию напрямую (без апелляции оператора)."""
    try:
        return store.override(req.evaluation_id, req.criterion_id, req.new_answer, req.reason, req.reviewer)
    except store.AppealError as e:
        raise HTTPException(e.status_code, str(e))


@router.post("/{appeal_id}/decision")
def decide(appeal_id: str, req: DecisionRequest):
    try:
        return store.decide(appeal_id, req.action, req.reason, req.new_answer, req.reviewer_role, req.reviewer)
    except store.AppealError as e:
        raise HTTPException(e.status_code, str(e))
