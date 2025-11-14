import {
  RunRecord,
  RunsResponse,
  ScannerStatus,
  SensorDataResponse,
  SensorsResponse
} from "./types";

const RAW_API_BASE = import.meta.env.VITE_API_BASE_URL?.trim() ?? "";
const SANITIZED_API_BASE = RAW_API_BASE.replace(/\/$/, "");
const LOCAL_BASE_PATTERN = /:\/\/(localhost|127\.0\.0\.1|\[?::1]?)/i;
const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "::1"]);
const runningOnLocalhost = typeof window !== "undefined" && LOCAL_HOSTS.has(window.location.hostname);
const preferRelativeBase =
  SANITIZED_API_BASE === "" || (!runningOnLocalhost && LOCAL_BASE_PATTERN.test(SANITIZED_API_BASE));
const API_BASE = preferRelativeBase ? "" : SANITIZED_API_BASE;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {})
    },
    ...init
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed (${response.status})`);
  }
  if (response.status === 204) {
    return {} as T;
  }
  return (await response.json()) as T;
}

export function fetchRuns(): Promise<RunsResponse> {
  return request("/api/runs");
}

export function fetchSensors(): Promise<SensorsResponse> {
  return request("/api/sensors");
}

export function fetchScannerStatus(): Promise<ScannerStatus> {
  return request("/api/scanner-status");
}

export function triggerScan(): Promise<{ status: string }> {
  return request("/api/scan", { method: "POST" });
}

export function updateNote(key: string, note: string): Promise<RunRecord> {
  return request(`/api/runs/${encodeURIComponent(key)}/note`, {
    method: "POST",
    body: JSON.stringify({ note })
  });
}

export interface DataQueryPayload {
  signal: string;
  start: string;
  end: string;
  limit?: number;
  no_limit?: boolean;
}

export function querySensorData(payload: DataQueryPayload): Promise<SensorDataResponse> {
  return request("/api/data/query", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}
