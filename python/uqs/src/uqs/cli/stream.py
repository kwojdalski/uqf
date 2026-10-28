"""`uqs job preview`: a streaming job, short of running it (#663).

It fetches and normalizes one page of a polling feed and says what it
would publish and where its cursor would move, publishing nothing and saving
nothing. It is not `uqs job backfill --mode dry-run`: that rehearses a bounded
range; this looks at the one page a running feed would take next.
"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table

from uqs import timerange
from uqs.cli.json_out import emit, json_opt
from uqs.cli.range_options import DateOpt, LastOpt, RangeFromOpt, RangeToOpt
from uqs.cli.shared import (
    JOB_OPERATIONS_PANEL,
    _die,
    _paths,
    console,
    job_app,
)
from uqs.paths import UqsError
from uqs.stack import stream_preview
from uqs.stack.trace_render import render_query_trace

_LIVE = {
    "live": "[green]live[/] - its source's credential is set",
    "fixture": "[yellow]fixture[/] - its source has no credential, so this is not live data",
    "unknown": "[dim]unknown - its poll names no source[/]",
}


def _cursor(value: object) -> str:
    """A cursor as one line: a timestamp as it is, a compound one as its fields."""
    if isinstance(value, dict):
        return ", ".join(f"{k}={v}" for k, v in value.items())
    return str(value) if value not in (None, "") else ""


def _print_trace(lines: list) -> None:
    """The child's traced lines, a query shown as a block as `uqs logs` shows it."""
    for level, source, message in lines:
        console.print(f"[dim]{level} {source}[/] ", end="")
        console.print(render_query_trace(message), markup=False, highlight=False)


PreviewJson = json_opt(
    '{"job", "mode", "state": previewed/idle/invalid, "live", "cursor", "next_cursor", '
    '"advances", "fetched", "rows": {table: count}, "sample": {table: [row, ...]}, '
    '"failures": [...], "trace": [{level, source, message}]} - '
    'with --last, also "window" and "kept"'
)


@job_app.command("preview", rich_help_panel=JOB_OPERATIONS_PANEL)
def preview(
    job: Annotated[str, typer.Argument(help="A polling feed - a streaming job that declares poll")],
    sample: Annotated[
        int, typer.Option("--sample", min=1, max=1000, help="Rows of each table to show")
    ] = 5,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="A preview is always dry: nothing published, no cursor saved.",
        ),
    ] = True,
    trace: Annotated[
        bool,
        typer.Option(
            "--trace",
            help="Show every query the fetch sends - before it goes, and with its rows, "
            "time or error - as `uqs job backfill --trace` does. Off by default.",
        ),
    ] = False,
    range_from: RangeFromOpt = None,
    range_to: RangeToOpt = None,
    day: DateOpt = None,
    last: LastOpt = None,
    as_json: PreviewJson = False,
) -> None:
    """One page of a polling feed: what it would publish, and where its cursor
    would move - with nothing published and no cursor saved.

    The page is fetched for real, through the feed's own fetch, so a live
    source is queried. Exits 1 when the output does not fit the plant's
    tables, or the cursor would not advance.

    With a time range - --from/--to, --date or --last - it previews the rows
    in that window instead, fetched from a temporary cursor: the saved cursor
    and the live poll are untouched, and it is not the running job's next page.
    e.g. `uqs job preview vectorize2 --last 30s`
    """
    try:
        given = (range_from, range_to, day, last)
        window = (
            None if given == (None,) * 4 else timerange.resolve(range_from, range_to, day, last)
        )
        r = stream_preview.preview(_paths(), job, sample, trace=trace, window=window)
    except UqsError as exc:
        if not as_json:
            _print_trace(getattr(exc, "trace", []))
        _die(exc)
        return
    if as_json:
        traced = [{"level": lv, "source": src, "message": m} for lv, src, m in r.get("trace", [])]
        emit(r | {"job": job, "trace": traced})
        if r["state"] == "invalid":
            raise typer.Exit(code=1)
        return
    _print_trace(r.get("trace", []))
    mode = "dry run" if dry_run else "preview"
    recent = r.get("mode") in ("recent", "window")
    if recent:
        what = "recent-data sample" if r.get("mode") == "recent" else "sample"
        console.print(
            f"[bold]{what} of {job}[/] - not the running job's next page; "
            "nothing published, saved cursor untouched"
        )
    else:
        console.print(f"[bold]{mode} of {job}[/] - nothing published, no cursor saved")
    console.print(f"source  {_LIVE.get(r.get('live', 'unknown'), r.get('live'))}")
    if recent:
        window = r.get("window", {})
        console.print(f"window  [{window.get('from')}, {window.get('to')}) UTC")
        console.print(f"start   {_cursor(window.get('start_cursor'))} (temporary)")
        limit = r.get("page_limit")
        cap = f", page limit {limit}" if limit is not None else ", page limit not declared"
        console.print(f"fetched {r['fetched']} row(s), {r.get('kept', 0)} in the window{cap}")
        if r.get("limited"):
            console.print("[yellow]the page was full: the window may hold rows past these[/]")
    else:
        cursor = _cursor(r.get("cursor")) or "none - a first run"
        console.print(f"cursor  {cursor}")
    if r["state"] == "idle":
        idle = (
            "nothing in the window"
            if recent
            else "nothing after the cursor: a run would publish nothing now"
        )
        console.print(f"[dim]{idle}[/]")
        return
    move = "[green]advances[/]" if r.get("advances") else "[red]would not advance[/]"
    console.print(f"next    {_cursor(r.get('next_cursor')) or '-'}  ({move})")
    if not recent:
        console.print(f"fetched {r['fetched']} row(s)")
    for table_name, count in r.get("rows", {}).items():
        rows = r.get("sample", {}).get(table_name, [])
        console.print(f"\n[bold]{table_name}[/]: {count} row(s), first {len(rows)}")
        shown = Table()
        for column in rows[0] if rows else []:
            shown.add_column(column)
        for row in rows:
            shown.add_row(*(str(v) for v in row.values()))
        console.print(shown)
    for failure in r.get("failures", []):
        console.print(failure, style="bold red", markup=False)
    if r["state"] == "invalid":
        raise typer.Exit(code=1)
