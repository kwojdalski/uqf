import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import App from "./App";
const ok = (body: unknown) => ({ ok: true, json: async () => body });
function mockApi(handler: (path: string, options: RequestInit) => unknown) {
  const fetcher = vi.fn(async (path: string, options: RequestInit) =>
    path === "/health"
      ? ok({ ok: true, gateway: "up", poll_seconds: 5 })
      : handler(path, options),
  );
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}
it("renders coverage gaps for the submitted source release with explicit UTC boundaries", async () => {
  const fetcher = mockApi((path) =>
    ok({
      dataset: "trades",
      source_version: "v2",
      requested: null,
      covered: [],
      gaps: [
        {
          range_from: "2026-09-16T00:00:00Z",
          range_to: "2026-09-17T00:00:00Z",
        },
      ],
      complete: false,
      poll_seconds: 60,
    }),
  );
  render(<App />);
  fireEvent.change(screen.getByLabelText("Source version"), {
    target: { value: "v2" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Check coverage" }));
  expect(await screen.findByText("Gaps found")).toBeInTheDocument();
  expect(screen.getByText("2026-09-16T00:00:00Z")).toBeInTheDocument();
  const path = fetcher.mock.calls.find(([path]) =>
    path.startsWith("/coverage"),
  )?.[0];
  expect(
    new URL(path!, "http://local").searchParams.get("source_version"),
  ).toBe("v2");
});
it("shows failed and idle workers distinctly, including unreadable files", async () => {
  mockApi(() =>
    ok({
      poll_seconds: 10,
      source: "/status",
      summary: { failed: 1, idle: 1 },
      workers: [
        { worker: "idle-worker", state: "idle" },
        {
          worker: "failed-worker",
          state: "failed",
          error: "Source unavailable",
        },
      ],
      unreadable: [{ path: "bad.json", error: "Invalid JSON" }],
    }),
  );
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: /Backfills/ }));
  expect(await screen.findByText("Success · no work")).toBeInTheDocument();
  expect(screen.getByText("Failure")).toBeInTheDocument();
  expect(screen.getByText("Source unavailable")).toBeInTheDocument();
  expect(screen.getByText("bad.json")).toBeInTheDocument();
});
it("builds filters from the catalog and renders a 409 with its missing range", async () => {
  const fetcher = mockApi((path) =>
    path === "/catalog"
      ? ok({
          tables: [
            {
              name: "trades",
              description: "Client fills",
              columns: [
                { name: "sym", type: "symbol", filterable: true },
                { name: "levels", type: "list", filterable: false },
              ],
            },
          ],
          operators: ["eq", "in"],
        })
      : {
          ok: false,
          status: 409,
          json: async () => ({
            detail: "missing: [2026-09-16, 2026-09-17)",
            transient: false,
          }),
        },
  );
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: /Desk/ }));
  await screen.findByRole("button", { name: "trades" });
  fireEvent.click(screen.getByRole("button", { name: "Add filter" }));
  expect(
    screen.queryByRole("option", { name: /levels/ }),
  ).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Value"), {
    target: { value: "EURUSD" },
  });
  fireEvent.change(screen.getByLabelText("Storage tier"), {
    target: { value: "hdb" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Apply filters" }));
  await waitFor(() =>
    expect(screen.getByRole("alert")).toHaveTextContent(
      "missing: [2026-09-16, 2026-09-17)",
    ),
  );
  // the LAST query: the glimpse ran unfiltered as soon as the table showed
  const call = fetcher.mock.calls.filter(([path]) => path === "/query").at(-1);
  expect(JSON.parse(call![1].body as string)).toMatchObject({
    tier: "hdb",
    filters: [{ column: "sym", op: "eq", value: "EURUSD" }],
  });
});
it("shows a table's first rows as soon as it is chosen, with nothing to submit", async () => {
  // A glimpse, not a form. The catalog's tables are a panel of choices;
  // choosing one queries it - 100 rows, current session, no filters - and
  // there is no Run button to find.
  const fetcher = mockApi((path, options) =>
    path === "/catalog"
      ? ok({
          tables: [
            { name: "trades", description: "Client fills", columns: [] },
            { name: "quotes", description: "Top of book", columns: [] },
          ],
          operators: ["eq"],
        })
      : ok({
          table: JSON.parse(options.body as string).table,
          tier: "rdb",
          row_count: 1,
          truncated: false,
          rows: [{ sym: "EURUSD" }],
          poll_seconds: 10,
        }),
  );
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: /Desk/ }));
  expect(await screen.findByText("EURUSD")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Run query" })).toBeNull();
  expect(
    JSON.parse(
      fetcher.mock.calls.find(([path]) => path === "/query")![1].body as string,
    ),
  ).toEqual({ table: "trades", tier: "rdb", limit: 100, filters: [] });

  fireEvent.click(screen.getByRole("button", { name: "quotes" }));
  await waitFor(() => {
    const last = fetcher.mock.calls
      .filter(([path]) => path === "/query")
      .at(-1);
    expect(JSON.parse(last![1].body as string).table).toBe("quotes");
  });
  expect(screen.getByRole("button", { name: "quotes" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
});
it("shows reloads as temporary status rather than a permanent failure", async () => {
  mockApi(() => ({
    ok: false,
    status: 503,
    json: async () => ({ detail: "EOD reload", transient: true }),
  }));
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: /Queue/ }));
  expect(
    await screen.findByText("Temporarily unavailable"),
  ).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.getByText("Retrying automatically.")).toBeInTheDocument();
});
