from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..services.evaluator import EvaluationError, evaluate

router = APIRouter(prefix="/api", tags=["evaluations"])


class EvaluateRequest(BaseModel):
    conversation_id: str
    checklist_id: str = "default"
    version: Optional[int] = None


@router.post("/evaluate")
async def evaluate_one(req: EvaluateRequest):
    try:
        return await run_in_threadpool(evaluate, req.conversation_id, req.checklist_id, req.version)
    except EvaluationError as e:
        body = {"error": str(e)}
        if e.evaluation:
            body["evaluation"] = e.evaluation
        return JSONResponse(status_code=e.status_code, content=body)
