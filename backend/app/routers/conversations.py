from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import JSONResponse, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from ..services import conversations as store
from ..services import datasets
from ..services import sla
from ..services.evaluator import history
from ..services.fetcher import FetchError, fetch_dataset

router = APIRouter(prefix="/api", tags=["conversations"])


class ImportRequest(BaseModel):
    group_id: str = "DEV"


@router.post("/datasets/import")
async def import_dataset(req: ImportRequest):
    group_id = req.group_id.strip()
    try:
        records, source = await run_in_threadpool(fetch_dataset, group_id)
        imported = store.import_records(records, group_id)
    except FetchError as e:
        raise HTTPException(502, str(e))
    return {"group_id": group_id, "imported": imported, "source": source}


@router.get("/datasets")
def list_datasets():
    return store.groups()


class UploadRequest(BaseModel):
    payload: Any
    group_id: Optional[str] = None


class FetchUrlRequest(BaseModel):
    url: str
    group_id: Optional[str] = None


@router.post("/datasets/upload")
def upload_dataset(req: UploadRequest):
    """Набор из файла или вставленного JSON: формат API организаторов, {records}, массив или один диалог."""
    try:
        return datasets.import_payload(req.payload, req.group_id)
    except FetchError as e:
        raise HTTPException(422, str(e))


@router.post("/datasets/fetch-url")
async def fetch_url_dataset(req: FetchUrlRequest):
    """Набор по любому адресу (свой API): GET, ответ JSON в тех же форматах."""
    try:
        payload = await run_in_threadpool(datasets.fetch_url, req.url)
        return datasets.import_payload(payload, req.group_id)
    except FetchError as e:
        raise HTTPException(422, str(e))


@router.get("/datasets/{group_id}/export")
def export_dataset(group_id: str):
    doc = datasets.export_dataset(group_id)
    if not doc["data"]["records"]:
        raise HTTPException(404, f"Нет диалогов набора {group_id}")
    return JSONResponse(doc, headers={"Content-Disposition": f'attachment; filename="dataset_{group_id}.json"'})


@router.get("/results/export.csv")
def export_results_csv(group_id: str):
    if not store.list_items(group_id=group_id):
        raise HTTPException(404, f"Нет диалогов набора {group_id}")
    return Response(datasets.results_csv(group_id), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="results_{group_id}.csv"'})


@router.post("/conversations/upload")
def upload_conversation(record: dict[str, Any] = Body(...)):
    if not isinstance(record.get("transcript"), list) or not record["transcript"]:
        raise HTTPException(422, "Нужен непустой transcript: [{timestamp, speaker, text}]")
    try:
        return store.upload(record)
    except FetchError as e:
        raise HTTPException(422, str(e))


@router.get("/conversations")
def list_conversations(group_id: Optional[str] = None, language: Optional[str] = None,
                       channel: Optional[str] = None, risk: Optional[bool] = None,
                       status: Optional[str] = None, q: Optional[str] = None,
                       gross: Optional[bool] = None, needs_review: Optional[bool] = None):
    return store.list_items(group_id=group_id, language=language, channel=channel, risk=risk, status=status, q=q,
                            gross=gross, needs_review=needs_review)


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: str):
    conv = store.get(conversation_id)
    if conv is None:
        raise HTTPException(404, f"Диалог {conversation_id} не найден")
    evaluation = store.latest_evaluation(conversation_id)
    appeals = store.appeals_for(evaluation["evaluation_id"]) if evaluation else []
    return {"conversation": conv, "evaluation": evaluation, "appeals": appeals, "sla": sla.first_response(conv),
            "history": [{k: h.get(k) for k in ("evaluation_id", "checklist_version", "total", "final_total", "created_at", "status")}
                        for h in history(conversation_id)]}
