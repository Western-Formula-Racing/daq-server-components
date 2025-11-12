import { RunRecord } from "../types";

interface Props {
  runs: RunRecord[];
  drafts: Record<string, string>;
  onChange: (key: string, value: string) => void;
  onSave: (key: string) => void;
  savingKey: string | null;
}

const formatDateTime = (iso: string) =>
  new Date(iso).toLocaleString(undefined, {
    hour12: false
  });

export function RunTable({ runs, drafts, onChange, onSave, savingKey }: Props) {
  if (runs.length === 0) {
    return <p className="subtitle">No runs found yet.</p>;
  }

  return (
    <div style={{ overflowX: "auto" }}>
      <table className="runs-table">
        <thead>
          <tr>
            <th>Window (local)</th>
            <th>UTC Start</th>
            <th>Bins</th>
            <th>Rows</th>
            <th style={{ width: "280px" }}>Note</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => {
            const draft = drafts[run.key] ?? run.note ?? "";
            return (
              <tr key={run.key}>
                <td>
                  <div>{formatDateTime(run.start_local)}</div>
                  <div className="subtitle">{formatDateTime(run.end_local)}</div>
                </td>
                <td>
                  <div>{formatDateTime(run.start_utc)}</div>
                  <div className="subtitle">{run.timezone ?? "UTC"}</div>
                </td>
                <td>
                  <span className="tag">{run.bins}</span>
                </td>
                <td>{run.row_count ?? "—"}</td>
                <td>
                  <textarea
                    className="note-input"
                    rows={draft.split("\n").length > 1 ? 3 : 2}
                    value={draft}
                    onChange={(event) => onChange(run.key, event.target.value)}
                  />
                  {run.note_updated_at && (
                    <div className="subtitle">
                      Updated {formatDateTime(run.note_updated_at)}
                    </div>
                  )}
                </td>
                <td>
                  <button
                    className="button"
                    disabled={savingKey === run.key}
                    onClick={() => onSave(run.key)}
                  >
                    {savingKey === run.key ? "Saving..." : "Save"}
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
