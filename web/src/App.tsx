import {
  lazy,
  Suspense,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { mutate, type Row, writeToken } from "./api/client";
import type {
  BackfillStarted,
  BackfillBody,
  CommandResult,
  ControlProcess,
  ControlStatus,
  LifecycleBody,
  ProcessConfigBody,
  ProcessConfigResult,
  WorkerConfigBody,
  WorkerConfigResult,
} from "./api/control";
import type { Health } from "./api/health";
import type { Backfill, OpsData } from "./api/ops";
import {
  type Catalog,
  type Coverage,
  type CoverageRequest,
  type FilterOp,
  type QueryInput,
  type QueryResult,
  type Tier,
  filterValue,
  validateRange,
} from "./api/query";
import type { Services } from "./api/services";
import { FileBrowser } from "./FileBrowser";
import { PlotPanel } from "./PlotPanel";
import { ChartsView } from "./ChartsView";
import { CompareView } from "./CompareView";
import { FleetConnections, TunnelPanel } from "./TunnelPanel";
import { TargetLabel, WorkspaceSelector } from "./WorkspaceSelector";
import { useWorkspaceTarget } from "./workspace";
import { useResource } from "./useResource";
import { ActivityView } from "./ActivityView";
import {
  CapabilitiesProvider,
  InspectionBanner,
  useCapabilities,
} from "./capabilities";
// The form library is loaded only when the view is opened (#2046).
const SettingsView = lazy(() => import("./SettingsView"));
import {
  adoptDefaultLevel,
  installActivity,
  record,
  tracked,
  triggerOf,
} from "./activity";

const views = [
  "Coverage",
  "Backfills",
  "Desk",
  "Charts",
  "Compare",
  "Fleet",
  "Queue",
  "Connections",
  "Usage",
  "Services",
  "Control",
  "Activity",
  "Settings",
] as const;
type View = (typeof views)[number];
/** An ISO instant, as the server sends one: a date, a T, a time, and
 *  optionally a fractional part. Matched rather than parsed - Date drops
 *  everything past milliseconds, and these carry microseconds. */
const ISO_INSTANT =
  /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})?$/;
/** The column types that hold an infinity, and so the only ones whose
 *  "inf" / "-inf" is the server's infinity rather than a symbol that happens
 *  to be spelled that way (#1330) - a timestamp's 0Wp too (#1371), and a
 *  time's 0Wt and a date's 0Wd (#2037). */
const NUMERIC = new Set([
  "float",
  "long",
  "int",
  "short",
  "timespan",
  "timestamp",
  "time",
  "date",
]);
const INFINITY: Record<string, string> = { inf: "∞", "-inf": "-∞" };
function text(value: unknown, decimals?: number | null, type?: string) {
  if (value == null) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  // The server sends an infinite number as a string, because JSON has none
  // and would carry it as null - shown as missing, the one reading an
  // operator takes as harmless.
  if (
    type &&
    NUMERIC.has(type) &&
    typeof value === "string" &&
    value in INFINITY
  )
    return INFINITY[value];
  if (type === "timespan" && typeof value === "number")
    return timespan(value, decimals ?? 9);
  if (decimals == null) return String(value);
  if (typeof value === "number") {
    return Number.isFinite(value) ? value.toFixed(decimals) : String(value);
  }
  if (typeof value === "string") {
    const parts = ISO_INSTANT.exec(value);
    // Cut or pad the fractional seconds rather than reformatting the whole
    // instant: the server's own rendering of the date and zone is the one
    // the operator sees elsewhere, and a round trip through Date would move
    // it into the browser's timezone.
    if (parts) {
      const fraction = (parts[2] ?? "")
        .padEnd(decimals, "0")
        .slice(0, decimals);
      return parts[1] + (decimals > 0 ? "." + fraction : "") + (parts[3] ?? "");
    }
  }
  return String(value);
}
/** A whole number of nanoseconds as q writes a timespan - 0D00:00:00.000 -
 *  with its fraction cut to `decimals`, as an instant's is. The server sends
 *  nanoseconds rather than a duration string because a Python timedelta
 *  stops at the microsecond (#1329), and this is the form a timespan filter
 *  takes back. */
export function timespan(nanoseconds: number, decimals: number) {
  const sign = nanoseconds < 0 ? "-" : "";
  const ns = BigInt(Math.abs(Math.trunc(nanoseconds)));
  const second = 1_000_000_000n;
  const seconds = ns / second;
  const two = (n: bigint) => String(n).padStart(2, "0");
  const fraction = String(ns % second)
    .padStart(9, "0")
    .slice(0, decimals);
  return (
    sign +
    String(seconds / 86400n) +
    "D" +
    two((seconds / 3600n) % 24n) +
    ":" +
    two((seconds / 60n) % 60n) +
    ":" +
    two(seconds % 60n) +
    (decimals > 0 ? "." + fraction : "")
  );
}
/** Column name -> q type for one queried table, from the catalog: what tells
 *  a timespan's nanoseconds from a long, and an infinity from a symbol. */
function typesFor(catalog: Catalog | undefined, table: string) {
  const entry = catalog?.tables.find((item) => item.name === table);
  if (!entry) return undefined;
  return Object.fromEntries(
    entry.columns.map((column) => [column.name, column.type]),
  );
}
/** Column name -> decimal places for one queried table, from the catalog the
 *  server published. Keyed by the table the RESULT names rather than the one
 *  currently selected in the panel: a result on screen belongs to the query
 *  that produced it, and picking a different table must not reformat it
 *  before its own rows arrive. */
function decimalsFor(catalog: Catalog | undefined, table: string) {
  const entry = catalog?.tables.find((item) => item.name === table);
  if (!entry) return undefined;
  return Object.fromEntries(
    entry.columns.map((column) => [column.name, column.decimals]),
  );
}
function Badge({ value }: { value: string }) {
  return (
    <span className={`badge ${value.toLowerCase().replaceAll(" ", "-")}`}>
      {value}
    </span>
  );
}
export function Table({
  rows,
  empty = "No records returned.",
  decimals,
  types,
}: {
  rows: Row[];
  empty?: string;
  /** Column name -> q type, from the catalog. Without it a timespan shows
   *  as its nanoseconds and an infinity as "inf". */
  types?: Record<string, string>;
  /** Column name -> decimal places, from the catalog. A table with no entry
   *  for a column shows it as it arrives, which is what every operational
   *  table here wants: those are counts, states and process names. */
  decimals?: Record<string, number | null>;
}) {
  if (!rows.length) return <p className="empty">{empty}</p>;
  const columns = [...new Set(rows.flatMap((row) => Object.keys(row)))];
  return (
    <div className="table-scroll" tabIndex={0} aria-label="Results table">
      <table>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column}>{column.replaceAll("_", " ")}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index}>
              {columns.map((column) => (
                <td key={column}>
                  {column === "state" ? (
                    <Badge value={text(row[column])} />
                  ) : (
                    text(row[column], decimals?.[column], types?.[column])
                  )}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
function Summary({ values }: { values: Record<string, number> }) {
  return (
    <dl className="summary">
      {Object.entries(values).map(([key, value]) => (
        <div key={key}>
          <dt>{key.replaceAll("_", " ")}</dt>
          <dd>{value.toLocaleString()}</dd>
        </div>
      ))}
    </dl>
  );
}
function ResourceStatus<T>({
  resource,
}: {
  resource: ReturnType<typeof useResource<T>>;
}) {
  return (
    <>
      <div className="refresh">
        <span role="status">
          {resource.loading
            ? "Refreshing…"
            : resource.updated
              ? `Updated ${resource.updated.toLocaleTimeString()}`
              : "Waiting for a response"}
          {resource.cadence && !resource.paused
            ? ` · Every ${resource.cadence}s`
            : ""}
        </span>
        <TargetLabel target={resource.target} />
        <button
          type="button"
          data-activity="resource.refresh"
          onClick={resource.refresh}
        >
          Refresh
        </button>
        <button
          type="button"
          data-activity="resource.pause"
          aria-pressed={resource.paused}
          onClick={() => resource.setPaused(!resource.paused)}
        >
          {resource.paused ? "Resume polling" : "Pause polling"}
        </button>
      </div>
      {resource.error && (
        <div
          className={`notice ${resource.error.transient ? "pending" : "error"}`}
          role={resource.error.transient ? "status" : "alert"}
        >
          <strong>
            {resource.error.transient
              ? "Temporarily unavailable"
              : resource.error.error === "CoverageIncomplete"
                ? "Coverage incomplete"
                : "Request failed"}
          </strong>
          <p>{resource.error.message}</p>
          {resource.error.transient && !resource.paused && (
            <p>Retrying automatically.</p>
          )}
          {resource.data && (
            <p>Showing the last successful result. It may be out of date.</p>
          )}
        </div>
      )}
    </>
  );
}
function initialRange(): CoverageRequest {
  const end = new Date();
  end.setUTCHours(0, 0, 0, 0);
  const start = new Date(end);
  start.setUTCDate(start.getUTCDate() - 1);
  return {
    dataset: "trades",
    slice: "",
    source_version: "",
    range_from: start.toISOString(),
    range_to: end.toISOString(),
  };
}
function RangeFields({
  value,
  onChange,
}: {
  value: CoverageRequest;
  onChange: (value: CoverageRequest) => void;
}) {
  return (
    <div className="fields">
      {(
        [
          ["dataset", "Dataset"],
          ["slice", "Slice (blank = whole dataset)"],
          ["source_version", "Source version"],
          ["range_from", "From (inclusive, timezone required)"],
          ["range_to", "To (exclusive, timezone required)"],
        ] as const
      ).map(([key, label]) => (
        <label key={key}>
          {label}
          <input
            data-activity={`range.${key}`}
            // Every field but `slice` must be non-empty. Blank IS the
            // slice's meaningful value - the sentinel for a dataset with no
            // slice dimension - so marking it required would make the
            // commonest case unsubmittable.
            required={key !== "slice"}
            value={value[key]}
            placeholder={
              key === "source_version"
                ? "e.g. v1"
                : key === "slice"
                  ? "e.g. EURUSD"
                  : undefined
            }
            onChange={(event) =>
              onChange({ ...value, [key]: event.target.value })
            }
          />
        </label>
      ))}
    </div>
  );
}
function CoverageView() {
  const [range, setRange] = useState(initialRange);
  const [applied, setApplied] = useState<CoverageRequest | null>(null);
  const [error, setError] = useState("");
  const resource = useResource<Coverage>(
    applied ? `/coverage?${new URLSearchParams({ ...applied })}` : null,
  );
  function submit(event: FormEvent) {
    event.preventDefault();
    try {
      validateRange(range);
      setApplied({ ...range });
      setError("");
      resource.refresh();
      record("submit", "coverage.check", {
        subject: range.dataset,
        detail: {
          dataset: range.dataset,
          source_version: range.source_version,
          range_from: range.range_from,
          range_to: range.range_to,
        },
      });
    } catch (error) {
      setError((error as Error).message);
      record("submit", "coverage.check", {
        outcome: "refused",
        detail: { reason: "invalid input" },
      });
    }
  }
  return (
    <>
      <form className="panel" onSubmit={submit}>
        <RangeFields value={range} onChange={setRange} />
        <div className="form-footer">
          <span>Half-open intervals · [from, to)</span>
          <button className="primary" data-activity="coverage.check">
            Check coverage
          </button>
        </div>
        {error && (
          <p role="alert" className="error-text">
            {error}
          </p>
        )}
      </form>
      <p className="note">
        Coverage uses the local ledger contract. The upstream schema is still
        awaiting verification (#60).
      </p>
      {!applied ? (
        <div className="empty">
          Choose a dataset, source version and time range to inspect publication
          gaps.
        </div>
      ) : (
        <>
          <ResourceStatus resource={resource} />
          {resource.data && (
            <section className="panel">
              <div className="section-heading">
                <h2>
                  {resource.data.dataset}{" "}
                  <span className="muted">
                    / {resource.data.source_version}
                  </span>
                </h2>
                <Badge
                  value={resource.data.complete ? "Complete" : "Gaps found"}
                />
              </div>
              {resource.data.requested && (
                <p className="mono">
                  [{resource.data.requested.range_from},{" "}
                  {resource.data.requested.range_to})
                </p>
              )}
              <h3>Missing intervals</h3>
              <Table
                rows={resource.data.gaps.map((row) => ({ ...row }))}
                empty="No gaps in the requested range."
              />
              <h3>Published intervals</h3>
              <Table
                rows={resource.data.covered.map((row) => ({ ...row }))}
                empty="No coverage recorded for this source version."
              />
            </section>
          )}
        </>
      )}
    </>
  );
}
function BackfillView() {
  const resource = useResource<Backfill>("/ops/backfill");
  return (
    <>
      <ResourceStatus resource={resource} />
      {resource.data && (
        <>
          <Summary values={resource.data.summary} />
          <section className="panel">
            <h2>Worker runs</h2>
            {!resource.data.source ? (
              <p className="notice pending">
                No status directory configured. Set UQF_FRONTEND_STACK_ROOT on
                the API.
              </p>
            ) : (
              <p className="note">Source: {resource.data.source}</p>
            )}
            <p className="note">
              Idle means the run succeeded with no work. Failed runs require
              attention.
            </p>
            <Table
              rows={resource.data.workers.map((worker) => ({
                ...worker,
                outcome: worker.abandoned
                  ? "Abandoned · process gone, no outcome recorded"
                  : worker.state === "idle"
                    ? "Success · no work"
                    : worker.state === "completed"
                      ? "Success · work completed"
                      : worker.state === "failed"
                        ? worker.reactions_owed > 0
                          ? `Failure · ${worker.reactions_owed} reaction(s) owed, a derived dataset is stale`
                          : "Failure"
                        : "In progress",
              }))}
              empty="No worker status files found."
            />
            {!!resource.data.unreadable.length && (
              <>
                <h3>Unreadable status files</h3>
                <Table rows={resource.data.unreadable} />
              </>
            )}
          </section>
        </>
      )}
    </>
  );
}
/** The Desk's row limit bounds, as its field declares them. */
const MAX_ROW_LIMIT = 10000;
/** How long the Row limit field must stay unchanged before it re-queries. */
const LIMIT_SETTLE_MS = 400;
/** The row limit `text` asks for, or null when it is not one to send. */
function rowLimit(text: string): number | null {
  if (!/^\s*\d+\s*$/.test(text)) return null;
  const value = Number(text);
  return value >= 1 && value <= MAX_ROW_LIMIT ? value : null;
}
function DeskView() {
  const catalog = useResource<Catalog>("/catalog", undefined, false);
  const [table, setTable] = useState("");
  const [tier, setTier] = useState<Tier>("rdb");
  const [limit, setLimit] = useState(100);
  // What the Row limit field holds. It becomes `limit`, which re-runs the
  // query, only once it is a whole number in range and has stopped changing:
  // not 0 while the field is cleared, not 5, 50 and 500 on the way to 5000
  // (#2017).
  const [limitText, setLimitText] = useState("100");
  const typedLimit = rowLimit(limitText);
  useEffect(() => {
    if (typedLimit === null) return;
    const timer = setTimeout(() => setLimit(typedLimit), LIMIT_SETTLE_MS);
    return () => clearTimeout(timer);
  }, [typedLimit]);
  const [filters, setFilters] = useState<
    { column: string; op: FilterOp; raw: string }[]
  >([]);
  const [coverage, setCoverage] = useState(initialRange);
  const [requireCoverage, setRequireCoverage] = useState(false);
  const [applied, setApplied] = useState<QueryInput | null>(null);
  const [error, setError] = useState("");
  const [display, setDisplay] = useState<"Table" | "Plot">("Table");
  const selected =
    catalog.data?.tables.find((item) => item.name === table) ??
    catalog.data?.tables[0];
  const columns = selected?.columns.filter((column) => column.filterable) ?? [];
  const resource = useResource<QueryResult>(
    applied ? "/query" : null,
    applied ? JSON.stringify(applied) : undefined,
  );
  // A glimpse, not a form: choosing a table (or a tier) shows its first rows
  // at once, unfiltered. Filters are the one input that cannot sensibly run
  // on every keystroke, so they stay behind an explicit Apply below.
  const selectedName = selected?.name;
  useEffect(() => {
    if (!selectedName) return;
    setFilters([]);
    setTier("rdb");
    setLimit(100);
    setLimitText("100");
    setApplied({ table: selectedName, tier: "rdb", limit: 100, filters: [] });
    setError("");
  }, [selectedName]); // a new table is a fresh glimpse: tier and limit reset too
  // Tier and limit re-run what is applied, filters included: they narrow
  // the same question, and must not throw away a filter someone typed.
  useEffect(() => {
    setApplied((prev) => (prev ? { ...prev, tier, limit } : prev));
  }, [tier, limit]);
  function submit(event: FormEvent) {
    event.preventDefault();
    if (!selected) return;
    try {
      if (typedLimit === null)
        throw new Error(
          `The row limit must be a whole number from 1 to ${MAX_ROW_LIMIT}.`,
        );
      const limit = typedLimit;
      setLimit(limit);
      if (requireCoverage) validateRange(coverage);
      const parsed = filters.map((filter) => {
        const column = columns.find((column) => column.name === filter.column);
        if (!column) throw new Error("Choose a filter column.");
        return {
          column: column.name,
          op: filter.op,
          value: filterValue(filter.raw, column, filter.op),
        };
      });
      setApplied({
        table: selected.name,
        tier,
        limit,
        filters: parsed,
        ...(requireCoverage ? { require_coverage: { ...coverage } } : {}),
      });
      setError("");
      resource.refresh();
      // How many filters, never their values: those are whatever was typed.
      record("submit", "desk.apply", {
        subject: selected.name,
        detail: {
          tier,
          limit,
          filter_count: parsed.length,
          require_coverage: requireCoverage,
        },
      });
    } catch (error) {
      setError((error as Error).message);
      record("submit", "desk.apply", {
        subject: selected.name,
        outcome: "refused",
        detail: { reason: "invalid input" },
      });
    }
  }
  return (
    <>
      {catalog.error && (
        <div role="alert" className="notice error">
          {catalog.error.message}{" "}
          <button data-activity="desk.catalog-retry" onClick={catalog.refresh}>
            Retry catalog
          </button>
        </div>
      )}
      <div className="desk">
        <nav className="panel table-list" aria-label="Published tables">
          <h2>Tables</h2>
          {!catalog.data && <p className="note">Loading the query catalog…</p>}
          {catalog.data?.tables.map((item) => (
            <button
              key={item.name}
              type="button"
              className="table-choice"
              data-activity="desk.table"
              aria-pressed={item.name === selected?.name}
              title={item.description}
              onClick={(event) => {
                if (item.name !== selected?.name)
                  record("select", "desk.table", {
                    subject: item.name,
                    trigger: triggerOf(event),
                  });
                setTable(item.name);
              }}
            >
              <code>{item.name}</code>
            </button>
          ))}
        </nav>
        <div className="desk-main">
          {applied && (
            <>
              <ResourceStatus resource={resource} />
              {resource.data && (
                <section className="panel">
                  <div className="section-heading">
                    <h2>{resource.data.table}</h2>
                    <Badge value={resource.data.tier.toUpperCase()} />
                    <div className="toggle" role="group" aria-label="Show as">
                      {(["Table", "Plot"] as const).map((item) => (
                        <button
                          key={item}
                          type="button"
                          data-activity={`desk.show.${item.toLowerCase()}`}
                          aria-pressed={display === item}
                          onClick={() => setDisplay(item)}
                        >
                          {item}
                        </button>
                      ))}
                    </div>
                  </div>
                  <p className="note">
                    {selected?.description}
                    {selected?.description ? " · " : ""}
                    {resource.data.row_count.toLocaleString()} rows returned
                    {resource.data.truncated &&
                      (resource.data.cut_by === "max_rows"
                        ? " · Server row cap reached; more rows exist"
                        : " · Showing the first " +
                          resource.data.row_count.toLocaleString() +
                          "; more rows exist")}
                  </p>
                  {display === "Plot" ? (
                    <PlotPanel
                      result={resource.data}
                      types={typesFor(catalog.data, resource.data.table)}
                    />
                  ) : (
                    <Table
                      rows={resource.data.rows}
                      empty="No rows match this query."
                      decimals={decimalsFor(catalog.data, resource.data.table)}
                      types={typesFor(catalog.data, resource.data.table)}
                    />
                  )}
                </section>
              )}
            </>
          )}
          <details className="panel">
            <summary data-activity="desk.refine">
              Refine · tier, row limit, filters, coverage
            </summary>
            <form onSubmit={submit}>
              <div className="fields">
                <label>
                  Storage tier
                  <select
                    data-activity="desk.tier"
                    value={tier}
                    onChange={(event) => {
                      setTier(event.target.value as Tier);
                      record("select", "desk.tier", {
                        subject: selected?.name,
                        detail: { tier: event.target.value },
                      });
                    }}
                  >
                    <option value="rdb">RDB · current session</option>
                    <option value="hdb">HDB · completed partitions</option>
                    <option value="both">Both tiers</option>
                  </select>
                </label>
                <label>
                  Row limit
                  <input
                    data-activity="desk.limit"
                    type="number"
                    min="1"
                    max={MAX_ROW_LIMIT}
                    required
                    value={limitText}
                    aria-invalid={typedLimit === null}
                    onChange={(event) => setLimitText(event.target.value)}
                  />
                </label>
              </div>
              {tier !== "rdb" && (
                <p className="note">
                  Historical queries can take longer. A reload window is shown
                  as temporary unavailability.
                </p>
              )}
              <div className="section-heading">
                <h3>Filters</h3>
                <button
                  type="button"
                  data-activity="desk.filter.add"
                  disabled={!columns.length || filters.length >= 16}
                  onClick={() =>
                    setFilters([
                      ...filters,
                      { column: columns[0].name, op: "eq", raw: "" },
                    ])
                  }
                >
                  Add filter
                </button>
              </div>
              {filters.map((filter, index) => (
                <div className="filter" key={index}>
                  <label>
                    Column
                    <select
                      data-activity="desk.filter.column"
                      value={filter.column}
                      onChange={(event) =>
                        setFilters(
                          filters.map((item, i) =>
                            i === index
                              ? { ...item, column: event.target.value, raw: "" }
                              : item,
                          ),
                        )
                      }
                    >
                      {columns.map((column) => (
                        <option key={column.name} value={column.name}>
                          {column.name} · {column.type}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Operator
                    <select
                      data-activity="desk.filter.op"
                      value={filter.op}
                      onChange={(event) =>
                        setFilters(
                          filters.map((item, i) =>
                            i === index
                              ? {
                                  ...item,
                                  // the options are the catalog's operators,
                                  // which the server derives from Filter.op
                                  op: event.target.value as FilterOp,
                                }
                              : item,
                          ),
                        )
                      }
                    >
                      {catalog.data?.operators.map((op) => (
                        <option key={op}>{op}</option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Value{filter.op === "in" ? " (JSON list)" : ""}
                    <input
                      data-activity="desk.filter.value"
                      required
                      value={filter.raw}
                      onChange={(event) =>
                        setFilters(
                          filters.map((item, i) =>
                            i === index
                              ? { ...item, raw: event.target.value }
                              : item,
                          ),
                        )
                      }
                    />
                  </label>
                  <button
                    type="button"
                    data-activity="desk.filter.remove"
                    aria-label={`Remove filter ${index + 1}`}
                    onClick={() =>
                      setFilters(filters.filter((_, i) => i !== index))
                    }
                  >
                    Remove
                  </button>
                </div>
              ))}
              <label className="checkbox">
                <input
                  type="checkbox"
                  data-activity="desk.require-coverage"
                  checked={requireCoverage}
                  onChange={(event) => setRequireCoverage(event.target.checked)}
                />
                Require complete publication coverage before querying
              </label>
              {requireCoverage && (
                <RangeFields value={coverage} onChange={setCoverage} />
              )}
              <div className="form-footer">
                <span>Read-only · filters validated by the API</span>
                <button
                  disabled={!selected}
                  className="primary"
                  data-activity="desk.apply"
                >
                  Apply filters
                </button>
              </div>
              {error && (
                <p role="alert" className="error-text">
                  {error}
                </p>
              )}
            </form>
          </details>
        </div>
      </div>
    </>
  );
}
/** The deployment targets `uqs target list` lists (#1949): read by the API
 *  from deploy_targets.toml, so the server is never contacted to list one. */
function ServicesView() {
  const resource = useResource<Services>("/services");
  const data = resource.data;
  // Services reads follow the workspace (#2029): a selected target is the
  // one browsed and planned, until the operator picks another here.
  const scoped = useWorkspaceTarget();
  const [browsing, setBrowsing] = useState<string | null>(scoped);
  const [tunnelling, setTunnelling] = useState<string | null>(scoped);
  const [followed, setFollowed] = useState(scoped);
  if (followed !== scoped) {
    setFollowed(scoped);
    setBrowsing(scoped);
    setTunnelling(scoped);
  }
  return (
    <>
      <ResourceStatus resource={resource} />
      {!data && resource.loading && (
        <p className="empty">Loading deployment targets…</p>
      )}
      {data && (
        <section className="panel">
          <div className="section-heading">
            <h2>Deployment targets</h2>
            <Badge
              value={
                data.writes_enabled
                  ? "Remote operations enabled"
                  : "Remote operations disabled"
              }
            />
          </div>
          <p className="note">Source: {data.source}</p>
          {data.invalid ? (
            <div className="notice error" role="alert">
              <strong>Invalid deployment configuration</strong>
              <p>{data.invalid}</p>
              <p>
                Fix the file - <code>uqs target edit</code> opens it - then
                refresh.
              </p>
            </div>
          ) : !data.declared ? (
            <p className="empty">
              No deployment targets declared. Add one with{" "}
              <code>uqs target add</code>, or <code>uqs deploy push</code> to a
              server.
            </p>
          ) : (
            <Table
              rows={data.targets.map((target) => ({
                target: target.target,
                ssh: target.ssh,
                dest: target.dest,
                environment: target.environment,
                remote_user: target.remote_user,
                ...(data.targets.some((t) => t.inspection)
                  ? { inspection: target.inspection ? "inspection mode" : null }
                  : {}),
                labels:
                  Object.entries(target.labels)
                    .map(([key, value]) => `${key}=${value}`)
                    .join(", ") || null,
                last_deployed: target.last_deployed
                  ? `${target.last_deployed.release} (${target.last_deployed.at})`
                  : null,
              }))}
              empty="The file declares no targets."
            />
          )}
          <p className="note">
            Listed from the file and this machine&apos;s deploy history - no
            server is contacted.
          </p>
          {data.targets.length > 0 &&
            (data.writes_enabled ? (
              <div className="actions" aria-label="Browse files">
                {data.targets.map((target) => (
                  <button
                    type="button"
                    data-activity="services.browse"
                    key={target.target}
                    aria-pressed={browsing === target.target}
                    onClick={() => setBrowsing(target.target)}
                  >
                    Browse {target.target}
                  </button>
                ))}
                {data.targets.map((target) => (
                  <button
                    type="button"
                    data-activity="services.tunnel"
                    key={`tunnel:${target.target}`}
                    aria-pressed={tunnelling === target.target}
                    onClick={() => setTunnelling(target.target)}
                  >
                    Tunnel {target.target}
                  </button>
                ))}
              </div>
            ) : (
              <p className="note">
                Browsing a target&apos;s files reaches it over ssh, so it needs
                remote operations enabled.
              </p>
            ))}
        </section>
      )}
      {browsing &&
        data?.writes_enabled &&
        data.targets.some((target) => target.target === browsing) && (
          <FileBrowser
            key={browsing}
            target={browsing}
            onClose={() => setBrowsing(null)}
          />
        )}
      {tunnelling &&
        data?.writes_enabled &&
        data.targets.some((target) => target.target === tunnelling) && (
          <TunnelPanel
            key={tunnelling}
            target={tunnelling}
            onClose={() => setTunnelling(null)}
          />
        )}
    </>
  );
}
function OpsView({
  view,
}: {
  view: "Fleet" | "Queue" | "Connections" | "Usage";
}) {
  const paths = {
    Fleet: "processes",
    Queue: "queue",
    Connections: "connections",
    Usage: "usage",
  };
  const resource = useResource<OpsData>(`/ops/${paths[view]}`);
  const data = resource.data;
  return (
    <>
      <ResourceStatus resource={resource} />
      {data && (
        <>
          {data.summary && <Summary values={data.summary} />}
          <section className="panel">
            <h2>{view}</h2>
            {data.truncated && data.rows && (
              <p className="notice pending" role="status">
                Showing the newest {data.rows.length.toLocaleString()}; more
                rows exist.
              </p>
            )}
            {data.processes_configured === 0 && (
              <p className="notice pending">
                No processes configured for usage collection. This is not an
                idle fleet.
              </p>
            )}
            {data.processes && (
              <Table
                rows={data.processes.map((process) => ({
                  state: process.port_unresolved
                    ? "Undetermined"
                    : process.identity_mismatch
                      ? "Identity mismatch"
                      : process.up
                        ? "Up"
                        : process.start_with_all
                          ? "Down"
                          : "Not started (optional)",
                  ...process,
                }))}
                empty="No processes declared."
              />
            )}
            {data.rows && (
              <Table
                rows={data.rows}
                empty={
                  view === "Queue"
                    ? "No pending or running queries."
                    : "No query history returned."
                }
              />
            )}
            {data.servers && (
              <>
                <h3>Backend servers</h3>
                <Table
                  rows={data.servers}
                  empty="No backend servers registered."
                />
                <h3>Connected clients</h3>
                <Table
                  rows={data.clients ?? []}
                  empty="No clients connected."
                />
              </>
            )}
            {!!data.unreachable?.length && (
              <>
                <h3>Unreachable processes</h3>
                <Table rows={data.unreachable} />
              </>
            )}
          </section>
        </>
      )}
      {view === "Fleet" && <FleetConnections />}
    </>
  );
}
/** torq.sh's selector for a chosen set: "all", or the names joined by
 * spaces. "all" is sent as the literal word rather than every name spelled
 * out, so what torq.sh does with it - startwithall=1 only - stays torq.sh's
 * decision and matches `uqs start all`.
 */
export function selectorFor(
  mode: "all" | "pick",
  picked: ReadonlySet<string>,
): string {
  return mode === "all" ? "all" : [...picked].join(" ");
}

/** Choose which processes a lifecycle action applies to.
 *
 * A closed list rather than a free-text selector: the names come from the
 * API, which reads the orchestrator's own effective process.csv, so a name
 * cannot be mistyped and learned about from torq.sh's exit code. Grouped by
 * proctype, because "restart every feed" is a thing an operator does and a
 * flat list of thirty names is not how they think of it.
 *
 * Liveness comes from /ops/processes when the API has it configured. It is
 * a hint, not a precondition - the picker works without it.
 */
function ProcessPicker({
  processes,
  mode,
  picked,
  onChange,
  liveness,
  disabled = false,
}: {
  processes: ControlProcess[];
  mode: "all" | "pick";
  picked: ReadonlySet<string>;
  onChange: (mode: "all" | "pick", picked: ReadonlySet<string>) => void;
  liveness: Map<string, boolean> | null;
  /** Frozen while a stop or restart awaits confirmation (#2009). */
  disabled?: boolean;
}) {
  const groups = new Map<string, ControlProcess[]>();
  for (const p of processes) {
    const list = groups.get(p.proctype) ?? [];
    list.push(p);
    groups.set(p.proctype, list);
  }
  const pick = (names: string[], on: boolean) => {
    const next = new Set(picked);
    for (const n of names)
      if (on) next.add(n);
      else next.delete(n);
    onChange("pick", next);
  };
  const startedByAll = processes
    .filter((p) => p.start_with_all)
    .map((p) => p.process);
  const count = mode === "all" ? startedByAll.length : picked.size;

  return (
    <fieldset className="picker" disabled={disabled}>
      <legend>
        Processes
        <span className="muted">
          {" "}
          {count} selected
          {mode === "all" ? " (torq.sh “all”: startwithall=1)" : ""}
        </span>
      </legend>
      <div className="picker-quick">
        <button
          type="button"
          className={mode === "all" ? "chip active" : "chip"}
          data-activity="control.pick.all"
          onClick={() => onChange("all", picked)}
        >
          all
        </button>
        <button
          type="button"
          className="chip"
          data-activity="control.pick.startwithall"
          onClick={() => onChange("pick", new Set(startedByAll))}
        >
          startwithall only
        </button>
        {liveness && (
          <>
            <button
              type="button"
              className="chip"
              data-activity="control.pick.down"
              onClick={() =>
                onChange(
                  "pick",
                  new Set(
                    processes
                      .filter((p) => liveness.get(p.process) === false)
                      .map((p) => p.process),
                  ),
                )
              }
            >
              down only
            </button>
            <button
              type="button"
              className="chip"
              data-activity="control.pick.up"
              onClick={() =>
                onChange(
                  "pick",
                  new Set(
                    processes
                      .filter((p) => liveness.get(p.process) === true)
                      .map((p) => p.process),
                  ),
                )
              }
            >
              up only
            </button>
          </>
        )}
        <button
          type="button"
          className="chip"
          data-activity="control.pick.none"
          onClick={() => onChange("pick", new Set())}
        >
          none
        </button>
      </div>
      <div className="picker-groups">
        {[...groups.entries()].map(([proctype, list]) => {
          const names = list.map((p) => p.process);
          const chosen = names.filter((n) => picked.has(n)).length;
          return (
            <div key={proctype} className="picker-group">
              <label className="picker-row picker-head">
                <input
                  type="checkbox"
                  data-activity="control.pick.type"
                  aria-label={`every ${proctype}`}
                  checked={mode === "pick" && chosen === names.length}
                  ref={(el) => {
                    if (el)
                      el.indeterminate =
                        mode === "pick" && chosen > 0 && chosen < names.length;
                  }}
                  onChange={(e) => pick(names, e.target.checked)}
                />
                <span className="picker-type">{proctype}</span>
                <span className="muted">{names.length}</span>
              </label>
              {list.map((p) => {
                const up = liveness?.get(p.process);
                return (
                  <label key={p.process} className="picker-row">
                    <input
                      type="checkbox"
                      data-activity="control.pick.process"
                      checked={
                        mode === "all"
                          ? p.start_with_all
                          : picked.has(p.process)
                      }
                      disabled={mode === "all"}
                      onChange={(e) => pick([p.process], e.target.checked)}
                    />
                    <code>{p.process}</code>
                    {up !== undefined && (
                      <span className={up ? "badge up" : "badge down"}>
                        {up ? "up" : "down"}
                      </span>
                    )}
                    {!p.start_with_all && (
                      <span
                        className="muted"
                        title="startwithall=0: not part of “all”"
                      >
                        manual
                      </span>
                    )}
                  </label>
                );
              })}
            </div>
          );
        })}
      </div>
    </fieldset>
  );
}

/** The only view that CHANGES anything.
 *
 * It asks `/control` first and renders nothing actionable when writes are
 * off - discovering that by pressing "Stop all" and reading a 403 would mean
 * having already tried to stop the fleet.
 */
/**
 * What setting a worker-config key achieved, in words. An override is not the
 * top layer: an environment variable outranks it, and the response used to
 * read as a success even then (#634). "Saved" is said only when the override
 * is what the process now reads.
 */
export function workerConfigOutcome(r: WorkerConfigResult): string {
  if (r.shadowed) {
    return (
      `Not in effect: ${r.key} was set, but ${r.env_var} in the environment wins ` +
      `(${r.env_var}=${r.effective_value ?? ""}). The override applies only once that variable is unset.`
    );
  }
  if (r.saved) {
    return `Saved: ${r.key} = ${r.value}, and the process now reads it.`;
  }
  return (
    `Set ${r.key}, but the process reports its value from ` +
    `${r.effective_layer ?? "an answer this page does not recognise"}, not the override.`
  );
}

/** Lifecycle actions that take something down: each asks to be confirmed,
 *  and the server refuses one sent without `confirmed: true`
 *  (models.CONFIRMED_LIFECYCLE, #2244). */
const DESTRUCTIVE = new Set(["stop", "restart"]);

/** The outcome a worker-config response supports (#634): set is not in effect. */
function workerConfigSettled(r: WorkerConfigResult) {
  return r.saved ? "succeeded" : r.shadowed ? "failed" : "unknown";
}

function ControlView() {
  const access = useCapabilities();
  const status = useResource<ControlStatus>("/control");
  const [busy, setBusy] = useState("");
  // The destructive action awaiting confirmation, if any, and the selection
  // it was opened for: Confirm acts on exactly that (#2009).
  const [confirming, setConfirming] = useState("");
  const [held, setHeld] = useState<{ procs: string; mode: "all" | "pick" }>({
    procs: "",
    mode: "all",
  });
  // The button that opened the confirmation. Focus goes back to it when the
  // confirmation closes, once it is enabled again (#2016).
  const opener = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {
    if (confirming || busy || !opener.current) return;
    const button = opener.current;
    opener.current = null;
    const focused = document.activeElement;
    if (!focused || focused === document.body) button.focus();
  }, [confirming, busy]);
  // A failed status poll keeps the view (#2008). useResource retries only a
  // transient failure, so a lasting one is retried here at the same cadence.
  useEffect(() => {
    if (!status.error || status.error.transient || status.paused) return;
    const timer = setTimeout(status.refresh, (status.cadence ?? 5) * 1000);
    return () => clearTimeout(timer);
  }, [status.error]);
  const [result, setResult] = useState<string>("");
  const [error, setError] = useState("");

  const [mode, setMode] = useState<"all" | "pick">("all");
  const [picked, setPicked] = useState<ReadonlySet<string>>(new Set());
  // Liveness for the picker, when the API has the fleet configured. The
  // error is deliberately not rendered here: a missing
  // UQF_FRONTEND_STACK_ROOT is the Fleet view's problem to report, and the
  // picker is complete without it.
  const fleet = useResource<OpsData>("/ops/processes");
  const liveness = fleet.data?.processes
    ? new Map(fleet.data.processes.map((row) => [row.process, row.up]))
    : null;
  const procs = selectorFor(mode, picked);
  const [cfg, setCfg] = useState({ process: "", field: "", value: "" });
  const [wcfg, setWcfg] = useState<WorkerConfigBody>({ key: "", value: "" });
  const [bf, setBf] = useState<BackfillBody>({
    worker: "",
    source_version: "",
    range_from: "",
    range_to: "",
  });
  const [token, setToken] = useState(writeToken.get());

  function lifecycle(
    action: string,
    kind: "submit" | "confirm",
    trigger: ReturnType<typeof triggerOf>,
    selector = procs,
    chosen = mode,
  ) {
    run(action, async () => {
      const r = await tracked(
        kind,
        `process.${action}`,
        { subject: selector, trigger, detail: { mode: chosen } },
        (correlation) =>
          mutate<CommandResult>(
            `/control/lifecycle/${encodeURIComponent(action)}`,
            (DESTRUCTIVE.has(action)
              ? { procs: selector, confirmed: kind === "confirm" }
              : { procs: selector }) satisfies LifecycleBody,
            "POST",
            correlation,
          ),
        (r) => (r.ok ? "succeeded" : "failed"),
      );
      return `${r.action} ${r.procs}: exit ${r.exit_code}\n${r.output}`;
    });
  }
  function cancel(trigger: ReturnType<typeof triggerOf>) {
    record("cancel", `process.${confirming}`, { subject: held.procs, trigger });
    setConfirming("");
  }

  async function run(label: string, fn: () => Promise<string>) {
    setBusy(label);
    setError("");
    setResult("");
    try {
      setResult(await fn());
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy("");
    }
  }

  const statusNotice = status.error && (
    <div
      className={`notice ${status.error.transient ? "pending" : "error"}`}
      role={status.error.transient ? "status" : "alert"}
    >
      <strong>Could not refresh the control status</strong>
      <p>{status.error.message}</p>
      <p>
        {status.data ? "Showing the last status loaded. " : ""}Retrying
        automatically.
      </p>
      <button
        type="button"
        data-activity="control.status-refresh"
        onClick={status.refresh}
      >
        Refresh control status
      </button>
    </div>
  );
  if (!status.data) return statusNotice || <p>Loading…</p>;

  if (!status.data.writes_enabled)
    return (
      <div className="panel">
        {statusNotice}
        <h2>Writes are disabled</h2>
        <p>
          Every control route returns 403. Set{" "}
          <code>UQF_FRONTEND_ENABLE_WRITES=true</code> on the server to enable
          them.
        </p>
        <p className="sidebar-note">
          They are off by default because this deployment has one shared
          credential and the caller&rsquo;s identity is claimed through a header
          anyone can set. That is safe while every route is a read.
        </p>
      </div>
    );

  // Shown, not enforced: the server refuses these routes to everyone while
  // the whole frontend is in inspection mode (#2032).
  if (access.inspection?.active)
    return (
      <div className="panel">
        {statusNotice}
        <h2>Inspection mode</h2>
        <p>
          The server refuses starting, stopping and configuring processes and
          starting backfills while inspection mode is on. The status above is
          still read live.
        </p>
      </div>
    );

  // Shown, not enforced: the server refuses these routes to this caller
  // whether or not the controls are on screen (#2045).
  if (!access.can("control", "write"))
    return (
      <div className="panel">
        {statusNotice}
        <h2>Changes are not part of your access</h2>
        <p>
          The server&rsquo;s access policy does not grant you{" "}
          <code>control:write</code>, so it refuses starting, stopping and
          configuring processes for you. Ask an administrator for it.
        </p>
      </div>
    );

  return (
    <>
      {statusNotice}
      {error && (
        <p role="alert" className="error-text">
          {error}
        </p>
      )}
      {result && <pre className="result">{result}</pre>}

      <div className="panel">
        <label>
          Write token
          <input
            data-activity="control.token"
            type="password"
            autoComplete="off"
            value={token}
            onChange={(e) => {
              setToken(e.target.value);
              writeToken.set(e.target.value);
            }}
          />
        </label>
        <p className="sidebar-note">
          The server&rsquo;s <code>UQF_FRONTEND_WRITE_TOKEN</code>, sent with
          every action below. Kept in this tab only.
        </p>
      </div>

      <form
        className="panel"
        onSubmit={(e) => {
          e.preventDefault();
        }}
      >
        <h2>Processes</h2>
        <ProcessPicker
          processes={status.data.processes}
          mode={mode}
          picked={picked}
          onChange={(m, p) => {
            setMode(m);
            setPicked(p);
            record("select", "process.selection", {
              detail: {
                mode: m,
                selected_count: m === "pick" ? p.size : undefined,
              },
            });
          }}
          liveness={liveness}
          disabled={confirming !== ""}
        />
        <div className="form-footer">
          <span>
            Runs torq.sh on <code>{procs || "nothing"}</code>. “clean” is
            deliberately not offered here.
          </span>
          <span>
            {status.data.lifecycle_actions.map((action) => (
              <button
                key={action}
                type="button"
                data-activity={`control.${action}`}
                className={action === "stop" ? "danger" : "primary"}
                disabled={busy !== "" || procs === "" || confirming !== ""}
                onClick={(event) => {
                  if (!DESTRUCTIVE.has(action))
                    return lifecycle(action, "submit", triggerOf(event));
                  record("submit", `process.${action}`, {
                    subject: procs,
                    trigger: triggerOf(event),
                    detail: { mode },
                  });
                  opener.current = event.currentTarget;
                  setHeld({ procs, mode });
                  setConfirming(action);
                }}
              >
                {busy === action ? "…" : action}
              </button>
            ))}
          </span>
        </div>
        {confirming && (
          <div
            className="notice pending"
            role="alertdialog"
            aria-label={`Confirm ${confirming}`}
            onKeyDown={(event) => {
              if (event.key === "Escape") cancel("keyboard");
            }}
          >
            <p>
              {confirming} <code>{held.procs}</code>? Running processes will go
              down.
            </p>
            <button
              type="button"
              className="danger"
              data-activity="control.confirm"
              onClick={(event) => {
                setConfirming("");
                lifecycle(
                  confirming,
                  "confirm",
                  triggerOf(event),
                  held.procs,
                  held.mode,
                );
              }}
            >
              Confirm {confirming}
            </button>{" "}
            <button
              type="button"
              data-activity="control.cancel"
              autoFocus
              onClick={(event) => cancel(triggerOf(event))}
            >
              Cancel
            </button>
          </div>
        )}
      </form>

      <form
        className="panel"
        onSubmit={(e) => {
          e.preventDefault();
          run("config", async () => {
            // The field, never the value typed into it.
            const r = await tracked(
              "submit",
              "process.config",
              {
                subject: cfg.process,
                trigger: triggerOf(),
                detail: { process: cfg.process, field: cfg.field },
              },
              (correlation) =>
                mutate<ProcessConfigResult>(
                  `/control/processes/${encodeURIComponent(cfg.process)}/config`,
                  {
                    field: cfg.field,
                    value: cfg.value,
                  } satisfies ProcessConfigBody,
                  "PUT",
                  correlation,
                ),
            );
            return `${r.process} now: ${JSON.stringify(r.config, null, 2)}`;
          });
        }}
      >
        <h2>Process config</h2>
        <div className="fields">
          <label>
            Process
            <input
              data-activity="control.config.process"
              required
              value={cfg.process}
              onChange={(e) => setCfg({ ...cfg, process: e.target.value })}
            />
          </label>
          <label>
            Field
            <select
              data-activity="control.config.field"
              required
              value={cfg.field}
              onChange={(e) => setCfg({ ...cfg, field: e.target.value })}
            >
              <option value="">Choose…</option>
              {status.data.settable_fields.map((f) => (
                <option key={f} value={f}>
                  {f}
                </option>
              ))}
            </select>
          </label>
          <label>
            Value
            <input
              data-activity="control.config.value"
              value={cfg.value}
              onChange={(e) => setCfg({ ...cfg, value: e.target.value })}
            />
          </label>
        </div>
        <div className="form-footer">
          <span>
            Persisted to process_overrides.csv; applied on the next start.
          </span>
          <button
            className="primary"
            data-activity="control.config.set"
            disabled={busy !== ""}
          >
            Set field
          </button>
        </div>
      </form>

      <form
        className="panel"
        onSubmit={(e) => {
          e.preventDefault();
          run("worker-config", async () => {
            const r = await tracked(
              "submit",
              "worker.config",
              {
                subject: wcfg.key,
                trigger: triggerOf(),
                detail: { key: wcfg.key },
              },
              (correlation) =>
                mutate<WorkerConfigResult>(
                  "/control/worker/config",
                  wcfg,
                  "PUT",
                  correlation,
                ),
              workerConfigSettled,
            );
            return `${workerConfigOutcome(r)}\n\n${r.note}`;
          });
        }}
      >
        <h2>Worker config (live process)</h2>
        <div className="fields">
          <label>
            Key
            <input
              data-activity="control.worker.key"
              required
              value={wcfg.key}
              onChange={(e) => setWcfg({ ...wcfg, key: e.target.value })}
            />
          </label>
          <label>
            Value
            <input
              data-activity="control.worker.value"
              value={wcfg.value}
              onChange={(e) => setWcfg({ ...wcfg, value: e.target.value })}
            />
          </label>
        </div>
        <div className="form-footer">
          <span>In memory only — lost when the process restarts.</span>
          <button
            className="primary"
            data-activity="control.worker.set"
            disabled={busy !== ""}
          >
            Set override
          </button>
        </div>
      </form>

      <form
        className="panel"
        onSubmit={(e) => {
          e.preventDefault();
          run("backfill", async () => {
            const r = await tracked(
              "submit",
              "backfill.start",
              {
                subject: bf.worker,
                trigger: triggerOf(),
                detail: {
                  worker: bf.worker,
                  source_version: bf.source_version,
                  range_from: bf.range_from,
                  range_to: bf.range_to,
                },
              },
              (correlation) =>
                mutate<BackfillStarted>(
                  "/control/backfill",
                  bf,
                  "POST",
                  correlation,
                ),
              // Handed off, not finished: the Backfills view says how it went.
              () => "accepted",
            );
            return `started ${r.worker} as ${r.process}\nwatch ${r.status_path}, or \`uqs logs ${r.process}\``;
          });
        }}
      >
        <h2>Run a backfill</h2>
        <div className="fields">
          {(
            [
              ["worker", "Worker"],
              ["source_version", "Source version"],
              ["range_from", "From (inclusive, timezone required)"],
              ["range_to", "To (exclusive, timezone required)"],
            ] as const
          ).map(([key, label]) => (
            <label key={key}>
              {label}
              <input
                data-activity={`control.backfill.${key}`}
                required
                value={bf[key]}
                onChange={(e) => setBf({ ...bf, [key]: e.target.value })}
              />
            </label>
          ))}
        </div>
        <div className="form-footer">
          <span>Detached — watch Backfill for the outcome.</span>
          <button
            className="primary"
            data-activity="control.backfill.start"
            disabled={busy !== ""}
          >
            Start backfill
          </button>
        </div>
      </form>
    </>
  );
}
export default function App() {
  return (
    <CapabilitiesProvider>
      <Workspace />
    </CapabilitiesProvider>
  );
}
function Workspace() {
  const access = useCapabilities();
  // Only the views the server's policy lets this caller read are offered
  // (#2045); a chosen view that is not falls back to the first that is.
  const shown = views.filter((item) => access.canView(item));
  const [chosen, setView] = useState<View>("Coverage");
  const view = shown.includes(chosen) ? chosen : (shown[0] ?? chosen);
  useEffect(() => installActivity(), []);
  const health = useResource<Health>("/health");
  // The server's default recording level, served beside its timeout (#2000).
  const serverLevel = health.data?.activity_level;
  useEffect(() => adoptDefaultLevel(serverLevel), [serverLevel]);
  // Asked once here so the sidebar can say what this deployment actually is,
  // rather than asserting "read-only" on a server where it is not true.
  const control = useResource<ControlStatus>("/control");
  const gateway = health.error
    ? "Unavailable"
    : health.data?.gateway === "reloading"
      ? "Reloading"
      : health.data?.gateway === "up"
        ? "Connected"
        : health.data?.gateway === "unreachable"
          ? "Unreachable"
          : health.data?.gateway === "wrong_process"
            ? "Wrong process"
            : "Connecting";
  return (
    <div className="app">
      <header>
        <a className="brand" href="/ui/" data-activity="nav.home">
          uqf<span> / FX DATA</span>
        </a>
        <WorkspaceSelector />
        <div className="gateway" role="status">
          Gateway <Badge value={gateway} />
          {/* The server's own words on what is wrong and what to set - for
              wrong_process, the port variable that points at the gateway. */}
          {health.data?.detail ? (
            <span className="gateway-detail">{health.data.detail}</span>
          ) : null}
        </div>
      </header>
      <div className="workspace">
        <aside>
          <p className="eyebrow">WORKSPACE</p>
          <nav aria-label="Workspace views">
            {shown.map((item, index) => (
              <button
                key={item}
                data-activity={`nav.${item}`}
                className={view === item ? "selected" : ""}
                aria-current={view === item ? "page" : undefined}
                onClick={(event) => {
                  if (item !== view)
                    record("navigate", "view.open", {
                      subject: item,
                      trigger: triggerOf(event),
                      detail: { from_view: view },
                    });
                  setView(item);
                }}
              >
                <span className="nav-number">
                  {String(index + 1).padStart(2, "0")}
                </span>
                {item}
              </button>
            ))}
          </nav>
          <p className="sidebar-note">
            {access.authenticated
              ? `Signed in as ${access.user}`
              : "Local session"}
            <br />
            {access.inspection?.active
              ? "Inspection mode"
              : control.data?.writes_enabled
                ? "Writes enabled"
                : "Read-only access"}
          </p>
        </aside>
        <main>
          <div className="page-heading">
            <div>
              <p className="eyebrow">
                {view === "Desk" || view === "Charts"
                  ? "PUBLISHED DATA"
                  : view === "Activity"
                    ? "HISTORY"
                    : "OPERATIONS"}
              </p>
              <h1>{view}</h1>
            </div>
            <span className="mono muted">UTC at source</span>
          </div>
          {health.data?.gateway === "reloading" && (
            <div className="notice pending" role="status">
              Gateway reload in progress. Views will retry automatically.
            </div>
          )}
          {access.inspection && (
            <InspectionBanner inspection={access.inspection} />
          )}
          {access.known && shown.length === 0 ? (
            <div className="notice error" role="alert">
              Your access includes none of these views. Ask an administrator to
              grant you one.
            </div>
          ) : view === "Coverage" ? (
            <CoverageView />
          ) : view === "Backfills" ? (
            <BackfillView />
          ) : view === "Desk" ? (
            <DeskView />
          ) : view === "Charts" ? (
            <ChartsView />
          ) : view === "Compare" ? (
            <CompareView />
          ) : view === "Control" ? (
            <ControlView />
          ) : view === "Activity" ? (
            <ActivityView />
          ) : view === "Services" ? (
            <ServicesView />
          ) : view === "Settings" ? (
            <Suspense fallback={<p>Loading…</p>}>
              <SettingsView />
            </Suspense>
          ) : (
            <OpsView key={view} view={view} />
          )}
        </main>
      </div>
    </div>
  );
}
