from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from ..services import conversations as store
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
                       status: Optional[str] = None, q: Optional[str] = None):
    return store.list_items(group_id=group_id, language=language, channel=channel, risk=risk, status=status, q=q)


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: str):
    conv = store.get(conversation_id)
    if conv is None:
        raise HTTPException(404, f"Диалог {conversation_id} не найден")
    evaluation = store.latest_evaluation(conversation_id)
    appeals = store.appeals_for(evaluation["evaluation_id"]) if evaluation else []
    return {"conversation": conv, "evaluation": evaluation, "appeals": appeals}
