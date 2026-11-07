"""CQA backend: FastAPI отдаёт /api/* и статику frontend/ с одного origin (без CORS).

Запуск из корня репозитория:  uvicorn backend.app.main:app --reload
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import db
from .config import FRONTEND_DIR, get_settings
from .routers import checklists, conversations, evaluations


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="CQA — Conversation Quality Analytics", version="0.1.0", lifespan=lifespan)


# --- Ошибки в формате контракта: {"error": "текст"} -------------------------------------------
@app.exception_handler(StarletteHTTPException)
async def http_error(_: Request, exc: StarletteHTTPException):
    return JSONResponse(status_code=exc.status_code, content={"error": str(exc.detail)})


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    parts = []
    for err in exc.errors():
        loc = ".".join(str(x) for x in err.get("loc", ()) if x != "body")
        parts.append(f"{loc}: {err.get('msg')}" if loc else str(err.get("msg")))
    return JSONResponse(status_code=422, content={"error": "; ".join(parts) or "Некорректный запрос"})


# --- API ---------------------------------------------------------------------------------------
@app.get("/api/health")
def health():
    s = get_settings()
    return {"ok": True, "llm": {"provider": "openai", "name": s.openai_model, "mode": s.llm_mode}}


app.include_router(conversations.router)
app.include_router(checklists.router)
app.include_router(evaluations.router)


# --- Фронт (монтируется последним, чтобы не перекрывать /api) ----------------------------------
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
else:
    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def no_frontend():
        return "<h1>CQA API</h1><p>Папка frontend/ ещё не появилась. Документация API: <a href='/docs'>/docs</a></p>"
