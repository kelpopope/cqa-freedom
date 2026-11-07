from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..services import accuracy
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


@router.get("/metrics/accuracy")
def accuracy_metrics(group_id: str = "DEV", details: bool = False):
    """Совпадение с эталоном data/gt/<group>_labels.json. details=true — со списком расхождений."""
    report = accuracy.compute(group_id)
    if "error" in report:
        return JSONResponse(status_code=404, content={"error": report["error"]})
    if not details:
        report.pop("mismatches", None)
    return report
