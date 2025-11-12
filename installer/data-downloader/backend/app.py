from __future__ import annotations

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.config import get_settings
from backend.services import DataDownloaderService


class NotePayload(BaseModel):
    note: str


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
