/**
 * The Desk's Plot view (#2043): an x column, up to MAX_Y y columns and a
 * line or scatter, drawn from the result already loaded.
 */
import { useState } from "react";
import type { QueryResult } from "./api/query";
import { PlotlyFigure } from "./Plotly";
import {
  MAX_Y,
  figureFor,
  plottableColumns,
  truncation,
  type PlotMode,
} from "./plot";

export function PlotPanel({
  result,
  types,
}: {
  result: QueryResult;
  types: Record<string, string> | undefined;
}) {
  const offered = plottableColumns(result.rows, types);
  const [x, setX] = useState("");
  const [ys, setYs] = useState<string[]>([]);
  const [mode, setMode] = useState<PlotMode>("lines");
  // A choice that is not in this result - the table changed under it - is
  // no choice: fall back to the first column on offer.
  const xColumn = offered.x.includes(x) ? x : (offered.x[0] ?? "");
  const kept = ys.filter((y) => offered.y.includes(y) && y !== xColumn);
  const yColumns = kept.length
    ? kept
    : offered.y.filter((y) => y !== xColumn).slice(0, 1);
  if (!result.rows.length) return <p className="empty">No rows to plot.</p>;
  if (!offered.x.length || !offered.y.length)
    return (
      <p className="empty">
        This result has no numeric column to plot
        {offered.x.length ? "" : " and nothing to order it by"}.
      </p>
    );
  const cut = truncation(result);
  return (
    <div className="plot-panel">
      <div className="fields plot-fields">
        <label>
          X axis
          <select
            data-activity="desk.plot.x"
            value={xColumn}
            onChange={(event) => setX(event.target.value)}
          >
            {offered.x.map((name) => (
              <option key={name} value={name}>
                {name} ({types?.[name]})
              </option>
            ))}
          </select>
        </label>
        <label>
          Draw as
          <select
            data-activity="desk.plot.mode"
            value={mode}
            onChange={(event) => setMode(event.target.value as PlotMode)}
          >
            <option value="lines">Line</option>
            <option value="markers">Scatter</option>
          </select>
        </label>
        <fieldset className="plot-y">
          <legend>Y axis · up to {MAX_Y}</legend>
          {offered.y.map((name) => {
            const checked = yColumns.includes(name);
            return (
              <label key={name} className="check">
                <input
                  type="checkbox"
                  data-activity="desk.plot.y"
                  checked={checked}
                  disabled={
                    name === xColumn ||
                    (!checked && yColumns.length >= MAX_Y) ||
                    (checked && yColumns.length === 1)
                  }
                  onChange={() =>
                    setYs(
                      checked
                        ? yColumns.filter((y) => y !== name)
                        : [...yColumns, name],
                    )
                  }
                />
                {name}
              </label>
            );
          })}
        </fieldset>
      </div>
      {cut && (
        <p className="note" role="status">
          {cut}
        </p>
      )}
      {yColumns.length ? (
        <PlotlyFigure
          name="desk.plot.figure"
          figure={figureFor({
            result,
            types: types ?? {},
            x: xColumn,
            ys: yColumns,
            mode,
          })}
        />
      ) : (
        <p className="empty">Choose a y column other than the x axis.</p>
      )}
    </div>
  );
}
