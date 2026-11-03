/**
 * What an operator does in this app, recorded for the activity history (#1954).
 *
 * Only meaningful actions are recorded, each from the one handler that
 * performs it: navigating between views, selecting a table or processes,
 * submitting a form, confirming or cancelling a destructive action, and the
 * result the browser saw. Polling is never recorded - `useResource` does not
 * call in here - so a dashboard left open writes nothing.
 *
 * Nothing typed is recorded: a target names a view, a table, a process
 * selector or a worker, and `detail` carries only keys the server allows
 * (activity.DETAIL_KEYS) - a filter or config VALUE has no key to go under,
 * and the write token is never passed in. The server drops anything else.
 *
 * These are the browser's account of intent and of what it saw come back.
 * The authority on what happened is the server's own audit row for the same
 * `request_id`, written whether or not this app sent the request.
 */
import {
  ApiError,
  type ActivityItem,
  type ActivityOutcome,
  type ActivityTrigger,
  type BrowserEvent,
  type BrowserKind,
  type DetailValue,
} from "./api";

/** Events sent per request; the server's ActivityBatchRequest allows 100. */
const BATCH = 100;
/** Events held while the API is unreachable; the oldest go first. */
const MAX_QUEUE = 500;

export type Transport = (events: BrowserEvent[]) => Promise<void>;

/** POST the batch. A 422 is not retried: the same events would be refused again. */
const httpTransport: Transport = async (events) => {
  const response = await fetch("/activity/events", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ events }),
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

/** Swap the transport or the batching delay - for tests, which have no API. */
export function configureActivity(options: {
  transport?: Transport | "http";
  flushDelayMs?: number;
}): void {
  if (options.transport)
    transport =
      options.transport === "http" ? httpTransport : options.transport;
  if (options.flushDelayMs !== undefined) flushDelayMs = options.flushDelayMs;
  queue = [];
  clearTimeout(timer);
  timer = undefined;
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

/** How an action was triggered. A click with `detail` 0 was not made with
 *  a pointer - Enter or Space on a focused button - so the last input
 *  decides; a form submit has no detail, and the last input decides too. */
export function triggerOf(event?: { detail?: number }): ActivityTrigger {
  if (event && typeof event.detail === "number" && event.detail > 0)
    return "pointer";
  return lastInput;
}

/** Track the last kind of input, and send what is queued when the page goes
 *  away. Returns the uninstaller. */
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
  target.addEventListener("keydown", onKey, true);
  target.addEventListener("pointerdown", onPointer, true);
  target.addEventListener("mousedown", onPointer, true);
  target.addEventListener("visibilitychange", onHide);
  return () => {
    target.removeEventListener("keydown", onKey, true);
    target.removeEventListener("pointerdown", onPointer, true);
    target.removeEventListener("mousedown", onPointer, true);
    target.removeEventListener("visibilitychange", onHide);
  };
}

export interface RecordOptions {
  target?: string;
  trigger?: ActivityTrigger;
  requestId?: string;
  outcome?: ActivityOutcome;
  durationMs?: number;
  items?: ActivityItem[];
  detail?: Record<string, DetailValue | undefined>;
}

/** Queue one event and schedule a send. */
export function record(
  kind: BrowserKind,
  action: string,
  options: RecordOptions = {},
): BrowserEvent {
  const detail: Record<string, DetailValue> = {};
  for (const [key, value] of Object.entries(options.detail ?? {}))
    if (value !== undefined && value !== "") detail[key] = value;
  const event: BrowserEvent = {
    event_id: newId(),
    session_id: sessionId(),
    ts: new Date().toISOString(),
    kind,
    action,
    target: options.target || null,
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
  if (queue.length > MAX_QUEUE) queue = queue.slice(-MAX_QUEUE);
  if (timer === undefined)
    timer = setTimeout(() => {
      timer = undefined;
      void flushActivity();
    }, flushDelayMs);
  return event;
}

/** Send what is queued. A failed send keeps the events for the next one -
 *  the server ignores an event id it already has, so a retry is harmless. */
export async function flushActivity(): Promise<void> {
  while (queue.length) {
    const batch = queue.slice(0, BATCH);
    queue = queue.slice(batch.length);
    try {
      await transport(batch);
    } catch {
      queue = [...batch, ...queue].slice(-MAX_QUEUE);
      return;
    }
  }
}

/** What is waiting to be sent - for tests. */
export function queuedActivity(): readonly BrowserEvent[] {
  return queue;
}

/** The outcome a failed request supports: a 4xx was refused before anything
 *  happened, as the server's audit records it; no answer at all, or a 5xx,
 *  leaves it unknown - the request may have done its work. */
export function outcomeOfError(error: unknown): ActivityOutcome {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500)
    return "refused";
  return "unknown";
}

/** Run one write as a tracked operation: the event that started it, the
 *  request carrying the same id, and the result the browser saw.
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
      target: options.target,
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
