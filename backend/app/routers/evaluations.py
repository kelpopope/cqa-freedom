from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..services import accuracy, analytics, coaching, jobs, results_export
from ..services import conversations as conversation_store
from ..services import settings as settings_service
from ..services.evaluator import EvaluationError, evaluate, get_evaluation, history, repeat, set_review

router = APIRouter(prefix="/api", tags=["evaluations"])


class EvaluateRequest(BaseModel):
    conversation_id: str
    checklist_id: str = "default"
    version: Optional[int] = None


class RepeatRequest(BaseModel):
    evaluation_id: str


class CoachRequest(BaseModel):
    criterion_id: str
    refresh: bool = False


class ReviewRequest(BaseModel):
    action: str = "confirm"
    reviewer: Optional[str] = None


class BatchRequest(BaseModel):
    group_id: Optional[str] = None
    conversation_ids: Optional[list[str]] = None
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


@router.post("/evaluate/batch")
def evaluate_batch(req: BatchRequest):
    """Фоновая оценка группы или списка диалогов. Прогресс — GET /api/jobs/{job_id}."""
    try:
        job = jobs.start(req.checklist_id, req.version, req.group_id, req.conversation_ids)
    except jobs.JobError as e:
        raise HTTPException(422, str(e))
    return {"job_id": job["job_id"], "total": job["total"], "status": job["status"],
            "checklist_version": job["checklist_version"]}


@router.get("/jobs")
def list_jobs():
    return [{k: v for k, v in j.items() if k != "conversation_ids"} for j in jobs.list_recent()]


@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, f"Задача {job_id} не найдена")
    return job


@router.get("/evaluations/compare")
def compare_versions(checklist_id: str = "default", from_version: int = 1, to_version: int = 2,
                     group_id: Optional[str] = None):
    """«Было → стало» после изменения чек-листа (массив по контракту). changed_criteria — id изменившихся,
    добавленных и удалённых критериев; подробности — в changes / added / removed."""
    return analytics.compare(checklist_id, from_version, to_version, group_id)["items"]


@router.get("/evaluations/{evaluation_id}")
def get_one(evaluation_id: str):
    ev = get_evaluation(evaluation_id)
    if ev is None:
        raise HTTPException(404, f"Оценка {evaluation_id} не найдена")
    return ev


@router.get("/conversations/{conversation_id}/evaluations")
def conversation_history(conversation_id: str):
    return history(conversation_id)


@router.get("/analytics")
def analytics_summary(checklist_id: str = "default", version: Optional[int] = None, group_id: Optional[str] = None):
    """Индикатор для доработки скриптов: провалы по критериям, языкам, операторам; грубые ошибки; needs_review."""
    report = analytics.summary(checklist_id, version, group_id)
    if "error" in report:
        raise HTTPException(404, report["error"])
    return report


@router.get("/metrics/accuracy")
def accuracy_metrics(group_id: str = "DEV", details: bool = False):
    """Совпадение с эталоном data/gt/<group>_labels.json. details=true — со списком расхождений."""
    report = accuracy.compute(group_id)
    if "error" in report:
        return JSONResponse(status_code=404, content={"error": report["error"]})
    if not details:
        report.pop("mismatches", None)
    return report


@router.get("/settings")
def get_settings_():
    return settings_service.public_settings()


@router.put("/settings")
def put_settings(values: dict[str, Any] = Body(...)):
    try:
        return settings_service.update(values)
    except settings_service.SettingsError as e:
        raise HTTPException(422, str(e))


@router.get("/results/export")
def export_results(group_id: str, team_name: str = "CQA Freedom", expected: Optional[int] = None):
    """Официальный results.json (формат организаторов 1.0) по набору + проверка формата в заголовке ответа."""
    ids = [i["conversation_id"] for i in conversation_store.list_items(group_id=group_id)]
    if not ids:
        raise HTTPException(404, f"Нет диалогов группы {group_id}")
    doc = results_export.build(ids, team_name, settings_service.runtime_settings()["model"])
    problems = results_export.validate(doc, ids, expected)
    return JSONResponse(content=doc, headers={"X-Results-Valid": "true" if not problems else "false",
                                              "Content-Disposition": f'attachment; filename="results_{group_id}.json"'})


@router.post("/evaluate/repeat")
async def evaluate_repeat(req: RepeatRequest):
    """Воспроизводимость: повторный независимый прогон той же оценки и доля совпавших ответов."""
    try:
        return await run_in_threadpool(repeat, req.evaluation_id)
    except EvaluationError as e:
        return JSONResponse(status_code=e.status_code, content={"error": str(e)})


@router.post("/evaluations/{evaluation_id}/coach")
async def coach(evaluation_id: str, req: CoachRequest):
    """«Как надо было ответить»: пример правильной реплики для нарушенного критерия (на баллы не влияет)."""
    try:
        return await run_in_threadpool(coaching.suggest, evaluation_id, req.criterion_id, req.refresh)
    except EvaluationError as e:
        return JSONResponse(status_code=e.status_code, content={"error": str(e)})


@router.post("/evaluations/{evaluation_id}/review")
def review(evaluation_id: str, req: ReviewRequest):
    try:
        return set_review(evaluation_id, req.action, req.reviewer)
    except EvaluationError as e:
        return JSONResponse(status_code=e.status_code, content={"error": str(e)})


@router.get("/stats")
def overview_stats(group_id: Optional[str] = None):
    """Экран «Обзор» руководителя."""
    return analytics.stats(group_id)
