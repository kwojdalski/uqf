/**
 * The Compare view (#2033) holds no rule: it sends both sides, the window,
 * the filters and the row limit as typed, and shows q's verdict as it came
 * back - an equal_in_sample result always with its label, which says whether
 * the sample was cut.
 */
import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { CompareView, compareBody } from "./CompareView";
import { writeToken } from "./api";
import type { CompareResult, CompareTable } from "./api";

const ok = (body: unknown) => ({ ok: true, json: async () => body });

function verdict(over: Partial<CompareTable> = {}): CompareTable {
  return {
    table: "demo_deals",
    outcome: "equal_in_sample",
    label:
      "equal in a sample cut at 50 rows per side - NOT proof the tables are equal: rows past the cut were not compared",
    truncated: true,
    key: ["deal_id"],
    basis: "events",
    read: [50, 50],
    schema: [],
    rows: [],
    unpaired: 0,
    coverage: {
      left: { checked: false, covered: [], gaps: [] },
      right: { checked: false, covered: [], gaps: [] },
    },
    reasons: { left: "", right: "" },
    ...over,
  };
}

function result(tables: CompareTable[]): CompareResult {
  return {
    ok: tables.every((t) => t.outcome === "equal_in_sample"),
    outcome: tables[0].outcome,
    left: "local",
    right: "prod",
    range_from: "2026-09-11T00:00:00.000000000+00:00",
    range_to: "2026-09-16T00:00:00.000000000+00:00",
    limit: 50,
    filters: ["sym=EURUSD"],
    source_version: null,
    partition: null,
    tables,
  };
}

function serve(answer: unknown) {
  const fetcher = vi.fn(async () => answer);
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}

function fill(values: Record<string, string>) {
  for (const [label, value] of Object.entries(values))
    fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

const FORM = {
  Right: "prod",
  Tables: "demo_deals",
  "From (included)": "2026-09-11T00:00:00Z",
  "To (excluded)": "2026-09-16T00:00:00Z",
  "Rows per side, at most": "50",
  "Filters, one per line": "sym=EURUSD\n",
};

it("sends both sides, the window, filters and limit as typed", async () => {
  const fetcher = serve(ok(result([verdict()])));
  render(<CompareView />);
  fill(FORM);
  fireEvent.click(screen.getByRole("button", { name: "Compare" }));
  await screen.findByLabelText("Comparison of demo_deals");
  const [path, options] = fetcher.mock.calls.at(-1) as unknown as [
    string,
    RequestInit,
  ];
  expect(path).toBe("/compare");
  expect(JSON.parse(options.body as string)).toEqual({
    left: "local",
    right: "prod",
    tables: ["demo_deals"],
    range_from: "2026-09-11T00:00:00Z",
    range_to: "2026-09-16T00:00:00Z",
    filters: ["sym=EURUSD"],
    limit: 50,
    source_version: null,
    partition: null,
  });
});

it("carries the write token, since a target side is reached over ssh", async () => {
  writeToken.set("s3cret");
  try {
    const fetcher = serve(ok(result([verdict()])));
    render(<CompareView />);
    fill(FORM);
    fireEvent.click(screen.getByRole("button", { name: "Compare" }));
    await screen.findByLabelText("Comparison of demo_deals");
    const [, options] = fetcher.mock.calls.at(-1) as unknown as [
      string,
      RequestInit,
    ];
    expect((options.headers as Record<string, string>).Authorization).toBe(
      "Bearer s3cret",
    );
  } finally {
    writeToken.set("");
  }
});

it("names both sides, and never shows a truncated sample without its label", async () => {
  serve(ok(result([verdict()])));
  render(<CompareView />);
  fill(FORM);
  fireEvent.click(screen.getByRole("button", { name: "Compare" }));
  const summary = await screen.findByLabelText("Comparison summary");
  expect(summary).toHaveTextContent("local (left) against prod (right)");
  const table = screen.getByLabelText("Comparison of demo_deals");
  expect(within(table).getByRole("status")).toHaveTextContent(
    "NOT proof the tables are equal",
  );
  expect(within(table).getByText("equal_in_sample")).toHaveClass("badge");
});

it("shows each kind of difference q named", async () => {
  serve(
    ok(
      result([
        verdict({
          outcome: "differs",
          label:
            "differs - schema (1 column), published coverage, rows (1 difference)",
          truncated: false,
          schema: [{ column: "venue", left: "absent", right: "symbol (s)" }],
          rows: [
            {
              rowkey: "deal_id=3",
              kind: "changed",
              detail: "rate: 1.0847 -> 1.2",
            },
          ],
          coverage: {
            left: { checked: true, covered: [], gaps: [] },
            right: {
              checked: true,
              covered: [],
              gaps: [
                {
                  range_from: "2026-09-13T00:00:00.000000000+00:00",
                  range_to: "2026-09-16T00:00:00.000000000+00:00",
                },
              ],
            },
          },
        }),
      ]),
    ),
  );
  render(<CompareView />);
  fill(FORM);
  fireEvent.click(screen.getByRole("button", { name: "Compare" }));
  expect(await screen.findByLabelText("Schema differences")).toHaveTextContent(
    "venue",
  );
  expect(screen.getByLabelText("Row differences")).toHaveTextContent(
    "rate: 1.0847 -> 1.2",
  );
  expect(screen.getByLabelText("Coverage not published")).toHaveTextContent(
    "prod",
  );
});

it("shows an unavailable side's reason", async () => {
  serve(
    ok(
      result([
        verdict({
          outcome: "endpoint_unavailable",
          label: "endpoint unavailable - prod: ssh refused",
          truncated: false,
          reasons: { left: "", right: "ssh refused" },
        }),
      ]),
    ),
  );
  render(<CompareView />);
  fill(FORM);
  fireEvent.click(screen.getByRole("button", { name: "Compare" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "prod: ssh refused",
  );
});

it("refuses a form that does not state both sides and a zoned window", () => {
  const base = {
    left: "local",
    right: "prod",
    tables: "t",
    from: "2026-09-11T00:00:00Z",
    to: "2026-09-12T00:00:00Z",
    filters: "",
    limit: "10",
    sourceVersion: "",
    partition: "",
  };
  expect(() => compareBody({ ...base, right: " " })).toThrow("Name both sides");
  expect(() => compareBody({ ...base, tables: "" })).toThrow(
    "at least one table",
  );
  expect(() => compareBody({ ...base, from: "2026-09-11T00:00:00" })).toThrow(
    "timezone",
  );
  expect(() => compareBody({ ...base, limit: "0" })).toThrow("row limit");
  expect(compareBody({ ...base, tables: "a, b c" }).tables).toEqual([
    "a",
    "b",
    "c",
  ]);
});

it("says the coverage slice: a blank partition with a source version is the no-partition slice", () => {
  const base = {
    left: "local",
    right: "prod",
    tables: "t",
    from: "2026-09-11T00:00:00Z",
    to: "2026-09-12T00:00:00Z",
    filters: "",
    limit: "10",
    sourceVersion: "",
    partition: "",
  };
  const slice = (over: Partial<typeof base>) => {
    const body = compareBody({ ...base, ...over });
    return [body.source_version, body.partition];
  };
  expect(slice({})).toEqual([null, null]);
  expect(slice({ sourceVersion: "v1" })).toEqual(["v1", ""]);
  expect(slice({ sourceVersion: "v1", partition: " EURUSD " })).toEqual([
    "v1",
    "EURUSD",
  ]);
  // sent, so the server refuses it, rather than dropped here
  expect(slice({ partition: "EURUSD" })).toEqual([null, "EURUSD"]);
});

it("shows the server's refusal", async () => {
  serve({
    ok: false,
    status: 422,
    json: async () => ({
      error: "CompareRefused",
      detail: "data diff: filter sym: expected COLUMN OP VALUE",
      transient: false,
    }),
  });
  render(<CompareView />);
  fill(FORM);
  fireEvent.click(screen.getByRole("button", { name: "Compare" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "expected COLUMN OP VALUE",
  );
});
