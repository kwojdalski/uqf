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
) -> None:
    """One page of a polling feed: what it would publish, and where its cursor
    would move - with nothing published and no cursor saved.

    The page is fetched for real, through the feed's own fetch, so a live
    source is queried. Exits 1 when the output does not fit the plant's
    tables, or the cursor would not advance.
    """
    try:
        r = stream_preview.preview(_paths(), job, sample)
    except UqsError as exc:
        _die(exc)
        return
    mode = "dry run" if dry_run else "preview"
    console.print(f"[bold]{mode} of {job}[/] - nothing published, no cursor saved")
    console.print(f"source  {_LIVE.get(r.get('live', 'unknown'), r.get('live'))}")
    cursor = r.get("cursor") or "none - a first run"
    console.print(f"cursor  {cursor}")
    if r["state"] == "idle":
        console.print("[dim]nothing after the cursor: a run would publish nothing now[/]")
        return
    move = "[green]advances[/]" if r.get("advances") else "[red]would not advance[/]"
    console.print(f"next    {r.get('next_cursor') or '-'}  ({move})")
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
