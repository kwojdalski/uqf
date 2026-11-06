/**
 * A Desk result as a figure (#2043): which of its columns can be drawn, and
 * the traces they make. No request is made - this plots the rows already
 * loaded, as the server sent them (app.py's `_wire`).
 */
import type { QueryResult, Row } from "./api";
import type { Figure } from "./Plotly";

/** The most y columns one chart draws: more than this on one axis is a
 *  tangle, not a comparison. */
export const MAX_Y = 3;

/** Types whose values are numbers on the wire, and so a y. */
const NUMBER = new Set(["float", "long", "int", "short"]);
/** Types an x axis can order by: a number, or a point in time. A timespan
 *  arrives as whole nanoseconds, so it is drawn as a number of those. */
const TEMPORAL = new Set(["timestamp", "date", "time", "timespan"]);

export type PlotMode = "lines" | "markers";

export interface PlotColumns {
  x: string[];
  y: string[];
}

/** The result's columns a chart can use, in the order the result has them.
 *  A column the catalog does not type is offered for neither. */
export function plottableColumns(
  rows: Row[],
  types: Record<string, string> | undefined,
): PlotColumns {
  const names = [...new Set(rows.flatMap((row) => Object.keys(row)))];
  const typeOf = (name: string) => types?.[name] ?? "";
  return {
    x: names.filter((n) => NUMBER.has(typeOf(n)) || TEMPORAL.has(typeOf(n))),
    y: names.filter((n) => NUMBER.has(typeOf(n))),
  };
}

/** A y value Plotly can place, or null - which Plotly leaves as a gap. A null,
 *  NaN, or the "inf"/"-inf" the server sends for an infinity is never 0. */
export function yValue(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** An x value Plotly can place, or null when the row has none.
 *
 *  A timestamp arrives as UTC text to the nanosecond with a trailing Z;
 *  Plotly reads a date to the microsecond with no zone, so the zone goes and
 *  the fraction is cut, never shifted into the browser's timezone. A time of
 *  day is put on 1970-01-01 so it is drawn on a time axis. */
export function xValue(value: unknown, type: string): number | string | null {
  if (NUMBER.has(type) || type === "timespan") return yValue(value);
  if (typeof value !== "string" || value === "inf" || value === "-inf")
    return null;
  if (type === "timestamp") {
    const parts =
      /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})(?:\.(\d+))?(?:Z|[+-]\d{2}:\d{2})?$/.exec(
        value,
      );
    if (!parts) return null;
    return `${parts[1]} ${parts[2]}${parts[3] ? "." + parts[3].slice(0, 6) : ""}`;
  }
  if (type === "date") return /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : null;
  if (type === "time")
    return /^\d{2}:\d{2}:\d{2}(\.\d+)?$/.test(value)
      ? `1970-01-01 ${value}`
      : null;
  return null;
}

/** What a result leaves out, in words for the chart, or "" when it is whole.
 *  The server says whether more rows existed and which bound cut them
 *  (#2220): the server's own cap, or the row limit asked for. */
export function truncation(result: QueryResult): string {
  if (!result.truncated) return "";
  return result.cut_by === "max_rows"
    ? `Truncated: the server row cap was reached at ${result.row_count.toLocaleString()} rows`
    : `Truncated: the first ${result.row_count.toLocaleString()} rows; more exist`;
}

/** The figure for `x` against each of `ys`.
 *
 *  Rows are drawn in x order, so a line does not double back on itself; a
 *  row with no usable x cannot be placed, and is left out and counted in
 *  the subtitle. Every y keeps its gaps (connectgaps off). */
export function figureFor({
  result,
  types,
  x,
  ys,
  mode,
}: {
  result: QueryResult;
  types: Record<string, string>;
  x: string;
  ys: string[];
  mode: PlotMode;
}): Figure {
  const xType = types[x] ?? "";
  const placed = result.rows
    .map((row) => ({ row, x: xValue(row[x], xType) }))
    .filter((point) => point.x !== null) as { row: Row; x: number | string }[];
  placed.sort((a, b) => (a.x < b.x ? -1 : a.x > b.x ? 1 : 0));
  const dropped = result.rows.length - placed.length;
  const temporal = xType !== "timespan" && TEMPORAL.has(xType);
  const notes = [
    truncation(result),
    dropped
      ? `${dropped.toLocaleString()} row${dropped === 1 ? "" : "s"} without a ${x} not drawn`
      : "",
  ].filter(Boolean);
  return {
    data: ys.map((y) => ({
      type: "scatter",
      mode,
      name: y,
      x: placed.map((point) => point.x),
      y: placed.map((point) => yValue(point.row[y])),
      connectgaps: false,
    })),
    layout: {
      title: {
        text:
          `${result.table} · ${result.tier.toUpperCase()}` +
          (notes.length ? `<br><sub>${notes.join(" · ")}</sub>` : ""),
      },
      xaxis: {
        title: { text: xType === "timespan" ? `${x} (ns)` : x },
        type: temporal ? "date" : "linear",
      },
      yaxis: { title: { text: ys.length === 1 ? ys[0] : "" } },
      showlegend: ys.length > 1,
      margin: { t: notes.length ? 72 : 48 },
    },
  };
}
