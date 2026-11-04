"""`uqs data diff`: one table on two endpoints - this machine's stack or a deploy
target - compared by schema, a bounded sample of rows and published coverage (#2033).

This is the orchestration half: each side named, reached (a target through an ssh
tunnel, as `uqs query --tunnel` reaches one), and handed to q as an address. What
the comparison says is q's - src/etl/core/compare.q - and stack/data_diff.py runs
it. A side that cannot be reached is not a refusal here: it is handed to q with
its reason, and comes back as the `endpoint_unavailable` outcome.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from typing import Annotated, Any

import typer
from rich.markup import escape

from uqs import timerange
from uqs.cli import completion
from uqs.cli import query_target as via_target
from uqs.cli.concepts import target_opt
from uqs.cli.hdb_scope import tables_given
from uqs.cli.json_out import emit, json_opt, timestamp
from uqs.cli.pipelines import needs_pipelines
from uqs.cli.q_login import DEFAULT_USER, QPasswordEnvOpt, QUserOpt, q_password
from uqs.cli.query import _proc_port
from uqs.cli.range_options import DateOpt, LastOpt, RangeFromOpt, RangeToOpt
from uqs.cli.safety import ReadOnly
from uqs.cli.shared import _die, _paths, console, data_app
from uqs.deploy import control, tunnel
from uqs.paths import UqsError, UqsUsageError, repo_root
from uqs.stack import data_diff
from uqs.stack import gateway as stack_gateway

#: Each outcome as the text output colours it.
_STYLE = {
    "equal_in_sample": "green",
    "differs": "red",
    "missing_table": "red",
    "incomplete_coverage": "yellow",
    "endpoint_unavailable": "yellow",
}


def _side_opt(flag: str, which: str) -> Any:
    return Annotated[
        str,
        target_opt(
            flag,
            f"The {which} side: `{data_diff.LOCAL}` for this machine's stack, or a deploy "
            "target, its gateway reached over ssh for the call",
        ),
    ]


LeftOpt = _side_opt("--left", "left")
RightOpt = _side_opt("--right", "right")
DiffJson = json_opt(
    '{"ok", "outcome", "left", "right", "from", "to", "limit", "filters", '
    '"tables": [{table, outcome, label, truncated, read, schema, rows, coverage}]}'
)


@contextmanager
def reach(name: str, user: str, passwd: str) -> Iterator[data_diff.Endpoint]:
    """`name`'s gateway, for the block: this machine's, or a target's through a tunnel.

    An undeclared target is refused - it is a mistake in the command - while one
    that is declared and cannot be reached is yielded with why, for q to report.
    """
    proc = stack_gateway.DEFAULT_PROC
    if name == data_diff.LOCAL:
        try:
            port = _proc_port(proc, None)
        except UqsError as exc:
            yield data_diff.Endpoint(name, error=f"{proc} on this machine: {exc}")
            return
        yield data_diff.Endpoint(name, address=f"localhost:{port}")
        return
    control.remote_for(name, repo_root())  # refuses a target nobody declared
    with ExitStack() as stack:
        try:
            via = stack.enter_context(via_target.connect(name, proc, None, None, user, passwd))
        except Exception as exc:  # ssh, the release's port list or the tunnel: all one outcome
            said = tunnel.scrub(str(exc), [passwd])
            yield data_diff.Endpoint(name, error=f"{proc} on target {name}: {said}")
            return
        yield data_diff.Endpoint(name, address=f"{via_target.LOOPBACK}:{via.port}")


def _utc(value: Any) -> Any:
    """Every time in a report as ISO-8601 in UTC: q's `.j.j` writes a timestamp
    with no zone, and each one here is `.z.p`'s."""
    if isinstance(value, dict):
        times = ("from", "to", "range_from", "range_to")
        return {k: timestamp(v, naive="utc") if k in times else _utc(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_utc(v) for v in value]
    return value


def render(report: dict) -> None:
    """The report as text: the window, then each table's outcome and its specifics."""
    console.print(
        f"[bold]{escape(report['left'])}[/] (left) against [bold]{escape(report['right'])}[/] "
        f"(right), [{report['from']}, {report['to']}), at most {report['limit']} rows per side"
    )
    if report["filters"]:
        console.print(f"  filters  {escape(' and '.join(report['filters']))}")
    for t in report["tables"]:
        style = _STYLE.get(t["outcome"], "white")
        console.print(f"\n[{style}]{t['outcome']}[/]  [bold]{escape(t['table'])}[/]")
        console.print(f"  {escape(t['label'])}")
        if t["key"]:
            console.print(f"  paired on {', '.join(t['key'])} ({t['basis']})", style="dim")
        for s in t["schema"]:
            console.print(f"  schema   {s['column']}: {escape(s['left'])} -> {escape(s['right'])}")
        for side in ("left", "right"):
            for g in t["coverage"][side]["gaps"]:
                gap = f"[{g['range_from']}, {g['range_to']})"
                console.print(f"  coverage {side} has not published {gap}", markup=False)
        for r in t["rows"]:
            console.print(f"  row      {escape(r['rowkey'])}: {r['kind']} - {escape(r['detail'])}")


@data_app.command(
    "diff",
    cls=needs_pipelines("reads this tree's declarations of each table's row key", ReadOnly),
)
def diff(
    left: LeftOpt,
    right: RightOpt,
    table: Annotated[
        list[str],
        typer.Option(
            "--table",
            metavar="TABLE",
            help="Compare this table; repeatable, and TABLE,TABLE also works",
            autocompletion=completion.plant_table,
        ),
    ],
    range_from: RangeFromOpt = None,
    range_to: RangeToOpt = None,
    day: DateOpt = None,
    last: LastOpt = None,
    filters: Annotated[
        list[str] | None,
        typer.Option(
            "--filter",
            metavar="COLUMN OP VALUE",
            help="Only rows where this holds, on both sides; repeatable. OP is one of "
            "= != < <= > >=, and = or != takes a list: sym=EURUSD,GBPUSD",
        ),
    ] = None,
    limit: Annotated[
        int,
        typer.Option(
            "--limit",
            help=f"Read at most N rows per side (default {data_diff.DEFAULT_LIMIT}); a side cut "
            "there is a sample, and its result says so",
            show_default=False,
        ),
    ] = data_diff.DEFAULT_LIMIT,
    source_version: Annotated[
        str | None,
        typer.Option(
            "--source-version",
            help="The source_version whose coverage to compare. Without it, coverage is not "
            "compared",
        ),
    ] = None,
    partition: Annotated[
        str | None,
        typer.Option(
            "--partition",
            help="The slice of the table whose coverage is read, with --source-version",
        ),
    ] = None,
    user: QUserOpt = DEFAULT_USER,
    password_env: QPasswordEnvOpt = None,
    as_json: DiffJson = False,
) -> None:
    """Compare tables on two endpoints over one window: schema, a sample of rows, coverage.

    Each side is this machine's stack (`local`) or a deploy target, its gateway
    reached through an ssh tunnel. Rows are read through the gateway under its
    query policy, at most --limit per side, and paired on the key the table's
    producers declare; `time`, stamped on receipt, is not compared. The result
    per table is one of: equal_in_sample, differs (schema, coverage or rows),
    missing_table, incomplete_coverage or endpoint_unavailable. equal_in_sample
    is never full equality: its label says whether every row in the window was
    read, or a sample cut at --limit. Exits 1 on anything but equal_in_sample.
    e.g. `uqs data diff --left local --right prod --table demo_deals --date 2026-09-11`
    """
    try:
        window = timerange.resolve(range_from, range_to, day, last)
        tables = tables_given(table)
        if left == right:
            raise UqsUsageError(f"--left and --right both name {left} - compare two endpoints")
        if partition and not source_version:
            raise UqsUsageError("--partition reads coverage, which needs --source-version")
        passwd = q_password(password_env, None)
        with reach(left, user, passwd) as lside, reach(right, user, passwd) as rside:
            report = data_diff.compare(
                _paths(),
                lside,
                rside,
                tables,
                window,
                filters=filters or [],
                limit=limit,
                source_version=source_version,
                partition=partition,
                login=f"{user}:{passwd}",
            )
    except UqsError as exc:
        _die(exc)
        return
    report = _utc(report)
    ok = report["outcome"] == data_diff.EQUAL
    if as_json:
        emit({"ok": ok, **report})
    else:
        render(report)
    if not ok:
        raise typer.Exit(code=1)
