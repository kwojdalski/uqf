"""Prepared charts: named figures over vetted reads (#2044).

A chart is defined once, here, and nowhere else: the browser lists them from
`GET /charts`, draws a form from each one's parameter schema, and renders the
figure `POST /charts/{name}` returns. It holds no chart-specific code.

Each entry in CHARTS is four things:

  params      a Pydantic model the request body must validate against. It
              forbids unknown keys, so a renamed field is a 422, not a
              default.
  read        the vetted query. It reads through the paths the rest of this
              API already trusts and nothing else: a published table through
              `Gateway.browse` (.uqf.browse, as the data login, under the
              table's query policy), with the table and every filter column
              checked against the catalog and every value coerced by
              `queries.build_filters` and sent as a typed IPC argument; the
              fleet's query log through `ops.USAGE`; the backfill status files
              through `status.read_dir`. No parameter is ever rendered into
              q text - there is no q text here at all.
  max_points  the most points the figure may carry. Enforced on the server,
              after the read: a long series is reduced by LTTB (largest
              triangle three buckets), which keeps the shape a line is read
              by, and anything that cannot be reduced is cut and said to be.
  build       the plotly.py figure, from what `read` returned.
  requires    the (feature, action) of the view whose data `read` reaches -
              `usage:read` for the query log, `backfills:read` for the status
              files, `desk:read` for a published table. POST /charts/{name}
              needs it as well as charts:read, so a chart never shows a caller
              what that view would refuse them (#2476).

Why LTTB in Python and not `xbar` in q: the data login may run `.uqf.browse`
and nothing else (#889), and `.uqf.browse` returns rows. Bucketing in q would
need a second gateway function for that login, with its own policy. Reading
under the existing cap (`Settings.max_rows`) and reducing here keeps one
query path - the one `/query` already uses - and every response says
whether the read was cut (`truncated`) and whether the figure was reduced
(`downsampled`).
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import plotly.graph_objects as go
import polars as pl
from plotly.subplots import make_subplots
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from uqf_frontend import ops, queries, status, workspaces
from uqf_frontend.catalog import Catalog, Table
from uqf_frontend.config import Settings
from uqf_frontend.errors import FrontendError, ValidationFailed
from uqf_frontend.fleet import Fleet
from uqf_frontend.gateway import TIERS, Gateway
from uqf_frontend.instants import AwareInstantOrBlank

Tier = Literal["rdb", "hdb", "both"]

#: Every figure's look. "plotly_white" rather than the default, which draws
#: on grey and clashes with the app's panels.
TEMPLATE = "plotly_white"


# ------------------------------------------------------------------ plumbing


@dataclass(frozen=True)
class Sources:
    """What a chart's read may touch - and so all it can touch."""

    gateway: Gateway
    catalog: Catalog
    fleet: Fleet
    settings: Settings
    #: The app's policy for one table (app.run_policy), asked before a read.
    authorize: Callable[[str], None]
    #: The workspace read: `local`, or a target (workspaces.Scope.target).
    target: str = "local"


@dataclass
class Read:
    """What a chart read, and whether the read was cut."""

    frames: dict[str, pl.DataFrame]
    truncated: bool = False
    notes: list[str] = field(default_factory=list)


@dataclass
class Built:
    """A figure, the points it carries, and what was done to fit the cap."""

    figure: go.Figure
    points: int
    downsampled: bool = False
    truncated: bool = False
    notes: list[str] = field(default_factory=list)


class ChartParams(BaseModel):
    """The base every chart's parameters extend: unknown keys are refused."""

    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class Chart:
    name: str
    title: str
    description: str
    params: type[ChartParams]
    max_points: int
    read: Callable[[Sources, Any], Read]
    build: Callable[[Read, Any, int], Built]
    #: What the caller must also hold to draw it (module docstring).
    requires: tuple[str, str]


@dataclass(frozen=True)
class Rendered:
    """One chart's answer, as POST /charts/{name} sends it."""

    figure: dict[str, Any]
    point_limit: int
    points: int
    downsampled: bool
    truncated: bool
    notes: list[str]


def render(chart: Chart, sources: Sources, body: dict[str, Any]) -> Rendered:
    """Validate `body` against the chart's schema, run its read and build its
    figure. A body the schema refuses is a 422 naming the field, before any
    read."""
    from pydantic import ValidationError

    try:
        params = chart.params.model_validate(body)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'body'}: {e['msg']}" for e in exc.errors()
        )
        raise ValidationFailed(f"chart {chart.name!r}: {problems}") from None
    read = chart.read(sources, params)
    built = chart.build(read, params, chart.max_points)
    if built.points > chart.max_points:  # pragma: no cover - a builder bug, never a caller's
        raise AssertionError(f"{chart.name} built {built.points} points past {chart.max_points}")
    built.figure.update_layout(template=TEMPLATE, margin={"t": 56, "r": 24, "b": 48, "l": 64})
    return Rendered(
        figure=json.loads(built.figure.to_json()),
        point_limit=chart.max_points,
        points=built.points,
        downsampled=built.downsampled,
        truncated=read.truncated or built.truncated,
        notes=[*read.notes, *built.notes],
    )


def _browse(
    sources: Sources,
    table: str,
    needs: Sequence[str],
    filters: list[tuple[str, str, Any]],
    tier: Tier,
) -> tuple[pl.DataFrame, bool]:
    """One table read through /query's own path: the catalog, the policy,
    `build_filters`, `.uqf.browse`. Returns the rows and whether the row cap
    cut them."""
    tbl = sources.catalog.table(table)
    sources.authorize(tbl.name)
    _require(tbl, needs)
    columns, operators, values = queries.build_filters(tbl, filters)
    cap = sources.settings.max_rows
    read = sources.gateway.browse(tbl.name, columns, operators, values, cap, TIERS[tier])
    frame = _frame(read.rows)
    return frame, frame.height >= read.cap


def _require(tbl: Table, needs: Sequence[str]) -> None:
    """Refuse a chart whose table, as the catalog reports it now, lacks a
    column the chart reads - rather than drawing nothing."""
    missing = [c for c in needs if c not in tbl.columns]
    if missing:
        raise ValidationFailed(
            f"table {tbl.name!r} has no column {', '.join(map(repr, missing))} "
            "that this chart reads"
        )


def _frame(rows: Any) -> pl.DataFrame:
    if rows is None:
        return pl.DataFrame()
    if isinstance(rows, pl.DataFrame):
        return rows
    return pl.DataFrame(rows)


def _time_filters(range_from: str, range_to: str) -> list[tuple[str, str, Any]]:
    """`time` bounds for a read, each only where given. A bound is coerced by
    build_filters, which requires an explicit timezone."""
    out: list[tuple[str, str, Any]] = []
    if range_from:
        out.append(("time", "ge", range_from))
    if range_to:
        out.append(("time", "lt", range_to))
    return out


def _instants(frame: pl.DataFrame, column: str) -> list[str]:
    """A timestamp column as text Plotly reads as UTC: to the microsecond, no
    zone, never moved into the server's local time."""
    col = pl.col(column)
    kind = frame.schema[column]
    if isinstance(kind, pl.Datetime) and kind.time_zone is not None:
        col = col.dt.convert_time_zone("UTC").dt.replace_time_zone(None)
    return frame.select(col.dt.to_string("%Y-%m-%d %H:%M:%S%.6f")).to_series().to_list()


def _nanos(frame: pl.DataFrame, column: str) -> list[int]:
    col = pl.col(column)
    kind = frame.schema[column]
    if isinstance(kind, pl.Datetime) and kind.time_zone is not None:
        col = col.dt.replace_time_zone(None)
    return frame.select(col.dt.cast_time_unit("ns").cast(pl.Int64)).to_series().to_list()


# ------------------------------------------------------------- downsampling


def lttb(xs: Sequence[float], ys: Sequence[float], n: int) -> list[int]:
    """The indices LTTB keeps to draw (xs, ys) in `n` points.

    Largest triangle three buckets (Steinarsson, 2013): the first and last
    points, and from each of n-2 buckets between them the point forming the
    largest triangle with the point kept before it and the next bucket's
    mean. Peaks and troughs survive; a flat run collapses. `xs` must be
    ascending, and no y may be missing.
    """
    m = len(xs)
    if n >= m:
        return list(range(m))
    if n < 3:
        return [0, m - 1][:n]
    every = (m - 2) / (n - 2)
    kept = [0]
    a = 0
    for i in range(n - 2):
        start = int(i * every) + 1
        end = int((i + 1) * every) + 1
        nxt = slice(end, min(int((i + 2) * every) + 1, m))
        nx, ny = xs[nxt], ys[nxt]
        if len(nx):
            avg_x, avg_y = sum(nx) / len(nx), sum(ny) / len(ny)
        else:
            avg_x, avg_y = xs[m - 1], ys[m - 1]
        best, area = start, -1.0
        for j in range(start, end):
            here = abs((xs[a] - avg_x) * (ys[j] - ys[a]) - (xs[a] - xs[j]) * (avg_y - ys[a]))
            if here > area:
                best, area = j, here
        kept.append(best)
        a = best
    kept.append(m - 1)
    return kept


def reduce(xs: Sequence[float], ys: Sequence[float | None], n: int) -> tuple[list[int], bool]:
    """The indices of (xs, ys) to draw in at most `n` points, and whether any
    were dropped.

    A missing y is a gap and stays one: the first missing point of each run
    is kept, so a line still breaks where the data does, and LTTB chooses
    among the rest with what budget is left.
    """
    m = len(xs)
    if m <= n:
        return list(range(m)), False
    present = [(i, y) for i, y in enumerate(ys) if y is not None]
    gaps = [i for i in range(m) if ys[i] is None and (i == 0 or ys[i - 1] is not None)]
    if len(gaps) > n // 4:  # a gap per point is noise: keep an even share of them
        gaps = gaps[:: -(-len(gaps) // max(1, n // 4))]
    budget = max(n - len(gaps), min(n, 3))
    chosen = lttb([xs[i] for i, _ in present], [y for _, y in present], budget)
    kept = sorted({present[k][0] for k in chosen} | set(gaps))[:n]
    return kept, True


def _pick(values: Sequence[Any], kept: Sequence[int]) -> list[Any]:
    return [values[i] for i in kept]


def _finite(value: Any) -> float | None:
    """A y value Plotly may place: never a null, NaN or infinity, which q
    can hold and a chart must show as a gap rather than as 0 or off-scale."""
    if value is None or isinstance(value, bool):
        return None
    number = float(value)
    return number if number == number and abs(number) != float("inf") else None


# ------------------------------------------------------------ markout curve


class MarkoutParams(ChartParams):
    dataset: Literal["fx_markout", "crypto_markout"] = Field(
        default="fx_markout",
        title="Dataset",
        description="fx_markout is in pips, crypto_markout in bps",
    )
    sym: str = Field(default="", title="Symbol", description="one symbol, or empty for all")
    range_from: AwareInstantOrBlank = Field(
        default="",
        title="From",
        description="fills at or after, ISO-8601 with an offset; empty for the policy's window",
    )
    range_to: AwareInstantOrBlank = Field(
        default="", title="To", description="fills before, ISO-8601 with an offset"
    )
    tier: Tier = Field(default="rdb", title="Tier")


#: Each markout dataset's measure: the column the curve averages, in its unit.
_MARKOUT_MEASURE = {"fx_markout": ("pips", "pips"), "crypto_markout": ("markout_bps", "bps")}


def _markout_read(sources: Sources, p: MarkoutParams) -> Read:
    measure, _ = _MARKOUT_MEASURE[p.dataset]
    # `kind` total only: the other kinds are components of it, and averaging
    # them in would count each fill several times per horizon.
    filters = [("kind", "eq", "total")]
    if p.sym:
        filters.append(("sym", "eq", p.sym))
    frame, cut = _browse(
        sources,
        p.dataset,
        ["venue", "horizon", "kind", measure],
        filters + _time_filters(p.range_from, p.range_to),
        p.tier,
    )
    notes = [f"read cut at the row cap ({sources.settings.max_rows:,} rows)"] if cut else []
    return Read({"markout": frame}, truncated=cut, notes=notes)


def _markout_build(read: Read, p: MarkoutParams, cap: int) -> Built:
    measure, unit = _MARKOUT_MEASURE[p.dataset]
    frame = read.frames["markout"]
    fig = go.Figure()
    fig.update_layout(
        title={"text": f"{p.dataset} by horizon" + (f" · {p.sym}" if p.sym else "")},
        xaxis={"title": {"text": "horizon (s)"}},
        yaxis={"title": {"text": f"mean markout ({unit})"}},
    )
    if frame.is_empty():
        return Built(fig, 0, notes=["no markout rows in this window"])
    horizon = pl.col("horizon")
    if isinstance(frame.schema["horizon"], pl.Duration):
        horizon = horizon.dt.total_nanoseconds()
    curves = (
        frame.with_columns(
            (horizon.cast(pl.Float64) / 1e9).round(9).alias("seconds"),
            pl.col(measure).cast(pl.Float64).fill_nan(None).alias("y"),
        )
        .filter(pl.col("y").is_finite() | pl.col("y").is_null())
        .group_by("venue", "seconds")
        .agg(pl.col("y").mean().alias("mean"), pl.col("y").count().alias("fills"))
        .sort("venue", "seconds")
    )
    # Busiest venue first, so a cut drops the venues with the least behind them.
    order = (
        curves.group_by("venue").agg(pl.col("fills").sum()).sort("fills", descending=True)["venue"]
    )
    points, shown, dropped = 0, 0, 0
    for venue in order.to_list():
        curve = curves.filter(pl.col("venue") == venue)
        if points + curve.height > cap:
            dropped += 1
            continue
        fig.add_trace(
            go.Scatter(
                x=curve["seconds"].to_list(),
                y=[_finite(v) for v in curve["mean"].to_list()],
                customdata=curve["fills"].to_list(),
                hovertemplate="%{x}s: %{y:.3f} " + unit + " over %{customdata} fills",
                mode="lines+markers",
                name=str(venue),
                connectgaps=False,
            )
        )
        points += curve.height
        shown += 1
    notes = [f"{dropped} venue(s) left out to stay under {cap} points"] if dropped else []
    return Built(fig, points, truncated=bool(dropped), notes=notes)


# --------------------------------------------------- backfill coverage heatmap


class CoverageParams(ChartParams):
    days: int = Field(default=14, ge=1, le=92, title="Days", description="days back from the end")
    end: str = Field(
        default="",
        title="End date",
        description="the last day shown, YYYY-MM-DD; empty for today (UTC)",
        pattern=r"^(\d{4}-\d{2}-\d{2})?$",
    )

    @field_validator("end")
    @classmethod
    def _end_is_a_day_the_window_fits(cls, end: str, info: ValidationInfo) -> str:
        """The pattern lets through 2026-02-30; the calendar does not (#2078).
        Refused here, so a bad end is a 422 before any read, and the build
        never steps back past the first day there is."""
        if not end:
            return end
        try:
            last = dt.date.fromisoformat(end)
        except ValueError:
            raise ValueError(f"{end!r} is not a date") from None
        back = info.data.get("days", 1) - 1
        if last.toordinal() - back < dt.date.min.toordinal():
            first = dt.date.min.isoformat()
            raise ValueError(f"{back} day(s) back from {end} is before the first day ({first})")
        return end


#: A worker's state on a day, worst first: what a cell shows when two runs of
#: one worker cover the same day. The number is the cell's colour.
_STATE_RANK = {
    "failed": 5,
    "abandoned": 4,
    "stopped": 4,
    "running": 3,
    "starting": 3,
    "completed": 2,
    "idle": 1,
}
_STATE_COLOUR = [
    (0.0, "#eef1f5"),  # 0: no run covers the day
    (0.2, "#eef1f5"),
    (0.2, "#c8d1dc"),  # 1: idle - ran, nothing to do
    (0.4, "#c8d1dc"),
    (0.4, "#2f9e6e"),  # 2: completed
    (0.6, "#2f9e6e"),
    (0.6, "#3b7dd8"),  # 3: running
    (0.8, "#3b7dd8"),
    (0.8, "#e0a020"),  # 4: abandoned
    (0.9, "#e0a020"),
    (0.9, "#d1453b"),  # 5: failed
    (1.0, "#d1453b"),
]


def _covered_days(range_from: str, range_to: str) -> tuple[dt.date, dt.date] | None:
    """The first and last day a status window `[range_from, range_to)` touches,
    or None when either bound is not an instant. The end is exclusive, so a
    window ending at midnight does not cover the day it ends on."""
    try:
        start = dt.datetime.fromisoformat(range_from[:19])
        end = dt.datetime.fromisoformat(range_to[:19])
    except ValueError:
        return None
    last = end.date()
    if end.time() == dt.time() and end > start:
        last -= dt.timedelta(days=1)
    return start.date(), last


def _coverage_read(sources: Sources, p: CoverageParams) -> Read:
    if sources.target != workspaces.LOCAL:
        # The status files are this server's own stack's: a target's are on
        # its machine, out of reach, as /ops/backfill is (#2585).
        raise workspaces.TargetNotScoped(
            f"chart 'backfill_coverage' reads this server's own backfill status files, "
            f"so it is not available in workspace {sources.target} - switch to local for it"
        )
    statuses, unreadable = status.read_dir(status.status_directory(sources.settings))
    rows = [
        {
            "worker": s.worker,
            "state": "abandoned" if s.abandoned else s.state,
            "range_from": s.range_from,
            "range_to": s.range_to,
            "updated_at": s.updated_at,
        }
        for s in statuses
    ]
    notes = [f"{len(unreadable)} status file(s) unreadable"] if unreadable else []
    schema = dict.fromkeys(("worker", "state", "range_from", "range_to", "updated_at"), pl.String)
    return Read({"status": pl.DataFrame(rows, schema=schema)}, notes=notes)


def _coverage_build(read: Read, p: CoverageParams, cap: int) -> Built:
    end = dt.date.fromisoformat(p.end) if p.end else dt.datetime.now(dt.UTC).date()
    days = [end - dt.timedelta(days=k) for k in range(p.days - 1, -1, -1)]
    # Most recently updated first: when there are more workers than fit, the
    # ones that ran lately are the ones kept.
    frame = read.frames["status"].sort("updated_at", descending=True)
    workers: list[str] = list(dict.fromkeys(frame["worker"].to_list()))
    fit = max(1, cap // len(days))
    cut = len(workers) > fit
    workers = sorted(workers[:fit])
    grid = {w: [0] * len(days) for w in workers}
    for row in frame.iter_rows(named=True):
        if row["worker"] not in grid:
            continue
        span = _covered_days(row["range_from"], row["range_to"])
        if span is None:
            continue
        first, last = span
        rank = _STATE_RANK.get(row["state"], 0)
        for k, day in enumerate(days):
            if first <= day <= last:
                grid[row["worker"]][k] = max(grid[row["worker"]][k], rank)
    names = {
        r: " / ".join(k for k, v in _STATE_RANK.items() if v == r and k != "starting")
        for r in set(_STATE_RANK.values())
    } | {0: "not covered"}
    z = [grid[w] for w in workers]
    fig = go.Figure(
        go.Heatmap(
            z=z,
            x=[d.isoformat() for d in days],
            y=workers,
            text=[[names[v] for v in r] for r in z],
            hovertemplate="%{y} · %{x}: %{text}<extra></extra>",
            zmin=0,
            zmax=5,
            colorscale=_STATE_COLOUR,
            showscale=False,
            xgap=2,
            ygap=2,
        )
    )
    fig.update_layout(
        title={"text": f"Backfill coverage · {days[0]} to {days[-1]}"},
        yaxis={"autorange": "reversed"},
        xaxis={"type": "category"},
    )
    notes = [] if workers else ["no backfill status files"]
    if cut:
        notes.append(f"{len(frame['worker'].unique()) - len(workers)} worker(s) left out")
    return Built(fig, len(workers) * len(days), truncated=cut, notes=notes)


# --------------------------------------------------- mid and spread with fills


class MidSpreadParams(ChartParams):
    sym: str = Field(min_length=1, title="Symbol")
    fills: Literal["executions", "trades"] = Field(
        default="executions",
        title="Fills",
        description="executions are venue fills, trades client fills",
    )
    source: str = Field(
        default="",
        title="Source",
        description="one market_data source (venue), or empty for a line per source",
    )
    range_from: AwareInstantOrBlank = Field(
        default="", title="From", description="ISO-8601 with an offset"
    )
    range_to: AwareInstantOrBlank = Field(
        default="", title="To", description="ISO-8601 with an offset"
    )
    tier: Tier = Field(default="rdb", title="Tier")


#: Each fills table's price column.
_FILL_PRICE = {"executions": "price", "trades": "trade_price"}
#: The book a mid and spread are read from: its first level each side.
_BOOK = "market_data"


def _mid_spread_read(sources: Sources, p: MidSpreadParams) -> Read:
    window = [("sym", "eq", p.sym), *_time_filters(p.range_from, p.range_to)]
    # market_data holds one book per (sym, source) (src/etl/streaming/
    # market_data.q): each source's rows are its own book, never one market.
    on = [("source", "eq", p.source)] if p.source else []
    book, book_cut = _browse(
        sources, _BOOK, ["time", "source", "bid_prices", "ask_prices"], window + on, p.tier
    )
    fills, fills_cut = _browse(
        sources, p.fills, ["time", "side", _FILL_PRICE[p.fills]], window, p.tier
    )
    cap = f"{sources.settings.max_rows:,}"
    notes = [f"{name} read cut at the row cap ({cap} rows)" for name, cut in
             ((_BOOK, book_cut), (p.fills, fills_cut)) if cut]  # fmt: skip
    return Read({"book": book, "fills": fills}, truncated=book_cut or fills_cut, notes=notes)


def _quoted(bid: Any, ask: Any, f: Callable[[float, float], float]) -> float | None:
    """`f(bid, ask)`, or a gap when either side is missing - an empty level,
    a null or an infinity is never a price."""
    b, a = _finite(bid), _finite(ask)
    return None if b is None or a is None else f(b, a)


def _mid_spread_build(read: Read, p: MidSpreadParams, cap: int) -> Built:
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, row_heights=[0.72, 0.28], vertical_spacing=0.06
    )
    fig.update_layout(title={"text": f"{p.sym} · mid and spread with {p.fills}"})
    fig.update_yaxes(title_text="mid", row=1, col=1)
    fig.update_yaxes(title_text="spread", row=2, col=1)
    # A third of the budget each: mid, spread, fills. A source's rows replace
    # its own book only, so each source is its own pair of lines: interleaving
    # two venues' books into one line draws a mid neither of them quoted.
    share = cap // 3
    points, downsampled, dropped = 0, False, 0
    book = read.frames["book"]
    if not book.is_empty():
        book = book.sort("time").with_columns(
            pl.col("source").cast(pl.String).fill_null(""),
            pl.col("bid_prices").list.first().cast(pl.Float64).alias("bid"),
            pl.col("ask_prices").list.first().cast(pl.Float64).alias("ask"),
        )
        # Busiest source first, so a cut drops the sources with least behind them.
        order = (
            book.group_by("source")
            .len()
            .sort(["len", "source"], descending=[True, False])["source"]
            .to_list()
        )
        fit = max(1, share // 3)
        dropped = max(0, len(order) - fit)
        order = order[:fit]
        each = share // len(order)
        for source in sorted(order):
            quotes = book.filter(pl.col("source") == source)
            xs = _nanos(quotes, "time")
            times = _instants(quotes, "time")
            pairs = list(zip(quotes["bid"].to_list(), quotes["ask"].to_list(), strict=True))
            mid = [_quoted(bid, ask, lambda b, a: (a + b) / 2) for bid, ask in pairs]
            spread = [_quoted(bid, ask, lambda b, a: a - b) for bid, ask in pairs]
            for name, ys, row in (("mid", mid, 1), ("spread", spread, 2)):
                kept, cut = reduce(xs, ys, each)
                downsampled |= cut
                points += len(kept)
                fig.add_trace(
                    go.Scatter(
                        x=_pick(times, kept),
                        y=_pick(ys, kept),
                        name=f"{name} · {source or '(no source)'}",
                        legendgroup=source,
                        mode="lines",
                        connectgaps=False,
                        line={"width": 1.4},
                    ),
                    row=row,
                    col=1,
                )
    fills = read.frames["fills"]
    if not fills.is_empty():
        fills = fills.sort("time")
        price = _FILL_PRICE[p.fills]
        for label, sign, symbol, colour in (
            ("buy", 1, "triangle-up", "#2f9e6e"),
            ("sell", -1, "triangle-down", "#d1453b"),
        ):
            side = fills.filter(pl.col("side") == sign)
            if side.is_empty():
                continue
            ys = [_finite(v) for v in side[price].to_list()]
            kept, cut = reduce(_nanos(side, "time"), ys, share // 2)
            downsampled |= cut
            points += len(kept)
            fig.add_trace(
                go.Scatter(
                    x=_pick(_instants(side, "time"), kept),
                    y=_pick(ys, kept),
                    name=f"{label} fills",
                    mode="markers",
                    marker={"symbol": symbol, "color": colour, "size": 8},
                ),
                row=1,
                col=1,
            )
    notes = [] if points else [f"no book or fills for {p.sym} in this window"]
    if dropped:
        notes.append(f"{dropped} source(s) left out to stay under {cap} points")
    return Built(fig, points, downsampled=downsampled, truncated=bool(dropped), notes=notes)


# ------------------------------------------------------------- ops latency


class LatencyParams(ChartParams):
    limit: int = Field(
        default=1000, ge=1, le=5000, title="Rows per process", description="newest first"
    )


def _latency_read(sources: Sources, p: LatencyParams) -> Read:
    limit = min(p.limit, sources.settings.max_rows)
    results = sources.fleet.per_process(ops.USAGE, limit)
    rows, unreachable = ops.merge_usage(results)
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["source_process"]] = counts.get(row["source_process"], 0) + 1
    full = sorted(name for name, n in counts.items() if n >= limit)
    notes = [f"{u['process']} unreachable: {u['error']}" for u in unreachable]
    if full:
        notes.append(f"only the newest {limit} queries read from {', '.join(full)}")
    frame = pl.DataFrame(
        [
            {
                "process": str(r["source_process"]),
                "status": _text(r.get("status")),
                "timer": r.get("timer"),
            }
            for r in rows
        ],
        schema={"process": pl.String, "status": pl.String, "timer": pl.Int64},
    )
    return Read({"usage": frame}, truncated=bool(full), notes=notes)


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode()
    return "" if value is None else str(value)


def _latency_thin(ms: list[float], share: int) -> list[float]:
    """At most `share` of the durations `ms`, at evenly spaced ranks that
    include both ends.

    A box is drawn from its quantiles and its extremes, and on a latency box
    the slowest query is the one the operator is looking for. A stride over
    the sorted sample (`sorted(ms)[::step]`) kept the fastest and usually
    dropped the slowest (#2076); ranks spread from 0 to len-1 keep both, and
    each kept rank is within half a step of every quantile's. A share of one
    keeps the slowest alone. A sample within its share is returned as is.
    """
    if len(ms) <= share:
        return ms
    s = sorted(ms)
    if share < 2:
        return s[-1:] if share == 1 else []
    last = len(s) - 1
    return [s[round(k * last / (share - 1))] for k in range(share)]


def _latency_build(read: Read, p: LatencyParams, cap: int) -> Built:
    # Only a completed query has a duration: TorQ logs `b`efore and `e`rror
    # rows too, whose timer is not one (lib/torq/code/handlers/logusage.q).
    # The timer is MICROSECONDS, not nanoseconds: logusage.q writes
    # `long$.001*endp-startp, a span a thousand times shorter whose ns count
    # is the elapsed µs - `long$.001*0D00:00:01 is 1000000 (#2075).
    done = (
        read.frames["usage"]
        .filter((pl.col("status") == "c") & pl.col("timer").is_not_null())
        .with_columns((pl.col("timer") / 1e3).alias("ms"))
        .sort("process")
    )
    fig = go.Figure()
    fig.update_layout(
        title={"text": "Query latency per process"},
        yaxis={"title": {"text": "ms"}, "type": "log"},
        showlegend=False,
    )
    points, downsampled = 0, False
    processes = done["process"].unique(maintain_order=True).to_list()
    share = cap // max(1, len(processes))
    for process in processes:
        ms = done.filter(pl.col("process") == process)["ms"].to_list()
        if len(ms) > share:
            ms = _latency_thin(ms, share)
            downsampled = True
        points += len(ms)
        fig.add_trace(go.Box(y=ms, name=process, boxpoints="outliers"))
    notes = [] if points else ["no completed queries in the usage logs"]
    return Built(fig, points, downsampled=downsampled, notes=notes)


# ------------------------------------------------------------------ registry

CHARTS: dict[str, Chart] = {
    c.name: c
    for c in (
        Chart(
            name="markout_curve",
            title="Markout curve by horizon",
            description="Mean markout of total rows per horizon, one line per venue",
            params=MarkoutParams,
            max_points=600,
            read=_markout_read,
            build=_markout_build,
            requires=("desk", "read"),
        ),
        Chart(
            name="backfill_coverage",
            title="Backfill coverage",
            description="Each backfill worker's last run per day, from its status file",
            params=CoverageParams,
            max_points=4000,
            read=_coverage_read,
            build=_coverage_build,
            requires=("backfills", "read"),
        ),
        Chart(
            name="mid_spread_fills",
            title="Mid and spread with fills",
            description="Top-of-book mid and spread per market_data source, with fills overlaid",
            params=MidSpreadParams,
            max_points=3000,
            read=_mid_spread_read,
            build=_mid_spread_build,
            requires=("desk", "read"),
        ),
        Chart(
            name="ops_latency",
            title="Query latency per process",
            description="Completed query durations from each process's usage log",
            params=LatencyParams,
            max_points=5000,
            read=_latency_read,
            build=_latency_build,
            requires=("usage", "read"),
        ),
    )
}


class ChartNotFound(FrontendError):
    """`/charts/{name}` names no prepared chart. 404, as an unknown target
    is (#2224): a 422 from this route means the body failed the chart's own
    schema, and the two must not be told apart only by reading `detail`."""

    status_code = 404


def chart(name: str) -> Chart:
    try:
        return CHARTS[name]
    except KeyError:
        known = ", ".join(sorted(CHARTS))
        raise ChartNotFound(f"unknown chart {name!r}; charts are: {known}") from None
