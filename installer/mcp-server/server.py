from __future__ import annotations

import logging
import os
import re
import sys
from datetime import datetime, timedelta
from typing import Any, Optional

import pandas as pd
import requests
import slicks
from mcp.server.fastmcp import FastMCP


def _setup_logging() -> None:
    # STDIO MCP servers must never write protocol logs to stdout.
    logging.basicConfig(
        level=os.getenv("MCP_LOG_LEVEL", "INFO").upper(),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(message)s",
    )


_setup_logging()
logger = logging.getLogger("daq-mcp-server")


DATA_DOWNLOADER_URL = os.getenv("DATA_DOWNLOADER_URL", "http://localhost:8000").rstrip("/")
REQUEST_TIMEOUT_S = int(os.getenv("MCP_HTTP_TIMEOUT", "8"))
DEFAULT_AGENT_SEASON = os.getenv("DEFAULT_AGENT_SEASON", "").strip().upper()
SANDBOX_URL = os.getenv("SANDBOX_URL", "http://sandbox:8080").rstrip("/")
PROMPT_GUIDE_PATH = os.getenv("PROMPT_GUIDE_PATH", "prompt-guide.txt")


mcp = FastMCP("daq-telemetry")


def _get_json(path: str, params: Optional[dict[str, Any]] = None) -> Any:
    url = f"{DATA_DOWNLOADER_URL}{path}"
    resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_S)
    resp.raise_for_status()
    return resp.json()


def _post_json(path: str, payload: dict[str, Any], params: Optional[dict[str, Any]] = None) -> Any:
    url = f"{DATA_DOWNLOADER_URL}{path}"
    resp = requests.post(url, params=params, json=payload, timeout=REQUEST_TIMEOUT_S)
    resp.raise_for_status()
    return resp.json()


def _list_seasons_raw() -> list[dict[str, Any]]:
    seasons = _get_json("/api/seasons")
    if not isinstance(seasons, list):
        raise ValueError("Unexpected /api/seasons response")
    return seasons


@mcp.tool()
def list_seasons() -> dict[str, Any]:
    """Return configured telemetry seasons with database/table metadata."""
    try:
        seasons = _list_seasons_raw()
        return {"ok": True, "seasons": seasons}
    except Exception as e:
        logger.exception("list_seasons failed")
        return {"ok": False, "error": str(e), "seasons": []}


@mcp.tool()
def resolve_season(
    user_text: str = "",
    explicit_season: str | None = None,
    explicit_year: int | None = None,
) -> dict[str, Any]:
    """Resolve the active season using explicit season/year or user prompt text."""
    try:
        seasons = _list_seasons_raw()
        if not seasons:
            return {"ok": False, "error": "No seasons configured"}

        by_name = {str(s.get("name", "")).upper(): s for s in seasons}
        by_year = {int(s.get("year")): s for s in seasons if isinstance(s.get("year"), int)}

        resolved = None
        reason = ""

        if explicit_season:
            resolved = by_name.get(str(explicit_season).upper())
            if resolved:
                reason = "explicit_season"

        if resolved is None and explicit_year is not None:
            resolved = by_year.get(int(explicit_year))
            if resolved:
                reason = "explicit_year"

        if resolved is None:
            prompt_match = re.search(r"\b(WFR\d{2})\b", user_text.upper())
            if prompt_match:
                resolved = by_name.get(prompt_match.group(1))
                if resolved:
                    reason = "prompt_explicit_season"

        if resolved is None:
            year_match = re.search(r"\b(20\d{2})\b", user_text)
            if year_match:
                resolved = by_year.get(int(year_match.group(1)))
                if resolved:
                    reason = "prompt_explicit_year"

        if resolved is None and DEFAULT_AGENT_SEASON:
            resolved = by_name.get(DEFAULT_AGENT_SEASON)
            if resolved:
                reason = "default_agent_season_env"

        if resolved is None:
            resolved = seasons[0]
            reason = "newest_configured_season"

        return {
            "ok": True,
            "season": {
                "name": resolved.get("name"),
                "year": resolved.get("year"),
                "database": resolved.get("database", resolved.get("name")),
                "table": resolved.get("table", resolved.get("name")),
            },
            "reason": reason,
        }
    except Exception as e:
        logger.exception("resolve_season failed")
        return {"ok": False, "error": str(e)}


@mcp.tool()
def get_runs(season: str, limit: int = 12) -> dict[str, Any]:
    """Get scanned run windows for a season."""
    try:
        payload = _get_json("/api/runs", params={"season": season})
        runs = payload.get("runs", []) if isinstance(payload, dict) else []
        lim = max(1, min(int(limit), 200))
        return {"ok": True, "season": season, "runs": runs[:lim], "total": len(runs)}
    except Exception as e:
        logger.exception("get_runs failed")
        return {"ok": False, "error": str(e), "season": season, "runs": []}


@mcp.tool()
def list_sensors(season: str, search: str = "", limit: int = 5000) -> dict[str, Any]:
    """List available sensors for a season, optionally filtered by substring."""
    try:
        payload = _get_json("/api/sensors", params={"season": season})
        sensors = payload.get("sensors", []) if isinstance(payload, dict) else []

        if search:
            needle = search.lower().strip()
            sensors = [s for s in sensors if needle in str(s).lower()]

        lim = max(1, min(int(limit), 10000))
        return {
            "ok": True,
            "season": season,
            "sensors": sensors[:lim],
            "total": len(sensors),
        }
    except Exception as e:
        logger.exception("list_sensors failed")
        return {"ok": False, "error": str(e), "season": season, "sensors": []}


@mcp.tool()
def query_signal(
    season: str,
    signal: str,
    start: str,
    end: str,
    limit: int = 2000,
) -> dict[str, Any]:
    """Query one signal over a time range from Influx via the data-downloader API."""
    try:
        payload = {
            "signal": signal,
            "start": start,
            "end": end,
            "limit": max(10, min(int(limit), 20000)),
            "no_limit": False,
        }
        query = _post_json("/api/query", payload, params={"season": season})
        if isinstance(query, dict) and query.get("detail"):
            return {"ok": False, "error": str(query["detail"])}
        if isinstance(query, dict) and query.get("error"):
            return {"ok": False, "error": str(query["error"])}
        if not isinstance(query, dict):
            return {"ok": False, "error": "Unexpected query response"}
        query["ok"] = True
        return query
    except Exception as e:
        logger.exception("query_signal failed")
        return {"ok": False, "error": str(e)}


@mcp.tool()
def validate_request(
    season: str,
    signals: list[str] | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    """Validate season, signal names, and time window before analysis."""
    errors: list[str] = []
    warnings: list[str] = []
    signals = signals or []

    try:
        seasons = _list_seasons_raw()
        known_seasons = {str(s.get("name", "")).upper() for s in seasons}
        if season.upper() not in known_seasons:
            errors.append(f"Unknown season: {season}")

        sensor_resp = list_sensors(season=season, limit=10000)
        if sensor_resp.get("ok"):
            known_sensors = set(sensor_resp.get("sensors", []))
            unknown = [s for s in signals if s not in known_sensors]
            if unknown:
                errors.append(f"Unknown signals for {season}: {', '.join(unknown[:10])}")

        if start and end:
            try:
                start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
                if start_dt >= end_dt:
                    errors.append("start must be before end")
                if end_dt - start_dt > timedelta(hours=2):
                    warnings.append(
                        "Time window > 2h; prefer chunked fetch to avoid Influx file-limit or memory errors"
                    )
            except ValueError:
                errors.append("Invalid ISO-8601 time format in start/end")

        return {
            "ok": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "season": season,
            "signals": signals,
        }
    except Exception as e:
        logger.exception("validate_request failed")
        return {"ok": False, "errors": [str(e)], "warnings": warnings, "season": season, "signals": signals}


@mcp.prompt()
def slicks_coding_guide() -> str:
    """Instructions for writing AI model code using the Slicks InfluxDB3 library."""
    try:
        with open(PROMPT_GUIDE_PATH, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        logger.exception("Failed to read prompt guide")
        return f"Error reading prompt guide: {e}"


@mcp.tool()
def execute_slicks_code(code: str) -> dict[str, Any]:
    """Execute python code in the restricted sandbox environment."""
    try:
        url = f"{SANDBOX_URL}/execute"
        resp = requests.post(url, json={"code": code}, timeout=125)
        if resp.status_code != 200:
            return {"ok": False, "error": f"Sandbox returned status {resp.status_code}", "detail": resp.text}
        return resp.json()
    except Exception as e:
        logger.exception("execute_slicks_code failed")
        return {"ok": False, "error": str(e)}


def _connect_slicks_for_season(season: str):
    """Helper to connect slicks to the correct InfluxDB3 database based on season."""
    # INFLUX_URL, INFLUX_TOKEN, and INFLUX_DB are automatically picked up by slicks.
    slicks.connect_influxdb3(db=season.upper(), table=season.upper())


@mcp.tool()
def slicks_describe_data(
    season: str,
    start: str,
    end: str,
    signals: list[str]
) -> dict[str, Any]:
    """Fetch telemetry data using Slicks and return Pandas descriptive statistics."""
    try:
        start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
        
        _connect_slicks_for_season(season)
        df = slicks.fetch_telemetry(start_time=start_dt, end_time=end_dt, signals=signals, schema="wide")
        
        if df is None or df.empty:
            return {"ok": False, "error": "No data found for given time range and signals."}
            
        # Replace NaNs with None for JSON serialization
        summary = df.describe().replace({float("nan"): None}).to_dict()
        
        return {
            "ok": True,
            "season": season,
            "start": start,
            "end": end,
            "row_count": len(df),
            "columns": list(df.columns),
            "summary": summary
        }
    except Exception as e:
        logger.exception("slicks_describe_data failed")
        return {"ok": False, "error": str(e)}


@mcp.tool()
def slicks_battery_analysis(
    season: str,
    start: str,
    end: str
) -> dict[str, Any]:
    """Run built-in Slicks battery analysis (cell stats, weak cells, pack health)."""
    try:
        start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
        
        _connect_slicks_for_season(season)
        # Fetch data without specifying signals; slicks battery functions will find M*_Cell*_Voltage
        df = slicks.fetch_telemetry(start_time=start_dt, end_time=end_dt, schema="wide")
        
        if df is None or df.empty:
            return {"ok": False, "error": "No data found for given time range."}
            
        cell_stats = slicks.battery.get_cell_statistics(df)
        weak_cells = slicks.battery.identify_weak_cells(df)
        pack_health = slicks.battery.get_pack_health(df)
        
        # Replace NaNs with None for JSON serialization
        if hasattr(cell_stats, 'replace'):
            cell_stats = cell_stats.replace({float("nan"): None})
        if hasattr(weak_cells, 'replace'):
            weak_cells = weak_cells.replace({float("nan"): None})
            
        return {
            "ok": True,
            "season": season,
            "row_count": len(df),
            "cell_statistics": cell_stats.to_dict() if hasattr(cell_stats, 'to_dict') else {},
            "weak_cells": weak_cells.to_dict() if hasattr(weak_cells, 'to_dict') else {},
            "pack_health": pack_health if isinstance(pack_health, dict) else {}
        }
    except Exception as e:
        logger.exception("slicks_battery_analysis failed")
        return {"ok": False, "error": str(e)}


@mcp.tool()
def slicks_correlation_analysis(
    season: str,
    start: str,
    end: str,
    signals: list[str]
) -> dict[str, Any]:
    """Calculate Pearson correlation matrix between specific signals using Pandas."""
    try:
        start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
        
        _connect_slicks_for_season(season)
        df = slicks.fetch_telemetry(start_time=start_dt, end_time=end_dt, signals=signals, schema="wide")
        
        if df is None or df.empty:
            return {"ok": False, "error": "No data found for given time range and signals."}
            
        corr_matrix = df.corr().replace({float("nan"): None}).to_dict()
        
        return {
            "ok": True,
            "season": season,
            "row_count": len(df),
            "correlation_matrix": corr_matrix
        }
    except Exception as e:
        logger.exception("slicks_correlation_analysis failed")
        return {"ok": False, "error": str(e)}


def main() -> None:
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    logger.info("Starting DAQ MCP server transport=%s data_downloader_url=%s", transport, DATA_DOWNLOADER_URL)
    if transport.lower() == "sse":
        port = int(os.getenv("MCP_PORT", "8085"))
        host = os.getenv("MCP_HOST", "0.0.0.0")
        logger.info(f"Listening on {host}:{port} for SSE")
        
        # Depending on the mcp python sdk version, run might not take host/port directly
        # FastMCP uses starlette/uvicorn under the hood for SSE
        mcp.settings.host = host
        mcp.settings.port = port
        mcp.run(transport="sse")
    else:
        mcp.run(transport="stdio")

if __name__ == "__main__":
    main()
