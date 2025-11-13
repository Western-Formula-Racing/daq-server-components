import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fetchRuns, fetchSensors, fetchScannerStatus, triggerScan, updateNote } from "./api";
import { RunRecord, RunsResponse, ScannerStatus, SensorsResponse } from "./types";
import { RunTable } from "./components/RunTable";
import { DataDownload } from "./components/data-download";

type ScanState = "idle" | "running" | "success" | "error";

interface DownloaderSelection {
  runKey?: string;
  startUtc?: string;
  endUtc?: string;
  sensor?: string;
  version: number;
}

export default function App() {
  const [runs, setRuns] = useState<RunsResponse | null>(null);
  const [sensors, setSensors] = useState<SensorsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [noteDrafts, setNoteDrafts] = useState<Record<string, string>>({});
  const [savingKey, setSavingKey] = useState<string | null>(null);
  const [scanState, setScanState] = useState<ScanState>("idle");
  const [downloaderSelection, setDownloaderSelection] = useState<DownloaderSelection | null>(null);
  const [scannerStatus, setScannerStatus] = useState<ScannerStatus | null>(null);
  const sensorsSectionRef = useRef<HTMLElement | null>(null);
  const downloaderSectionRef = useRef<HTMLElement | null>(null);
  const statusFinishedRef = useRef<string | null>(null);

  const loadData = useCallback(async () => {
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

  const loadStatus = useCallback(
    async (syncOnFinishChange: boolean) => {
      try {
        const status = await fetchScannerStatus();
        setScannerStatus(status);
        const finished = status.finished_at ?? null;
        const prevFinished = statusFinishedRef.current;
        statusFinishedRef.current = finished;
        if (
          syncOnFinishChange &&
          !status.scanning &&
          finished &&
          finished !== prevFinished
        ) {
          await loadData();
        }
      } catch (err) {
        console.error("Failed to load scanner status", err);
      }
    },
    [loadData]
  );

  useEffect(() => {
    void loadData();
    void loadStatus(false);
  }, [loadData, loadStatus]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    const id = window.setInterval(() => {
      void loadStatus(true);
    }, 5000);
    return () => window.clearInterval(id);
  }, [loadStatus]);

  const handleScan = async () => {
    setScanState("running");
    setScannerStatus((prev) => ({
      scanning: true,
      started_at: new Date().toISOString(),
      finished_at: prev?.finished_at ?? null,
      source: "manual",
      last_result: prev?.last_result ?? null,
      error: null,
      updated_at: new Date().toISOString()
    }));
    try {
      await triggerScan();
      setScanState("success");
      if (typeof window !== "undefined") {
        window.setTimeout(() => {
          void loadStatus(false);
        }, 1500);
      } else {
        void loadStatus(false);
      }
    } catch (err) {
      console.error(err);
      setScanState("error");
      setError(err instanceof Error ? err.message : "Failed to start scan");
      const message = err instanceof Error ? err.message : "Scan failed";
      setScannerStatus((prev) =>
        prev
          ? { ...prev, scanning: false, last_result: "error", error: message }
          : {
              scanning: false,
              started_at: null,
              finished_at: null,
              source: null,
              last_result: "error",
              error: message,
              updated_at: new Date().toISOString()
            }
      );
    } finally {
      setTimeout(() => setScanState("idle"), 5000);
    }
  };

  const handleRefreshClick = async () => {
    await loadData();
    await loadStatus(false);
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

  const handleRunPick = (run: RunRecord) => {
    setDownloaderSelection((prev) => ({
      runKey: run.key,
      startUtc: run.start_utc,
      endUtc: run.end_utc,
      sensor: prev?.sensor,
      version: (prev?.version ?? 0) + 1
    }));
    sensorsSectionRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const handleSensorPick = (sensor: string) => {
    setDownloaderSelection((prev) => ({
      runKey: prev?.runKey,
      sensor,
      version: (prev?.version ?? 0) + 1
    }));
    downloaderSectionRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const sensorsPreview = useMemo(() => sensors?.sensors ?? [], [sensors]);
  const scanningActive = scannerStatus?.scanning ?? false;
  const scanButtonDisabled = scanningActive || scanState === "running";
  const scanButtonLabel =
    scanState === "running" ? "Scanning..." : scanningActive ? "Scan Running..." : "Trigger Scan";

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

      {scanningActive && (
        <div className="scan-banner" role="alert">
          Scanning database. Do not click again.
        </div>
      )}

      <div className="actions">
        <button className="button" onClick={handleScan} disabled={scanButtonDisabled}>
          {scanButtonLabel}
        </button>
        <button className="button secondary" onClick={() => void handleRefreshClick()} disabled={loading}>
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
            onPickRun={handleRunPick}
          />
        ) : (
          <p className="subtitle">No data yet.</p>
        )}
      </section>

      <section className="card" ref={sensorsSectionRef}>
        <h2>Unique Sensors</h2>
        <p className="subtitle">Last refresh: {lastSensorRefresh}</p>
        {loading && !sensors ? (
          <p className="subtitle">Loading sensors...</p>
        ) : (
          <div className="sensor-grid">
            {sensorsPreview.length === 0 && <p className="subtitle">No sensors captured.</p>}
            {sensorsPreview.map((sensor) => (
              <button
                key={sensor}
                type="button"
                className="sensor-chip"
                onClick={() => handleSensorPick(sensor)}
              >
                {sensor}
              </button>
            ))}
          </div>
        )}
      </section>

      <section className="card" ref={downloaderSectionRef}>
        <DataDownload
          runs={runs?.runs ?? []}
          sensors={sensorsPreview}
          externalSelection={downloaderSelection ?? undefined}
        />
      </section>
    </div>
  );
}
