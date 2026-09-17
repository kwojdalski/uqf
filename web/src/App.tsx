import { useState, type FormEvent } from "react";
import {
  type Backfill,
  type Catalog,
  type Coverage,
  type CoverageRequest,
  type Health,
  type OpsData,
  type QueryInput,
  type QueryResult,
  type Row,
  filterValue,
  validateRange,
} from "./api";
import { useResource } from "./useResource";

const views = [
  "Coverage",
  "Backfills",
  "Desk",
  "Fleet",
  "Queue",
  "Connections",
  "Usage",
] as const;
type View = (typeof views)[number];
function text(value: unknown) {
  return value == null
    ? "—"
    : typeof value === "object"
      ? JSON.stringify(value)
      : String(value);
}
function Badge({ value }: { value: string }) {
  return (
    <span className={`badge ${value.toLowerCase().replaceAll(" ", "-")}`}>
      {value}
    </span>
  );
}
function Table({
  rows,
  empty = "No records returned.",
}: {
  rows: Row[];
  empty?: string;
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
                    text(row[column])
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
        <button type="button" onClick={resource.refresh}>
          Refresh
        </button>
        <button
          type="button"
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
              : resource.error.status === 409
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
    partition: "",
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
          ["partition", "Partition (blank = whole dataset)"],
          ["source_version", "Source version"],
          ["range_from", "From (inclusive, timezone required)"],
          ["range_to", "To (exclusive, timezone required)"],
        ] as const
      ).map(([key, label]) => (
        <label key={key}>
          {label}
          <input
            // Every field but `partition` must be non-empty. Blank IS the
            // partition's meaningful value - the sentinel for a dataset with
            // no partition dimension - so marking it required would make the
            // commonest case unsubmittable.
            required={key !== "partition"}
            value={value[key]}
            placeholder={
              key === "source_version"
                ? "e.g. v1"
                : key === "partition"
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
    } catch (error) {
      setError((error as Error).message);
    }
  }
  return (
    <>
      <form className="panel" onSubmit={submit}>
        <RangeFields value={range} onChange={setRange} />
        <div className="form-footer">
          <span>Half-open intervals · [from, to)</span>
          <button className="primary">Check coverage</button>
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
                No status directory configured. Set UQF_FRONTEND_STATUS_DIR on
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
                outcome:
                  worker.state === "idle"
                    ? "Success · no work"
                    : worker.state === "completed"
                      ? "Success · work completed"
                      : worker.state === "failed"
                        ? "Failure"
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
function DeskView() {
  const catalog = useResource<Catalog>("/catalog", undefined, false);
  const [table, setTable] = useState("");
  const [tier, setTier] = useState("rdb");
  const [limit, setLimit] = useState(100);
  const [filters, setFilters] = useState<
    { column: string; op: string; raw: string }[]
  >([]);
  const [coverage, setCoverage] = useState(initialRange);
  const [requireCoverage, setRequireCoverage] = useState(false);
  const [applied, setApplied] = useState<QueryInput | null>(null);
  const [error, setError] = useState("");
  const selected =
    catalog.data?.tables.find((item) => item.name === table) ??
    catalog.data?.tables[0];
  const columns = selected?.columns.filter((column) => column.filterable) ?? [];
  const resource = useResource<QueryResult>(
    applied ? "/query" : null,
    applied ? JSON.stringify(applied) : undefined,
  );
  function submit(event: FormEvent) {
    event.preventDefault();
    if (!selected) return;
    try {
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
    } catch (error) {
      setError((error as Error).message);
    }
  }
  return (
    <>
      {catalog.error && (
        <div role="alert" className="notice error">
          {catalog.error.message}{" "}
          <button onClick={catalog.refresh}>Retry catalog</button>
        </div>
      )}
      <form className="panel" onSubmit={submit}>
        <div className="fields">
          <label>
            Published table
            <select
              value={selected?.name ?? ""}
              onChange={(event) => {
                setTable(event.target.value);
                setFilters([]);
              }}
            >
              {catalog.data?.tables.map((table) => (
                <option key={table.name}>{table.name}</option>
              ))}
            </select>
          </label>
          <label>
            Storage tier
            <select
              value={tier}
              onChange={(event) => setTier(event.target.value)}
            >
              <option value="rdb">RDB · current session</option>
              <option value="hdb">HDB · completed partitions</option>
              <option value="both">Both tiers</option>
            </select>
          </label>
          <label>
            Row limit
            <input
              type="number"
              min="1"
              max="10000"
              required
              value={limit}
              onChange={(event) => setLimit(Number(event.target.value))}
            />
          </label>
        </div>
        <p className="note">
          {selected?.description ?? "Loading the query catalog…"}
        </p>
        {tier !== "rdb" && (
          <p className="note">
            Historical queries can take longer. A reload window is shown as
            temporary unavailability.
          </p>
        )}
        <div className="section-heading">
          <h3>Filters</h3>
          <button
            type="button"
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
                value={filter.op}
                onChange={(event) =>
                  setFilters(
                    filters.map((item, i) =>
                      i === index ? { ...item, op: event.target.value } : item,
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
                required
                value={filter.raw}
                onChange={(event) =>
                  setFilters(
                    filters.map((item, i) =>
                      i === index ? { ...item, raw: event.target.value } : item,
                    ),
                  )
                }
              />
            </label>
            <button
              type="button"
              aria-label={`Remove filter ${index + 1}`}
              onClick={() => setFilters(filters.filter((_, i) => i !== index))}
            >
              Remove
            </button>
          </div>
        ))}
        <label className="checkbox">
          <input
            type="checkbox"
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
          <button disabled={!selected} className="primary">
            Run query
          </button>
        </div>
        {error && (
          <p role="alert" className="error-text">
            {error}
          </p>
        )}
      </form>
      {applied && (
        <>
          <ResourceStatus resource={resource} />
          {resource.data && (
            <section className="panel">
              <div className="section-heading">
                <h2>{resource.data.table}</h2>
                <Badge value={resource.data.tier.toUpperCase()} />
              </div>
              <p className="note">
                {resource.data.row_count.toLocaleString()} rows returned
                {resource.data.truncated
                  ? " · Server row cap reached"
                  : resource.data.row_count >= applied.limit
                    ? " · Requested limit reached; more rows may exist"
                    : ""}
              </p>
              <Table
                rows={resource.data.rows}
                empty="No rows match this query."
              />
            </section>
          )}
        </>
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
    </>
  );
}
export default function App() {
  const [view, setView] = useState<View>("Coverage");
  const health = useResource<Health>("/health");
  const gateway = health.error
    ? "Unavailable"
    : health.data?.gateway === "reloading"
      ? "Reloading"
      : health.data?.gateway === "up"
        ? "Connected"
        : health.data?.gateway === "unreachable"
          ? "Unreachable"
          : "Connecting";
  return (
    <div className="app">
      <header>
        <a className="brand" href="/ui/">
          uqf<span> / FX DATA</span>
        </a>
        <div className="gateway" role="status">
          Gateway <Badge value={gateway} />
        </div>
      </header>
      <div className="workspace">
        <aside>
          <p className="eyebrow">WORKSPACE</p>
          <nav aria-label="Workspace views">
            {views.map((item, index) => (
              <button
                key={item}
                className={view === item ? "selected" : ""}
                aria-current={view === item ? "page" : undefined}
                onClick={() => setView(item)}
              >
                <span className="nav-number">0{index + 1}</span>
                {item}
              </button>
            ))}
          </nav>
          <p className="sidebar-note">
            Local session
            <br />
            Read-only access
          </p>
        </aside>
        <main>
          <div className="page-heading">
            <div>
              <p className="eyebrow">
                {view === "Desk" ? "PUBLISHED DATA" : "OPERATIONS"}
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
          {view === "Coverage" ? (
            <CoverageView />
          ) : view === "Backfills" ? (
            <BackfillView />
          ) : view === "Desk" ? (
            <DeskView />
          ) : (
            <OpsView key={view} view={view} />
          )}
        </main>
      </div>
    </div>
  );
}
