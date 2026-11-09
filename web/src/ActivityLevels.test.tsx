/**
 * Each level records exactly its documented events, nothing typed at any of
 * them, and the volume stays bounded and counted (#2000).
 *
 * One script drives the real app at every level - navigate, choose a table,
 * expand a section, change a select and a field, copy, open a confirm and
 * cancel it, export - and what the recorder sent is compared with the level's
 * event set (activity.ts's docstring).
 */
import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import App from "./App";
import type { BrowserDrop, BrowserEvent, SessionLevel } from "./api/activity";
import {
  configureActivity,
  effectiveLevel,
  flushActivity,
  installActivity,
  noteResource,
  pendingDrops,
  queuedActivity,
  record,
  routeOf,
  setSessionLevel,
} from "./activity";
import { mockWholeApi } from "./testing/activityFixtures";

let sent: BrowserEvent[] = [];
let reported: BrowserDrop[] = [];
let sends = 0;
function collect(level: SessionLevel) {
  sent = [];
  reported = [];
  sends = 0;
  configureActivity({
    level,
    transport: async (events, dropped) => {
      sends++;
      sent.push(...events);
      reported.push(...dropped);
    },
  });
}
beforeEach(() => collect("actions"));

async function recorded(): Promise<BrowserEvent[]> {
  await act(flushActivity);
  return sent;
}
const settle = () => act(async () => new Promise((r) => setTimeout(r, 0)));

/** Every secret the script types: none may reach an event at any level. */
const SECRETS = [
  "TOKEN-SHOULD-NOT-APPEAR",
  "FILTER-VALUE-SHOULD-NOT-APPEAR",
  "CONFIG-VALUE-SHOULD-NOT-APPEAR",
  "WORKER-VALUE-SHOULD-NOT-APPEAR",
  "ACTIVITY-FILTER-SHOULD-NOT-APPEAR",
  "COPIED-TEXT-SHOULD-NOT-APPEAR",
];

/** The same operator session, whatever the level. */
async function script() {
  render(<App />);
  await settle();
  // navigate, choose a table, expand Refine, change a select and a field
  fireEvent.click(screen.getByRole("button", { name: /Desk/ }), { detail: 1 });
  for (const table of ["quotes", "trades"])
    fireEvent.click(await screen.findByRole("button", { name: table }), {
      detail: 1,
    });
  const refine = screen.getByText(/^Refine/).closest("details")!;
  refine.open = true;
  fireEvent(refine, new Event("toggle"));
  fireEvent.change(screen.getByLabelText("Storage tier"), {
    target: { value: "hdb" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Add filter" }));
  fireEvent.change(screen.getByLabelText(/^Value/), {
    target: { value: "FILTER-VALUE-SHOULD-NOT-APPEAR" },
  });
  // copy a selection: that it was copied, never what
  const copied = document.createElement("p");
  copied.textContent = "COPIED-TEXT-SHOULD-NOT-APPEAR";
  document.body.append(copied);
  fireEvent.copy(copied);
  // type secrets on Control, ask for a stop and cancel it with the keyboard
  fireEvent.click(screen.getByRole("button", { name: /Control/ }));
  fireEvent.change(await screen.findByLabelText("Write token"), {
    target: { value: "TOKEN-SHOULD-NOT-APPEAR" },
  });
  fireEvent.change(screen.getAllByLabelText("Value")[0], {
    target: { value: "CONFIG-VALUE-SHOULD-NOT-APPEAR" },
  });
  fireEvent.change(screen.getAllByLabelText("Value")[1], {
    target: { value: "WORKER-VALUE-SHOULD-NOT-APPEAR" },
  });
  fireEvent.click(screen.getByRole("button", { name: "stop" }), { detail: 1 });
  fireEvent.keyDown(screen.getByRole("button", { name: "Cancel" }), {
    key: "Enter",
  });
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }), {
    detail: 0,
  });
  // filter the history by typed text, then export it
  fireEvent.click(screen.getByRole("button", { name: /Activity/ }));
  fireEvent.change(await screen.findByLabelText("Target"), {
    target: { value: "ACTIVITY-FILTER-SHOULD-NOT-APPEAR" },
  });
  const link = screen.getByRole("link", { name: "Export" });
  link.addEventListener("click", (event) => event.preventDefault());
  fireEvent.click(link, { detail: 1 });
  await settle();
  return recorded();
}

const ACTION_KINDS = [
  "cancel",
  "confirm",
  "navigate",
  "result",
  "select",
  "submit",
];
const kinds = (events: BrowserEvent[]) =>
  [...new Set(events.map((e) => e.kind))].sort();
const actions = (events: BrowserEvent[], kind: string) =>
  [
    ...new Set(events.filter((e) => e.kind === kind).map((e) => e.action)),
  ].sort();

it("off records nothing in the browser", async () => {
  collect("off");
  mockWholeApi("off");
  expect(await script()).toEqual([]);
  expect(reported).toEqual([]);
});

it("actions records what was done, and no control or request", async () => {
  mockWholeApi("actions");
  const events = await script();
  expect(kinds(events).every((k) => ACTION_KINDS.includes(k))).toBe(true);
  expect(actions(events, "navigate")).toEqual(["view.open"]);
  expect(actions(events, "select")).toEqual(["desk.table", "desk.tier"]);
  expect(actions(events, "submit")).toEqual(["process.stop"]);
  expect(actions(events, "cancel")).toEqual(["process.stop"]);
});

it("interactions adds every control used, by name, and no request", async () => {
  collect("interactions");
  mockWholeApi("interactions");
  const events = await script();
  expect(kinds(events)).toEqual(
    ["cancel", "interact", "navigate", "select", "submit"].sort(),
  );
  expect(actions(events, "interact")).toEqual([
    "ui.change",
    "ui.click",
    "ui.copy",
    "ui.download",
    "ui.toggle",
  ]);
  const used = events
    .filter((e) => e.kind === "interact")
    .map((e) => [e.action, e.subject]);
  expect(used).toEqual(
    expect.arrayContaining([
      ["ui.click", "nav.Desk"],
      ["ui.click", "desk.table"],
      ["ui.toggle", "desk.refine"],
      ["ui.change", "desk.tier"],
      ["ui.change", "desk.filter.value"],
      ["ui.copy", "page"],
      ["ui.change", "control.token"],
      ["ui.click", "control.stop"],
      ["ui.click", "control.cancel"],
      ["ui.change", "activity.filter.target"],
      ["ui.download", "activity.export"],
    ]),
  );
  expect(used.some(([, target]) => target === "unnamed")).toBe(false);
  // the keyboard press that activated Cancel is the click's trigger
  const cancel = events.find(
    (e) => e.kind === "interact" && e.subject === "control.cancel",
  )!;
  expect(cancel.trigger).toBe("keyboard");
  const toggle = events.find((e) => e.action === "ui.toggle")!;
  expect(toggle.detail).toEqual({ open: true });
});

it("debug adds requests, and every level's events with it", async () => {
  collect("debug");
  mockWholeApi("debug");
  const events = await script();
  expect(kinds(events)).toEqual(
    ["cancel", "debug", "interact", "navigate", "select", "submit"].sort(),
  );
  expect(actions(events, "debug")).toEqual(["request.finish", "request.start"]);
  const finish = events.find((e) => e.action === "request.finish")!;
  expect(finish.subject).toMatch(/^GET \//);
  expect(finish.outcome).toBe("succeeded");
  expect(finish.detail).toEqual({ status: 200 });
});

it.each(["off", "actions", "interactions", "debug"] as const)(
  "never records a typed value, the token or copied text at %s",
  async (level) => {
    collect(level);
    mockWholeApi(level);
    const text = JSON.stringify(await script());
    for (const secret of SECRETS) expect(text).not.toContain(secret);
  },
);

it("records a file input by how many files, never their names", async () => {
  collect("debug");
  const uninstall = installActivity();
  const input = document.createElement("input");
  input.type = "file";
  input.setAttribute("data-activity", "files.upload.files");
  document.body.append(input);
  const file = new File(["secret,row"], "SECRET-FILE-NAME.csv");
  Object.defineProperty(input, "files", { value: [file] });
  fireEvent.change(input);
  uninstall();
  const events = await recorded();
  expect(events.map((e) => [e.action, e.subject, e.detail])).toEqual([
    ["ui.change", "files.upload.files", { control: "file", file_count: 1 }],
  ]);
  expect(JSON.stringify(events)).not.toContain("SECRET");
});

it("records a request's route, never its query string or a name in it", () => {
  expect(routeOf("/coverage?dataset=SECRET&range_from=x")).toBe("/coverage");
  expect(routeOf("/services/prod-a/files?path=SECRET")).toBe("/services/*");
  expect(routeOf("/ops/processes")).toBe("/ops/processes");
  expect(routeOf("/control/lifecycle/stop")).toBe("/control/lifecycle");
});

it("follows the server's default from /health until the tab picks its own", async () => {
  mockWholeApi("interactions");
  render(<App />);
  await settle();
  expect(effectiveLevel()).toBe("interactions");
  fireEvent.click(screen.getByRole("button", { name: /Activity/ }));
  const select = await screen.findByLabelText("Record this tab at");
  expect(
    screen.getByRole("option", { name: /Server default \(interactions/ }),
  ).toBeTruthy();
  fireEvent.change(select, { target: { value: "off" } });
  expect(effectiveLevel()).toBe("off");
  fireEvent.click(screen.getByRole("button", { name: /Desk/ }));
  setSessionLevel(null);
  expect(effectiveLevel()).toBe("interactions");
  // switching off records nothing after it: the Desk click above is absent
  const events = await recorded();
  expect(events.some((e) => e.subject === "nav.Desk")).toBe(false);
});

it("shows what each level keeps and what was not kept", async () => {
  mockWholeApi();
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: /Activity/ }));
  expect(
    await screen.findByText(/interactions 7 days, newest 50,000/),
  ).toBeTruthy();
  expect(screen.getByText(/debug sampled 12/)).toBeTruthy();
  const link = screen.getByRole("link", { name: "Export" });
  expect(link.getAttribute("href")).toBe("/activity/export?format=text");
  fireEvent.change(screen.getByLabelText("Level"), {
    target: { value: "interactions" },
  });
  fireEvent.change(screen.getByLabelText("Export format"), {
    target: { value: "csv" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Apply filters" }));
  expect(link.getAttribute("href")).toBe(
    "/activity/export?level=interactions&format=csv",
  );
});

// ------------------------------------------------------------ volume

it("rate-limits debug, and counts and reports what it sampled", async () => {
  collect("debug");
  for (let i = 0; i < 100; i++) noteResource("poll", "/ops/queue");
  const kept = queuedActivity().length;
  expect(kept).toBeGreaterThan(0);
  expect(kept).toBeLessThanOrEqual(30);
  expect(pendingDrops()).toEqual([
    { level: "debug", reason: "sampled", count: 100 - kept },
  ]);
  await recorded();
  expect(reported).toEqual([
    { level: "debug", reason: "sampled", count: 100 - kept },
  ]);
  expect(pendingDrops()).toEqual([]);
});

it("counts what overflows the queue while the API is down, and reports it later", async () => {
  let down = true;
  sent = [];
  reported = [];
  configureActivity({
    level: "interactions",
    transport: async (events, dropped) => {
      if (down) throw new Error("down");
      sent.push(...events);
      reported.push(...dropped);
    },
  });
  for (let i = 0; i < 1100; i++)
    record("interact", "ui.click", { subject: "nav.Desk" });
  await flushActivity();
  expect(queuedActivity()).toHaveLength(1000);
  down = false;
  await flushActivity();
  expect(sent).toHaveLength(1000);
  expect(reported).toEqual([
    { level: "interactions", reason: "queue_overflow", count: 100 },
  ]);
});

it("re-sends an unacknowledged drop report as it was, under its own id (#2480)", async () => {
  // The server stored the first send and the answer was lost: the retry
  // must carry the same id and the same counts, so it is counted once -
  // and what was counted since waits for a report of its own.
  const sends: { drops: BrowserDrop[]; id: string | null }[] = [];
  let down = true;
  configureActivity({
    level: "debug",
    transport: async (_events, dropped, batchId) => {
      sends.push({ drops: dropped, id: batchId });
      if (down) throw new Error("answer lost");
    },
  });
  for (let i = 0; i < 40; i++) noteResource("poll", "/ops/queue");
  await flushActivity();
  for (let i = 0; i < 40; i++) noteResource("poll", "/ops/queue");
  down = false;
  await flushActivity();
  const [first, retry, next] = sends.filter((s) => s.drops.length);
  expect(first.id).toBeTruthy();
  expect(retry).toEqual(first);
  expect(next.id).not.toBe(first.id);
  expect(pendingDrops()).toEqual([]);
});

it("batches interactions: 500 clicks cost 5 requests and little time", async () => {
  // timers only: the clock stays real, so the cost of a click is measured
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  collect("interactions");
  const uninstall = installActivity();
  const button = document.createElement("button");
  button.setAttribute("data-activity", "nav.Desk");
  document.body.append(button);
  const started = performance.now();
  for (let i = 0; i < 500; i++) fireEvent.click(button, { detail: 1 });
  const perClickMs = (performance.now() - started) / 500;
  expect(sends).toBe(0);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1000);
  });
  uninstall();
  expect(sent).toHaveLength(500);
  expect(sends).toBe(5);
  // the recorder's share of a click, in jsdom: a fraction of a frame
  expect(perClickMs).toBeLessThan(2);
  console.info(`activity: ${perClickMs.toFixed(3)} ms a click recorded`);
});
