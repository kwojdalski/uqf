/**
 * Prepared charts (#2044). Every chart is defined on the server - its
 * parameters, its query, its point limit and its figure (charts.py); this
 * view only lists them, draws a form from each one's JSON Schema, and renders
 * the figure it is sent. Nothing here knows any chart by name.
 */
import { useState, type FormEvent } from "react";
import type {
  ChartField,
  ChartInfo,
  ChartList,
  ChartResult,
} from "./api/charts";
import { PlotlyFigure } from "./Plotly";
import { useResource } from "./useResource";
import { record } from "./activity";

type Values = Record<string, string | number | boolean>;

/** The form's starting values: each field's own default, else empty. */
function defaults(chart: ChartInfo): Values {
  return Object.fromEntries(
    Object.entries(chart.params.properties).map(([name, field]) => [
      name,
      field.default ??
        (field.type === "boolean" ? false : field.enum ? field.enum[0] : ""),
    ]),
  );
}

/** The body to send: a number field as a number, so the server's schema -
 *  not this view - decides whether it is in range. */
function body(chart: ChartInfo, values: Values) {
  return Object.fromEntries(
    Object.entries(chart.params.properties).map(([name, field]) => {
      const value = values[name];
      const numeric = field.type === "integer" || field.type === "number";
      return [name, numeric && value !== "" ? Number(value) : value];
    }),
  );
}

function Field({
  chart,
  name,
  field,
  value,
  onChange,
}: {
  chart: string;
  name: string;
  field: ChartField;
  value: string | number | boolean;
  onChange: (value: string | number | boolean) => void;
}) {
  const activity = `charts.param.${name}`;
  const label = field.title ?? name;
  if (field.type === "boolean")
    return (
      <label className="check" title={field.description}>
        <input
          type="checkbox"
          data-activity={activity}
          checked={value === true}
          onChange={(event) => onChange(event.target.checked)}
        />
        {label}
      </label>
    );
  return (
    <label title={field.description}>
      {label}
      {field.enum ? (
        <select
          data-activity={activity}
          value={String(value)}
          onChange={(event) => onChange(event.target.value)}
        >
          {field.enum.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      ) : (
        <input
          data-activity={activity}
          name={`${chart}.${name}`}
          type={
            field.type === "integer" || field.type === "number"
              ? "number"
              : "text"
          }
          min={field.minimum}
          max={field.maximum}
          placeholder={field.description}
          value={String(value)}
          onChange={(event) => onChange(event.target.value)}
        />
      )}
    </label>
  );
}

function ChartPanel({ chart }: { chart: ChartInfo }) {
  const [values, setValues] = useState<Values>(() => defaults(chart));
  const [sent, setSent] = useState<string | null>(null);
  const drawn = useResource<ChartResult>(
    sent === null ? null : `/charts/${encodeURIComponent(chart.name)}`,
    sent ?? undefined,
    false,
  );
  function submit(event: FormEvent) {
    event.preventDefault();
    const next = JSON.stringify(body(chart, values));
    record("submit", "charts.draw", { subject: chart.name });
    if (next === sent) drawn.refresh();
    setSent(next);
  }
  const result = drawn.data;
  return (
    <section className="panel">
      <div className="section-heading">
        <h2>{chart.title}</h2>
        <span className="mono muted">
          up to {chart.point_limit.toLocaleString()} points
        </span>
      </div>
      <p className="note">{chart.description}</p>
      <form onSubmit={submit}>
        <div className="fields">
          {Object.entries(chart.params.properties).map(([name, field]) => (
            <Field
              key={name}
              chart={chart.name}
              name={name}
              field={field}
              value={values[name]}
              onChange={(value) =>
                setValues((prev) => ({ ...prev, [name]: value }))
              }
            />
          ))}
        </div>
        <div className="form-footer">
          <span>
            {drawn.loading
              ? "Drawing…"
              : result
                ? `${result.points.toLocaleString()} of up to ${result.point_limit.toLocaleString()} points` +
                  (result.downsampled ? " · downsampled" : "") +
                  (result.truncated ? " · truncated" : "")
                : ""}
          </span>
          <button className="primary" data-activity="charts.draw">
            Draw
          </button>
        </div>
      </form>
      {drawn.error && (
        <div role="alert" className="notice error">
          {drawn.error.message}
        </div>
      )}
      {result && !drawn.error && (
        <>
          {result.notes.length > 0 && (
            <ul className="note chart-notes" aria-label="Chart notes">
              {result.notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          )}
          <PlotlyFigure name="charts.figure" figure={result.figure} />
        </>
      )}
    </section>
  );
}

export function ChartsView() {
  const list = useResource<ChartList>("/charts", undefined, false);
  const [chosen, setChosen] = useState("");
  const charts = list.data?.charts ?? [];
  const chart = charts.find((item) => item.name === chosen) ?? charts[0];
  return (
    <>
      {list.error && (
        <div role="alert" className="notice error">
          {list.error.message}{" "}
          <button data-activity="charts.retry" onClick={list.refresh}>
            Retry
          </button>
        </div>
      )}
      <div className="desk">
        <nav className="panel table-list" aria-label="Prepared charts">
          <h2>Charts</h2>
          {!list.data && !list.error && (
            <p className="note">Loading the charts…</p>
          )}
          {charts.map((item) => (
            <button
              key={item.name}
              type="button"
              className="table-choice"
              data-activity="charts.choose"
              aria-pressed={item.name === chart?.name}
              title={item.description}
              onClick={() => setChosen(item.name)}
            >
              {item.title}
            </button>
          ))}
        </nav>
        <div className="desk-main">
          {chart && <ChartPanel key={chart.name} chart={chart} />}
        </div>
      </div>
    </>
  );
}
