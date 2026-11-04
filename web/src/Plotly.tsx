/**
 * The one way this app draws a Plotly figure (#2043).
 *
 * Plotly is several megabytes, and most sessions never draw anything, so it
 * is not in the main bundle: `import()` puts react-plotly.js and
 * plotly.js-dist-min in a chunk of their own, fetched the first time a
 * figure is shown. scripts/check-bundle.mjs holds the build to that.
 *
 * React only renders here. What a figure holds - its traces, axes and
 * labels - is decided by whoever builds the `data` and `layout`.
 */
import {
  Component,
  Suspense,
  lazy,
  type CSSProperties,
  type ReactNode,
} from "react";

export interface Figure {
  data: unknown[];
  layout?: Record<string, unknown>;
}

/** react-plotly.js bound to the minified dist build, which carries no
 *  compiler and no WebGL traces this app does not draw. Loaded once. */
const Plot = lazy(async () => {
  const [{ default: createPlotlyComponent }, { default: Plotly }] =
    await Promise.all([
      import("react-plotly.js/factory"),
      import("plotly.js-dist-min"),
    ]);
  return { default: createPlotlyComponent(Plotly) };
});

class LoadFailure extends Component<
  { children: ReactNode },
  { error: Error | null }
> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  render() {
    if (this.state.error)
      return (
        <div role="alert" className="notice error">
          The plotting library could not be loaded: {this.state.error.message}
        </div>
      );
    return this.props.children;
  }
}

/** The figure, once Plotly has loaded. `name` is the activity name its
 *  modebar's buttons are recorded under: Plotly writes them, so no source
 *  line can name them, and activity.ts takes the nearest named ancestor's. */
export function PlotlyFigure({
  figure,
  name,
  style,
}: {
  figure: Figure;
  name: string;
  style?: CSSProperties;
}) {
  return (
    <LoadFailure>
      <Suspense
        fallback={<p className="note">Loading the plotting library…</p>}
      >
        <div className="plot" data-activity={name}>
          <Plot
            data={figure.data}
            layout={{ autosize: true, ...figure.layout }}
            config={{ displaylogo: false, responsive: true }}
            useResizeHandler
            style={{ width: "100%", height: "420px", ...style }}
          />
        </div>
      </Suspense>
    </LoadFailure>
  );
}
