import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import App from "./App";

const ok = (body: unknown) => ({ ok: true, json: async () => body });

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
            poll_seconds: 5,
          })
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

it("sends the process selector unreinterpreted", async () => {
  const fetcher = mockApi(true, () =>
    ok({
      action: "stop",
      target: "rdb1 hdb1",
      exit_code: 0,
      ok: true,
      output: "stopped",
    }),
  );
  await openControl();
  fireEvent.change(await screen.findByLabelText(/Selector/), {
    target: { value: "rdb1 hdb1" },
  });
  fireEvent.click(screen.getByRole("button", { name: "stop" }));

  await waitFor(() => {
    const call = fetcher.mock.calls.find(([path]) =>
      path.startsWith("/control/process/stop"),
    );
    expect(call).toBeTruthy();
    expect(JSON.parse(call![1].body as string)).toEqual({
      procs: "rdb1 hdb1",
    });
  });
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
  // A .qwcfg override lives in the process's memory; a process.csv override
  // survives a restart. The two look identical in a UI and are not.
  mockApi(true, () =>
    ok({
      key: "dry_run",
      value: "true",
      explain: { source: "override" },
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
