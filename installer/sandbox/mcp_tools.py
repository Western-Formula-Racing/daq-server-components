from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import requests


@dataclass(frozen=True)
class MCPConfig:
    data_downloader_url: str = os.getenv("DATA_DOWNLOADER_URL", "http://data-downloader-api:8000")
    request_timeout_s: int = int(os.getenv("MCP_HTTP_TIMEOUT", "8"))
    default_season: str = os.getenv("DEFAULT_AGENT_SEASON", "").strip()


class MCPTools:
    """Thin read-only MCP bridge for season-aware telemetry operations."""

    def __init__(self, config: Optional[MCPConfig] = None):
        self.config = config or MCPConfig()

    def list_tools(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "list_seasons",
                "description": "Return configured telemetry seasons with database/table metadata.",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "resolve_season",
                "description": "Resolve the active season using explicit season/year or user prompt text.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "user_text": {"type": "string"},
                        "explicit_season": {"type": "string"},
                        "explicit_year": {"type": "integer"},
                    },
                },
            },
            {
                "name": "get_runs",
                "description": "Get scanned run windows for a season.",
                "input_schema": {
                    "type": "object",
                    "properties": {"season": {"type": "string"}, "limit": {"type": "integer"}},
                },
            },
            {
                "name": "list_sensors",
                "description": "List available sensors for a season, optionally filtered by substring.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "season": {"type": "string"},
                        "search": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                },
            },
            {
                "name": "query_signal",
                "description": "Query one signal over a time range from Influx via data-downloader API.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "season": {"type": "string"},
                        "signal": {"type": "string"},
                        "start": {"type": "string"},
                        "end": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                    "required": ["season", "signal", "start", "end"],
                },
            },
            {
                "name": "validate_request",
                "description": "Validate season, signal names, and time window before analysis.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "season": {"type": "string"},
                        "signals": {"type": "array", "items": {"type": "string"}},
                        "start": {"type": "string"},
                        "end": {"type": "string"},
                    },
                    "required": ["season"],
                },
            },
        ]

    def call(self, tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        args = arguments or {}
        if tool_name == "list_seasons":
            return self.list_seasons()
        if tool_name == "resolve_season":
            return self.resolve_season(
                user_text=args.get("user_text", ""),
                explicit_season=args.get("explicit_season"),
                explicit_year=args.get("explicit_year"),
            )
        if tool_name == "get_runs":
            return self.get_runs(args.get("season", ""), args.get("limit", 10))
        if tool_name == "list_sensors":
            return self.list_sensors(args.get("season", ""), args.get("search", ""), args.get("limit", 200))
        if tool_name == "query_signal":
            return self.query_signal(
                season=args.get("season", ""),
                signal=args.get("signal", ""),
                start=args.get("start", ""),
                end=args.get("end", ""),
                limit=args.get("limit", 2000),
            )
        if tool_name == "validate_request":
            return self.validate_request(
                season=args.get("season", ""),
                signals=args.get("signals", []),
                start=args.get("start"),
                end=args.get("end"),
            )
        return {"ok": False, "error": f"Unknown tool: {tool_name}"}

    def list_seasons(self) -> Dict[str, Any]:
        seasons = self._get_json("/api/seasons")
        if isinstance(seasons, dict) and seasons.get("error"):
            return {"ok": False, "error": seasons["error"], "seasons": []}
        if not isinstance(seasons, list):
            return {"ok": False, "error": "Unexpected seasons response", "seasons": []}
        return {"ok": True, "seasons": seasons}

    def resolve_season(
        self,
        user_text: str,
        explicit_season: Optional[str] = None,
        explicit_year: Optional[int] = None,
    ) -> Dict[str, Any]:
        seasons_result = self.list_seasons()
        if not seasons_result.get("ok"):
            return {"ok": False, "error": seasons_result.get("error", "Failed to list seasons")}

        seasons = seasons_result["seasons"]
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

        if resolved is None and self.config.default_season:
            resolved = by_name.get(self.config.default_season.upper())
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

    def get_runs(self, season: str, limit: int = 10) -> Dict[str, Any]:
        runs_payload = self._get_json("/api/runs", params={"season": season})
        runs = runs_payload.get("runs", []) if isinstance(runs_payload, dict) else []
        return {
            "ok": True,
            "season": season,
            "runs": runs[: max(1, min(limit, 100))],
            "total": len(runs),
        }

    def list_sensors(self, season: str, search: str = "", limit: int = 200) -> Dict[str, Any]:
        sensors_payload = self._get_json("/api/sensors", params={"season": season})
        sensors = sensors_payload.get("sensors", []) if isinstance(sensors_payload, dict) else []

        if search:
            needle = search.lower().strip()
            sensors = [s for s in sensors if needle in str(s).lower()]

        limit = max(1, min(limit, 2000))
        return {
            "ok": True,
            "season": season,
            "sensors": sensors[:limit],
            "total": len(sensors),
        }

    def query_signal(self, season: str, signal: str, start: str, end: str, limit: int = 2000) -> Dict[str, Any]:
        """
        Fetch one signal directly from InfluxDB via HTTP REST (/api/v3/query_sql).
        Avoids gRPC/Arrow Flight which hits InfluxDB3 Core's parquet file limit.
        Returns {ok, data: [row_dicts]} where each dict has 'time' and the signal column.
        """
        influx_url = os.getenv("INFLUX_URL", "http://influxdb3:8181").rstrip("/")
        influx_token = os.getenv("INFLUX_TOKEN", "")
        limit_val = max(10, min(int(limit), 20000))
        sql = (
            f'SELECT time, "{signal}" FROM "{season}" '
            f"WHERE time >= '{start}' AND time <= '{end}' "
            f'AND "{signal}" IS NOT NULL '
            f"ORDER BY time LIMIT {limit_val}"
        )
        try:
            resp = requests.post(
                f"{influx_url}/api/v3/query_sql",
                json={"db": season, "q": sql},
                headers={
                    "Authorization": f"Token {influx_token}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                timeout=30,
            )
            resp.raise_for_status()
            rows = resp.json() or []
            # Normalize timestamps: InfluxDB3 returns nanosecond precision strings
            # (e.g. "2025-10-04T12:51:35.446000128") which pandas can't parse directly.
            # Truncate to microseconds so pd.to_datetime() always works.
            import re as _re
            _ts_pat = _re.compile(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6})\d+')
            for row in rows:
                if "time" in row and isinstance(row["time"], str):
                    row["time"] = _ts_pat.sub(r'\1', row["time"])
            return {"ok": True, "data": rows}
        except Exception as e:
            return {"ok": False, "error": str(e), "data": []}

    def validate_request(
        self,
        season: str,
        signals: Optional[List[str]] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> Dict[str, Any]:
        errors: List[str] = []
        warnings: List[str] = []
        signals = signals or []

        seasons_result = self.list_seasons()
        if not seasons_result.get("ok"):
            errors.append(seasons_result.get("error", "Failed to list seasons"))
            return {"ok": False, "errors": errors, "warnings": warnings}

        known = {str(s.get("name", "")).upper() for s in seasons_result.get("seasons", [])}
        if season.upper() not in known:
            errors.append(f"Unknown season: {season}")

        sensors_result = self.list_sensors(season=season, limit=5000)
        if sensors_result.get("ok"):
            known_sensors = set(sensors_result.get("sensors", []))
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
                    warnings.append("Time window > 2h; prefer chunked fetch to avoid Influx file-limit or memory errors")
            except ValueError:
                errors.append("Invalid ISO-8601 time format in start/end")

        return {
            "ok": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "season": season,
            "signals": signals,
        }

    def build_prompt_context(self, user_prompt: str) -> Dict[str, Any]:
        called_tools: List[str] = []

        resolved = self.resolve_season(user_text=user_prompt)
        called_tools.append("resolve_season")
        if not resolved.get("ok"):
            return {
                "ok": False,
                "error": resolved.get("error", "Season resolution failed"),
                "context": "",
                "called_tools": called_tools,
            }

        season_name = resolved["season"]["name"]
        sensors = self.list_sensors(season=season_name, limit=5000)
        called_tools.append("list_sensors")
        runs = self.get_runs(season=season_name, limit=12)
        called_tools.append("get_runs")

        run_lines = []
        for run in runs.get("runs", []):
            run_lines.append(
                f"- {run.get('start_local')} -> {run.get('end_local')} "
                f"(UTC {run.get('start_utc')} -> {run.get('end_utc')}, rows={run.get('row_count')})"
            )

        sensors_list = sensors.get("sensors", [])
        context = (
            "MCP_CONTEXT\n"
            f"resolved_season={season_name} (reason={resolved.get('reason')})\n"
            "policy: always use this resolved season unless user explicitly asks another season/year.\n"
            "runs:\n"
            + ("\n".join(run_lines) if run_lines else "- none")
            + "\n"
            + f"sensors_total={sensors.get('total', 0)}\n"
            + "sensors_exact_names=\n"
            + (", ".join(sensors_list) if sensors_list else "")
        )

        return {
            "ok": True,
            "resolved_season": season_name,
            "resolution_reason": resolved.get("reason"),
            "context": context,
            "runs_count": runs.get("total", 0),
            "sensors_count": sensors.get("total", 0),
            "called_tools": called_tools,
        }

    def _get_json(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self.config.data_downloader_url.rstrip('/')}{path}"
        resp = requests.get(url, params=params, timeout=self.config.request_timeout_s)
        resp.raise_for_status()
        return resp.json()

    def _post_json(self, path: str, payload: Dict[str, Any], params: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self.config.data_downloader_url.rstrip('/')}{path}"
        resp = requests.post(url, params=params, json=payload, timeout=self.config.request_timeout_s)
        resp.raise_for_status()
        return resp.json()
