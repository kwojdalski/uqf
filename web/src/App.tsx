import { useState, type FormEvent } from "react";
import {
  type Backfill,
  type Catalog,
  type Coverage,
  type BackfillStarted,
  type CommandResult,
  type ControlProcess,
  type ControlStatus,
  type CoverageRequest,
  type ProcessConfigResult,
  type WorkerConfigResult,
  mutate,
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
  "Control",
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
/** torq.sh's selector for a chosen set: "all", or the names joined by
 * spaces. "all" is sent as the literal word rather than every name spelled
 * out, so what torq.sh does with it - startwithall=1 only - stays torq.sh's
 * decision and matches `uqf-stack start all`.
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
}: {
  processes: ControlProcess[];
  mode: "all" | "pick";
  picked: ReadonlySet<string>;
  onChange: (mode: "all" | "pick", picked: ReadonlySet<string>) => void;
  liveness: Map<string, boolean> | null;
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
    .map((p) => p.procname);
  const count = mode === "all" ? startedByAll.length : picked.size;

  return (
    <fieldset className="picker">
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
          onClick={() => onChange("all", picked)}
        >
          all
        </button>
        <button
          type="button"
          className="chip"
          onClick={() => onChange("pick", new Set(startedByAll))}
        >
          startwithall only
        </button>
        {liveness && (
          <>
            <button
              type="button"
              className="chip"
              onClick={() =>
                onChange(
                  "pick",
                  new Set(
                    processes
                      .filter((p) => liveness.get(p.procname) === false)
                      .map((p) => p.procname),
                  ),
                )
              }
            >
              down only
            </button>
            <button
              type="button"
              className="chip"
              onClick={() =>
                onChange(
                  "pick",
                  new Set(
                    processes
                      .filter((p) => liveness.get(p.procname) === true)
                      .map((p) => p.procname),
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
          onClick={() => onChange("pick", new Set())}
        >
          none
        </button>
      </div>
      <div className="picker-groups">
        {[...groups.entries()].map(([proctype, list]) => {
          const names = list.map((p) => p.procname);
          const chosen = names.filter((n) => picked.has(n)).length;
          return (
            <div key={proctype} className="picker-group">
              <label className="picker-row picker-head">
                <input
                  type="checkbox"
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
                const up = liveness?.get(p.procname);
                return (
                  <label key={p.procname} className="picker-row">
                    <input
                      type="checkbox"
                      checked={
                        mode === "all"
                          ? p.start_with_all
                          : picked.has(p.procname)
                      }
                      disabled={mode === "all"}
                      onChange={(e) => pick([p.procname], e.target.checked)}
                    />
                    <code>{p.procname}</code>
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
function ControlView() {
  const status = useResource<ControlStatus>("/control");
  const [busy, setBusy] = useState("");
  const [result, setResult] = useState<string>("");
  const [error, setError] = useState("");

  const [mode, setMode] = useState<"all" | "pick">("all");
  const [picked, setPicked] = useState<ReadonlySet<string>>(new Set());
  // Liveness for the picker, when the API has the fleet configured. The
  // error is deliberately not rendered here: a missing
  // UQF_FRONTEND_PROCESS_CSV is the Fleet view's problem to report, and the
  // picker is complete without it.
  const fleet = useResource<OpsData>("/ops/processes");
  const liveness = fleet.data?.processes
    ? new Map(
        fleet.data.processes.map((row) => [
          String(row.procname),
          Boolean(row.up),
        ]),
      )
    : null;
  const procs = selectorFor(mode, picked);
  const [cfg, setCfg] = useState({ procname: "", field: "", value: "" });
  const [wcfg, setWcfg] = useState({ key: "", value: "" });
  const [bf, setBf] = useState({
    worker: "",
    source_version: "",
    range_from: "",
    range_to: "",
  });

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

  if (status.error)
    return (
      <p role="alert" className="error-text">
        {status.error.message}
      </p>
    );
  if (!status.data) return <p>Loading…</p>;

  if (!status.data.writes_enabled)
    return (
      <div className="panel">
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

  return (
    <>
      {error && (
        <p role="alert" className="error-text">
          {error}
        </p>
      )}
      {result && <pre className="result">{result}</pre>}

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
          }}
          liveness={liveness}
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
                className={action === "stop" ? "danger" : "primary"}
                disabled={busy !== "" || procs === ""}
                onClick={() =>
                  run(action, async () => {
                    const r = await mutate<CommandResult>(
                      `/control/process/${action}`,
                      { procs },
                    );
                    return `${r.action} ${r.target}: exit ${r.exit_code}\n${r.output}`;
                  })
                }
              >
                {busy === action ? "…" : action}
              </button>
            ))}
          </span>
        </div>
      </form>

      <form
        className="panel"
        onSubmit={(e) => {
          e.preventDefault();
          run("config", async () => {
            const r = await mutate<ProcessConfigResult>(
              `/control/process/${cfg.procname}/config`,
              { field: cfg.field, value: cfg.value },
              "PUT",
            );
            return `${r.procname} now: ${JSON.stringify(r.config, null, 2)}`;
          });
        }}
      >
        <h2>Process config</h2>
        <div className="fields">
          <label>
            Process
            <input
              required
              value={cfg.procname}
              onChange={(e) => setCfg({ ...cfg, procname: e.target.value })}
            />
          </label>
          <label>
            Field
            <select
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
              value={cfg.value}
              onChange={(e) => setCfg({ ...cfg, value: e.target.value })}
            />
          </label>
        </div>
        <div className="form-footer">
          <span>
            Persisted to process_overrides.csv; applied on the next start.
          </span>
          <button className="primary" disabled={busy !== ""}>
            Set field
          </button>
        </div>
      </form>

      <form
        className="panel"
        onSubmit={(e) => {
          e.preventDefault();
          run("worker-config", async () => {
            const r = await mutate<WorkerConfigResult>(
              "/control/worker-config",
              wcfg,
              "PUT",
            );
            return `${r.key} = ${r.value}\n${JSON.stringify(r.explain, null, 2)}\n\n${r.note}`;
          });
        }}
      >
        <h2>Worker config (live process)</h2>
        <div className="fields">
          <label>
            Key
            <input
              required
              value={wcfg.key}
              onChange={(e) => setWcfg({ ...wcfg, key: e.target.value })}
            />
          </label>
          <label>
            Value
            <input
              value={wcfg.value}
              onChange={(e) => setWcfg({ ...wcfg, value: e.target.value })}
            />
          </label>
        </div>
        <div className="form-footer">
          <span>In memory only — lost when the process restarts.</span>
          <button className="primary" disabled={busy !== ""}>
            Set override
          </button>
        </div>
      </form>

      <form
        className="panel"
        onSubmit={(e) => {
          e.preventDefault();
          run("backfill", async () => {
            const r = await mutate<BackfillStarted>("/control/backfill", bf);
            return `started ${r.worker} (pid ${r.pid})\nwatch ${r.status_path}`;
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
                required
                value={bf[key]}
                onChange={(e) => setBf({ ...bf, [key]: e.target.value })}
              />
            </label>
          ))}
        </div>
        <div className="form-footer">
          <span>Detached — watch Backfill for the outcome.</span>
          <button className="primary" disabled={busy !== ""}>
            Start backfill
          </button>
        </div>
      </form>
    </>
  );
}
export default function App() {
  const [view, setView] = useState<View>("Coverage");
  const health = useResource<Health>("/health");
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
            {control.data?.writes_enabled
              ? "Writes enabled"
              : "Read-only access"}
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
          ) : view === "Control" ? (
            <ControlView />
          ) : (
            <OpsView key={view} view={view} />
          )}
        </main>
      </div>
    </div>
  );
}
