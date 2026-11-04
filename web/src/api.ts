export type Row = Record<string, unknown>;
export interface Pollable {
  poll_seconds: number;
}
/** The column types the server reports: catalog.QType, held to this list by
 *  test_api_types.py. A new type needs a branch in filterValue. */
export type ColumnType =
  | "symbol"
  | "timestamp"
  | "timespan"
  | "float"
  | "long"
  | "int"
  | "short"
  | "boolean"
  | "date"
  | "time"
  | "char"
  | "guid"
  | "list";
export interface Column {
  name: string;
  type: ColumnType;
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
  /** seconds the browser waits for any request - the server's UQF_FRONTEND_TIMEOUT plus headroom */
  request_timeout_seconds: number;
  ok: boolean;
  gateway: "up" | "reloading" | "unreachable" | "wrong_process";
  detail: string | null;
  /** the level a tab records its activity at unless its operator picks another (#2000) */
  activity_level: SessionLevel;
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
/** One deployment target - models.ServiceTargetOut, as `uqs target list
 * --json` lists it. Read from deploy_targets.toml: the server is never asked. */
export interface ServiceTarget {
  name: string;
  ssh: string;
  dest: string;
  remote_user: string | null;
  environment: string | null;
  labels: Record<string, string>;
  last_deployed: { release: string; at: string } | null;
}
/** GET /services - models.ServicesResponse. `invalid` is the registry's own
 * refusal, naming the line, when the file exists but cannot be read. */
export interface Services extends Pollable {
  targets: ServiceTarget[];
  source: string;
  declared: boolean;
  invalid: string | null;
  remote_operations_enabled: boolean;
}
/** One entry of a remote directory - models.RemoteEntryOut. A symlink,
 * special file or undecodable name is `blocked`: listed, never entered. */
export interface RemoteEntry {
  name: string;
  kind: "dir" | "file" | "symlink" | "special" | "unknown";
  size: number | null;
  mtime: number | null;
  blocked: boolean;
  reason: string | null;
}
/** The release `current` names, and its deploy report - models.ReleaseOut. */
export interface Release {
  current: string | null;
  release: string | null;
  report: Record<string, unknown> | null;
  report_error: string | null;
}
/** GET /services/{target}/files - models.RemoteFilesResponse. Not polled:
 * each listing is an ssh round trip, made only when the operator asks. */
export interface RemoteFiles {
  target: string;
  root: string;
  path: string;
  parent: string | null;
  entries: RemoteEntry[];
  total: number | null;
  truncated: boolean;
  release: Release;
}
/** The URL that lists `path` under `target`'s deployment root. */
export function remoteFilesPath(target: string, path: string): string {
  const query = new URLSearchParams({ path });
  return `/services/${encodeURIComponent(target)}/files?${query}`;
}
/** A mapping's last successful check, as `uqs target tunnel check`
 * recorded it - models.TunnelCheckRecordOut. */
export interface TunnelCheckRecord {
  at: string;
  identity: string | null;
  remote_port: number | null;
  local_port: number | null;
  release: string | null;
}
/** One forward of a tunnel plan - models.TunnelMappingOut. Every endpoint
 * here is uqs's; the browser never builds one from a port. */
export interface TunnelMapping {
  process: string;
  role: string;
  remote_host: string;
  remote_port: number;
  local_host: string;
  local_port: number;
  production_endpoint: string;
  local_endpoint: string;
  query_command: string;
  state: "verified" | "stale" | "unverified";
  last_check: TunnelCheckRecord | null;
  listening: boolean;
}
/** GET /services/{target}/tunnel - models.TunnelPlanResponse. Not polled:
 * each plan is an ssh round trip to read the release's processes. */
export interface TunnelPlan {
  target: string;
  ssh: string;
  release: string | null;
  mappings: TunnelMapping[];
  launch_argv: string[];
  launch_command: string;
  available: string[];
}
/** One endpoint of a tunnel check - models.TunnelEndpointOut. */
export interface TunnelEndpoint {
  process: string;
  role: string;
  production_endpoint: string;
  local_endpoint: string;
  remote_port: number;
  local_port: number;
  status: string;
  detail: string;
  layers: Record<string, string>;
  connected: boolean;
  identity: string | null;
  gateway_servers: string[] | null;
  last_success: TunnelCheckRecord | null;
}
/** GET /services/{target}/tunnel/check - models.TunnelCheckResponse. */
export interface TunnelCheck {
  ok: boolean;
  target: string;
  ssh: string | null;
  release: string | null;
  checked_at: string;
  category: string | null;
  error: string | null;
  launch_command: string | null;
  endpoints: TunnelEndpoint[];
  categories: Record<string, string>;
}
/** The URL of `target`'s tunnel plan, or with `check` its check, for the
 *  chosen processes (none: every rdb, hdb, wdb and gateway). */
export function tunnelPath(
  target: string,
  processes: readonly string[] = [],
  check = false,
): string {
  const query = new URLSearchParams(processes.map((p) => ["process", p]));
  const route = check ? "tunnel/check" : "tunnel";
  const base = `/services/${encodeURIComponent(target)}/${route}`;
  return query.size ? `${base}?${query}` : base;
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
/** How long a request may take before the browser gives up, until the server
 * says otherwise. The server owns this (config.Settings.request_timeout,
 * served on /health); this is only what applies before the first /health
 * answer, and a pytest (test_request_timeout.py) holds it equal to the
 * server's default. */
export const DEFAULT_REQUEST_TIMEOUT_MS = 45000;
let requestTimeoutMs = DEFAULT_REQUEST_TIMEOUT_MS;

/** Adopt the timeout /health serves; anything not a positive number is ignored. */
export function adoptRequestTimeout(seconds: unknown): void {
  if (typeof seconds === "number" && seconds > 0)
    requestTimeoutMs = seconds * 1000;
}

export function requestTimeout(): number {
  return requestTimeoutMs;
}

/** A request's start or finish, for the activity recorder's debug level
 *  (activity.ts). A note carries the method, the path and the status - never
 *  a body, a header or the token. */
export interface RequestNote {
  phase: "start" | "finish";
  method: string;
  path: string;
  requestId?: string;
  status?: number;
  durationMs?: number;
}
let requestObserver: ((note: RequestNote) => void) | undefined;
/** Be told of every request `request` makes; one observer, the recorder.
 *  Returns the uninstaller. */
export function observeRequests(
  observer: (note: RequestNote) => void,
): () => void {
  requestObserver = observer;
  return () => {
    if (requestObserver === observer) requestObserver = undefined;
  };
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
  extra?: Record<string, string>,
): Promise<T> {
  let response: Response;
  const headers: Record<string, string> = { ...extra };
  if (body) headers["Content-Type"] = "application/json";
  if (authorization) headers.Authorization = authorization;
  const verb = method ?? (body ? "POST" : "GET");
  const note = {
    method: verb,
    path,
    requestId: extra?.["X-UQF-Request-Id"],
  };
  const started = performance.now();
  requestObserver?.({ ...note, phase: "start" });
  const finished = (status?: number) =>
    requestObserver?.({
      ...note,
      phase: "finish",
      status,
      durationMs: performance.now() - started,
    });
  try {
    response = await fetch(path, {
      signal: AbortSignal.any([signal, AbortSignal.timeout(requestTimeoutMs)]),
      method: verb,
      headers: Object.keys(headers).length ? headers : undefined,
      body,
    });
  } catch (error) {
    finished();
    if (signal.aborted) throw error;
    throw new ApiError(
      "The API is unavailable or the request timed out.",
      true,
    );
  }
  finished(response.status);
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
  if (path === "/health") adoptRequestTimeout(payload.request_timeout_seconds);
  return payload as T;
}

export function filterValue(
  raw: string,
  column: Column,
  operator: string,
): unknown {
  const scalar = (value: unknown): unknown => {
    if (["float", "long", "int", "short", "timespan"].includes(column.type)) {
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
        (["long", "int", "short", "timespan"].includes(column.type) &&
          !Number.isSafeInteger(number))
      )
        throw new Error(
          column.type === "timespan"
            ? "Enter a whole number of nanoseconds, or a timespan like 0D00:00:01.5."
            : "Enter a valid number (a whole number for long, int and short columns).",
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
    if (column.type === "date" && !/^\d{4}-\d{2}-\d{2}$/.test(value))
      throw new Error("Use a date like 2026-09-16.");
    if (
      column.type === "time" &&
      !/^\d{2}:\d{2}:\d{2}(\.\d{1,3})?$/.test(value)
    )
      throw new Error(
        "Use a time like 14:30:00 or 14:30:00.250 (milliseconds).",
      );
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
  /** The override is what the process now reads (the server compares q's layer name). */
  saved: boolean;
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
  correlation?: Correlation,
): Promise<T> {
  const controller = new AbortController();
  const token = writeToken.get();
  return request<T>(
    path,
    controller.signal,
    JSON.stringify(body),
    method,
    token ? `Bearer ${token}` : undefined,
    correlation
      ? {
          "X-UQF-Request-Id": correlation.requestId,
          "X-UQF-Session-Id": correlation.sessionId,
        }
      : undefined,
  );
}
/** The ids a write carries so the server's audit row and the browser's
 *  activity events for it can be read together (#1954). */
export interface Correlation {
  requestId: string;
  sessionId: string;
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

/** PUT /services/{target}/files/content - models.RemoteUploadResponse: one
 * file written below the target's shared/config, published atomically. */
export interface RemoteUpload {
  target: string;
  root: string;
  path: string;
  size: number;
  sha256: string;
  replaced: boolean;
  mode: number;
  created: string[];
  push_id: string;
  previous_size: number | null;
  previous_sha256: string | null;
}
/** Upload limits and destination, held equal to remote_helper.py's by
 * test_remote_upload.py: the browser refuses first, the API and the server
 * refuse again. */
export const UPLOAD_MAX_BYTES = 16 * 1024 * 1024;
export const UPLOAD_ROOT = "shared/config";
export const UPLOAD_MAX_DEPTH = 16;
/** The longest root-relative path, in characters, the server will split. */
export const UPLOAD_MAX_PATH = 4096;
export const UPLOAD_TEMP_PREFIX = ".uqf-upload-";
/** Longer than the server's own UPLOAD_TIMEOUT, so the server's answer -
 * including "uncertain" - arrives before the browser gives up. */
export const UPLOAD_TIMEOUT_MS = 150 * 1000;
/** What one queue may hold - the browser's own bound, on top of the
 * per-file limit every side enforces. */
export const UPLOAD_MAX_FILES = 500;
export const UPLOAD_MAX_TOTAL_BYTES = 256 * 1024 * 1024;

/** The URL that writes `path` (relative to the deployment root) as one
 * file of push `push`. */
export function remoteUploadPath(
  target: string,
  path: string,
  overwrite: boolean,
  push: string,
): string {
  const query = new URLSearchParams({
    path,
    overwrite: String(overwrite),
    push,
  });
  return `/services/${encodeURIComponent(target)}/files/content?${query}`;
}

/** Why `path` (relative to the upload root's parent, the deployment root)
 * cannot be uploaded to, or null. The same rules remote_helper.upload_parts
 * applies - the server's word is still final. */
export function uploadPathProblem(path: string): string | null {
  // characters as Python's len() counts them: code points, not UTF-16 units
  if ([...path].length > UPLOAD_MAX_PATH)
    return `the path is longer than ${UPLOAD_MAX_PATH} characters`;
  if (path.includes("\0")) return "the path contains a NUL byte";
  if (path.startsWith("/")) return "absolute paths are refused";
  const parts = path.split("/").filter((p) => p !== "" && p !== ".");
  if (parts.includes("..")) return "'..' is refused";
  const root = UPLOAD_ROOT.split("/");
  if (
    parts.length <= root.length ||
    root.some((part, index) => parts[index] !== part)
  )
    return `uploads go to a file below ${UPLOAD_ROOT}/ and nowhere else`;
  if (parts.length - root.length - 1 > UPLOAD_MAX_DEPTH)
    return `more than ${UPLOAD_MAX_DEPTH} directories below ${UPLOAD_ROOT}/`;
  const encoder = new TextEncoder();
  for (const part of parts) {
    if (encoder.encode(part).length > 255)
      return "a name is longer than 255 bytes";
    if (part.startsWith(UPLOAD_TEMP_PREFIX))
      return `names starting ${UPLOAD_TEMP_PREFIX} are the upload's own`;
  }
  return null;
}

/** An upload that did not succeed. `uncertain` when the file may have been
 * written all the same: a timeout, a dropped connection, a cancellation in
 * flight, or the server saying so. Such an upload is never retried. */
export class UploadFailed extends ApiError {
  constructor(
    message: string,
    status: number,
    public uncertain: boolean,
  ) {
    super(message, false, status);
  }
}

/** PUT one file's bytes, reporting progress; one attempt, no retry.
 * XMLHttpRequest rather than fetch, because only it reports upload
 * progress. */
export function uploadRemoteFile(
  target: string,
  path: string,
  file: Blob,
  overwrite: boolean,
  onProgress: (fraction: number) => void,
  signal: AbortSignal,
  push: string,
): Promise<RemoteUpload> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", remoteUploadPath(target, path, overwrite, push));
    const token = writeToken.get();
    if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.timeout = UPLOAD_TIMEOUT_MS;
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && event.total > 0)
        onProgress(event.loaded / event.total);
    };
    const uncertain = (why: string) =>
      reject(
        new UploadFailed(
          `${why} - the file may or may not have been written. List the directory before uploading it again.`,
          0,
          true,
        ),
      );
    xhr.onload = () => {
      let payload: { error?: unknown; detail?: unknown } | null = null;
      try {
        payload = JSON.parse(xhr.responseText);
      } catch {
        payload = null;
      }
      if (xhr.status >= 200 && xhr.status < 300 && payload) {
        onProgress(1);
        resolve(payload as unknown as RemoteUpload);
        return;
      }
      const detail =
        typeof payload?.detail === "string"
          ? payload.detail
          : `Upload failed (${xhr.status}).`;
      // Only the server's word, or an answer it never gave, is uncertain; a
      // refusal (4xx) and a failure it names as certain wrote nothing.
      const unsure =
        payload?.error === "UploadOutcomeUncertain" ||
        payload === null ||
        xhr.status === 504;
      reject(new UploadFailed(detail, xhr.status, unsure));
    };
    xhr.onerror = () => uncertain("The connection to the API failed");
    xhr.ontimeout = () => uncertain("The upload timed out");
    xhr.onabort = () => uncertain("The upload was cancelled in flight");
    if (signal.aborted) {
      reject(new UploadFailed("Cancelled before it was sent.", 0, false));
      return;
    }
    signal.addEventListener("abort", () => xhr.abort(), { once: true });
    xhr.send(file);
  });
}

// --- config push history (#2002) -------------------------------------------

/** POST /services/{target}/config/pushes - models.ConfigPushOpenRequest. */
export interface ConfigPushOpenBody {
  destination: string;
  file_count: number;
  total_bytes: number;
}
/** A push whose history retention removed - models.PrunedPushOut. */
export interface PrunedPush {
  push_id: string;
  why: "count" | "age";
  pruned_at: string;
}
/** models.ConfigPushOpenResponse: the push's id, which every upload names. */
export interface ConfigPushOpened {
  target: string;
  root: string;
  push_id: string;
  pruned: PrunedPush[];
  keep: number;
  keep_days: number;
}
/** What the browser saw of a file the server may never have recorded -
 * models.ConfigPushReported. */
export interface ConfigPushReport {
  path: string;
  outcome: "uncertain" | "cancelled" | "failed" | "refused" | "invalid";
}
/** POST .../config/pushes/{id}/close - models.ConfigPushCloseRequest. */
export interface ConfigPushCloseBody {
  reported: ConfigPushReport[];
}
/** One file of a push - models.ConfigPushFileOut. */
export interface ConfigPushFile {
  path: string;
  action: "create" | "replace" | "delete";
  size: number | null;
  sha256: string | null;
  mode: number | null;
  previous_size: number | null;
  previous_sha256: string | null;
  previous_mode: number | null;
  created_dirs: string[];
  state: "pending" | "published" | "failed";
  error: string | null;
  at: string;
  now?: "after" | "before" | "changed" | "blocked" | null;
  now_sha256?: string | null;
  now_error?: string | null;
}
/** One push in a target's history - models.ConfigPushSummaryOut. */
export interface ConfigPushSummary {
  push_id: string;
  kind?: "push" | "restore" | null;
  restores?: string | null;
  created?: string | null;
  closed?: string | null;
  user?: string | null;
  target?: string | null;
  via?: string | null;
  destination?: string | null;
  declared?: Record<string, number> | null;
  file_count?: number | null;
  total_bytes?: number | null;
  states?: Record<string, number> | null;
  reported?: number | null;
  error?: string | null;
}
/** GET /services/{target}/config/pushes - models.ConfigHistoryResponse. */
export interface ConfigHistory {
  target: string;
  root: string;
  pushes: ConfigPushSummary[];
  total: number;
  truncated: boolean;
  pruned: PrunedPush[];
}
/** models.ConfigPushReportedOut. */
export interface ConfigPushReported {
  path: string;
  outcome: string;
}
/** A push's manifest - models.ConfigPushOut. */
export interface ConfigPush {
  push_id: string;
  kind: "push" | "restore";
  restores: string | null;
  created: string;
  closed: string | null;
  user: string | null;
  target: string | null;
  via: string | null;
  destination: string;
  declared: Record<string, number>;
  reported: ConfigPushReported[];
  files: ConfigPushFile[];
}
/** GET .../config/pushes/{id}, and a push closed - models.ConfigPushResponse. */
export interface ConfigPushDetail {
  target: string;
  root: string;
  summary: ConfigPushSummary;
  push: ConfigPush;
}
/** Before against after for one file - models.ConfigDiffFileOut. */
export interface ConfigDiffFile {
  path: string;
  action: "create" | "replace" | "delete";
  previous_size: number | null;
  size: number | null;
  previous_sha256: string | null;
  sha256: string | null;
  kind: "text" | "binary" | "oversized" | "omitted" | "missing";
  diff: string | null;
}
/** GET .../config/pushes/{id}/diff - models.ConfigDiffResponse. */
export interface ConfigDiff {
  target: string;
  root: string;
  push_id: string;
  diff_cap: number;
  files: ConfigDiffFile[];
}
/** What restoring one file takes - models.RestoreStepOut. */
export interface RestoreStep {
  path: string;
  step: "write" | "delete" | "unchanged" | "conflict";
  now_sha256: string | null;
  restore_sha256: string | null;
  error: string | null;
}
/** models.RestoreResultOut. */
export interface RestoreResult {
  path: string;
  step: "write" | "delete";
  outcome: "succeeded" | "failed";
  error: string | null;
}
/** POST .../config/pushes/{id}/restore - models.ConfigRestoreRequest. */
export interface ConfigRestoreBody {
  confirmed: boolean;
}
/** A restore's plan (GET) or result (POST) - models.ConfigRestoreResponse. */
export interface ConfigRestore {
  target: string;
  root: string;
  source_push_id: string;
  dry_run: boolean;
  push_id: string | null;
  plan: RestoreStep[];
  results: RestoreResult[];
  pruned: PrunedPush[];
}

function pushesPath(target: string, ...rest: string[]): string {
  return [
    `/services/${encodeURIComponent(target)}/config/pushes`,
    ...rest.map(encodeURIComponent),
  ].join("/");
}

function authorised(): string | undefined {
  const token = writeToken.get();
  return token ? `Bearer ${token}` : undefined;
}

/** A push-history read: over ssh on the server, so it carries the token. */
function readHistory<T>(path: string, signal?: AbortSignal): Promise<T> {
  return request<T>(
    path,
    signal ?? new AbortController().signal,
    undefined,
    "GET",
    authorised(),
  );
}

/** A push-history write, sent once: never retried. */
function writeHistory<T>(path: string, body: unknown): Promise<T> {
  return request<T>(
    path,
    new AbortController().signal,
    JSON.stringify(body),
    "POST",
    authorised(),
  );
}

/** The calls a push makes: injectable, so a test reaches no server. */
export interface PushOps {
  open(target: string, body: ConfigPushOpenBody): Promise<ConfigPushOpened>;
  close(
    target: string,
    push: string,
    reported: ConfigPushReport[],
  ): Promise<ConfigPushDetail>;
}

export const pushOps: PushOps = {
  open: (target, body) => writeHistory(pushesPath(target), body),
  close: (target, push, reported) =>
    writeHistory(pushesPath(target, push, "close"), { reported }),
};

/** The calls the history view makes: injectable, as PushOps is. */
export interface HistoryOps {
  list(target: string, signal?: AbortSignal): Promise<ConfigHistory>;
  show(target: string, push: string): Promise<ConfigPushDetail>;
  diff(target: string, push: string, path?: string): Promise<ConfigDiff>;
  plan(target: string, push: string): Promise<ConfigRestore>;
  restore(target: string, push: string): Promise<ConfigRestore>;
}

export const historyOps: HistoryOps = {
  list: (target, signal) => readHistory(pushesPath(target), signal),
  show: (target, push) => readHistory(pushesPath(target, push)),
  diff: (target, push, path) =>
    readHistory(
      pushesPath(target, push, "diff") +
        (path ? `?${new URLSearchParams({ path })}` : ""),
    ),
  plan: (target, push) => readHistory(pushesPath(target, push, "restore")),
  restore: (target, push) =>
    writeHistory(pushesPath(target, push, "restore"), { confirmed: true }),
};

// --- activity history (#1954) ----------------------------------------------

/** models.ActivitySource and friends, held to them by test_api_types.py. */
export type ActivitySource = "browser" | "server";
export type BrowserKind =
  | "navigate"
  | "select"
  | "submit"
  | "confirm"
  | "cancel"
  | "result"
  | "interact"
  | "debug";
export type ActivityKind =
  | "navigate"
  | "select"
  | "submit"
  | "confirm"
  | "cancel"
  | "result"
  | "interact"
  | "debug"
  | "request"
  | "attempt";
/** What a tab records at, least detail first; `off` records no browser
 *  history - the server's audit of every write is kept regardless. */
export type SessionLevel = "off" | "actions" | "interactions" | "debug";
/** What an event is recorded under. */
export type ActivityLevel = "actions" | "interactions" | "debug";
export type ActivityDropReason =
  "sampled" | "queue_overflow" | "row_cap" | "retention";
export type BrowserDropReason = "sampled" | "queue_overflow";
export type ActivityExportFormat = "jsonl" | "csv" | "text";
export type ActivityOutcome =
  "pending" | "succeeded" | "failed" | "refused" | "accepted" | "unknown";
export type ActivityTrigger =
  "pointer" | "keyboard" | "programmatic" | "unknown";
/** A `detail` value. Only the keys activity.DETAIL_KEYS allows are kept. */
export type DetailValue = string | number | boolean;
export interface ActivityItem {
  target: string;
  outcome: ActivityOutcome;
}
/** POST /activity/events, one event - models.BrowserEventIn. */
export interface BrowserEvent {
  event_id: string;
  session_id: string;
  ts: string;
  kind: BrowserKind;
  action: string;
  target?: string | null;
  trigger: ActivityTrigger;
  request_id?: string | null;
  outcome?: ActivityOutcome | null;
  duration_ms?: number | null;
  items?: ActivityItem[];
  detail?: Record<string, DetailValue>;
}
/** Events the browser did not send, counted - models.BrowserDropIn. */
export interface BrowserDrop {
  level: ActivityLevel;
  reason: BrowserDropReason;
  count: number;
}
/** POST /activity/events - models.ActivityBatchRequest. */
export interface ActivityBatch {
  events?: BrowserEvent[];
  dropped?: BrowserDrop[];
}
export interface ActivityBatchResult {
  stored: number;
  not_stored: number;
  dropped_detail: number;
  persistent: boolean;
}
/** One stored event - models.ActivityEventOut. `claimed_user` is a header
 *  anyone can set: shown as claimed, never as who it was. */
export interface ActivityEvent {
  event_id: string;
  ts: string;
  recorded_at: string;
  level: ActivityLevel;
  source: ActivitySource;
  kind: ActivityKind;
  action: string;
  target: string | null;
  outcome: ActivityOutcome | null;
  session_id: string | null;
  request_id: string | null;
  claimed_user: string | null;
  trigger: ActivityTrigger | null;
  method: string | null;
  status_code: number | null;
  duration_ms: number | null;
  items: ActivityItem[];
  detail: Record<string, unknown>;
  dropped: number;
}
export interface ActivityPersistence {
  persistent: boolean;
  path: string | null;
  failures: number;
  last_error: string | null;
  last_failure_at: string | null;
}
/** How much of one level is kept - models.ActivityBound. */
export interface ActivityBound {
  level: ActivityLevel;
  retention_days: number;
  max_rows: number;
}
/** Events not in the history, by level and why - models.ActivityDrop. */
export interface ActivityDrop {
  level: ActivityLevel;
  reason: ActivityDropReason;
  count: number;
  last_at: string;
}
/** GET /activity - models.ActivityResponse. */
export interface ActivityHistory extends Pollable {
  events: ActivityEvent[];
  persistence: ActivityPersistence;
  default_level: SessionLevel;
  bounds: ActivityBound[];
  drops: ActivityDrop[];
}

export interface RemoteDeleteRequest {
  paths: string[];
  confirmed: boolean;
}
export interface RemoteDeleteResult {
  path: string;
  deleted: boolean;
  error: string | null;
}
export interface RemoteDeleteResponse {
  target: string;
  root: string;
  results: RemoteDeleteResult[];
}
