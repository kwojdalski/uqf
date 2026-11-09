// /query, /coverage and /catalog, and the filter values the Desk sends.

import type { Pollable, Row } from "./client";

/** The column types the server reports: catalog.QType, held to this list by
 *  test_api_types.py. A new type needs a branch in filterValue. */
export type ColumnType =
  | "symbol"
  | "timestamp"
  | "timespan"
  | "float"
  | "long"
  | "int"
  | "short"
  | "boolean"
  | "date"
  | "time"
  | "char"
  | "guid"
  | "list";
export interface Column {
  name: string;
  type: ColumnType;
  filterable: boolean;
  /** Decimal places this column is shown with, resolved server-side from the
   * column, then its table, then its type. Null means show it as it arrives. */
  decimals: number | null;
}
export interface Catalog {
  tables: { name: string; description: string; columns: Column[] }[];
  operators: string[];
}
export interface CoverageRequest {
  dataset: string;
  // The coverage slice to ask about. "" is the sentinel for a dataset with
  // no slice dimension - not "any slice". The gateway requires the field,
  // because a coverage read that omits it aggregates across every slice and
  // reports a range published for one symbol as covered for all of them
  // (#185). `slice`, as `uqs data diff --slice`: a partition is a date (#2261).
  slice: string;
  source_version: string;
  range_from: string;
  range_to: string;
}
export interface Interval {
  range_from: string;
  range_to: string;
}
export interface Coverage extends Pollable {
  dataset: string;
  source_version: string;
  covered: Interval[];
  requested: Interval | null;
  gaps: Interval[];
  complete: boolean;
}
/** Where a query reads - models.Tier, held to it by test_api_types.py. */
export type Tier = "rdb" | "hdb" | "both";
/** A filter operator - models.Filter.op, held to it by test_api_types.py. */
export type FilterOp = "eq" | "ne" | "lt" | "le" | "gt" | "ge" | "in";
/** One predicate of a query - models.Filter. The request bodies below are
 * held to their models field for field by test_api_types.py, and the server
 * refuses a key it does not know (#1361): a renamed field is a failing test
 * and a 422, never a default the server quietly fills in. */
export interface QueryFilter {
  column: string;
  op: FilterOp;
  value: unknown;
}
export interface QueryInput {
  table: string;
  tier: Tier;
  limit: number;
  filters: QueryFilter[];
  require_coverage?: CoverageRequest;
}
export interface QueryResult extends Pollable {
  table: string;
  tier: Tier;
  rows: Row[];
  row_count: number;
  /** More rows existed than were returned, whatever cut them (#2220). */
  truncated: boolean;
  /** What cut them: the caller's `limit`, or a server row cap. */
  cut_by: "limit" | "max_rows" | null;
}

export function filterValue(
  raw: string,
  column: Column,
  operator: string,
): unknown {
  const scalar = (value: unknown): unknown => {
    if (["float", "long", "int", "short", "timespan"].includes(column.type)) {
      if (
        !["number", "string"].includes(typeof value) ||
        value === null ||
        String(value).trim() === ""
      )
        throw new Error("Enter a number.");
      const span =
        column.type === "timespan" ? qTimespan(String(value)) : undefined;
      const number = span ?? decimal(value as number | string);
      // A timespan is a whole number of nanoseconds (#1329): a fraction has
      // nowhere to go, and past 2^53 a JavaScript number is not the one typed.
      if (
        !Number.isFinite(number) ||
        (["long", "int", "short", "timespan"].includes(column.type) &&
          !Number.isSafeInteger(number))
      )
        throw new Error(
          column.type === "timespan"
            ? "Enter a whole number of nanoseconds, or a timespan like 0D00:00:01.5."
            : "Enter a valid number (a whole number for long, int and short columns).",
        );
      return number;
    }
    if (column.type === "boolean") {
      if (value === true || value === "true") return true;
      if (value === false || value === "false") return false;
      throw new Error("Enter true or false.");
    }
    if (typeof value !== "string") throw new Error("Enter a string value.");
    if (column.type === "timestamp") validateTime(value);
    if (column.type === "date" && !/^\d{4}-\d{2}-\d{2}$/.test(value))
      throw new Error("Use a date like 2026-09-16.");
    if (
      column.type === "time" &&
      !/^\d{2}:\d{2}:\d{2}(\.\d{1,3})?$/.test(value)
    )
      throw new Error(
        "Use a time like 14:30:00 or 14:30:00.250 (milliseconds).",
      );
    return value;
  };
  if (operator !== "in") return scalar(raw);
  const values: unknown = JSON.parse(raw);
  if (!Array.isArray(values) || values.length === 0)
    throw new Error('Use a non-empty JSON list, e.g. ["EURUSD", "GBPUSD"].');
  return values.map(scalar);
}
/** A decimal number as typed, or NaN. Not Number(): it reads `0x1F`,
 *  `0b101` and `0o17` as 31, 5 and 15, which q and the server would refuse
 *  as typed (#2587). */
function decimal(value: number | string): number {
  if (typeof value === "number") return value;
  return /^\s*-?\d+(\.\d+)?([eE][+-]?\d+)?\s*$/.test(value)
    ? Number(value)
    : Number.NaN;
}
/** A q timespan literal - 0D00:00:00.000001234, as the Desk shows one - as
 *  nanoseconds, or undefined when `value` is not one (a plain number of
 *  nanoseconds is read by the caller). Exact: summed as a BigInt. */
export function qTimespan(value: string): number | undefined {
  const parts = /^(-)?(\d+)D(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?$/.exec(
    value.trim(),
  );
  if (!parts) return undefined;
  const [, sign, days, hours, minutes, seconds, fraction] = parts;
  // q refuses 0D00:61:00 with 'parse; never read it as 61 minutes (#2587)
  if (Number(hours) > 23 || Number(minutes) > 59 || Number(seconds) > 59)
    return undefined;
  const ns =
    ((BigInt(days) * 24n + BigInt(hours)) * 60n + BigInt(minutes)) * 60n +
    BigInt(seconds);
  const total = ns * 1_000_000_000n + BigInt((fraction ?? "").padEnd(9, "0"));
  return Number(sign ? -total : total);
}
export function validateTime(value: string) {
  if (
    !/T.*(?:Z|[+-]\d{2}:\d{2})$/i.test(value) ||
    !Number.isFinite(Date.parse(value))
  )
    throw new Error(
      "Use an ISO timestamp with a timezone, e.g. 2026-09-16T00:00:00Z.",
    );
}
export function validateRange(value: CoverageRequest) {
  if (!value.dataset.trim() || !value.source_version.trim())
    throw new Error("Dataset and source version are required.");
  validateTime(value.range_from);
  validateTime(value.range_to);
  if (instant(value.range_from) >= instant(value.range_to))
    throw new Error("Range end must be after range start.");
}
/** An ISO instant as nanoseconds since 1970, exact: the server takes nine
 *  fractional digits (#1371), and Date.parse stops at the millisecond, so
 *  two bounds within one would read as an empty range. */
function instant(value: string): bigint {
  const parts = /^(.*?\d{2}:\d{2}:\d{2})(?:[.,](\d+))?(.*)$/.exec(value);
  const [, head, fraction, zone] = parts ?? [, value, "", ""];
  const fine = BigInt((fraction ?? "").padEnd(9, "0").slice(0, 9));
  return BigInt(Date.parse(head + zone)) * 1_000_000n + fine;
}
