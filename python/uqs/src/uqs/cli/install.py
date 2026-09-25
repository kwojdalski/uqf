"""`uqs install-jobs`: bring jobs kept outside the tree - a "sidecar" folder
of sources, workers and streaming jobs - into it, as copies or symlinks.

Its own module because, like `new-job`, it writes into the tree rather than
acting on a running fleet; the logic that decides what goes where is in
stack/install.py, so this is only the wizard around it: show the plan, ask
how, ask before replacing anything, then say how to check it worked.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Group
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

from uqs.cli import completion
from uqs.cli.create import _DERIVED, _plant_tables, _regenerate_derived
from uqs.cli.shared import _die, _paths, app, console
from uqs.model.declarations import Declaration, declaration_calls, read_file, symbols
from uqs.model.pipeline import PipelineKind
from uqs.paths import UqsError
from uqs.stack import install as stack_install
from uqs.stack.install import Item, Kind, Mode, Status

_STATUS_STYLE = {
    Status.NEW: "green",
    Status.SAME: "dim",
    Status.CONFLICT: "bold yellow",
    Status.SKIPPED: "red",
}

_MODE_HELP = {
    Mode.COPY: "the tree owns a snapshot; edit the sidecar and you must install again",
    Mode.SYMLINK: "the sidecar stays the source of truth; edits there are live on the next "
    "start, and moving or deleting it breaks the tree",
}


def _where(item: Item, repo_root: Path) -> str:
    return str(item.destination.relative_to(repo_root)) if item.destination else "-"


def _plan_table(items: list[Item], sidecar: Path, repo_root: Path) -> Table:
    table = Table(title="Plan", title_justify="left", header_style="bold cyan", expand=False)
    table.add_column("File", no_wrap=True)
    table.add_column("Kind", no_wrap=True)
    table.add_column("Declares")
    # The folder only: a file keeps its name, so the full path would repeat it.
    table.add_column("Into", no_wrap=True)
    table.add_column("Status")
    for item in items:
        status = Text(item.status.value, style=_STATUS_STYLE[item.status])
        if item.reason:
            status.append(f"\n{item.reason}", style="dim")
        table.add_row(
            str(item.source.relative_to(sidecar)),
            item.kind.value if item.kind else "-",
            ", ".join(item.names) or "-",
            f"{item.destination.parent.relative_to(repo_root)}/" if item.destination else "-",
            status,
        )
    return table


def _ask_mode() -> Mode:
    choices = Table.grid(padding=(0, 2))
    for mode, help_text in _MODE_HELP.items():
        choices.add_row(f"[bold]{mode.value}[/]", help_text)
    console.print(Panel(choices, title="How should they be installed?", border_style="cyan"))
    answer = Prompt.ask(
        "Mode", choices=[m.value for m in Mode], default=Mode.COPY.value, console=console
    )
    return Mode(answer)


def _resolve_conflicts(items: list[Item], overwrite: bool, yes: bool, repo_root: Path) -> set[Path]:
    """The conflicting destinations to replace: all of them with --overwrite,
    none with --yes alone, and otherwise whichever the operator agrees to."""
    conflicts = [i for i in items if i.status is Status.CONFLICT]
    if overwrite:
        return {i.source for i in conflicts}
    if yes:
        return set()
    return {
        i.source
        for i in conflicts
        if Confirm.ask(
            f"[yellow]{_where(i, repo_root)}[/] exists and differs. Replace it?",
            default=False,
            console=console,
        )
    }


def _declarations(items: list[Item]) -> list[Declaration]:
    """What the installed files declare, read back from where they now are."""
    found: list[Declaration] = []
    for item in items:
        if item.destination is None or item.kind is Kind.SOURCE:
            continue
        try:
            found.extend(read_file(item.destination))
        except UqsError as exc:
            console.print(f"[red]cannot read {item.destination.name}:[/] {exc}")
    return found


def _report_undefined_tables(items: list[Item], declarations: list[Declaration]) -> None:
    """A job or worker writing or reading a table the plant does not define
    starts, and then has nowhere to put its rows - say so now, not at the
    first tick."""
    known = _plant_tables(_paths())
    uses: list[tuple[str, str, tuple[str, ...]]] = []
    for decl in declarations:
        uses += [(decl.name, "reads", decl.subscribe_to), (decl.name, "publishes", decl.publishes)]
    for item in items:
        if item.kind is Kind.WORKER and item.destination is not None:
            for fn, name, fields in declaration_calls(item.destination.read_text()):
                if fn == "qbw.define":
                    uses.append((name, "fills", symbols(fields.get("dataset", ""))))
    for name, verb, used in uses:
        missing = [t for t in used if t not in known]
        if missing:
            console.print(
                f"[yellow]![/] {name} {verb} {', '.join(missing)}, which the plant does not "
                "define: add it to scripts/processes/uqs_tables.q"
            )


def _next_steps(declarations: list[Declaration], sidecar_tests: list[Path]) -> Panel:
    streaming = [d.procname for d in declarations if d.kind is not PipelineKind.BACKFILL]
    workers = [d.name for d in declarations if d.kind is PipelineKind.BACKFILL]
    procs = " ".join(streaming) or "<procname>"
    steps = Table.grid(padding=(0, 2))
    steps.add_column(style="bold cyan", justify="right")
    steps.add_column()
    steps.add_column(style="green")

    def step(what: str, command: str) -> None:
        steps.add_row(f"{len(steps.rows) + 1}.", what, command)

    step("The registry sees them", "uqs list processes")
    step("The exact start line", f"uqs print {procs}")
    if streaming or not workers:
        step("Start them", f"uqs start {procs}")
    for worker in workers:
        step(
            "Run a worker (the fleet must be up)",
            f"uqs backfill {worker} --version v1 --from 2026-09-01 --to 2026-09-02",
        )
    step("Each one answers", "uqs summary --columns status   (Responds: yes)")
    step("How long each took to load", "uqs summary --debug")
    step("Their logs", f"uqs logs {procs} -f")
    step("The whole suite", "q tests/run_tests.q")

    notes = [
        "",
        "[bold]q tests[/] run only once listed: put each in [cyan]tests/q/[/] and add its "
        "namespace to [cyan]nsList[/] in [cyan]tests/run_tests.q[/].",
    ]
    if sidecar_tests:
        notes.append(
            f"  The sidecar carries {', '.join(p.name for p in sidecar_tests)} - not installed."
        )
    notes += [
        "[bold]Down or not answering?[/] Check [cyan]output/uqs/logs/err_<procname>.log[/], "
        "or run it in the foreground: [green]uqs raw -- debug <procname>[/].",
        "[bold]Commit[/] what [cyan]git status[/] now shows, derived files included - "
        "CI checks they are current.",
    ]
    return Panel(
        Group(steps, *notes), title="Next steps: check it is up and running", border_style="green"
    )


@app.command("install-jobs")
def install_jobs(
    sidecar: Annotated[
        Path,
        typer.Argument(
            help="Folder holding the job files, laid out any way: each file is placed by "
            "what it declares",
            exists=True,
            file_okay=False,
            dir_okay=True,
        ),
    ],
    mode: Annotated[
        Mode | None,
        typer.Option(
            "--mode",
            help="copy or symlink. Omit to be asked",
            autocompletion=completion.choices(*(m.value for m in Mode)),
            show_default=False,
        ),
    ] = None,
    overwrite: Annotated[
        bool, typer.Option("--overwrite", help="Replace tree files that differ, without asking")
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show the plan, install nothing")
    ] = False,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes", "-y", help="Ask nothing (needs --mode); conflicts are kept unless --overwrite"
        ),
    ] = False,
) -> None:
    """Install sources, workers and streaming jobs from a folder outside the tree.

    Each .q file goes where src/etl/init.q and the registry look for its kind -
    src/etl/sources, src/etl/workers or src/etl/streaming - decided by what it
    declares (.qsrc.define, .qbw.define, .qstream.define/.qnorm.define), so
    the sidecar can be laid out any way. Then the derived files are
    regenerated, and the next steps say how to check the jobs are running.

        uqs install-jobs ../sidecars

        uqs install-jobs ../sidecars --mode symlink --yes
    """
    repo_root = _paths().repo_root
    sidecar = sidecar.resolve()
    try:
        items = stack_install.plan(sidecar, repo_root)
    except (FileNotFoundError, UqsError) as exc:
        _die(UqsError(str(exc)))
        return

    console.print(
        Panel(
            f"from [cyan]{sidecar}[/]\ninto [cyan]{repo_root}[/]",
            title="uqs install-jobs",
            border_style="cyan",
            expand=False,
        )
    )
    if not items:
        console.print("[yellow]No .q files in that folder.[/]")
        return
    console.print(_plan_table(items, sidecar, repo_root))
    todo = [i for i in items if i.status in (Status.NEW, Status.CONFLICT)]
    tests = [i.source for i in items if i.reason.startswith("a q test")]
    if not todo:
        console.print("[green]Nothing to install:[/] every job file is already in place.")
        return
    if dry_run:
        console.print("[dim]--dry-run: nothing installed[/]")
        return

    if mode is None:
        if yes:
            _die(UqsError("--yes asks nothing, so it needs --mode copy or --mode symlink"))
            return
        mode = _ask_mode()
    replace = _resolve_conflicts(items, overwrite, yes, repo_root)
    todo = [i for i in todo if i.status is Status.NEW or i.source in replace]
    if not todo:
        console.print("[yellow]Nothing installed:[/] every remaining file was kept as it is.")
        return
    if not yes and not Confirm.ask(
        f"Install {len(todo)} file(s) as [bold]{mode.value}[/]?", default=True, console=console
    ):
        console.print("[dim]Nothing installed.[/]")
        raise typer.Exit(code=1)

    for item in todo:
        try:
            stack_install.install(item, mode, overwrite=item.source in replace)
        except OSError as exc:
            console.print(f"[red]✗[/] {item.source.name}: {exc}")
            continue
        console.print(
            f"[green]✓[/] {item.source.name} → {_where(item, repo_root)} [dim]({mode.value})[/]"
        )

    # A file that does not parse, or a job whose edges do not resolve, is
    # refused by the generators - which is the earliest the tree can say so.
    for script, regen in zip(_DERIVED, _regenerate_derived(repo_root), strict=True):
        if regen.returncode == 0:
            console.print(f"[green]✓[/] regenerated via {script}")
        else:
            console.print(
                f"[red]✗ could not regenerate[/] - run `python3 {script}` "
                f"and fix what it reports:\n{regen.stdout}{regen.stderr}"
            )

    in_place = [
        i
        for i in items
        if (i.status is Status.SAME or i in todo) and i.destination and i.destination.exists()
    ]
    declarations = _declarations(in_place)
    _report_undefined_tables(in_place, declarations)
    console.print(_next_steps(declarations, tests))
