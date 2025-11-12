export interface RunRecord {
  key: string;
  start_utc: string;
  end_utc: string;
  start_local: string;
  end_local: string;
  bins: number;
  row_count?: number;
  note?: string;
  note_updated_at?: string | null;
  timezone?: string;
}

export interface RunsResponse {
  updated_at: string | null;
  runs: RunRecord[];
}

export interface SensorsResponse {
  updated_at: string | null;
  sensors: string[];
}
