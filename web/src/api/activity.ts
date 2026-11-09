// /activity (#1954) - models/activity.py.

import type { Pollable } from "./client";

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
  subject: string;
  outcome: ActivityOutcome;
}
/** POST /activity/events, one event - models.BrowserEventIn. */
export interface BrowserEvent {
  event_id: string;
  session_id: string;
  time: string;
  kind: BrowserKind;
  action: string;
  /** what the event is about - a view, a control, a process selector */
  subject?: string | null;
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
  drops?: BrowserDrop[];
  /** The drop report's id, required with `drops`: a retry sends the same
   *  one, and the server counts its drops once (#2480). */
  batch_id?: string | null;
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
  time: string;
  recorded_at: string;
  level: ActivityLevel;
  source: ActivitySource;
  kind: ActivityKind;
  action: string;
  /** the deployment target a server row acted on, `local` for this stack */
  target: string | null;
  /** what it acted on: a process selector, a cfg key, a worker, a view */
  subject: string | null;
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
  /** `detail` keys dropped - the batch result's `dropped_detail`. */
  dropped_detail: number;
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
  max_kept: number;
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
  /** More events matched than the `limit` returned (#2220). */
  truncated: boolean;
  persistence: ActivityPersistence;
  activity_level: SessionLevel;
  bounds: ActivityBound[];
  drops: ActivityDrop[];
}
