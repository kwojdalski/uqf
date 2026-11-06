import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import App from "./App";
import { ApiError, type BrowserEvent } from "./api";
import {
  configureActivity,
  flushActivity,
  outcomeOfError,
  queuedActivity,
  record,
} from "./activity";
import { HISTORY } from "./testing/activityFixtures";

const ok = (body: unknown) => ({ ok: true, json: async () => body });

/** What the recorder sent, in order. */
let sent: BrowserEvent[] = [];
beforeEach(() => {
  sent = [];
  configureActivity({
    transport: async (events) => {
      sent.push(...events);
    },
  });
});
async function recorded(): Promise<BrowserEvent[]> {
  await act(flushActivity);
  return sent;
}

const PROCESSES = [
  { process: "rdb1", proctype: "rdb", start_with_all: true },
  { process: "rdb2", proctype: "rdb", start_with_all: true },
];
function mockApi(
  handler: (path: string, options: RequestInit) => unknown = () => ok({}),
) {
  const fetcher = vi.fn(async (path: string, options: RequestInit) =>
    path === "/health"
      ? ok({ ok: true, gateway: "up", poll_seconds: 5 })
      : path === "/control"
        ? ok({
            writes_enabled: true,
            lifecycle_actions: ["start", "stop", "restart"],
            settable_fields: ["extras", "port"],
            processes: PROCESSES,
            poll_seconds: 5,
          })
        : path === "/ops/processes"
          ? ok({ processes: [], poll_seconds: 5 })
          : path.startsWith("/activity")
            ? ok(HISTORY)
            : handler(path, options),
  );
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}

/** Enter on a focused button: the browser fires keydown, then a click whose
 *  `detail` is 0. */
function pressEnter(element: HTMLElement) {
  fireEvent.keyDown(element, { key: "Enter" });
  fireEvent.click(element, { detail: 0 });
}

it("records navigation once per change, and how it was triggered", async () => {
  mockApi();
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: /Desk/ }), { detail: 1 });
  // the view already open: not a navigation
  fireEvent.click(screen.getByRole("button", { name: /Desk/ }), { detail: 1 });
  pressEnter(screen.getByRole("button", { name: /Fleet/ }));
  const events = (await recorded()).filter((e) => e.kind === "navigate");
  expect(
    events.map((e) => [e.subject, e.trigger, e.detail?.from_view]),
  ).toEqual([
    ["Desk", "pointer", "Coverage"],
    ["Fleet", "keyboard", "Desk"],
  ]);
});

it("records nothing while views poll", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  mockApi(() => ok({ rows: [], poll_seconds: 2 }));
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: /Queue/ }));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10_000);
  });
  expect((await recorded()).map((e) => e.kind)).toEqual(["navigate"]);
});

async function openControl() {
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: /Control/ }));
  await screen.findByRole("button", { name: "stop" });
}

it("asks before a stop, and a keyboard cancel sends nothing", async () => {
  const fetcher = mockApi();
  await openControl();
  pressEnter(screen.getByRole("button", { name: "stop" }));
  const dialog = screen.getByRole("alertdialog", { name: "Confirm stop" });
  fireEvent.keyDown(dialog, { key: "Escape" });
  expect(screen.queryByRole("alertdialog")).toBeNull();
  expect(
    fetcher.mock.calls.filter(([path]) =>
      path.startsWith("/control/lifecycle"),
    ),
  ).toEqual([]);
  const events = (await recorded()).filter((e) => e.action === "process.stop");
  expect(events.map((e) => [e.kind, e.trigger, e.subject])).toEqual([
    ["submit", "keyboard", "all"],
    ["cancel", "keyboard", "all"],
  ]);
});

it("a confirmed stop carries one request id from the click to the server and back", async () => {
  const fetcher = mockApi(() =>
    ok({
      action: "stop",
      procs: "all",
      exit_code: 0,
      ok: true,
      output: "done",
    }),
  );
  await openControl();
  fireEvent.click(screen.getByRole("button", { name: "stop" }), { detail: 1 });
  fireEvent.click(screen.getByRole("button", { name: "Confirm stop" }), {
    detail: 1,
  });
  await screen.findByText(/exit 0/);
  const call = fetcher.mock.calls.find(([path]) =>
    path.startsWith("/control/lifecycle/stop"),
  )!;
  const headers = call[1].headers as Record<string, string>;
  const events = (await recorded()).filter((e) => e.action === "process.stop");
  const [submit, confirm, result] = events;
  expect([submit.kind, confirm.kind, result.kind]).toEqual([
    "submit",
    "confirm",
    "result",
  ]);
  expect(confirm.request_id).toBe(headers["X-UQF-Request-Id"]);
  expect(result.request_id).toBe(confirm.request_id);
  expect(headers["X-UQF-Session-Id"]).toBe(confirm.session_id);
  expect(result.outcome).toBe("succeeded");
  expect(result.duration_ms).toBeGreaterThanOrEqual(0);
});

it("a non-zero exit is recorded as failed, a refusal as refused", async () => {
  mockApi((path) =>
    path.endsWith("/start")
      ? ok({
          action: "start",
          procs: "all",
          exit_code: 3,
          ok: false,
          output: "boom",
        })
      : { ok: false, status: 401, json: async () => ({ detail: "no token" }) },
  );
  await openControl();
  fireEvent.click(screen.getByRole("button", { name: "start" }));
  await screen.findByText(/exit 3/);
  fireEvent.click(screen.getByRole("button", { name: "restart" }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm restart" }));
  await screen.findByText("no token");
  const results = (await recorded()).filter((e) => e.kind === "result");
  expect(results.map((e) => [e.action, e.outcome, e.detail?.status])).toEqual([
    ["process.start", "failed", undefined],
    ["process.restart", "refused", 401],
  ]);
});

it("never records a typed value or the write token", async () => {
  mockApi(() => ok({ process: "rdb1", config: {}, settable_fields: [] }));
  await openControl();
  fireEvent.change(screen.getByLabelText("Write token"), {
    target: { value: "TOKEN-SHOULD-NOT-APPEAR" },
  });
  fireEvent.change(screen.getByLabelText("Process"), {
    target: { value: "rdb1" },
  });
  fireEvent.change(screen.getByLabelText("Field"), {
    target: { value: "extras" },
  });
  fireEvent.change(screen.getAllByLabelText("Value")[0], {
    target: { value: "VALUE-SHOULD-NOT-APPEAR" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Set field" }));
  await screen.findByText(/rdb1 now/);
  const events = await recorded();
  const text = JSON.stringify(events);
  expect(events.some((e) => e.action === "process.config")).toBe(true);
  expect(text).not.toContain("TOKEN-SHOULD-NOT-APPEAR");
  expect(text).not.toContain("VALUE-SHOULD-NOT-APPEAR");
  expect(events.find((e) => e.kind === "submit")?.detail).toEqual({
    process: "rdb1",
    field: "extras",
  });
});

it("shows the history, says how it is kept, and narrows to one operation", async () => {
  const fetcher = mockApi();
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: /Activity/ }));
  expect(await screen.findByText("process.stop")).toBeInTheDocument();
  expect(screen.getByText(/kept in memory only/)).toBeInTheDocument();
  expect(screen.getByText(/2 history write\(s\) failed/)).toBeInTheDocument();
  expect(screen.getByText("claimed user (unverified)")).toBeInTheDocument();
  expect(screen.getByText("rdb1: unknown, rdb2: unknown")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "req01234" }));
  await waitFor(() =>
    expect(
      fetcher.mock.calls.some(([path]) =>
        path.includes("request_id=req0123456789"),
      ),
    ).toBe(true),
  );
});

it("filters the history by outcome and refuses a bound without a timezone", async () => {
  const fetcher = mockApi();
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: /Activity/ }));
  await screen.findByText("process.stop");
  fireEvent.change(screen.getByLabelText("Outcome"), {
    target: { value: "failed" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Apply filters" }));
  await waitFor(() =>
    expect(
      fetcher.mock.calls.some(([path]) => path === "/activity?outcome=failed"),
    ).toBe(true),
  );
  fireEvent.change(screen.getByLabelText(/^From/), {
    target: { value: "2026-11-03T00:00:00" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Apply filters" }));
  expect(screen.getByText(/with a timezone/)).toHaveAttribute("role", "alert");
});

it("keeps events the API did not take, and sends them next time", async () => {
  let fail = true;
  configureActivity({
    transport: async (events) => {
      if (fail) throw new Error("down");
      sent.push(...events);
    },
  });
  record("navigate", "view.open", { subject: "Desk" });
  await flushActivity();
  expect(queuedActivity()).toHaveLength(1);
  fail = false;
  await flushActivity();
  expect(sent.map((e) => e.subject)).toEqual(["Desk"]);
  expect(queuedActivity()).toHaveLength(0);
});

it("reads a 4xx as refused and anything else as unknown", () => {
  expect(outcomeOfError(new ApiError("x", false, 422))).toBe("refused");
  expect(outcomeOfError(new ApiError("x", true, 503))).toBe("unknown");
  expect(outcomeOfError(new ApiError("timed out", true))).toBe("unknown");
});
