from __future__ import annotations

from datetime import datetime

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.config import get_settings
from backend.services import DataDownloaderService


class NotePayload(BaseModel):
    note: str


class DataQueryPayload(BaseModel):
    signal: str
    start: datetime
    end: datetime
    limit: int | None = 2000
    no_limit: bool = False


settings = get_settings()
service = DataDownloaderService(settings)

app = FastAPI(title="DAQ Data Downloader API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def healthcheck() -> dict:
    return {"status": "ok"}


@app.get("/api/runs")
def list_runs() -> dict:
    return service.get_runs()


@app.get("/api/sensors")
def list_sensors() -> dict:
    return service.get_sensors()


@app.post("/api/runs/{key}/note")
def save_note(key: str, payload: NotePayload) -> dict:
    run = service.update_note(key, payload.note.strip())
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {key} not found")
    return run


@app.post("/api/scan")
def trigger_scan(background_tasks: BackgroundTasks) -> dict:
    background_tasks.add_task(service.run_full_scan)
    return {"status": "scheduled"}


@app.post("/api/data/query")
def query_data(payload: DataQueryPayload) -> dict:
    limit = None if payload.no_limit else (payload.limit or 2000)
    return service.query_signal_series(payload.signal, payload.start, payload.end, limit)
