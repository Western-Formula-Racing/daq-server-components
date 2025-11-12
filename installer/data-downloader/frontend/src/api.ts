import { RunRecord, RunsResponse, SensorsResponse } from "./types";

const API_BASE = import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, "") || "";

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

export function triggerScan(): Promise<{ status: string }> {
  return request("/api/scan", { method: "POST" });
}

export function updateNote(key: string, note: string): Promise<RunRecord> {
  return request(`/api/runs/${encodeURIComponent(key)}/note`, {
    method: "POST",
    body: JSON.stringify({ note })
  });
}
