// /control/* (writes) - models/control.py.

import type { Pollable } from "./client";

export interface ControlProcess {
  process: string;
  proctype: string;
  start_with_all: boolean;
}
export interface ControlStatus extends Pollable {
  writes_enabled: boolean;
  lifecycle_actions: string[];
  settable_fields: string[];
  processes: ControlProcess[];
}
/** POST /control/lifecycle/{action} - models.LifecycleRequest. `procs` is
 * required on both sides: it once defaulted to "all" on the server, so a body
 * without it stopped the whole fleet (#1361). */
export interface LifecycleBody {
  procs: string;
  /** required true for stop and restart, which the UI confirms (#2244) */
  confirmed?: boolean;
}
/** PUT /control/processes/{process}/config - models.ProcessConfigRequest. */
export interface ProcessConfigBody {
  field: string;
  value: string;
}
/** PUT /control/worker/config - models.WorkerConfigRequest. */
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
  /** torq.sh's selector, as sent - not a deployment target (#2234) */
  procs: string;
  exit_code: number;
  ok: boolean;
  output: string;
}
export interface ProcessConfigResult {
  process: string;
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
  process: string;
  status_path: string;
}
