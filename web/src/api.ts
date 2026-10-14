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
/** Where a query reads - models.Tier, held to it by test_api_types.py. */
export type Tier = "rdb" | "hdb" | "both";
/** A worker's state as status.q writes it - models.RunState, held to it by
 * test_api_types.py, and RunState to q by test_status.py (#820). */
export type RunState = "starting" | "running" | "idle" | "completed" | "failed";
/** A filter operator - models.Filter.op, held to it by test_api_types.py. */
export type FilterOp = "eq" | "ne" | "lt" | "le" | "gt" | "ge" | "in";
/** One predicate of a query - models.Filter. The request bodies below are
 * held to their models field for field by test_api_types.py, and the server
 * refuses a key it does not know (#1361): a renamed field is a failing test
 * and a 422, never a default the server quietly fills in. */
export interface QueryFilter {
  column: string;
  op: FilterOp;
  value: unknown;
}
export interface QueryInput {
  table: string;
  tier: Tier;
  limit: number;
  filters: QueryFilter[];
  require_coverage?: CoverageRequest;
}
export interface QueryResult extends Pollable {
  table: string;
  tier: Tier;
  rows: Row[];
  row_count: number;
  truncated: boolean;
}
export interface Health extends Pollable {
  ok: boolean;
  gateway: "up" | "reloading" | "unreachable" | "wrong_process";
  detail: string | null;
}
export interface Worker {
  worker: string;
  instance_id: string;
  state: RunState;
  source_version: string;
  range_from: string;
  range_to: string;
  cursor: string | null;
  rows_published: number;
  windows_completed: number;
  /** reactions to this run's publications that have not succeeded: a derived dataset is stale (#632) */
  reactions_owed: number;
  error: string | null;
  updated_at: string;
  terminal: boolean;
  pid: number;
  host: string;
  run_id: string | null;
  abandoned: boolean;
  warnings: string[];
}
export interface Backfill extends Pollable {
  summary: Record<string, number>;
  workers: Worker[];
  unreadable: Row[];
  source: string | null;
}
/** One row of /ops/processes - models.ProcessHealthOut. Typed rather than a
 * Row read by string key, so a renamed `up` is a compile error and a failing
 * test_api_types.py, not every process shown Down (#1361). */
export interface ProcessHealth {
  procname: string;
  proctype: string;
  group: string;
  host: string;
  declared_port: number | null;
  start_with_all: boolean;
  up: boolean;
  pid: number | null;
  reported_port: number | null;
  reported_procname: string | null;
  error: string | null;
  identity_mismatch: string | null;
  port_unresolved: boolean;
}
export interface OpsData extends Pollable {
  rows?: Row[];
  servers?: Row[];
  clients?: Row[];
  processes?: ProcessHealth[];
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
  authorization?: string,
): Promise<T> {
  let response: Response;
  const headers: Record<string, string> = {};
  if (body) headers["Content-Type"] = "application/json";
  if (authorization) headers.Authorization = authorization;
  try {
    response = await fetch(path, {
      signal: AbortSignal.any([signal, AbortSignal.timeout(45000)]),
      method: method ?? (body ? "POST" : "GET"),
      headers: Object.keys(headers).length ? headers : undefined,
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
      const span =
        column.type === "timespan" ? qTimespan(String(value)) : undefined;
      const number = span ?? Number(value);
      // A timespan is a whole number of nanoseconds (#1329): a fraction has
      // nowhere to go, and past 2^53 a JavaScript number is not the one typed.
      if (
        !Number.isFinite(number) ||
        (["long", "timespan"].includes(column.type) &&
          !Number.isSafeInteger(number))
      )
        throw new Error(
          column.type === "timespan"
            ? "Enter a whole number of nanoseconds, or a timespan like 0D00:00:01.5."
            : "Enter a valid number (a safe integer for long columns).",
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
/** A q timespan literal - 0D00:00:00.000001234, as the Desk shows one - as
 *  nanoseconds, or undefined when `value` is not one (a plain number of
 *  nanoseconds is read by the caller). Exact: summed as a BigInt. */
export function qTimespan(value: string): number | undefined {
  const parts = /^(-)?(\d+)D(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?$/.exec(
    value.trim(),
  );
  if (!parts) return undefined;
  const [, sign, days, hours, minutes, seconds, fraction] = parts;
  const ns =
    ((BigInt(days) * 24n + BigInt(hours)) * 60n + BigInt(minutes)) * 60n +
    BigInt(seconds);
  const total = ns * 1_000_000_000n + BigInt((fraction ?? "").padEnd(9, "0"));
  return Number(sign ? -total : total);
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
  if (instant(value.range_from) >= instant(value.range_to))
    throw new Error("Range end must be after range start.");
}
/** An ISO instant as nanoseconds since 1970, exact: the server takes nine
 *  fractional digits (#1371), and Date.parse stops at the millisecond, so
 *  two bounds within one would read as an empty range. */
function instant(value: string): bigint {
  const parts = /^(.*?\d{2}:\d{2}:\d{2})(?:[.,](\d+))?(.*)$/.exec(value);
  const [, head, fraction, zone] = parts ?? [, value, "", ""];
  const fine = BigInt((fraction ?? "").padEnd(9, "0").slice(0, 9));
  return BigInt(Date.parse(head + zone)) * 1_000_000n + fine;
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
/** POST /control/process/{action} - models.LifecycleRequest. `procs` is
 * required on both sides: it once defaulted to "all" on the server, so a body
 * without it stopped the whole fleet (#1361). */
export interface LifecycleBody {
  procs: string;
}
/** PUT /control/process/{procname}/config - models.ProcessConfigRequest. */
export interface ProcessConfigBody {
  field: string;
  value: string;
}
/** PUT /control/worker-config - models.WorkerConfigRequest. */
export interface WorkerConfigBody {
  key: string;
  value: string;
}
/** POST /control/backfill - models.BackfillRequest. */
export interface BackfillBody {
  worker: string;
  source_version: string;
  range_from: string;
  range_to: string;
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
  /** The layer now answering for the key; "overrides" when the value just set is in effect. */
  effective_layer: string | null;
  effective_value: string | null;
  /** An environment variable outranks the override, which has no effect until it is unset. */
  shadowed: boolean;
  env_var: string | null;
  note: string;
}
export interface BackfillStarted {
  worker: string;
  source_version: string;
  range_from: string;
  range_to: string;
  procname: string;
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
  const token = writeToken.get();
  return request<T>(
    path,
    controller.signal,
    JSON.stringify(body),
    method,
    token ? `Bearer ${token}` : undefined,
  );
}

/** The write token for the control routes (#631), when the operator typed it.
 *
 * Only the built app served at /ui/ needs it: under `npm run dev` the Vite
 * proxy adds it from the server's environment. Kept in sessionStorage - this
 * tab only, gone when it closes - and every access is guarded, since storage
 * can be blocked outright. */
const WRITE_TOKEN_KEY = "uqf.writeToken";
let heldToken = "";
export const writeToken = {
  get(): string {
    try {
      return sessionStorage.getItem(WRITE_TOKEN_KEY) ?? heldToken;
    } catch {
      return heldToken;
    }
  },
  set(value: string): void {
    // Held in memory too, so a tab with storage blocked still sends it
    // until the page is reloaded.
    heldToken = value;
    try {
      if (value) sessionStorage.setItem(WRITE_TOKEN_KEY, value);
      else sessionStorage.removeItem(WRITE_TOKEN_KEY);
    } catch {
      /* storage blocked: heldToken carries it */
    }
  },
};
