"""`uqs cron`: add, list, remove, install, run, status (#2987).

The command line of uqs.cron, which holds the logic. What only this layer can
do is parse an entry's `run` with the real command tree - strictly, every value
converted, and nothing executed - and hand uqs.cron.check the command's path
and the options it was given.
"""

from __future__ import annotations

import shlex
import shutil
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import typer
from typer._click.exceptions import ClickException

from uqs.cli.common import safety
from uqs.cli.common.json_out import emit, json_opt
from uqs.cli.common.shared import _die, app, console
from uqs.cli.common.target_confirm import root_command, subcommands
from uqs.cron import check, crontab, entries, runs, schedule
from uqs.cron.entries import Entry
from uqs.paths import UqsError, repo_root

cron_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Scheduled uqs commands: add, list, remove, install, run, status.\n\nEach entry is "
    "a five-field cron expression, a time zone (UTC unless given) and the uqs command to "
    "run, installed into your crontab inside one managed block.",
)
app.add_typer(cron_app, name="cron")

EntryArg = Annotated[
    str, typer.Argument(metavar="ENTRY", help="The entry's name, e.g. deals-daily")
]


def parse_run(words: Sequence[str]) -> check.Parsed:
    """`words` parsed by the uqs command tree as `uqs WORDS` would be, running nothing:
    a word it does not take, a missing argument or a bad value is refused here."""
    root = root_command()
    try:
        ctx = root.make_context("uqs", list(words))
        cmd, path = root, []
        while subcommands(cmd):
            rest = [*ctx._protected_args, *ctx.args]
            if not rest:
                raise UqsError(f"`uqs {' '.join(words)}` names a group, not a command")
            name, sub, rest = cmd.resolve_command(ctx, rest)
            if subcommands(sub) and not rest:  # a group alone would print its help
                raise UqsError(f"`uqs {' '.join(words)}` names a group, not a command")
            ctx = sub.make_context(name, list(rest), parent=ctx)
            cmd = sub
            path.append(name)
    except UqsError:
        raise
    except Exception as exc:  # Click's UsageError, BadParameter, Exit for --help
        message = exc.format_message() if isinstance(exc, ClickException) else str(exc)
        raise UqsError(f"`uqs {' '.join(words)}` does not parse: {message}") from None
    given = set()
    for param in cmd.params:
        if getattr(ctx.get_parameter_source(param.name), "name", None) == "COMMANDLINE":
            given.update(param.opts)
    params = ctx.params
    return check.Parsed(
        tuple(path),
        job=params.get("worker") or params.get("job"),
        attachment=params.get("attach"),
        given=frozenset(given),
    )


def _uqs() -> str:
    """The absolute uqs to put on a crontab line: this one, else the one on PATH,
    else this Python running the package."""
    own = Path(sys.argv[0])
    if own.name == "uqs" and own.exists():
        return str(own.absolute())
    found = shutil.which("uqs")
    return str(Path(found).absolute()) if found else f"{sys.executable} -m uqs"


def _context(now: datetime) -> crontab.Context:
    return crontab.Context(
        repo=repo_root(),
        uqs=_uqs(),
        schedules=entries.path(),
        log_dir=entries.directory(),
        host_tz=schedule.host_zone(),
        now=now,
    )


def _load() -> dict[str, Entry]:
    try:
        return entries.load()
    except UqsError as exc:
        _die(exc)


def _entry(name: str, all_: dict[str, Entry]) -> Entry:
    if name not in all_:
        _die(UqsError(f"{name} is not an entry (`uqs cron list` names them)"))
    return all_[name]


@cron_app.command(
    "add",
    cls=safety.marked(
        safety.Effect.CHANGES_STATE,
        dry_run_starts="it runs a backfill once with --mode plan, which starts the worker",
    ),
)
def add(
    name: EntryArg,
    words: Annotated[
        list[str],
        typer.Argument(
            metavar="COMMAND...",
            help="After --, the uqs command to run, as typed after `uqs`",
        ),
    ],
    cron: Annotated[
        str, typer.Option("--cron", metavar="EXPR", help="Five-field cron, e.g. '10 6 * * 1-5'")
    ],
    tz: Annotated[
        str,
        typer.Option(
            "--tz", help="The IANA time zone the expression is read in, e.g. Europe/London"
        ),
    ] = schedule.DEFAULT_TZ,
    dry_run: safety.DryRunOpt = False,
) -> None:
    """Check an entry and save it: uqs cron add ENTRY --cron EXPR [--tz ZONE] -- COMMAND...

    COMMAND is parsed by uqs itself and must be `job backfill JOB ...` or
    `job run JOB --once`: a streaming job is supervised by TorQ, and a reaction
    runs when its window is published. A backfill is run once with `--mode
    plan`, so the stack must be up; with `--attachment`, a time inside its
    end-of-day fence is refused. `uqs cron install` puts it in the crontab.

    e.g.

        uqs cron add deals-daily --cron '10 6 * * 1-5' -- job backfill demo_deals_backfill --last 3d
    """
    from uqs.attach import config as attach_config
    from uqs.model.jobs import job_rows

    now = datetime.now(UTC)
    try:
        current = entries.load()
        if name in current:
            raise UqsError(f"{name} is an entry already: `uqs cron remove {name}` first")
        e = entries.entry(name, cron, shlex.join(words), tz)
        if not schedule.next_runs(e.parsed, e.tz, now, 1):
            raise UqsError(f"{e.cron} never fires")
        parsed = parse_run(e.args)
        check.refuse_unschedulable(parsed, job_rows(repo_root()), attach_config.names())
        plan = check.run_plan(e, parsed, check.default_runner(repo_root()))
        if plan is not None:
            check.refuse_in_fence(e, plan, now)
        nexts = schedule.next_runs(e.parsed, e.tz, now, 1)
        if dry_run:
            console.print(f"{name}: {e.cron} {e.tz} - uqs {e.run} (next {nexts[0].isoformat()})")
            console.print(safety.dry_run_note())
            return
        file = entries.save({**current, name: e})
    except UqsError as exc:
        _die(exc)
    console.print(f"saved {name} in {file}: next run {nexts[0].isoformat()}")
    console.print("`uqs cron install` puts it in the crontab")


def _rows() -> list[dict[str, Any]]:
    now = datetime.now(UTC)
    out = []
    for e in entries.load().values():
        nexts = schedule.next_runs(e.parsed, e.tz, now, 3)
        out.append(
            {
                "entry": e.name,
                "cron": e.cron,
                "tz": e.tz,
                "run": e.run,
                "next": ", ".join(t.strftime("%Y-%m-%d %H:%M %Z") for t in nexts),
                "next_utc": ", ".join(t.astimezone(UTC).strftime("%Y-%m-%d %H:%M") for t in nexts),
            }
        )
    return out


def _list_command() -> Any:
    from uqs.cli.config.lists import _noun_list

    return _noun_list(
        "entries",
        "List the scheduled entries: each with its next runs in its own zone and in UTC.",
        rows=_rows,
        columns=("entry", "cron", "tz", "run", "next", "next_utc"),
    )


cron_app.command("list", cls=safety.ReadOnly)(_list_command())


@cron_app.command("remove", cls=safety.ChangesState)
def remove(name: EntryArg, dry_run: safety.DryRunOpt = False) -> None:
    """Remove an entry; `uqs cron install` then takes it out of the crontab."""
    current = _load()
    _entry(name, current)
    if dry_run:
        console.print(f"would remove {name}")
        console.print(safety.dry_run_note())
        return
    try:
        entries.save({k: v for k, v in current.items() if k != name})
    except UqsError as exc:
        _die(exc)
    console.print(f"removed {name}: `uqs cron install` takes it out of the crontab")


InstallJson = json_opt('{"changed", "diff", "findings": [{kind, entry, line, message}]}')


@cron_app.command("install", cls=safety.ChangesState)
def install(dry_run: safety.DryRunOpt = False, as_json: InstallJson = False) -> None:
    """Write the entries into your crontab, inside the uqs cron block.

    Reads `crontab -l` and writes `crontab -`; every line outside the block is
    written back as it was. An unchanged block writes nothing. A block edited
    by hand is refused, with the edit shown. `--dry-run` prints the diff."""
    current = _load()
    try:
        text = crontab.read()
        planned = crontab.plan(text, current, _context(datetime.now(UTC)))
        orphans = [f for f in crontab.findings(text, current) if f.kind == "orphan"]
        if planned.changed and not dry_run:
            crontab.write(planned.after)
    except UqsError as exc:
        _die(exc)
    if as_json:
        emit({"changed": planned.changed, "diff": planned.diff(), "dry_run": dry_run,
              "findings": [vars(f) for f in orphans]})  # fmt: skip
        return
    for f in orphans:
        console.print(f"[yellow]{f.message}[/]: {f.line}", highlight=False)
    if not planned.changed:
        console.print("the crontab already holds these entries: nothing written")
        return
    console.print(planned.diff(), markup=False, highlight=False, end="")
    if dry_run:
        console.print(safety.dry_run_note())
    else:
        console.print(f"installed {len(current)} entr{'y' if len(current) == 1 else 'ies'}")


@cron_app.command(
    "run",
    cls=safety.marked(
        safety.Effect.CHANGES_STATE,
        own_output="what the crontab calls: the command's output goes to the entry's log",
    ),
)
def run(name: EntryArg) -> None:
    """Run an entry now, as the crontab does: log it, record its outcome, exit with its code.

    A backfill gets --wait. On a crontab line that fires more often than the
    entry (another zone than the host's), a firing that is not the entry's
    exits 0 at once and records nothing."""
    now = datetime.now(UTC)
    current = _load()
    e = _entry(name, current)
    if schedule.guarded(e.parsed, e.tz, schedule.host_zone(), now) and not schedule.due(
        e.parsed, e.tz, now
    ):
        raise typer.Exit(code=0)
    try:
        parsed = parse_run(e.args)
    except UqsError as exc:
        _die(exc)
    raise typer.Exit(code=runs.run(e, parsed.path, repo_root()))


StatusJson = json_opt(
    '{"entries": [{entry, cron, tz, last_started, last_exit_code, state}], '
    '"findings": [{kind, entry, line, message}]}'
)


@cron_app.command("status", cls=safety.ReadOnly)
def status(as_json: StatusJson = False) -> None:
    """Show each entry's last outcome, and crontab lines that do not match the entries.

    An entry is ok, failing, overdue or never run. A line in the block is
    reported when no entry owns it (unknown) or its entry changed since it was
    installed (stale); an entry not in the block is missing; a line outside the
    block that calls `uqs cron run` is an orphan, which uqs never changes."""
    current = _load()
    now = datetime.now(UTC)
    rows = [runs.status(e, now) for e in current.values()]
    try:
        found = crontab.findings(crontab.read(), current)
    except UqsError as exc:
        _die(exc)
    if as_json:
        emit({"entries": rows, "findings": [vars(f) for f in found]})
        return
    if not rows:
        console.print(f"no entries in {entries.path()} - add one with `uqs cron add`")
    for r in rows:
        when = r["last_started"] or "-"
        code = "-" if r["last_exit_code"] is None else r["last_exit_code"]
        console.print(f"{r['entry']}: {r['state']} (last {when}, exit {code})", highlight=False)
    for f in found:
        console.print(f"[yellow]{f.kind}[/]: {f.message}" + (f"\n  {f.line}" if f.line else ""),
                      highlight=False)  # fmt: skip
