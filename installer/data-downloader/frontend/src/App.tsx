import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchRuns, fetchSensors, triggerScan, updateNote } from "./api";
import { RunsResponse, SensorsResponse } from "./types";
import { RunTable } from "./components/RunTable";
import { DataDownload } from "./components/data-download";

type ScanState = "idle" | "running" | "success" | "error";

export default function App() {
  const [runs, setRuns] = useState<RunsResponse | null>(null);
  const [sensors, setSensors] = useState<SensorsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [noteDrafts, setNoteDrafts] = useState<Record<string, string>>({});
  const [savingKey, setSavingKey] = useState<string | null>(null);
  const [scanState, setScanState] = useState<ScanState>("idle");

  const refresh = useCallback(async () => {
    try {
      setLoading(true);
      const [runsData, sensorsData] = await Promise.all([fetchRuns(), fetchSensors()]);
      setRuns(runsData);
      setSensors(sensorsData);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch data");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const handleScan = async () => {
    setScanState("running");
    try {
      await triggerScan();
      setScanState("success");
      await refresh();
    } catch (err) {
      console.error(err);
      setScanState("error");
    } finally {
      setTimeout(() => setScanState("idle"), 5000);
    }
  };

  const handleNoteChange = (key: string, value: string) => {
    setNoteDrafts((prev) => ({ ...prev, [key]: value }));
  };

  const handleSaveNote = async (key: string) => {
    const nextNote = noteDrafts[key] ?? runs?.runs.find((r) => r.key === key)?.note ?? "";
    setSavingKey(key);
    try {
      const updated = await updateNote(key, nextNote);
      setRuns((prev) => {
        if (!prev) return prev;
        const updatedRuns = prev.runs.map((run) => (run.key === key ? updated : run));
        return { ...prev, runs: updatedRuns, updated_at: updated.note_updated_at ?? prev.updated_at };
      });
      setNoteDrafts((prev) => {
        const clone = { ...prev };
        delete clone[key];
        return clone;
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save note");
    } finally {
      setSavingKey(null);
    }
  };

  const sensorsPreview = useMemo(() => sensors?.sensors ?? [], [sensors]);

  const lastRunsRefresh = runs?.updated_at
    ? new Date(runs.updated_at).toLocaleString()
    : "never";
  const lastSensorRefresh = sensors?.updated_at
    ? new Date(sensors.updated_at).toLocaleString()
    : "never";

  return (
    <div className="app-shell">
      <header style={{ marginBottom: "1.5rem" }}>
        <h1 style={{ margin: 0 }}>DAQ Data Downloader</h1>
        <p className="subtitle">
          Inspect historical scans, refresh availability, and capture run notes.
        </p>
      </header>

      <div className="actions">
        <button className="button" onClick={handleScan} disabled={scanState === "running"}>
          {scanState === "running" ? "Scanning..." : "Trigger Scan"}
        </button>
        <button className="button secondary" onClick={() => refresh()} disabled={loading}>
          {loading ? "Refreshing..." : "Refresh Data"}
        </button>
        {scanState !== "idle" && (
          <span
            className="status-pill"
            style={{
              background:
                scanState === "success" ? "#dcfce7" : scanState === "error" ? "#fee2e2" : "#fef9c3",
              color:
                scanState === "success" ? "#15803d" : scanState === "error" ? "#b91c1c" : "#a16207"
            }}
          >
            {scanState === "running" && "Scan in progress..."}
            {scanState === "success" && "Scan queued and data refreshed"}
            {scanState === "error" && "Scan failed"}
          </span>
        )}
      </div>

      {error && (
        <div className="card" style={{ border: "1px solid #fecaca", background: "#fef2f2" }}>
          <strong>Heads up:</strong> {error}
        </div>
      )}

      <section className="card">
        <h2>Past Runs</h2>
        <p className="subtitle">Last refresh: {lastRunsRefresh}</p>
        {loading && !runs ? (
          <p className="subtitle">Loading runs...</p>
        ) : runs ? (
          <RunTable
            runs={runs.runs}
            drafts={noteDrafts}
            onChange={handleNoteChange}
            onSave={handleSaveNote}
            savingKey={savingKey}
          />
        ) : (
          <p className="subtitle">No data yet.</p>
        )}
      </section>

      <section className="card">
        <h2>Unique Sensors</h2>
        <p className="subtitle">Last refresh: {lastSensorRefresh}</p>
        {loading && !sensors ? (
          <p className="subtitle">Loading sensors...</p>
        ) : (
          <div className="sensor-grid">
            {sensorsPreview.length === 0 && <p className="subtitle">No sensors captured.</p>}
            {sensorsPreview.map((sensor) => (
              <div key={sensor} className="sensor-chip">
                {sensor}
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="card">
        <DataDownload runs={runs?.runs ?? []} sensors={sensorsPreview} />
      </section>
    </div>
  );
}
