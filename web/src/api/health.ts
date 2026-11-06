// /health - models/health.py.

import type { SessionLevel } from "./activity";
import type { Pollable } from "./client";

export interface Health extends Pollable {
  /** seconds the browser waits for any request - the server's UQF_FRONTEND_TIMEOUT_SECONDS plus headroom */
  request_timeout_seconds: number;
  ok: boolean;
  gateway: "up" | "reloading" | "unreachable" | "wrong_process";
  detail: string | null;
  /** the level a tab records its activity at unless its operator picks another (#2000) */
  activity_level: SessionLevel;
}
