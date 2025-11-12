import { useEffect, useMemo, useState } from "react";
import Papa from "papaparse";
import { Download } from "lucide-react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid
} from "recharts";

import { RunRecord, SensorDataPoint, SensorDataResponse } from "../types";
import { querySensorData } from "../api";

interface Props {
  runs: RunRecord[];
  sensors: string[];
}

const formatInputValue = (value: string) => {
  if (!value) return "";
  const date = new Date(value);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
};

const toIsoString = (value: string) => {
  if (!value) return "";
  const date = new Date(value);
  return date.toISOString();
};

const toLocaleTimestamp = (value: string) =>
  new Date(value).toLocaleString(undefined, { hour12: false });

export function DataDownload({ runs, sensors }: Props) {
  const [selectedRunKey, setSelectedRunKey] = useState<string>("");
  const [selectedSensor, setSelectedSensor] = useState<string>("");
  const [startInput, setStartInput] = useState<string>("");
  const [endInput, setEndInput] = useState<string>("");
  const [series, setSeries] = useState<SensorDataPoint[]>([]);
  const [queryMeta, setQueryMeta] = useState<Omit<SensorDataResponse, "points"> | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!selectedSensor && sensors.length > 0) {
      setSelectedSensor(sensors[0]);
    }
  }, [sensors, selectedSensor]);

  const handleRunSelect = (runKey: string) => {
    setSelectedRunKey(runKey);
    const run = runs.find((r) => r.key === runKey);
    if (run) {
      setStartInput(formatInputValue(run.start_utc));
      setEndInput(formatInputValue(run.end_utc));
    }
  };

  const handleFetch = async () => {
    if (!selectedSensor || !startInput || !endInput) {
      setError("Select a sensor and provide both start and end times.");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const payload = {
        signal: selectedSensor,
        start: toIsoString(startInput),
        end: toIsoString(endInput),
        limit: 5000
      };
      const response = await querySensorData(payload);
      setSeries(response.points);
      const { points, ...meta } = response;
      setQueryMeta(meta);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load data.");
      setSeries([]);
      setQueryMeta(null);
    } finally {
      setLoading(false);
    }
  };

  const chartData = useMemo(
    () =>
      series.map((point) => ({
        iso: point.time,
        label: toLocaleTimestamp(point.time),
        value: point.value
      })),
    [series]
  );

  const handleDownload = () => {
    if (series.length === 0) return;
    const csv = Papa.unparse(
      series.map((point) => ({
        time: point.time,
        value: point.value
      }))
    );
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${selectedSensor || "sensor"}_data.csv`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div>
      <h2>Data Downloader</h2>
      <p className="subtitle">
        Choose a run window and sensor to pull raw readings directly from InfluxDB3 via SQL.
      </p>

      <div className="data-download-grid">
        <div className="selector-panel">
          <label className="selector-label">Pick a run window</label>
          <select
            className="selector-input"
            value={selectedRunKey}
            onChange={(event) => handleRunSelect(event.target.value)}
          >
            <option value="">Manual selection</option>
            {runs.map((run) => (
              <option key={run.key} value={run.key}>
                {`${toLocaleTimestamp(run.start_local)} -> ${toLocaleTimestamp(run.end_local)}`}
              </option>
            ))}
          </select>

          <div className="selector-field">
            <label className="selector-label">Start (UTC)</label>
            <input
              type="datetime-local"
              className="selector-input"
              value={startInput}
              onChange={(event) => setStartInput(event.target.value)}
            />
          </div>
          <div className="selector-field">
            <label className="selector-label">End (UTC)</label>
            <input
              type="datetime-local"
              className="selector-input"
              value={endInput}
              onChange={(event) => setEndInput(event.target.value)}
            />
          </div>

          <div className="selector-field">
            <label className="selector-label">Sensor</label>
            <select
              className="selector-input"
              value={selectedSensor}
              onChange={(event) => setSelectedSensor(event.target.value)}
            >
              {sensors.length === 0 ? (
                <option value="">No sensors available</option>
              ) : (
                sensors.map((sensor) => (
                  <option value={sensor} key={sensor}>
                    {sensor}
                  </option>
                ))
              )}
            </select>
          </div>

          <div className="selector-actions">
            <button className="button" disabled={loading} onClick={handleFetch}>
              {loading ? "Querying..." : "Query Data"}
            </button>
            <button
              className="button secondary"
              disabled={series.length === 0}
              onClick={handleDownload}
            >
              <Download size={16} />
              Export CSV
            </button>
          </div>
          {error && <p className="selector-error">{error}</p>}
          {queryMeta && (
            <>
              <p className="selector-meta">
                {queryMeta.row_count} points retrieved between{" "}
                {toLocaleTimestamp(queryMeta.start)} and {toLocaleTimestamp(queryMeta.end)}.
              </p>
              <div className="selector-sql">
                <p className="selector-label">SQL</p>
                <pre>{queryMeta.sql}</pre>
              </div>
            </>
          )}
        </div>

        <div className="chart-panel">
          <div className="chart-header">
            <h3>{selectedSensor || "Select a sensor"}</h3>
            <p className="subtitle">
              {series.length > 0
                ? `Previewing ${series.length} samples`
                : "Run a query to see values"}
            </p>
          </div>
          <div className="chart-wrapper">
            {loading ? (
              <div className="chart-placeholder">Loading data...</div>
            ) : series.length === 0 ? (
              <div className="chart-placeholder">No data loaded yet.</div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={chartData}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="label" minTickGap={30} />
                  <YAxis />
                  <Tooltip
                    formatter={(value: number) => [`${value}`, selectedSensor]}
                    labelFormatter={(label: string) => label}
                  />
                  <Line type="monotone" dataKey="value" stroke="#2563eb" dot={false} strokeWidth={2} />
                </LineChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
