// /services/{target}/config/pushes (#2002) - models/config_push.py.

import { authorised, request } from "./client";

/** What happened to one file - models.FileOutcome, the one vocabulary every
 * per-file result uses (#2108): a delete, a restore step, a reported file. */
export type FileOutcome =
  "succeeded" | "failed" | "refused" | "unknown" | "cancelled" | "invalid";

/** POST /services/{target}/config/pushes - models.ConfigPushOpenRequest. */
export interface ConfigPushOpenBody {
  /** the operator confirmed the push: required true (#2244) */
  confirmed: boolean;
  config_dir: string;
  file_count: number;
  total_bytes: number;
}
/** A push whose history retention removed - models.PrunedPushOut. */
export interface PrunedPush {
  push_id: string;
  why: "row_cap" | "retention";
  pruned_at: string;
}
/** models.ConfigPushOpenResponse: the push's id, which every upload names. */
export interface ConfigPushOpened {
  target: string;
  dest: string;
  push_id: string;
  pruned: PrunedPush[];
  max_kept: number;
  retention_days: number;
}
/** What the browser saw of a file the server may never have recorded -
 * models.ConfigPushReported. */
export interface ConfigPushReport {
  path: string;
  outcome: FileOutcome;
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
  config_dir?: string | null;
  declared?: Record<string, number> | null;
  file_count?: number | null;
  total_bytes?: number | null;
  states?: Record<string, number> | null;
  reported_count?: number | null;
  error?: string | null;
}
/** GET /services/{target}/config/pushes - models.ConfigHistoryResponse. */
export interface ConfigHistory {
  target: string;
  dest: string;
  pushes: ConfigPushSummary[];
  total: number;
  truncated: boolean;
  pruned: PrunedPush[];
}
/** models.ConfigPushReportedOut. */
export interface ConfigPushReported {
  path: string;
  outcome: FileOutcome;
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
  config_dir: string;
  declared: Record<string, number>;
  reported: ConfigPushReported[];
  files: ConfigPushFile[];
}
/** GET .../config/pushes/{id}, and a push closed - models.ConfigPushResponse. */
export interface ConfigPushDetail {
  target: string;
  dest: string;
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
  dest: string;
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
  outcome: FileOutcome;
  error: string | null;
}
/** POST .../config/pushes/{id}/restore - models.ConfigRestoreRequest. */
export interface ConfigRestoreBody {
  confirmed: boolean;
}
/** A restore's plan (GET) or result (POST) - models.ConfigRestoreResponse. */
export interface ConfigRestore {
  target: string;
  dest: string;
  restores: string;
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
    pushId: string,
    reported: ConfigPushReport[],
  ): Promise<ConfigPushDetail>;
}

export const pushOps: PushOps = {
  open: (target, body) => writeHistory(pushesPath(target), body),
  close: (target, pushId, reported) =>
    writeHistory(pushesPath(target, pushId, "close"), { reported }),
};

/** The calls the history view makes: injectable, as PushOps is. */
export interface HistoryOps {
  list(target: string, signal?: AbortSignal): Promise<ConfigHistory>;
  show(target: string, pushId: string): Promise<ConfigPushDetail>;
  diff(target: string, pushId: string, path?: string): Promise<ConfigDiff>;
  plan(target: string, pushId: string): Promise<ConfigRestore>;
  restore(target: string, pushId: string): Promise<ConfigRestore>;
}

export const historyOps: HistoryOps = {
  list: (target, signal) => readHistory(pushesPath(target), signal),
  show: (target, pushId) => readHistory(pushesPath(target, pushId)),
  diff: (target, pushId, path) =>
    readHistory(
      pushesPath(target, pushId, "diff") +
        (path ? `?${new URLSearchParams({ path })}` : ""),
    ),
  plan: (target, pushId) => readHistory(pushesPath(target, pushId, "restore")),
  restore: (target, pushId) =>
    writeHistory(pushesPath(target, pushId, "restore"), { confirmed: true }),
};
