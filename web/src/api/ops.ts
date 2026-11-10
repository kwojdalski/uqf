// /ops/* - models/ops.py.

import type { Pollable, Row } from "./client";

/** A worker's state as status.q writes it - models.RunState, held to it by
 * test_api_types.py, and RunState to q by test_status.py (#820). */
export type RunState =
  | "starting"
  | "running"
  | "idle"
  | "completed"
  | "failed"
  | "stopped";
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
  process: string;
  proctype: string;
  host: string;
  declared_port: number | null;
  start_with_all: boolean;
  up: boolean;
  pid: number | null;
  reported_port: number | null;
  reported_process: string | null;
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
  /** /ops/usage: more rows existed than the `limit` returned (#2220). */
  truncated?: boolean;
}
