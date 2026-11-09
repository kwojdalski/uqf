// The one request path every API call takes: errors, timeouts, the workspace
// header, the write token and one-shot writes.

import {
  LOCAL,
  TARGET_HEADER,
  currentWorkspace,
  workspaceSignal,
} from "../workspace";

export type Row = Record<string, unknown>;
export interface Pollable {
  poll_seconds: number;
}

/** Every error the API answers, in its one shape (app.py `_error`, #2106):
 *  its type, a readable reason, and whether retrying may help. A 422 from
 *  request validation also carries `errors`, each failure's `loc` and `msg`. */
export interface ErrorBody {
  error: string;
  detail: string;
  transient: boolean;
  errors?: { loc: (string | number)[]; msg: string; type?: string }[];
}

/** Read an error response's body. Anything not in the shape - an answer a
 *  proxy gave, or none - falls back to naming the status, never to "". */
export function errorBody(payload: unknown, status: number): ErrorBody {
  const body = (payload ?? {}) as Partial<ErrorBody>;
  return {
    error: typeof body.error === "string" ? body.error : "",
    detail:
      typeof body.detail === "string" && body.detail
        ? body.detail
        : `Request failed (${status}).`,
    transient: body.transient === true,
    errors: Array.isArray(body.errors) ? body.errors : undefined,
  };
}

export class ApiError extends Error {
  /** `error` is the API's error type (ErrorBody.error, e.g.
   *  CoverageIncomplete): several unrelated refusals share a status, so a
   *  heading is chosen by this, never by the status alone (#2481). */
  constructor(
    message: string,
    public transient = false,
    public status = 0,
    public error = "",
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
  // deleting remote files DELETE (#2248), and inferring that from anything
  // would be guessing.
  method?: "GET" | "POST" | "PUT" | "DELETE",
  authorization?: string,
  extra?: Record<string, string>,
): Promise<T> {
  let response: Response;
  // The workspace this request is for, fixed now: a switch while it is in
  // flight aborts it rather than letting its answer land under the new one.
  const target = currentWorkspace();
  const switched = workspaceSignal();
  const headers: Record<string, string> = {
    [TARGET_HEADER]: target,
    ...extra,
  };
  if (body) headers["Content-Type"] = "application/json";
  // A target workspace is reached over ssh with the operator's key, so the
  // server holds it to the write token as it does /services (#2067): every
  // request naming a target carries the token this tab holds.
  const credential =
    authorization ?? (target === LOCAL ? undefined : authorised());
  if (credential) headers.Authorization = credential;
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
      signal: AbortSignal.any([
        signal,
        switched,
        AbortSignal.timeout(requestTimeoutMs),
      ]),
      method: verb,
      headers: Object.keys(headers).length ? headers : undefined,
      body,
    });
  } catch (error) {
    finished();
    if (signal.aborted || switched.aborted) throw error;
    throw new ApiError(
      "The API is unavailable or the request timed out.",
      true,
    );
  }
  finished(response.status);
  // A scoped answer names the workspace it came from. One labelled for any
  // other is refused - never shown as this workspace's (#2029).
  const label = response.headers?.get?.(TARGET_HEADER) ?? null;
  if (label !== null && label !== target)
    throw new ApiError(
      `The answer came from ${label}, not ${target}; it was not shown.`,
    );
  if (switched.aborted)
    throw new DOMException("workspace changed", "AbortError");
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const failure = errorBody(payload, response.status);
    throw new ApiError(
      failure.detail,
      failure.transient,
      response.status,
      failure.error,
    );
  }
  if (payload === null)
    throw new ApiError("The API returned an invalid response.", true);
  if (path === "/health") adoptRequestTimeout(payload.request_timeout_seconds);
  return payload as T;
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
 * The built app at /ui/ and `npm run dev` alike: the Vite proxy adds no
 * credential (#2182), so the browser sends it. Kept in sessionStorage - this
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

export function authorised(): string | undefined {
  const token = writeToken.get();
  return token ? `Bearer ${token}` : undefined;
}
