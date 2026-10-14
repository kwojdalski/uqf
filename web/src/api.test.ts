import { expect, it, vi } from "vitest";
import {
  DEFAULT_REQUEST_TIMEOUT_MS,
  adoptRequestTimeout,
  request,
  requestTimeout,
  filterValue,
  validateRange,
  type Column,
  type ColumnType,
} from "./api";
const column = (type: ColumnType): Column => ({
  name: "value",
  type,
  filterable: true,
  decimals: null,
});
it("sends typed numbers, booleans and lists; preserves symbol text without query interpolation", () => {
  expect(filterValue("1.25", column("float"), "eq")).toBe(1.25);
  expect(filterValue("[1,2]", column("long"), "in")).toEqual([1, 2]);
  expect(filterValue("false", column("boolean"), "eq")).toBe(false);
  expect(filterValue("EURUSD;delete from trades", column("symbol"), "eq")).toBe(
    "EURUSD;delete from trades",
  );
  expect(() => filterValue("", column("float"), "eq")).toThrow();
  expect(() => filterValue("1.2", column("long"), "eq")).toThrow();
  expect(() => filterValue("[true]", column("long"), "in")).toThrow();
});
it("coerces the int, short, date and time columns the scaffold can create (#1364)", () => {
  expect(filterValue("5", column("int"), "eq")).toBe(5);
  expect(filterValue("[1,2]", column("short"), "in")).toEqual([1, 2]);
  expect(() => filterValue("1.5", column("int"), "eq")).toThrow();
  expect(filterValue("2026-09-16", column("date"), "eq")).toBe("2026-09-16");
  expect(() => filterValue("16/09/2026", column("date"), "eq")).toThrow();
  expect(filterValue("14:30:00.250", column("time"), "ge")).toBe(
    "14:30:00.250",
  );
  expect(() => filterValue("14:30", column("time"), "ge")).toThrow();
});
it("rejects naive and reversed time ranges before requesting coverage", () => {
  const range = {
    dataset: "trades",
    // The sentinel, not an omission: "" means the dataset has no partition
    // dimension. validateRange must accept it, or every unpartitioned
    // dataset becomes unqueryable (#185).
    partition: "",
    source_version: "v1",
    range_from: "2026-09-16T00:00:00Z",
    range_to: "2026-09-17T00:00:00Z",
  };
  expect(() => validateRange(range)).not.toThrow();
  expect(() =>
    validateRange({ ...range, range_from: "2026-09-16T00:00:00" }),
  ).toThrow(/timezone/);
  expect(() => validateRange({ ...range, range_to: range.range_from })).toThrow(
    /after/,
  );
  expect(() => validateRange({ ...range, source_version: "" })).toThrow(
    /required/,
  );
});
it("sends a timespan as whole nanoseconds, typed either way the Desk shows it", () => {
  // #1329: the server takes nanoseconds and no longer rounds them to the
  // microsecond, so 1234 must leave as 1234 - and a timespan copied from the
  // table, in q's own form, as the same number.
  const span = column("timespan");
  expect(filterValue("1234", span, "eq")).toBe(1234);
  expect(filterValue("0D00:00:00.000001234", span, "eq")).toBe(1234);
  expect(filterValue("0D00:00:01.5", span, "eq")).toBe(1_500_000_000);
  expect(filterValue("-1D02:00:00", span, "eq")).toBe(-93_600_000_000_000);
  expect(filterValue('["0D00:00:00.000001234", 2500]', span, "in")).toEqual([
    1234, 2500,
  ]);
  expect(() => filterValue("1234.5", span, "eq")).toThrow(
    "Enter a whole number of nanoseconds",
  );
  expect(() => filterValue("1e20", span, "eq")).toThrow(
    "Enter a whole number of nanoseconds",
  );
});
it("takes a timestamp to the nanosecond, and orders a range by it", () => {
  // #1371: the Desk now shows nine digits. A filter must take that value as
  // shown, and a coverage range 765 ns long is a range - Date.parse, which
  // stops at the millisecond, read it as an empty one.
  const stamp = column("timestamp");
  expect(filterValue("2026-10-14T10:00:00.000001234Z", stamp, "eq")).toBe(
    "2026-10-14T10:00:00.000001234Z",
  );
  const range = {
    dataset: "trades",
    partition: "",
    source_version: "v1",
    range_from: "2026-10-14T10:00:00.000001234Z",
    range_to: "2026-10-14T12:00:00.000001999+02:00",
  };
  expect(() => validateRange(range)).not.toThrow();
  expect(() =>
    validateRange({ ...range, range_to: "2026-10-14T10:00:00.000001234Z" }),
  ).toThrow(/after/);
  expect(() =>
    validateRange({
      ...range,
      range_to: "2026-10-14T12:00:00.000001233+02:00",
    }),
  ).toThrow(/after/);
});
it("takes its request timeout from what /health serves, not a copy of its own (#1370)", async () => {
  const timeout = vi.spyOn(AbortSignal, "timeout");
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ ok: true, request_timeout_seconds: 90 })),
  );
  expect(requestTimeout()).toBe(DEFAULT_REQUEST_TIMEOUT_MS);
  await request("/health", new AbortController().signal);
  expect(requestTimeout()).toBe(90000);
  await request("/health", new AbortController().signal);
  expect(timeout).toHaveBeenLastCalledWith(90000);
  adoptRequestTimeout(undefined);
  adoptRequestTimeout(0);
  expect(requestTimeout()).toBe(90000);
  adoptRequestTimeout(DEFAULT_REQUEST_TIMEOUT_MS / 1000);
});
