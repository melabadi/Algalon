from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path
from time import monotonic

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .csv_export import build_csv_export
from .store import SCENARIOS, ValueStore


database_path = Path(os.environ.get("COPILOT_VALUE_DB", "/data/app/value.db"))
session_directory = Path(os.environ.get("COPILOT_VALUE_SESSION_DIR", "/data/value/sessions"))
trace_archive = Path(os.environ.get("COPILOT_VALUE_TRACE_ARCHIVE", "/data/otel/traces.json"))
chat_log_root = Path(os.environ.get("COPILOT_VALUE_CHAT_LOG_ROOT", "/vscode-workspace-storage"))
config_path = Path(os.environ.get("COPILOT_VALUE_CONFIG", "/app/config/value-model.local.json"))
static_directory = Path(os.environ.get("COPILOT_VALUE_STATIC_DIR", "/app/static"))
store = ValueStore(database_path, session_directory, trace_archive, config_path, chat_log_root)
logger = logging.getLogger(__name__)
indexing_failure: str | None = None
indexing_started_at: float | None = None
INDEXING_STALE_AFTER_SECONDS = 60


async def indexing_loop() -> None:
    global indexing_failure, indexing_started_at
    while True:
        indexing_started_at = monotonic()
        try:
            await asyncio.to_thread(store.index_once)
            indexing_failure = None
        except Exception as error:
            indexing_failure = str(error)
            logger.exception("Local evidence indexing failed; retrying.")
        finally:
            indexing_started_at = None
        await asyncio.sleep(2)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    task = asyncio.create_task(indexing_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Algalon", version="0.2.0", lifespan=lifespan)


def scenario_value(scenario: str) -> str:
    if scenario not in SCENARIOS:
        raise HTTPException(status_code=400, detail=f"scenario must be one of {', '.join(SCENARIOS)}")
    return scenario


@app.get("/api/health")
def health():
    if indexing_failure is not None:
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "component": "indexing"},
        )
    started_at = indexing_started_at
    if started_at is not None:
        elapsed_seconds = monotonic() - started_at
        if elapsed_seconds >= INDEXING_STALE_AFTER_SECONDS:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "degraded",
                    "component": "indexing",
                    "reason": "stale",
                    "elapsedSeconds": round(elapsed_seconds, 1),
                },
            )
    return {"status": "ok"}


@app.post("/api/internal/otel/v1/traces", include_in_schema=False)
def ingest_otel_traces(payload: dict) -> dict:
    try:
        store.ingest_otlp_traces(payload)
        return {}
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.get("/api/internal/otel/records", include_in_schema=False)
def otel_records(
    after: int = Query(0, ge=0),
    limit: int = Query(1_000, ge=1, le=10_000),
) -> dict:
    return store.otel_records(after, limit)


@app.get("/api/overview")
def overview(
    scenario: str = Query("base"),
    days: int = Query(30, ge=1, le=3650),
) -> dict:
    return store.overview(scenario_value(scenario), days)


@app.get("/api/insights")
def insights(days: int = Query(30, ge=1, le=3650)) -> dict:
    return store.insights(days)


@app.get("/api/sessions/{experiment}")
def session(experiment: str, scenario: str = Query("base")) -> dict:
    payload = store.session(experiment, scenario_value(scenario))
    if not payload:
        raise HTTPException(status_code=404, detail="session not found")
    return payload


@app.get("/api/sessions/{experiment}/prompts")
def prompts(experiment: str) -> list[dict]:
    if not store.session(experiment):
        raise HTTPException(status_code=404, detail="session not found")
    return store.prompts(experiment)


@app.get("/api/prompts/{prompt_id}")
def prompt(prompt_id: str) -> dict:
    payload = store.prompt(prompt_id)
    if not payload:
        raise HTTPException(status_code=404, detail="prompt not found")
    return payload


@app.get("/api/methodology")
def methodology() -> dict:
    return store.methodology()


@app.get("/api/export.csv")
def export_csv() -> Response:
    sessions, prompts_by_session = store.export_data()
    return Response(
        content=build_csv_export(sessions, prompts_by_session),
        media_type="text/csv",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": 'attachment; filename="algalon-data.csv"',
        },
    )


if static_directory.exists():
    frontend_files = StaticFiles(directory=static_directory)
    assets_directory = static_directory / "assets"
    if assets_directory.exists():
        app.mount("/assets", StaticFiles(directory=assets_directory), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def frontend(path: str, request: Request):
        try:
            return await frontend_files.get_response(path, request.scope)
        except StarletteHTTPException as error:
            if error.status_code != 404:
                raise
        return await frontend_files.get_response("index.html", request.scope)