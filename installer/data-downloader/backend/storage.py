from __future__ import annotations

import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock
from typing import Dict, List, Optional
from datetime import datetime, timezone


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class JSONStore:
    """Lightweight helper around json files with atomic writes."""

    def __init__(self, path: Path, default_payload: dict):
        self.path = path
        self.default_payload = default_payload
        self._lock = Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write_file(self.default_payload)

    def read(self) -> dict:
        with self._lock:
            with self.path.open("r", encoding="utf-8") as fh:
                return json.load(fh)

    def write(self, payload: dict) -> None:
        payload["updated_at"] = payload.get("updated_at") or now_iso()
        with self._lock:
            self._write_file(payload)

    def _write_file(self, payload: dict) -> None:
        with NamedTemporaryFile("w", delete=False, dir=str(self.path.parent), encoding="utf-8") as tmp:
            json.dump(payload, tmp, indent=2, ensure_ascii=True)
            tmp.flush()
        Path(tmp.name).replace(self.path)


class RunsRepository:
    def __init__(self, data_dir: Path):
        default = {"updated_at": None, "runs": []}
        self.store = JSONStore(data_dir / "runs.json", default)

    def list_runs(self) -> dict:
        return self.store.read()

    def merge_scanned_runs(self, scanned: List[dict]) -> dict:
        current = self.store.read()
        existing: Dict[str, dict] = {r["key"]: r for r in current.get("runs", [])}
        merged: Dict[str, dict] = {}

        for run in scanned:
            key = run["key"]
            note = existing.get(key, {}).get("note", "")
            note_ts = existing.get(key, {}).get("note_updated_at")
            merged[key] = {
                **run,
                "note": note,
                "note_updated_at": note_ts,
            }

        # Keep runs that vanished but still have notes to preserve manual metadata
        for key, run in existing.items():
            if key not in merged:
                merged[key] = run

        runs_list = sorted(
            merged.values(),
            key=lambda r: r.get("start_utc", ""),
            reverse=True,
        )
        payload = {
            "updated_at": now_iso(),
            "runs": runs_list,
        }
        self.store.write(payload)
        return payload

    def update_note(self, key: str, note: str) -> Optional[dict]:
        payload = self.store.read()
        updated_run: Optional[dict] = None
        for run in payload.get("runs", []):
            if run["key"] == key:
                run["note"] = note
                run["note_updated_at"] = now_iso()
                updated_run = run
                break
        if updated_run is not None:
            payload["updated_at"] = now_iso()
            self.store.write(payload)
        return updated_run


class SensorsRepository:
    def __init__(self, data_dir: Path):
        default = {"updated_at": None, "sensors": []}
        self.store = JSONStore(data_dir / "sensors.json", default)

    def list_sensors(self) -> dict:
        return self.store.read()

    def write_sensors(self, sensors: List[str]) -> dict:
        payload = {
            "updated_at": now_iso(),
            "sensors": sorted(sensors),
        }
        self.store.write(payload)
        return payload


class ScannerStatusRepository:
    def __init__(self, data_dir: Path):
        default = {
            "updated_at": None,
            "scanning": False,
            "started_at": None,
            "finished_at": None,
            "source": None,
            "last_result": None,
            "error": None,
        }
        self.store = JSONStore(data_dir / "scanner_status.json", default)

    def get_status(self) -> dict:
        return self.store.read()

    def mark_start(self, source: str) -> dict:
        payload = self.store.read()
        payload.update(
            {
                "scanning": True,
                "source": source,
                "started_at": now_iso(),
            }
        )
        payload.pop("error", None)
        payload["updated_at"] = now_iso()
        self.store.write(payload)
        return payload

    def mark_finish(self, success: bool, error: str | None = None) -> dict:
        payload = self.store.read()
        payload.update(
            {
                "scanning": False,
                "finished_at": now_iso(),
                "last_result": "success" if success else "error",
            }
        )
        if success:
            payload.pop("error", None)
        else:
            payload["error"] = error or "scan failed"
        payload["updated_at"] = now_iso()
        self.store.write(payload)
        return payload
