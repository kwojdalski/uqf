import { expect, it } from "vitest";
import { filterValue, validateRange, type Column } from "./api";
const column = (type: string): Column => ({
  name: "value",
  type,
  filterable: true,
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
