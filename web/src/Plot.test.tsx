/**
 * The Desk's Plot view (#2043): the columns it offers, the gaps it keeps and
 * the truncation it labels. Plotly itself is replaced by a stand-in that
 * keeps the figure it was given - jsdom cannot lay out an SVG plot, and
 * what is under test is the figure, not Plotly's drawing of it.
 */
import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import type { QueryResult } from "./api/query";
import type { Figure } from "./Plotly";
import { figureFor, plottableColumns, xValue, yValue } from "./plot";

const drawn: Figure[] = [];
vi.mock("./Plotly", () => ({
  PlotlyFigure: ({ figure, name }: { figure: Figure; name: string }) => {
    drawn.push(figure);
    return <div data-testid="figure" data-name={name} />;
  },
}));
const { PlotPanel } = await import("./PlotPanel");
const App = (await import("./App")).default;

beforeEach(() => {
  drawn.length = 0;
});

const types = {
  time: "timestamp",
  sym: "symbol",
  bid: "float",
  ask: "float",
  size: "long",
  ok: "boolean",
  day: "date",
};
function result(rows: QueryResult["rows"], extra = {}): QueryResult {
  return {
    table: "quotes",
    tier: "rdb",
    rows,
    row_count: rows.length,
    truncated: false,
    cut_by: null,
    poll_seconds: 10,
    ...extra,
  };
}
const rows = [
  {
    time: "2026-11-02T10:00:01.000000000Z",
    sym: "EURUSD",
    bid: 1.1,
    ask: 1.2,
    size: 5,
    ok: true,
    day: "2026-11-02",
  },
  {
    time: "2026-11-02T10:00:00.123456789Z",
    sym: "EURUSD",
    bid: null,
    ask: "inf",
    size: 7,
    ok: false,
    day: "2026-11-02",
  },
  {
    time: null,
    sym: "EURUSD",
    bid: 1.3,
    ask: 1.4,
    size: 9,
    ok: true,
    day: "2026-11-02",
  },
];
interface Trace {
  x: unknown[];
  y: unknown[];
  name: string;
  mode: string;
  connectgaps: boolean;
}
const traces = (figure: Figure) => figure.data as Trace[];
const last = () => ({ ...drawn.at(-1)!, data: traces(drawn.at(-1)!) });

it("offers numeric columns for y, and numeric or temporal ones for x", () => {
  expect(plottableColumns(rows, types)).toEqual({
    x: ["time", "bid", "ask", "size", "day"],
    y: ["bid", "ask", "size"],
  });
  // a column the catalog does not type is offered for neither
  expect(plottableColumns(rows, undefined)).toEqual({ x: [], y: [] });
});

it("leaves nulls and infinities as gaps, never 0", () => {
  for (const value of [null, undefined, "inf", "-inf", NaN, Infinity, "1.5"])
    expect(yValue(value)).toBeNull();
  expect(yValue(0)).toBe(0);
  const figure = figureFor({
    result: result(rows),
    types,
    x: "time",
    ys: ["bid", "ask"],
    mode: "lines",
  });
  const data = traces(figure);
  // in time order, the row with no time left out; nanoseconds cut, zone gone
  expect(data[0].x).toEqual([
    "2026-11-02 10:00:00.123456",
    "2026-11-02 10:00:01.000000",
  ]);
  expect(data[0].y).toEqual([null, 1.1]);
  expect(data[1].y).toEqual([null, 1.2]);
  expect(data.every((t) => t.connectgaps === false)).toBe(true);
  expect(JSON.stringify(figure.layout)).toContain(
    "1 row without a time not drawn",
  );
});

it("reads every temporal x the server sends, and nothing else", () => {
  expect(xValue("2026-11-02", "date")).toBe("2026-11-02");
  expect(xValue("09:30:00.250000", "time")).toBe("1970-01-01 09:30:00.250000");
  expect(xValue(1500, "timespan")).toBe(1500);
  expect(xValue("inf", "timestamp")).toBeNull();
  expect(xValue("-inf", "timespan")).toBeNull();
  expect(xValue("EURUSD", "symbol")).toBeNull();
});

it("labels a truncated result on the chart itself", () => {
  const title = (r: QueryResult) =>
    JSON.stringify(
      figureFor({
        result: r,
        types,
        x: "size",
        ys: ["bid"],
        mode: "markers",
      }).layout,
    );
  // the server says which bound cut it (#2220); nothing is guessed here
  expect(
    title(result(rows, { truncated: true, cut_by: "max_rows" })),
  ).toContain("Truncated: the server row cap was reached at 3 rows");
  expect(title(result(rows, { truncated: true, cut_by: "limit" }))).toContain(
    "Truncated: the first 3 rows; more exist",
  );
  expect(title(result(rows))).not.toContain("Truncated");
});

it("draws the chosen x against up to three y columns, as line or scatter", () => {
  const wide = { ...types, a: "float", b: "float" };
  const r = result(rows.map((row, i) => ({ ...row, a: i, b: i })));
  render(<PlotPanel result={r} types={wide} />);
  expect(screen.getByTestId("figure")).toHaveAttribute(
    "data-name",
    "desk.plot.figure",
  );
  // defaults: the first x, the first y that is not it
  expect(last().data.map((t) => t.name)).toEqual(["bid"]);

  fireEvent.change(screen.getByLabelText("X axis"), {
    target: { value: "size" },
  });
  const y = within(screen.getByRole("group", { name: /Y axis/ }));
  expect(y.getByLabelText("size")).toBeDisabled(); // it is the x
  fireEvent.click(y.getByLabelText("ask"));
  fireEvent.click(y.getByLabelText("a"));
  expect(last().data.map((t) => t.name)).toEqual(["bid", "ask", "a"]);
  expect(y.getByLabelText("b")).toBeDisabled(); // three is the most
  fireEvent.change(screen.getByLabelText("Draw as"), {
    target: { value: "markers" },
  });
  expect(last().data.every((t) => t.mode === "markers")).toBe(true);
  expect(last().data[0].x).toEqual([5, 7, 9]);
});

it("says so when there is nothing numeric to draw", () => {
  render(<PlotPanel result={result([{ sym: "EURUSD" }])} types={types} />);
  expect(screen.getByText(/no numeric column to plot/)).toBeInTheDocument();
  expect(screen.queryByTestId("figure")).toBeNull();
});

it("switches a Desk result between its table and its plot", async () => {
  const ok = (body: unknown) => ({ ok: true, json: async () => body });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string) =>
      path === "/health"
        ? ok({ ok: true, gateway: "up", poll_seconds: 5 })
        : path === "/catalog"
          ? ok({
              tables: [
                {
                  name: "quotes",
                  description: "Top of book",
                  columns: Object.entries(types).map(([name, type]) => ({
                    name,
                    type,
                    filterable: true,
                    decimals: null,
                  })),
                },
              ],
              operators: ["eq"],
            })
          : ok(result(rows, { truncated: true, cut_by: "max_rows" })),
    ),
  );
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: /Desk/ }));
  const plot = await screen.findByRole("button", { name: "Plot" });
  expect(plot).toHaveAttribute("data-activity", "desk.show.plot");
  expect(screen.getByRole("button", { name: "Table" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  fireEvent.click(plot);
  expect(await screen.findByTestId("figure")).toBeInTheDocument();
  expect(screen.queryByLabelText("Results table")).toBeNull();
  expect(
    screen.getByText(/Truncated: the server row cap was reached/),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Table" }));
  expect(screen.getByLabelText("Results table")).toBeInTheDocument();
});
