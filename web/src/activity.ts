/**
 * What an operator does in this app, recorded for the activity history
 * (#1954), at the level of detail the tab records at (#2000).
 *
 * LEVELS, least detail first (uqs/activity_log.py has the same list):
 *
 *   off           nothing is recorded in the browser. The server still audits
 *                 every request that changes something, whatever this says.
 *   actions       what each handler that performs one records: navigating
 *                 between views, selecting a table or processes, submitting
 *                 a form, confirming or cancelling a destructive action, and
 *                 the result the browser saw.
 *   interactions  also every control used, recorded by ONE delegated listener
 *                 at the document (`installActivity`), not by any component:
 *                 a click or a keyboard press that activates a control
 *                 (`ui.click`), a download link (`ui.download`), a change to a
 *                 select, checkbox or field (`ui.change` - its name, never its
 *                 value), a section expanded or collapsed (`ui.toggle`), and a
 *                 copy (`ui.copy`). Each is named by the control's
 *                 `data-activity` attribute; Activity.test.tsx fails on an
 *                 interactive element without one, so a new control is
 *                 covered the day it is added.
 *   debug         also each request's start and finish (`request.start`,
 *                 `request.finish`), each poll and each retry of a polled view
 *                 (`resource.poll`, `resource.retry`) - rate-limited here, and
 *                 what the limit drops is counted and sent, not lost.
 *
 * The server's default comes with /health (`activity_level`); the operator
 * can pick another for this tab in the Activity view, kept in sessionStorage.
 *
 * Nothing typed is recorded at any level: a subject names a view, a table, a
 * process selector, a worker or a control, and `detail` carries only keys the
 * server allows (activity.DETAIL_KEYS) - a filter or config VALUE has no key
 * to go under, a field's change records that it changed, and the write token
 * is never passed in. The server drops anything else.
 *
 * These are the browser's account of intent and of what it saw come back.
 * The authority on what happened is the server's own audit row for the same
 * `request_id`, written whether or not this app sent the request.
 */
import type {
  ActivityItem,
  ActivityLevel,
  ActivityOutcome,
  ActivityTrigger,
  BrowserDrop,
  BrowserDropReason,
  BrowserEvent,
  BrowserKind,
  DetailValue,
  SessionLevel,
} from "./api/activity";
import { ApiError, observeRequests, type RequestNote } from "./api/client";

/** Every level a tab can record at, least detail first. */
export const LEVELS: readonly SessionLevel[] = [
  "off",
  "actions",
  "interactions",
  "debug",
];
/** The level each kind is recorded under - the server derives the same
 *  (activity.LEVEL_OF_KIND), so a stored row's level is never this word. */
export const KIND_LEVEL: Record<BrowserKind, ActivityLevel> = {
  navigate: "actions",
  select: "actions",
  submit: "actions",
  confirm: "actions",
  cancel: "actions",
  result: "actions",
  interact: "interactions",
  debug: "debug",
};

/** Events sent per request; the server's ActivityBatchRequest allows 100. */
const BATCH = 100;
/** Events held while the API is unreachable; the oldest go first, counted. */
const MAX_QUEUE = 1000;
/** debug's rate limit: a burst of this many, refilled at DEBUG_PER_SECOND. */
const DEBUG_BURST = 30;
const DEBUG_PER_SECOND = 5;

export type Transport = (
  events: BrowserEvent[],
  lost: BrowserDrop[],
  batchId: string | null,
) => Promise<void>;

/** POST the batch. A 422 is not retried: the same events would be refused again. */
const httpTransport: Transport = async (events, lost, batchId) => {
  const response = await fetch("/activity/events", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    // `drops`, as GET /activity names the same records (#2242).
    body: JSON.stringify({ events, drops: lost, batch_id: batchId }),
    // Lets the last batch out as the tab closes.
    keepalive: true,
  });
  if (!response.ok && response.status !== 422)
    throw new Error(`activity not recorded (${response.status})`);
};

let transport: Transport = httpTransport;
let flushDelayMs = 1000;
let queue: BrowserEvent[] = [];
let timer: ReturnType<typeof setTimeout> | undefined;
let lastInput: ActivityTrigger = "unknown";
let serverLevel: SessionLevel = "actions";
let heldLevel: SessionLevel | null = null;
/** Events not sent, by `level:reason`, waiting to be reported. */
let drops = new Map<string, number>();
/** A drop report sent and not yet acknowledged. It is sent again as it is,
 *  under its own id, until one send succeeds: the server counts an id once,
 *  so a report whose answer was lost is not counted twice (#2480). */
let unacknowledged: { id: string; drops: BrowserDrop[] } | null = null;
let tokens = DEBUG_BURST;
let refilled = 0;
/** False when the server's access policy does not let this caller record
 *  its history (activity:write, #2045): nothing is queued or sent. */
let recordingAllowed = true;
const listeners = new Set<() => void>();

/** Swap the transport, the batching delay or the server's default level -
 *  for tests, which have no API. Clears the queue, the counts, the rate
 *  limit and this tab's chosen level. */
export function configureActivity(options: {
  transport?: Transport | "http";
  flushDelayMs?: number;
  level?: SessionLevel;
}): void {
  if (options.transport)
    transport =
      options.transport === "http" ? httpTransport : options.transport;
  if (options.flushDelayMs !== undefined) flushDelayMs = options.flushDelayMs;
  serverLevel = options.level ?? "actions";
  setSessionLevel(null);
  queue = [];
  drops = new Map();
  unacknowledged = null;
  tokens = DEBUG_BURST;
  refilled = 0;
  recordingAllowed = true;
  clearTimeout(timer);
  timer = undefined;
}

/** Stop or resume recording, as /me/capabilities says (capabilities.tsx).
 *  Stopping drops what is queued: it would only be refused. */
export function allowRecording(allowed: boolean): void {
  recordingAllowed = allowed;
  if (!allowed) {
    queue = [];
    drops = new Map();
    unacknowledged = null;
    clearTimeout(timer);
    timer = undefined;
  }
}

export function newId(): string {
  return crypto.randomUUID().replaceAll("-", "");
}

/** One id per tab, kept for its life: what "this session" means in the history. */
const SESSION_KEY = "uqf.activitySession";
let heldSession = "";
export function sessionId(): string {
  try {
    const stored = sessionStorage.getItem(SESSION_KEY);
    if (stored) return stored;
    const fresh = heldSession || newId();
    sessionStorage.setItem(SESSION_KEY, fresh);
    heldSession = fresh;
    return fresh;
  } catch {
    heldSession ||= newId();
    return heldSession;
  }
}

// --- levels -----------------------------------------------------------------

const LEVEL_KEY = "uqf.activityLevel";
function isLevel(value: unknown): value is SessionLevel {
  return LEVELS.includes(value as SessionLevel);
}

/** Adopt the server's default, as /health serves it; anything else is ignored. */
export function adoptDefaultLevel(level: unknown): void {
  if (isLevel(level) && level !== serverLevel) {
    serverLevel = level;
    listeners.forEach((listener) => listener());
  }
}

export function defaultLevel(): SessionLevel {
  return serverLevel;
}

/** The level this tab's operator chose, or null to follow the server. */
export function sessionLevel(): SessionLevel | null {
  try {
    const stored = sessionStorage.getItem(LEVEL_KEY);
    if (isLevel(stored)) return stored;
  } catch {
    /* storage blocked: heldLevel carries it */
  }
  return heldLevel;
}

export function setSessionLevel(level: SessionLevel | null): void {
  heldLevel = level;
  try {
    if (level) sessionStorage.setItem(LEVEL_KEY, level);
    else sessionStorage.removeItem(LEVEL_KEY);
  } catch {
    /* storage blocked: heldLevel carries it */
  }
  listeners.forEach((listener) => listener());
}

/** What this tab records at now. */
export function effectiveLevel(): SessionLevel {
  return sessionLevel() ?? serverLevel;
}

/** Whether an event of `level` is recorded at the tab's level. */
export function recordsAt(level: ActivityLevel): boolean {
  return LEVELS.indexOf(level) <= LEVELS.indexOf(effectiveLevel());
}

/** Be told when the level changes - for the view that shows it. */
export function onLevelChange(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

// --- recording --------------------------------------------------------------

/** How an action was triggered. A click with `detail` 0 was not made with
 *  a pointer - Enter or Space on a focused button - so the last input
 *  decides; a form submit has no detail, and the last input decides too. */
export function triggerOf(event?: { detail?: number }): ActivityTrigger {
  if (event && typeof event.detail === "number" && event.detail > 0)
    return "pointer";
  return lastInput;
}

function countDrop(level: ActivityLevel, reason: BrowserDropReason, n = 1) {
  const key = `${level}:${reason}`;
  drops.set(key, (drops.get(key) ?? 0) + n);
}

/** What has not been sent and not yet been reported - for tests and the view. */
export function pendingDrops(): BrowserDrop[] {
  const all = new Map<string, number>();
  for (const d of unacknowledged?.drops ?? []) {
    const key = `${d.level}:${d.reason}`;
    all.set(key, (all.get(key) ?? 0) + d.count);
  }
  for (const [key, n] of drops) all.set(key, (all.get(key) ?? 0) + n);
  return [...all.entries()].map(([key, count]) => {
    const [level, reason] = key.split(":");
    return {
      level: level as ActivityLevel,
      reason: reason as BrowserDropReason,
      count,
    };
  });
}

/** The drop report the next send carries: the one not yet acknowledged, as
 *  it was, or a new one of what has been counted since - or none. */
function nextReport(): { id: string; drops: BrowserDrop[] } | null {
  if (unacknowledged) return unacknowledged;
  if (!drops.size) return null;
  const counted = [...drops.entries()].map(([key, count]) => {
    const [level, reason] = key.split(":");
    return {
      level: level as ActivityLevel,
      reason: reason as BrowserDropReason,
      count,
    };
  });
  drops = new Map();
  return { id: newId(), drops: counted };
}

/** debug's token bucket: whether one more event may go now. */
function debugAllowed(): boolean {
  const now = performance.now();
  tokens = Math.min(
    DEBUG_BURST,
    tokens + ((now - refilled) / 1000) * DEBUG_PER_SECOND,
  );
  refilled = now;
  if (tokens < 1) return false;
  tokens -= 1;
  return true;
}

function schedule() {
  if (timer === undefined)
    timer = setTimeout(() => {
      timer = undefined;
      void flushActivity();
    }, flushDelayMs);
}

export interface RecordOptions {
  /** what the event is about - never a deployment target (#2234) */
  subject?: string;
  trigger?: ActivityTrigger;
  requestId?: string;
  outcome?: ActivityOutcome;
  durationMs?: number;
  items?: ActivityItem[];
  detail?: Record<string, DetailValue | undefined>;
}

/** Queue one event and schedule a send - or nothing, when the tab's level
 *  does not record its kind. Returns the event, or null when not recorded. */
export function record(
  kind: BrowserKind,
  action: string,
  options: RecordOptions = {},
): BrowserEvent | null {
  const level = KIND_LEVEL[kind];
  if (!recordingAllowed || !recordsAt(level)) return null;
  if (level === "debug" && !debugAllowed()) {
    countDrop(level, "sampled");
    schedule();
    return null;
  }
  const detail: Record<string, DetailValue> = {};
  for (const [key, value] of Object.entries(options.detail ?? {}))
    if (value !== undefined && value !== "") detail[key] = value;
  const event: BrowserEvent = {
    event_id: newId(),
    session_id: sessionId(),
    time: new Date().toISOString(),
    kind,
    action,
    subject: options.subject || null,
    trigger: options.trigger ?? lastInput,
    request_id: options.requestId ?? null,
    outcome: options.outcome ?? null,
    duration_ms:
      options.durationMs === undefined
        ? null
        : Math.max(0, Math.round(options.durationMs * 1000) / 1000),
    items: options.items ?? [],
    detail,
  };
  queue.push(event);
  if (queue.length > MAX_QUEUE) {
    for (const lost of queue.slice(0, queue.length - MAX_QUEUE))
      countDrop(KIND_LEVEL[lost.kind], "queue_overflow");
    queue = queue.slice(-MAX_QUEUE);
  }
  schedule();
  return event;
}

/** Send what is queued, and the count of what was not. A failed send keeps
 *  both for the next one - the server ignores an event id it already has,
 *  and a drop report's batch id (#2480), so a retry is harmless. */
export async function flushActivity(): Promise<void> {
  while (queue.length || drops.size || unacknowledged) {
    const batch = queue.slice(0, BATCH);
    const report = nextReport();
    unacknowledged = report;
    queue = queue.slice(batch.length);
    try {
      await transport(batch, report?.drops ?? [], report?.id ?? null);
      unacknowledged = null;
    } catch {
      // record() may have refilled the queue while the send was in flight:
      // what no longer fits is counted, as record() counts it (#2408).
      const merged = [...batch, ...queue];
      for (const lost of merged.slice(
        0,
        Math.max(0, merged.length - MAX_QUEUE),
      ))
        countDrop(KIND_LEVEL[lost.kind], "queue_overflow");
      queue = merged.slice(-MAX_QUEUE);
      return;
    }
  }
}

/** What is waiting to be sent - for tests. */
export function queuedActivity(): readonly BrowserEvent[] {
  return queue;
}

// --- the shared instrumentation ---------------------------------------------

/** What counts as a control: what a person clicks, types into or toggles.
 *  A scroll region with tabIndex is focusable, but it is not a control. */
export const INTERACTIVE =
  "button, a[href], input, select, textarea, summary, " +
  "[role='button'], [role='tab'], [role='checkbox'], [role='switch'], " +
  "[role='menuitem'], [role='option']";

/** The name a control is recorded under: its own `data-activity`, else the
 *  nearest named ancestor's, else `unnamed` - which the coverage test
 *  refuses, so it is never what a shipped control is called. */
export function activityName(element: Element | null): string {
  return (
    element?.closest("[data-activity]")?.getAttribute("data-activity") ??
    "unnamed"
  );
}

/** What kind of control an element is, in one word. */
function controlOf(element: Element): string {
  const tag = element.tagName.toLowerCase();
  if (tag === "input")
    return (element as HTMLInputElement).type.toLowerCase() || "text";
  return element.getAttribute("role") ?? tag;
}

/** A request's path as a route to record: its first two segments, each kept
 *  only when it is a plain lower-case word - so a name in the path, which
 *  might have been typed, is never recorded - and the query string never. */
export function routeOf(path: string): string {
  const bare = path.split(/[?#]/)[0];
  const parts = bare
    .split("/")
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => (/^[a-z_]+$/.test(part) ? part : "*"));
  return `/${parts.join("/")}`;
}

/** A request the app made, at the debug level. */
function noteRequest(note: RequestNote) {
  if (note.path.startsWith("/activity/events")) return;
  const finished = note.phase === "finish";
  record("debug", `request.${note.phase}`, {
    subject: `${note.method} ${routeOf(note.path)}`,
    trigger: "programmatic",
    requestId: note.requestId,
    durationMs: finished ? note.durationMs : undefined,
    outcome: !finished
      ? undefined
      : note.status === undefined
        ? "unknown"
        : note.status < 400
          ? "succeeded"
          : note.status < 500
            ? "refused"
            : "failed",
    detail: { status: note.status },
  });
}

/** A polled view asking again: on its cadence, or after a transient failure. */
export function noteResource(
  why: "poll" | "retry",
  path: string,
  attempt?: number,
): void {
  record("debug", `resource.${why}`, {
    subject: `GET ${routeOf(path)}`,
    trigger: "programmatic",
    detail: { attempt },
  });
}

/** Track the last kind of input, record every control used at the
 *  `interactions` level and every request at `debug`, and send what is
 *  queued when the page goes away. Installed once, at the app's root.
 *  Returns the uninstaller. */
export function installActivity(target: Document = document): () => void {
  const onKey = (event: KeyboardEvent) => {
    if (!["Shift", "Control", "Alt", "Meta"].includes(event.key))
      lastInput = "keyboard";
  };
  const onPointer = () => {
    lastInput = "pointer";
  };
  const onHide = () => {
    if (target.visibilityState === "hidden") void flushActivity();
  };
  // A click on a control - or Enter or Space on one, which the browser turns
  // into a click with detail 0. Fields and toggles are recorded by their
  // change or toggle instead, so one use is one event.
  const onClick = (event: MouseEvent) => {
    if (!recordsAt("interactions")) return;
    const element = (event.target as Element | null)?.closest?.(INTERACTIVE);
    if (!element) return;
    const tag = element.tagName.toLowerCase();
    if (tag === "input" || tag === "select" || tag === "textarea") return;
    if (tag === "summary") return;
    const download = tag === "a" && element.hasAttribute("download");
    record("interact", download ? "ui.download" : "ui.click", {
      subject: activityName(element),
      trigger: triggerOf(event),
      detail: { control: controlOf(element) },
    });
  };
  // That a field, a select or a box changed - never to what.
  const onChange = (event: Event) => {
    if (!recordsAt("interactions")) return;
    const element = event.target as Element | null;
    if (!element?.matches?.("input, select, textarea")) return;
    const control = controlOf(element);
    const input = element as HTMLInputElement;
    record("interact", "ui.change", {
      subject: activityName(element),
      detail: {
        control,
        checked:
          control === "checkbox" || control === "radio"
            ? input.checked
            : undefined,
        file_count: control === "file" ? (input.files?.length ?? 0) : undefined,
      },
    });
  };
  // A section expanded or collapsed: named by its summary.
  const onToggle = (event: Event) => {
    if (!recordsAt("interactions")) return;
    const details = event.target as HTMLDetailsElement | null;
    if (details?.tagName?.toLowerCase() !== "details") return;
    const summary = details.querySelector(":scope > summary");
    record("interact", "ui.toggle", {
      subject: activityName(summary ?? details),
      detail: { open: details.open },
    });
  };
  // That something was copied, and from which control - never what.
  const onCopy = (event: ClipboardEvent) => {
    if (!recordsAt("interactions")) return;
    const element = event.target as Element | null;
    const named = element?.closest?.("[data-activity]");
    record("interact", "ui.copy", {
      subject: named ? activityName(named) : "page",
    });
  };
  const unobserve = observeRequests(noteRequest);
  target.addEventListener("keydown", onKey, true);
  target.addEventListener("pointerdown", onPointer, true);
  target.addEventListener("mousedown", onPointer, true);
  target.addEventListener("click", onClick, true);
  target.addEventListener("change", onChange, true);
  target.addEventListener("toggle", onToggle, true);
  target.addEventListener("copy", onCopy, true);
  target.addEventListener("visibilitychange", onHide);
  return () => {
    unobserve();
    target.removeEventListener("keydown", onKey, true);
    target.removeEventListener("pointerdown", onPointer, true);
    target.removeEventListener("mousedown", onPointer, true);
    target.removeEventListener("click", onClick, true);
    target.removeEventListener("change", onChange, true);
    target.removeEventListener("toggle", onToggle, true);
    target.removeEventListener("copy", onCopy, true);
    target.removeEventListener("visibilitychange", onHide);
  };
}

// --- tracked writes ---------------------------------------------------------

/** The outcome a failed request supports: a 4xx was refused before anything
 *  happened, as the server's audit records it; no answer at all, or a 5xx,
 *  leaves it unknown - the request may have done its work. */
export function outcomeOfError(error: unknown): ActivityOutcome {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500)
    return "refused";
  return "unknown";
}

/** Run one write as a tracked operation: the event that started it, the
 *  request carrying the same id, and the result the browser saw. The id is
 *  sent whatever the level - the server's audit row carries it even when the
 *  tab records nothing.
 *
 *  `settle` reads the response for an outcome the status cannot carry - a
 *  torq.sh exit code, an override shadowed by the environment. */
export async function tracked<T>(
  kind: "submit" | "confirm",
  action: string,
  options: Omit<RecordOptions, "requestId" | "outcome" | "durationMs">,
  send: (correlation: { requestId: string; sessionId: string }) => Promise<T>,
  settle: (result: T) => ActivityOutcome = () => "succeeded",
): Promise<T> {
  const requestId = newId();
  const started = performance.now();
  record(kind, action, { ...options, requestId });
  const result = (outcome: ActivityOutcome, status?: number) =>
    record("result", action, {
      subject: options.subject,
      trigger: "programmatic",
      requestId,
      outcome,
      durationMs: performance.now() - started,
      detail: { status },
    });
  try {
    const value = await send({ requestId, sessionId: sessionId() });
    result(settle(value));
    return value;
  } catch (error) {
    result(
      outcomeOfError(error),
      error instanceof ApiError && error.status ? error.status : undefined,
    );
    throw error;
  }
}
