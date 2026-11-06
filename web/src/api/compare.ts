// /compare (#2033) - models/compare.py.

import type { QueryFilter } from "./query";

/** POST /compare's body (#2033): two explicit sides, an explicit window,
 *  filters as /query takes them (#2223) and the rows read per side. */
export interface CompareBody {
  left: string;
  right: string;
  tables: string[];
  range_from: string;
  range_to: string;
  filters: QueryFilter[];
  limit: number;
  source_version?: string | null;
  slice?: string | null;
}
/** The outcomes q reaches (.qetl.compare.outcomes), first that applies. */
export type CompareOutcome =
  | "endpoint_unavailable"
  | "missing_table"
  | "differs"
  | "not_comparable"
  | "incomplete_coverage"
  | "equal_in_sample";
export interface CompareInterval {
  range_from: string;
  range_to: string;
}
export interface CompareCoverageSide {
  checked: boolean;
  covered: CompareInterval[];
  gaps: CompareInterval[];
}
export interface CompareSchemaDifference {
  column: string;
  left: string;
  right: string;
}
export interface CompareRowDifference {
  rowkey: string;
  kind: string;
  detail: string;
}
/** One table's verdict, as q decided it: shown, never recomputed. */
export interface CompareTable {
  table: string;
  outcome: CompareOutcome;
  label: string;
  truncated: boolean;
  key: string[];
  basis: string;
  read: number[];
  schema: CompareSchemaDifference[];
  rows: CompareRowDifference[];
  unpaired: number;
  coverage: Record<string, CompareCoverageSide>;
  reasons: Record<string, string>;
}
/** POST /compare: `uqs data diff --json`. */
export interface CompareResult {
  ok: boolean;
  outcome: CompareOutcome;
  left: string;
  right: string;
  range_from: string;
  range_to: string;
  limit: number;
  /** The filters as they were sent. */
  filters: QueryFilter[];
  /** null when no coverage was compared; slice "" is the no-slice-dimension slice. */
  source_version: string | null;
  slice: string | null;
  tables: CompareTable[];
}
