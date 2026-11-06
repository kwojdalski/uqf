/**
 * Compare (#2033): one table, or several, on two endpoints - this stack
 * (`local`) or a registered deploy target - over an explicit window.
 *
 * This view decides nothing. The server delegates to `uqs data diff`, and
 * every rule - how a filter is typed, the row key, what differs, the outcome
 * and the label a truncated sample carries - is q's. What is shown is the
 * document that comes back, as it came back: in particular an
 * equal_in_sample result is never shown as "equal" without its label, which
 * says whether every row was read or only a sample.
 */
import { useState, type FormEvent } from "react";
import {
  validateTime,
  type Catalog,
  type CompareBody,
  type CompareResult,
  type CompareTable,
  type FilterOp,
  type QueryFilter,
  writeToken,
} from "./api";
import { useResource } from "./useResource";
import { record } from "./activity";

/** One filter as typed: the Desk's {column, op, value}, with the column
 *  typed rather than chosen, since a side may be a target this stack's
 *  catalog does not describe (#2223). */
export interface FilterDraft {
  column: string;
  op: FilterOp;
  raw: string;
}

/** What the form holds, as typed. */
interface Form {
  left: string;
  right: string;
  tables: string;
  from: string;
  to: string;
  filters: FilterDraft[];
  limit: string;
  sourceVersion: string;
  partition: string;
}
type TextField = Exclude<keyof Form, "filters">;

/** A typed filter as /compare takes it: the value as text, which q types by
 *  the column it names on each side; `in` a JSON list, as on the Desk. */
export function compareFilter(draft: FilterDraft): QueryFilter {
  const column = draft.column.trim();
  if (!column) throw new Error("Name each filter's column.");
  if (draft.op !== "in") {
    if (!draft.raw.trim())
      throw new Error(`Give the filter on ${column} a value.`);
    return { column, op: draft.op, value: draft.raw.trim() };
  }
  let values: unknown;
  try {
    values = JSON.parse(draft.raw);
  } catch {
    values = null;
  }
  if (!Array.isArray(values) || values.length === 0)
    throw new Error('Use a non-empty JSON list, e.g. ["EURUSD", "GBPUSD"].');
  return { column, op: draft.op, value: values };
}

const START: Form = {
  left: "local",
  right: "",
  tables: "",
  from: "",
  to: "",
  filters: [],
  limit: "1000",
  sourceVersion: "",
  partition: "",
};

/** The body to send, or why the form cannot be sent. Only what the request
 *  must state is checked here - both sides, a table, a window with a zone -
 *  everything else (a filter's grammar, a value's type) is the server's. */
export function compareBody(form: Form): CompareBody {
  const left = form.left.trim();
  const right = form.right.trim();
  if (!left || !right)
    throw new Error("Name both sides: local, or a registered target.");
  const tables = form.tables
    .split(/[\s,]+/)
    .map((t) => t.trim())
    .filter(Boolean);
  if (!tables.length) throw new Error("Name at least one table.");
  validateTime(form.from.trim());
  validateTime(form.to.trim());
  const limit = Number(form.limit);
  if (!Number.isInteger(limit) || limit < 1)
    throw new Error("The row limit is a whole number of rows, at least 1.");
  const sourceVersion = form.sourceVersion.trim() || null;
  return {
    left,
    right,
    tables,
    range_from: form.from.trim(),
    range_to: form.to.trim(),
    filters: form.filters.map(compareFilter),
    limit,
    source_version: sourceVersion,
    // With a source version the slice is always said, and a blank partition
    // is "" - the dataset has no partition dimension - as on the Coverage
    // view (#2221). Without one, a partition is sent as typed, so the server
    // refuses it rather than this form dropping it.
    partition:
      sourceVersion !== null
        ? form.partition.trim()
        : form.partition.trim() || null,
  };
}

function Input({
  name,
  label,
  value,
  onChange,
  placeholder,
  type = "text",
}: {
  name: TextField;
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  type?: string;
}) {
  return (
    <label>
      {label}
      <input
        name={name}
        type={type}
        data-activity={`compare.${name}`}
        placeholder={placeholder}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}

function TableVerdict({
  verdict,
  left,
  right,
}: {
  verdict: CompareTable;
  left: string;
  right: string;
}) {
  const sides: [string, string][] = [
    ["left", left],
    ["right", right],
  ];
  const gaps = sides.flatMap(([side, name]) =>
    (verdict.coverage[side]?.gaps ?? []).map((gap) => ({ name, ...gap })),
  );
  return (
    <section className="panel" aria-label={`Comparison of ${verdict.table}`}>
      <div className="section-heading">
        <h2>{verdict.table}</h2>
        <span className={`badge ${verdict.outcome}`}>{verdict.outcome}</span>
      </div>
      <p
        className={verdict.truncated ? "notice pending" : "note"}
        role={verdict.truncated ? "status" : undefined}
      >
        {verdict.label}
      </p>
      {verdict.key.length > 0 && (
        <p className="note">
          Rows paired on <span className="mono">{verdict.key.join(", ")}</span>{" "}
          ({verdict.basis}); read {verdict.read[0] ?? 0} on {left},{" "}
          {verdict.read[1] ?? 0} on {right}.
        </p>
      )}
      {Object.entries(verdict.reasons)
        .filter(([, reason]) => reason)
        .map(([side, reason]) => (
          <div key={side} className="notice error" role="alert">
            {side === "left" ? left : right}: {reason}
          </div>
        ))}
      {verdict.schema.length > 0 && (
        <div className="table-scroll">
          <table aria-label="Schema differences">
            <thead>
              <tr>
                <th>column</th>
                <th>{left}</th>
                <th>{right}</th>
              </tr>
            </thead>
            <tbody>
              {verdict.schema.map((s) => (
                <tr key={s.column}>
                  <td className="mono">{s.column}</td>
                  <td>{s.left}</td>
                  <td>{s.right}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {gaps.length > 0 && (
        <div className="table-scroll">
          <table aria-label="Coverage not published">
            <thead>
              <tr>
                <th>side</th>
                <th>not published from</th>
                <th>to</th>
              </tr>
            </thead>
            <tbody>
              {gaps.map((g) => (
                <tr key={`${g.name}${g.range_from}`}>
                  <td>{g.name}</td>
                  <td className="mono">{g.range_from}</td>
                  <td className="mono">{g.range_to}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {verdict.rows.length > 0 && (
        <div className="table-scroll">
          <table aria-label="Row differences">
            <thead>
              <tr>
                <th>key</th>
                <th>difference</th>
                <th>detail</th>
              </tr>
            </thead>
            <tbody>
              {verdict.rows.map((r, i) => (
                <tr key={`${r.rowkey}${i}`}>
                  <td className="mono">{r.rowkey}</td>
                  <td>{r.kind}</td>
                  <td className="mono">{r.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export function CompareView() {
  const [form, setForm] = useState<Form>(START);
  const [problem, setProblem] = useState<string | null>(null);
  const [sent, setSent] = useState<string | null>(null);
  // A target side is reached over ssh with the API's key, so the request
  // carries the write token, as browsing a target's files does (#2066).
  const token = writeToken.get();
  const result = useResource<CompareResult>(
    sent === null ? null : "/compare",
    sent ?? undefined,
    false,
    token ? `Bearer ${token}` : undefined,
  );
  const set = (name: TextField) => (value: string) =>
    setForm((prev) => ({ ...prev, [name]: value }));
  // The operators /query takes, as /catalog serves them - the Desk's list.
  const catalog = useResource<Catalog>("/catalog", undefined, false);
  const operators = (catalog.data?.operators ?? []) as FilterOp[];
  const setFilter = (index: number, change: Partial<FilterDraft>) =>
    setForm((prev) => ({
      ...prev,
      filters: prev.filters.map((f, i) =>
        i === index ? { ...f, ...change } : f,
      ),
    }));
  function submit(event: FormEvent) {
    event.preventDefault();
    let body: CompareBody;
    try {
      body = compareBody(form);
    } catch (error) {
      setProblem((error as Error).message);
      return;
    }
    setProblem(null);
    const next = JSON.stringify(body);
    record("submit", "compare.run", {
      target: `${body.left}..${body.right}`,
    });
    if (next === sent) result.refresh();
    setSent(next);
  }
  const doc = result.data;
  return (
    <>
      <section className="panel">
        <div className="section-heading">
          <h2>Compare a table on two endpoints</h2>
        </div>
        <p className="note">
          Schema, a bounded sample of rows and published coverage, compared by{" "}
          <span className="mono">uqs data diff</span>. A side is{" "}
          <span className="mono">local</span> - this stack - or a registered
          deploy target, reached over ssh.
        </p>
        <form onSubmit={submit} aria-label="Comparison">
          <div className="fields">
            <Input
              name="left"
              label="Left"
              value={form.left}
              onChange={set("left")}
              placeholder="local"
            />
            <Input
              name="right"
              label="Right"
              value={form.right}
              onChange={set("right")}
              placeholder="a target, e.g. prod"
            />
            <Input
              name="tables"
              label="Tables"
              value={form.tables}
              onChange={set("tables")}
              placeholder="demo_deals, trades"
            />
            <Input
              name="from"
              label="From (included)"
              value={form.from}
              onChange={set("from")}
              placeholder="2026-09-11T00:00:00Z"
            />
            <Input
              name="to"
              label="To (excluded)"
              value={form.to}
              onChange={set("to")}
              placeholder="2026-09-12T00:00:00Z"
            />
            <Input
              name="limit"
              label="Rows per side, at most"
              type="number"
              value={form.limit}
              onChange={set("limit")}
            />
            <Input
              name="sourceVersion"
              label="Source version (coverage)"
              value={form.sourceVersion}
              onChange={set("sourceVersion")}
              placeholder="optional"
            />
            <Input
              name="partition"
              label="Slice (coverage)"
              value={form.partition}
              onChange={set("partition")}
              placeholder="blank: no partition dimension"
            />
          </div>
          <div className="section-heading">
            <h3>Filters</h3>
            <button
              type="button"
              data-activity="compare.filter.add"
              disabled={!operators.length || form.filters.length >= 20}
              onClick={() =>
                setForm((prev) => ({
                  ...prev,
                  filters: [...prev.filters, { column: "", op: "eq", raw: "" }],
                }))
              }
            >
              Add filter
            </button>
          </div>
          {form.filters.map((filter, index) => (
            <div className="filter" key={index}>
              <label>
                Column
                <input
                  data-activity="compare.filter.column"
                  placeholder="sym"
                  value={filter.column}
                  onChange={(event) =>
                    setFilter(index, { column: event.target.value })
                  }
                />
              </label>
              <label>
                Operator
                <select
                  data-activity="compare.filter.op"
                  value={filter.op}
                  onChange={(event) =>
                    setFilter(index, { op: event.target.value as FilterOp })
                  }
                >
                  {operators.map((op) => (
                    <option key={op}>{op}</option>
                  ))}
                </select>
              </label>
              <label>
                Value{filter.op === "in" ? " (JSON list)" : ""}
                <input
                  data-activity="compare.filter.value"
                  value={filter.raw}
                  onChange={(event) =>
                    setFilter(index, { raw: event.target.value })
                  }
                />
              </label>
              <button
                type="button"
                data-activity="compare.filter.remove"
                aria-label={`Remove filter ${index + 1}`}
                onClick={() =>
                  setForm((prev) => ({
                    ...prev,
                    filters: prev.filters.filter((_, i) => i !== index),
                  }))
                }
              >
                Remove
              </button>
            </div>
          ))}
          <div className="form-footer">
            <span>{result.loading ? "Comparing…" : ""}</span>
            <button className="primary" data-activity="compare.run">
              Compare
            </button>
          </div>
        </form>
        {problem && (
          <div role="alert" className="notice error">
            {problem}
          </div>
        )}
        {result.error && (
          <div role="alert" className="notice error">
            {result.error.message}
          </div>
        )}
      </section>
      {doc && !result.error && (
        <>
          <section className="panel" aria-label="Comparison summary">
            <div className="section-heading">
              <h2>
                <span className="mono">{doc.left}</span> (left) against{" "}
                <span className="mono">{doc.right}</span> (right)
              </h2>
              <span className={`badge ${doc.outcome}`}>{doc.outcome}</span>
            </div>
            <p className="note mono">
              [{doc.range_from}, {doc.range_to}) · at most{" "}
              {doc.limit.toLocaleString()} rows per side
              {doc.filters.length > 0 &&
                ` · ${doc.filters.map((f) => `${f.column} ${f.op} ${JSON.stringify(f.value)}`).join(" and ")}`}
              {doc.source_version !== null &&
                ` · coverage at ${doc.source_version}, ${doc.partition ? `partition ${doc.partition}` : "no partition dimension"}`}
            </p>
          </section>
          {doc.tables.map((t) => (
            <TableVerdict
              key={t.table}
              verdict={t}
              left={doc.left}
              right={doc.right}
            />
          ))}
        </>
      )}
    </>
  );
}
