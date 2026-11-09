// /services, /workspaces and a target's tunnels and files - models/services.py.

import { ApiError, type Pollable, errorBody, writeToken } from "./client";
import type { FileOutcome } from "./config_push";

/** One deployment target - models.ServiceTargetOut, as `uqs target list
 * --json` lists it. Read from deploy_targets.toml: the server is never asked. */
export interface ServiceTarget {
  target: string;
  ssh: string;
  dest: string;
  remote_user: string | null;
  environment: string | null;
  labels: Record<string, string>;
  /** in inspection mode: the API and uqs refuse every write to it (#2032) */
  inspection: boolean;
  last_deployed: { release: string; at: string } | null;
}
/** GET /services - models.ServicesResponse. `invalid` is the registry's own
 * refusal, naming the line, when the file exists but cannot be read. */
export interface Services extends Pollable {
  targets: ServiceTarget[];
  source: string;
  declared: boolean;
  invalid: string | null;
  writes_enabled: boolean;
}
/** One workspace - models.WorkspaceOut: `local`, or a target the uqs
 * registry declares (#2029). The browser keeps no list of its own. */
export interface Workspace {
  target: string;
  kind: "local" | "target";
  connection: "direct" | "ssh tunnel";
  access: "read-only" | "read-write";
  environment: string | null;
  ssh: string | null;
  available: boolean;
  reason: string | null;
}
/** GET /workspaces - models.WorkspacesResponse. */
export interface Workspaces {
  workspaces: Workspace[];
  header: string;
  source: string;
  invalid: string | null;
}
/** One of a target's processes as the server reaches it -
 * models.WorkspaceEndpointOut. */
export interface WorkspaceEndpoint {
  process: string;
  proctype: string;
  remote_port: number;
  local_endpoint: string;
}
/** GET /workspace - models.WorkspaceResponse: the workspace this request
 * named, reached now. */
export interface WorkspaceStatus extends Pollable {
  target: string;
  connection: "direct" | "ssh tunnel";
  access: "read-only" | "read-write";
  ssh: string | null;
  endpoints: WorkspaceEndpoint[];
}
/** One entry of a remote directory - models.RemoteEntryOut. A symlink,
 * special file or undecodable name is `blocked`: listed, never entered. */
export interface RemoteEntry {
  name: string;
  kind: "dir" | "file" | "symlink" | "special" | "unknown";
  size: number | null;
  /** Last modified: ISO-8601, UTC to the nanosecond. */
  mtime: string | null;
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
  dest: string;
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
  reported_process: string | null;
  remote_port: number | null;
  local_port: number | null;
  release: string | null;
}
/** One forward of a tunnel plan - models.TunnelMappingOut. Every endpoint
 * here is uqs's; the browser never builds one from a port. */
export interface TunnelMapping {
  process: string;
  proctype: string;
  remote_host: string;
  remote_port: number;
  local_host: string;
  local_port: number;
  production_endpoint: string;
  local_endpoint: string;
  query_command: string;
  state: "verified" | "stale" | "unverified";
  last_success: TunnelCheckRecord | null;
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
  proctype: string;
  production_endpoint: string;
  local_endpoint: string;
  remote_port: number;
  local_port: number;
  /** null when it passed, else a key of `categories` - the check's own `category`. */
  category: string | null;
  error: string | null;
  layers: Record<string, string>;
  connected: boolean;
  reported_process: string | null;
  gateway_servers: string[] | null;
  last_success: TunnelCheckRecord | null;
}
/** GET /services/{target}/tunnel/check - models.TunnelCheckResponse. */
export interface TunnelCheck {
  ok: boolean;
  target: string;
  ssh: string;
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

/** PUT /services/{target}/files - models.RemoteUploadResponse: one
 * file written below the target's shared/config, published atomically. */
export interface RemoteUpload {
  target: string;
  dest: string;
  path: string;
  size: number;
  sha256: string;
  replaced: boolean;
  mode: number;
  created_dirs: string[];
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
/** The config push history's directory below UPLOAD_ROOT: never uploaded to. */
export const UPLOAD_HISTORY_DIR = ".uqf-history";
/** Longer than the server's own UPLOAD_TIMEOUT, so the server's answer -
 * including "uncertain" - arrives before the browser gives up. */
export const UPLOAD_TIMEOUT_MS = 150 * 1000;
/** What one queue may hold - the browser's own bound, on top of the
 * per-file limit every side enforces. */
export const UPLOAD_MAX_FILES = 500;
export const UPLOAD_MAX_TOTAL_BYTES = 256 * 1024 * 1024;

/** The URL that writes `path` (relative to the deployment root) as one
 * file of push `pushId`. */
export function remoteUploadPath(
  target: string,
  path: string,
  overwrite: boolean,
  pushId: string,
): string {
  const query = new URLSearchParams({
    path,
    overwrite: String(overwrite),
    push_id: pushId,
  });
  return `/services/${encodeURIComponent(target)}/files?${query}`;
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
  // in any letter case, as the server folds it: a case-insensitive
  // filesystem would open it all the same (#2586)
  const history = [...root, UPLOAD_HISTORY_DIR];
  if (history.every((part, index) => parts[index]?.toLowerCase() === part))
    return `${history.join("/")} is the config push history - read it with the History view`;
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
  pushId: string,
): Promise<RemoteUpload> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", remoteUploadPath(target, path, overwrite, pushId));
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
      let payload: unknown = null;
      try {
        payload = JSON.parse(xhr.responseText);
      } catch {
        payload = null;
      }
      if (xhr.status >= 200 && xhr.status < 300 && payload) {
        onProgress(1);
        resolve(payload as RemoteUpload);
        return;
      }
      // The same reading as every other request: a validation refusal's
      // reason reaches the operator too, not "Upload failed (422)." (#2106).
      const failure = errorBody(payload, xhr.status);
      // Only the server's word, or an answer it never gave, is uncertain; a
      // refusal (4xx) and a failure it names as certain wrote nothing.
      const unsure =
        failure.error === "UploadOutcomeUncertain" ||
        payload === null ||
        xhr.status === 504;
      reject(new UploadFailed(failure.detail, xhr.status, unsure));
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

export interface RemoteDeleteRequest {
  paths: string[];
  confirmed: boolean;
}
export interface RemoteDeleteResult {
  path: string;
  outcome: FileOutcome;
  error: string | null;
}
export interface RemoteDeleteResponse {
  target: string;
  dest: string;
  results: RemoteDeleteResult[];
}
