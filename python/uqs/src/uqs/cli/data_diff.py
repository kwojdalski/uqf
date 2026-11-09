"""`uqs data diff`: one table on two endpoints - this machine's stack or a deploy
target - compared by schema, a bounded sample of rows and published coverage (#2033).

This is the command: its options and the report as text. Reaching each side and
running the comparison is deploy/diff_endpoints.py's (`run`), which the frontend's
/compare calls too - so neither loads the other's layer (#2655). What the
comparison says is q's - src/etl/core/compare.q.
"""

from __future__ import annotations

from typing import Annotated, Any

import typer
from rich.markup import escape

from uqs import timerange
from uqs.cli import completion
from uqs.cli.concepts import target_opt
from uqs.cli.hdb_scope import tables_given
from uqs.cli.json_out import Verbatim, emit, json_opt
from uqs.cli.pipelines import needs_pipelines
from uqs.cli.q_login import DEFAULT_USER, QPasswordEnvOpt, QUserOpt, q_password
from uqs.cli.range_options import DateOpt, LastOpt, RangeFromOpt, RangeToOpt
from uqs.cli.renames import SLICE, refused
from uqs.cli.safety import ReadOnly
from uqs.cli.shared import _die, _paths, console, data_app
from uqs.deploy import diff_endpoints
from uqs.paths import UqsError
from uqs.stack import data_diff

#: Each outcome as the text output colours it.
_STYLE = {
    "equal_in_sample": "green",
    "differs": "red",
    "missing_table": "red",
    "not_comparable": "yellow",
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
    '{"ok", "outcome", "left", "right", "range_from", "range_to", "limit", "filters", '
    '"tables": [{table, outcome, label, truncated, read, schema, rows, coverage}]}'
)


def render(report: dict) -> None:
    """The report as text: the window, then each table's outcome and its specifics."""
    console.print(
        f"[bold]{escape(report['left'])}[/] (left) against [bold]{escape(report['right'])}[/] "
        f"(right), [{report['range_from']}, {report['range_to']}), "
        f"at most {report['limit']} rows per side"
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
    cls=needs_pipelines(
        "reads this tree's declarations of each table's row key", refused(SLICE, ReadOnly)
    ),
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
            "--slice",
            help="The slice of the table whose coverage is read, with --source-version - a "
            "coverage key such as a symbol, not a date",
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
    missing_table, not_comparable (no row key, and a sample was cut),
    incomplete_coverage or endpoint_unavailable. equal_in_sample is
    never full equality: its label says whether every row in the window was
    read, or a sample cut at --limit. Exits 1 on anything but equal_in_sample.
    e.g. `uqs data diff --left local --right prod --table demo_deals --date 2026-09-11`
    """
    try:
        window = timerange.resolve(range_from, range_to, day, last)
        passwd = q_password(password_env, None)
        report = diff_endpoints.run(
            _paths(), left, right, tables_given(table), window,
            filters=filters, limit=limit, source_version=source_version,
            partition=partition, user=user, passwd=passwd,
        )  # fmt: skip
    except UqsError as exc:
        _die(exc)
        return
    if as_json:
        # a table's rows are its own columns' - data, not keys the document spells
        tables = [t | {"rows": Verbatim(t["rows"])} if "rows" in t else t for t in report["tables"]]
        emit(report | {"tables": tables})
    else:
        render(report)
    if not report["ok"]:
        raise typer.Exit(code=1)
