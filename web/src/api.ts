export type Row = Record<string, unknown>;
export interface Pollable {
  poll_seconds: number;
}
export interface Column {
  name: string;
  type: string;
  filterable: boolean;
  /** Decimal places this column is shown with, resolved server-side from the
   * column, then its table, then its type. Null means show it as it arrives. */
  decimals: number | null;
}
export interface Catalog {
  tables: { name: string; description: string; columns: Column[] }[];
  operators: string[];
}
export interface CoverageRequest {
  dataset: string;
  // The slice to ask about. "" is the sentinel for a dataset with no
  // partition dimension - not "any partition". The gateway requires the
  // field, because a coverage read that omits it aggregates across every
  // partition and reports a range published for one symbol as covered for
  // all of them (#185).
  partition: string;
  source_version: string;
  range_from: string;
  range_to: string;
}
export interface Interval {
  range_from: string;
  range_to: string;
}
export interface Coverage extends Pollable {
  dataset: string;
  source_version: string;
  covered: Interval[];
  requested: Interval | null;
  gaps: Interval[];
  complete: boolean;
}
export interface QueryInput {
  table: string;
  tier: string;
  limit: number;
  filters: { column: string; op: string; value: unknown }[];
  require_coverage?: CoverageRequest;
}
export interface QueryResult extends Pollable {
  table: string;
  tier: string;
  rows: Row[];
  row_count: number;
  truncated: boolean;
}
export interface Health extends Pollable {
  ok: boolean;
  gateway: "up" | "reloading" | "unreachable";
  detail: string | null;
}
export interface Worker {
  worker: string;
  instance_id: string;
  state: string;
  source_version: string;
  range_from: string;
  range_to: string;
  cursor: string | null;
  rows_published: number;
  windows_completed: number;
  error: string | null;
  updated_at: string;
  terminal: boolean;
  warnings: string[];
}
export interface Backfill extends Pollable {
  summary: Record<string, number>;
  workers: Worker[];
  unreadable: Row[];
  source: string | null;
}
export interface OpsData extends Pollable {
  rows?: Row[];
  servers?: Row[];
  clients?: Row[];
  processes?: Row[];
  summary?: Record<string, number>;
  unreachable?: Row[];
  processes_configured?: number;
}

export class ApiError extends Error {
  constructor(
    message: string,
    public transient = false,
    public status = 0,
  ) {
    super(message);
  }
}
export async function request<T>(
  path: string,
  signal: AbortSignal,
  body?: string,
  // Explicit only when it is not the obvious one. GET without a body and
  // POST with one cover every read in this app; the control routes need PUT,
  // and inferring that from anything would be guessing.
  method?: "GET" | "POST" | "PUT",
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      signal: AbortSignal.any([signal, AbortSignal.timeout(45000)]),
      method: method ?? (body ? "POST" : "GET"),
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body,
    });
  } catch (error) {
    if (signal.aborted) throw error;
    throw new ApiError(
      "The API is unavailable or the request timed out.",
      true,
    );
  }
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = payload?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail
              .map(
                (item: { loc?: string[]; msg: string }) =>
                  `${item.loc?.join(".")}: ${item.msg}`,
              )
              .join("; ")
          : `Request failed (${response.status}).`;
    throw new ApiError(message, payload?.transient === true, response.status);
  }
  if (payload === null)
    throw new ApiError("The API returned an invalid response.", true);
  return payload as T;
}

export function filterValue(
  raw: string,
  column: Column,
  operator: string,
): unknown {
  const scalar = (value: unknown): unknown => {
    if (["float", "long", "timespan"].includes(column.type)) {
      if (
        !["number", "string"].includes(typeof value) ||
        value === null ||
        String(value).trim() === ""
      )
        throw new Error("Enter a number.");
      const number = Number(value);
      if (
        !Number.isFinite(number) ||
        (column.type === "long" && !Number.isSafeInteger(number))
      )
        throw new Error(
          "Enter a valid number (a safe integer for long columns).",
        );
      return number;
    }
    if (column.type === "boolean") {
      if (value === true || value === "true") return true;
      if (value === false || value === "false") return false;
      throw new Error("Enter true or false.");
    }
    if (typeof value !== "string") throw new Error("Enter a string value.");
    if (column.type === "timestamp") validateTime(value);
    return value;
  };
  if (operator !== "in") return scalar(raw);
  const values: unknown = JSON.parse(raw);
  if (!Array.isArray(values) || values.length === 0)
    throw new Error('Use a non-empty JSON list, e.g. ["EURUSD", "GBPUSD"].');
  return values.map(scalar);
}
export function validateTime(value: string) {
  if (
    !/T.*(?:Z|[+-]\d{2}:\d{2})$/i.test(value) ||
    !Number.isFinite(Date.parse(value))
  )
    throw new Error(
      "Use an ISO timestamp with a timezone, e.g. 2026-09-16T00:00:00Z.",
    );
}
export function validateRange(value: CoverageRequest) {
  if (!value.dataset.trim() || !value.source_version.trim())
    throw new Error("Dataset and source version are required.");
  validateTime(value.range_from);
  validateTime(value.range_to);
  if (Date.parse(value.range_from) >= Date.parse(value.range_to))
    throw new Error("Range end must be after range start.");
}

// --- control (writes) ------------------------------------------------------

export interface ControlProcess {
  procname: string;
  proctype: string;
  start_with_all: boolean;
}
export interface ControlStatus extends Pollable {
  writes_enabled: boolean;
  lifecycle_actions: string[];
  settable_fields: string[];
  processes: ControlProcess[];
}
export interface CommandResult {
  action: string;
  target: string;
  exit_code: number;
  ok: boolean;
  output: string;
}
export interface ProcessConfigResult {
  procname: string;
  config: Record<string, string>;
  settable_fields: string[];
}
export interface WorkerConfigResult {
  key: string;
  value: string;
  explain: unknown;
  note: string;
}
export interface BackfillStarted {
  worker: string;
  source_version: string;
  range_from: string;
  range_to: string;
  pid: number;
  status_path: string;
}

/** A one-shot write. Separate from `useResource`, which polls.
 *
 * Polling a control route would re-issue it, and re-issuing `stop all` on a
 * timer is not a refresh. So writes are explicit, single, and their own
 * abort signal.
 */
export async function mutate<T>(
  path: string,
  body: unknown,
  method: "POST" | "PUT" = "POST",
): Promise<T> {
  const controller = new AbortController();
  return request<T>(path, controller.signal, JSON.stringify(body), method);
}
