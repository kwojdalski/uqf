import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import App from "./App";

const ok = (body: unknown) => ({ ok: true, json: async () => body });

/** What the API reads from the effective process.csv: two rdbs, a feed, and
 * one process "all" does not start. */
const PROCESSES = [
  { process: "rdb1", proctype: "rdb", start_with_all: true },
  { process: "rdb2", proctype: "rdb", start_with_all: true },
  { process: "feed1", proctype: "feed", start_with_all: true },
  { process: "tap1", proctype: "metrics", start_with_all: false },
];
const FLEET = [
  { process: "rdb1", up: true },
  { process: "rdb2", up: false },
  { process: "feed1", up: true },
  { process: "tap1", up: false },
];

/** The control status the app asks for on mount, plus whatever else a test
 * needs. `/health` is answered here too, because App renders it regardless
 * and an unhandled path would make every test fail for the wrong reason. */
function mockApi(
  writesEnabled: boolean,
  handler: (path: string, options: RequestInit) => unknown = () => ok({}),
) {
  const fetcher = vi.fn(async (path: string, options: RequestInit) =>
    path === "/health"
      ? ok({ ok: true, gateway: "up", poll_seconds: 5 })
      : path === "/control"
        ? ok({
            writes_enabled: writesEnabled,
            lifecycle_actions: ["start", "stop", "restart"],
            settable_fields: ["startwithall", "port"],
            processes: PROCESSES,
            poll_seconds: 5,
          })
        : path === "/ops/processes"
          ? ok({ processes: FLEET, poll_seconds: 5 })
          : handler(path, options),
  );
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}

async function openControl() {
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: /Control/ }));
}

it("offers nothing actionable when writes are disabled, and says which variable enables them", async () => {
  // The property that matters. Discovering the surface is off by pressing
  // "stop" and reading a 403 would mean having already tried to stop the
  // fleet, so the state is rendered rather than provoked.
  mockApi(false);
  await openControl();
  expect(await screen.findByText("Writes are disabled")).toBeInTheDocument();
  expect(
    screen.getByText("UQF_FRONTEND_ENABLE_WRITES=true"),
  ).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "stop" })).toBeNull();
});

it("renders the lifecycle buttons the server declares, not a hardcoded list", async () => {
  mockApi(true);
  await openControl();
  for (const action of ["start", "stop", "restart"]) {
    expect(
      await screen.findByRole("button", { name: action }),
    ).toBeInTheDocument();
  }
});

async function lifecycleBody(
  fetcher: ReturnType<typeof mockApi>,
  action: string,
): Promise<unknown> {
  let body: unknown;
  await waitFor(() => {
    const call = fetcher.mock.calls.find(([path]) =>
      path.startsWith(`/control/process/${action}`),
    );
    expect(call).toBeTruthy();
    body = JSON.parse(call![1].body as string);
  });
  return body;
}

const command = (action: string, target: string) =>
  ok({ action, target, exit_code: 0, ok: true, output: "done" });

it("defaults to torq.sh's own “all”, sent as the word and not spelled out", async () => {
  // "all" means startwithall=1 to torq.sh. Sending every name instead would
  // make the UI's idea of "all" a second definition that can drift from
  // `uqs start all`.
  const fetcher = mockApi(true, () => command("start", "all"));
  await openControl();
  // three of the four: tap1 has startwithall=0, so "all" does not start it
  expect(await screen.findByText(/3 selected/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "start" }));
  expect(await lifecycleBody(fetcher, "start")).toEqual({ procs: "all" });
});

it("lists every process the server declares, grouped by type, and sends the picked names", async () => {
  const fetcher = mockApi(true, () => command("stop", "rdb1 rdb2"));
  await openControl();
  await screen.findByText("rdb1");
  for (const name of ["rdb1", "rdb2", "feed1", "tap1"])
    expect(screen.getByText(name)).toBeInTheDocument();
  // the group checkbox picks the whole proctype in one go
  fireEvent.click(screen.getByLabelText("every rdb"));
  expect(screen.getByText(/2 selected/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "stop" }));
  // stop takes processes down, so it is confirmed first
  fireEvent.click(screen.getByRole("button", { name: "Confirm stop" }));
  expect(await lifecycleBody(fetcher, "stop")).toEqual({ procs: "rdb1 rdb2" });
});

it("marks a process “all” would not start, and shows liveness when the fleet is known", async () => {
  mockApi(true);
  await openControl();
  const tap = (await screen.findByText("tap1")).closest("label")!;
  expect(tap).toHaveTextContent("manual");
  expect(tap).toHaveTextContent("down");
  expect(screen.getByText("rdb1").closest("label")).toHaveTextContent("up");
});

it("can pick exactly the processes that are down", async () => {
  const fetcher = mockApi(true, () => command("start", "rdb2 tap1"));
  await openControl();
  fireEvent.click(await screen.findByRole("button", { name: "down only" }));
  fireEvent.click(screen.getByRole("button", { name: "start" }));
  expect(await lifecycleBody(fetcher, "start")).toEqual({ procs: "rdb2 tap1" });
});

it("refuses to run an action on an empty selection", async () => {
  // torq.sh with no selector would be an error at best and "all" at worst;
  // neither is what an operator who deselected everything meant.
  mockApi(true);
  await openControl();
  fireEvent.click(await screen.findByRole("button", { name: "none" }));
  expect(screen.getByRole("button", { name: "start" })).toBeDisabled();
});

it("reports a non-zero exit rather than showing success", async () => {
  // torq.sh distinguishes "nothing to do" from "failed"; a UI that showed
  // both as done would hide the one an operator must act on.
  mockApi(true, () =>
    ok({
      action: "start",
      target: "all",
      exit_code: 3,
      ok: false,
      output: "could not start rdb1",
    }),
  );
  await openControl();
  fireEvent.click(await screen.findByRole("button", { name: "start" }));
  expect(await screen.findByText(/exit 3/)).toBeInTheDocument();
  expect(screen.getByText(/could not start rdb1/)).toBeInTheDocument();
});

it("offers only the fields the server says are settable", async () => {
  // A free-text box would let a caller invent a column and learn it was
  // wrong from a 422; the whitelist lives on the server and is echoed here.
  mockApi(true);
  await openControl();
  const select = (await screen.findByLabelText("Field")) as HTMLSelectElement;
  const options = [...select.options].map((o) => o.value).filter(Boolean);
  expect(options).toEqual(["startwithall", "port"]);
});

it("surfaces a refusal from the server instead of failing silently", async () => {
  mockApi(true, async () => ({
    ok: false,
    status: 422,
    json: async () => ({ detail: "unknown process.csv field 'nope'" }),
  }));
  await openControl();
  fireEvent.click(await screen.findByRole("button", { name: "start" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "unknown process.csv field",
  );
});

it("says a backfill was started and where to watch it, not that it finished", async () => {
  // It is detached. Telling the operator it succeeded would be a claim the
  // response cannot support.
  mockApi(true, () =>
    ok({
      worker: "demo_deals_backfill",
      source_version: "v1",
      range_from: "2026-09-11T00:00:00Z",
      range_to: "2026-09-12T00:00:00Z",
      pid: 4242,
      status_path: "/ops/backfill",
    }),
  );
  await openControl();
  for (const [label, value] of [
    ["Worker", "demo_deals_backfill"],
    ["Source version", "v1"],
    ["From (inclusive, timezone required)", "2026-09-11T00:00:00Z"],
    ["To (exclusive, timezone required)", "2026-09-12T00:00:00Z"],
  ] as const) {
    fireEvent.change(await screen.findByLabelText(label), {
      target: { value },
    });
  }
  fireEvent.click(screen.getByRole("button", { name: "Start backfill" }));
  expect(
    await screen.findByText(/started demo_deals_backfill/),
  ).toBeInTheDocument();
  expect(screen.getByText(/watch \/ops\/backfill/)).toBeInTheDocument();
});

it("tells the operator a worker-config override is not durable", async () => {
  // A .qetl.cfg override lives in the process's memory; a process.csv override
  // survives a restart. The two look identical in a UI and are not.
  mockApi(true, () =>
    ok({
      key: "dry_run",
      value: "true",
      explain: ["overrides", "true"],
      effective_layer: "overrides",
      effective_value: "true",
      shadowed: false,
      saved: true,
      env_var: null,
      note: "this override lives in the process's memory and is lost when it restarts; a process.csv override survives",
    }),
  );
  await openControl();
  fireEvent.change(await screen.findByLabelText("Key"), {
    target: { value: "dry_run" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Set override" }));
  expect(await screen.findByText(/lost when it restarts/)).toBeInTheDocument();
});

it("warns when an environment variable outranks the override just set", async () => {
  // #634: UQF_DRY_RUN in the process's environment beats a .qetl.cfg
  // override, and the view used to report the PUT as a plain success.
  mockApi(true, () =>
    ok({
      key: "dry_run",
      value: "true",
      explain: ["env", "false"],
      effective_layer: "env",
      effective_value: "false",
      shadowed: true,
      saved: false,
      env_var: "UQF_DRY_RUN",
      note: "this override lives in the process's memory and is lost when it restarts; a process.csv override survives",
    }),
  );
  await openControl();
  fireEvent.change(await screen.findByLabelText("Key"), {
    target: { value: "dry_run" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Set override" }));
  expect(
    await screen.findByText(
      /Not in effect: .*UQF_DRY_RUN in the environment wins/,
    ),
  ).toBeInTheDocument();
  expect(screen.queryByText(/Saved:/)).not.toBeInTheDocument();
});

it("says saved only when the override is what the process reads", async () => {
  mockApi(true, () =>
    ok({
      key: "dry_run",
      value: "true",
      explain: ["overrides", "true"],
      effective_layer: "overrides",
      effective_value: "true",
      shadowed: false,
      saved: true,
      env_var: null,
      note: "",
    }),
  );
  await openControl();
  fireEvent.change(await screen.findByLabelText("Key"), {
    target: { value: "dry_run" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Set override" }));
  expect(await screen.findByText(/Saved: dry_run = true/)).toBeInTheDocument();
});

it("describes the deployment honestly in the sidebar", async () => {
  // The sidebar asserted "Read-only access" flatly, which stops being true
  // the moment writes are enabled. A regex because the text shares its <p>
  // with "Local session", so the element's content is not the string alone.
  mockApi(false);
  const { unmount } = render(<App />);
  expect(await screen.findByText(/Read-only access/)).toBeInTheDocument();
  unmount();

  mockApi(true);
  render(<App />);
  expect(await screen.findByText(/Writes enabled/)).toBeInTheDocument();
});

it("sends the typed write token with an action, and keeps it out of reads", async () => {
  // The built app at /ui/ has no proxy to add it (#631): the operator types
  // it once, and every control action carries it as a bearer token.
  sessionStorage.clear();
  const fetcher = mockApi(true, () => command("start", "all"));
  await openControl();
  fireEvent.change(await screen.findByLabelText("Write token"), {
    target: { value: "tok-123" },
  });
  expect(sessionStorage.getItem("uqf.writeToken")).toBe("tok-123");
  fireEvent.click(screen.getByRole("button", { name: "start" }));
  await waitFor(() =>
    expect(
      fetcher.mock.calls.some(([path]) => path.startsWith("/control/process/")),
    ).toBe(true),
  );
  const [, action] = fetcher.mock.calls.find(([path]) =>
    path.startsWith("/control/process/"),
  )!;
  expect((action.headers as Record<string, string>).Authorization).toBe(
    "Bearer tok-123",
  );
  const [, status] = fetcher.mock.calls.find(([path]) => path === "/control")!;
  // a read names its workspace (#2029), never the token
  expect(
    (status?.headers as Record<string, string>).Authorization,
  ).toBeUndefined();
  sessionStorage.clear();
});

it("keeps the view and its last result when a status poll fails, and polls on (#2008)", async () => {
  let healthy = true;
  const control = () =>
    healthy
      ? ok({
          writes_enabled: true,
          lifecycle_actions: ["start", "stop", "restart"],
          settable_fields: ["startwithall", "port"],
          processes: PROCESSES,
          poll_seconds: 0.05,
        })
      : { ok: false, status: 500, json: async () => ({}) };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string) =>
      path === "/health"
        ? ok({ ok: true, gateway: "up", poll_seconds: 5 })
        : path === "/control"
          ? control()
          : path === "/ops/processes"
            ? ok({ processes: FLEET, poll_seconds: 5 })
            : command("start", "all"),
    ),
  );
  await openControl();
  fireEvent.click(await screen.findByRole("button", { name: "start" }));
  expect(await screen.findByText(/start all: exit 0/)).toBeInTheDocument();
  healthy = false;
  expect(await screen.findByText(/Request failed \(500\)/)).toBeInTheDocument();
  // the controls and the outcome just shown stay on screen
  expect(screen.getByText(/start all: exit 0/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "start" })).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Refresh control status" }),
  ).toBeInTheDocument();
  healthy = true;
  await waitFor(() =>
    expect(screen.queryByText(/Request failed \(500\)/)).toBeNull(),
  );
  expect(screen.getByText(/start all: exit 0/)).toBeInTheDocument();
});

it("offers a retry when the first status never loaded (#2008)", async () => {
  let healthy = false;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string) =>
      path === "/control" && !healthy
        ? { ok: false, status: 500, json: async () => ({}) }
        : path === "/control"
          ? ok({
              writes_enabled: true,
              lifecycle_actions: ["start"],
              settable_fields: [],
              processes: PROCESSES,
              poll_seconds: 5,
            })
          : ok({ ok: true, gateway: "up", poll_seconds: 5, processes: [] }),
    ),
  );
  await openControl();
  const retry = await screen.findByRole("button", {
    name: "Refresh control status",
  });
  healthy = true;
  fireEvent.click(retry);
  expect(
    await screen.findByRole("button", { name: "start" }),
  ).toBeInTheDocument();
});

it("confirms exactly the selection the confirmation opened for (#2009)", async () => {
  const fetcher = mockApi(true, () => command("stop", "rdb1"));
  await openControl();
  fireEvent.click(await screen.findByRole("button", { name: "none" }));
  fireEvent.click(
    screen.getByText("rdb1").closest("label")!.querySelector("input")!,
  );
  fireEvent.click(screen.getByRole("button", { name: "stop" }));
  // the picker is frozen while the confirmation is open
  expect(screen.getByRole("button", { name: "all" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "all" }));
  expect(
    screen.getByRole("alertdialog", { name: "Confirm stop" }),
  ).toHaveTextContent("stop rdb1?");
  fireEvent.click(screen.getByRole("button", { name: "Confirm stop" }));
  expect(await lifecycleBody(fetcher, "stop")).toEqual({ procs: "rdb1" });
});

it("sends a typed process as one encoded path segment (#2015)", async () => {
  const fetcher = mockApi(true, () => ok({ process: "rdb1?x#y", config: {} }));
  await openControl();
  fireEvent.change(await screen.findByLabelText("Process"), {
    target: { value: "rdb1?x#y" },
  });
  fireEvent.change(screen.getByLabelText("Field"), {
    target: { value: "port" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Set field" }));
  await waitFor(() =>
    expect(
      fetcher.mock.calls.map(([path]) => path).filter((p) => /config$/.test(p)),
    ).toEqual(["/control/process/rdb1%3Fx%23y/config"]),
  );
});

it("returns focus to the button that opened the confirmation (#2016)", async () => {
  mockApi(true, () => command("stop", "all"));
  await openControl();
  const stop = await screen.findByRole("button", { name: "stop" });
  stop.focus();
  fireEvent.click(stop);
  expect(document.activeElement).toBe(
    screen.getByRole("button", { name: "Cancel" }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(document.activeElement).toBe(stop));

  fireEvent.click(stop);
  fireEvent.click(screen.getByRole("button", { name: "Confirm stop" }));
  expect(await screen.findByText(/stop all: exit 0/)).toBeInTheDocument();
  await waitFor(() => expect(document.activeElement).toBe(stop));
});
