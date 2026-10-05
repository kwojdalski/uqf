"""`uqs stream ...`: streaming jobs, short of running them (#663).

`preview` fetches and normalizes one page of a polling feed and says what it
would publish and where its cursor would move, publishing nothing and saving
nothing. It is not `uqs backfill --mode dry-run`: that rehearses a bounded
range; this looks at the one page a running feed would take next.
"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table

from uqs.cli.shared import _die, _paths, app, console
from uqs.paths import UqsError
from uqs.stack import stream_preview
from uqs.stack.trace_render import render_query_trace

stream_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Streaming jobs: preview a polling feed's next page without publishing it.",
)
app.add_typer(stream_app, name="stream")

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


@stream_app.command("preview")
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
            "time or error - as `uqs backfill --trace` does. Off by default.",
        ),
    ] = False,
    last: Annotated[
        str | None,
        typer.Option(
            "--last",
            help="Preview the recent data instead: the rows in [now - LAST, now), e.g. 30s, "
            "5m, 2h. Fetched from a temporary cursor - the saved cursor and the live poll "
            "are untouched, and this is not the running job's next page.",
        ),
    ] = None,
) -> None:
    """One page of a polling feed: what it would publish, and where its cursor
    would move - with nothing published and no cursor saved.

    The page is fetched for real, through the feed's own fetch, so a live
    source is queried. Exits 1 when the output does not fit the plant's
    tables, or the cursor would not advance.
    """
    try:
        last_ns = None if last is None else stream_preview.duration_ns(last)
        r = stream_preview.preview(_paths(), job, sample, trace=trace, last_ns=last_ns)
    except UqsError as exc:
        _print_trace(getattr(exc, "trace", []))
        _die(exc)
        return
    _print_trace(r.get("trace", []))
    mode = "dry run" if dry_run else "preview"
    recent = r.get("mode") == "recent"
    if recent:
        console.print(
            f"[bold]recent-data sample of {job}[/] - not the running job's next page; "
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
