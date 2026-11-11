"""`uqs job check`: do a dataset's producers fit it, agree, and miss nothing? (#1851)

One read-only report over every producer of a dataset - a polling feed, a backfill,
both or either - built by `.qetl.verify.run` in q. This module is the command line and
the printing: the producers, the keys, what the destination generates and every
refusal are q's. Four results, one per category, each PASS, FAIL or UNVERIFIED.

It is `job check`: `job` is where `preview`, `backfill` and `gaps` are, the commands
that take a producer and a time range (`data` operates on stored tables), and `check`
is the vocabulary's verb for "actively probe; exit non-zero on a problem" (CLI
conventions, rule 4). The dataset is the argument because the grouping of producers
comes from it.
"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.markup import escape

from uqs import timerange
from uqs.cli import completion
from uqs.cli.json_out import emit, json_opt, utc_times
from uqs.cli.pipelines import needs_pipelines
from uqs.cli.range_options import DateOpt, LastOpt, RangeFromOpt, RangeToOpt
from uqs.cli.safety import ReadOnly
from uqs.cli.shared import JOB_OPERATIONS_PANEL, _die, _paths, console, job_app
from uqs.paths import UqsError
from uqs.stack import dataset_verify

_COLOUR = {"PASS": "green", "FAIL": "red", "UNVERIFIED": "yellow"}

VerifyJson = json_opt(
    '{"dataset", "range_from", "range_to", "producers": [{producer, mode, source}], "readonly", '
    '"checks": [{category: schema/behaviour/cross_mode/completeness, status: PASS/FAIL/UNVERIFIED, '
    "dataset, scope, range_from, range_to, basis, reason, findings: [text]}], "
    '"behaviours": [{behaviour, mode, producer, status, reason, evidence}], "worst", "ok"} - '
    "ok false when it exits 1; the bounds ISO-8601 in UTC"
)


def _utc_bounds(record: dict) -> dict:
    """`record`, and each of its checks, with its bounds ISO-8601 in UTC (#1882):
    the verifier reads the window in UTC and spells it with no zone."""
    out = utc_times(record)
    if isinstance(out.get("checks"), list):
        out["checks"] = [_utc_bounds(c) if isinstance(c, dict) else c for c in out["checks"]]
    return out


def _word(status: str) -> str:
    return f"[bold {_COLOUR.get(status, 'white')}]{status}[/]"


def render(report: dict) -> None:
    """The report as text: one block per category, then the behaviours."""
    span = f"[{report['range_from']}, {report['range_to']}) UTC"
    console.print(f"[bold]verify {report['dataset']}[/] over {span}")
    for p in report["producers"]:
        console.print(f"  producer  {p['producer']} ({p['mode']})")
    held = "held equal" if report["readonly"] else "[red]CHANGED[/]"
    console.print(f"  read only (nothing published or advanced); snapshot {held}")
    for c in report["checks"]:
        console.print(f"\n{_word(c['status'])}  {c['category']}")
        if c["scope"]:
            console.print(f"  scope   {escape(c['scope'])}")
        console.print(f"  basis   {escape(c['basis'])}")
        if c["reason"]:
            console.print(f"  reason  {escape(c['reason'])}")
        for line in c["findings"]:
            console.print(f"    {escape(line)}")


@job_app.command(
    "check",
    rich_help_panel=JOB_OPERATIONS_PANEL,
    cls=needs_pipelines("checks this tree's producers of a dataset", ReadOnly),
)
def check(
    dataset: Annotated[
        str,
        typer.Argument(
            metavar="DATASET",
            help="A dataset: a plant table its producers fill",
            autocompletion=completion.plant_table,
        ),
    ],
    scope: Annotated[
        list[str] | None,
        typer.Option(
            "--scope",
            help="`column=value`, repeatable: the records the completeness check reconciles. "
            "Without one it is UNVERIFIED",
        ),
    ] = None,
    gateway: Annotated[
        str | None,
        typer.Option(
            "--gateway",
            help="HOST:PORT of a gateway whose RDB and HDB hold the destination. Without it the "
            "completeness check is UNVERIFIED",
        ),
    ] = None,
    behaviours: Annotated[
        bool,
        typer.Option(
            "--behaviours/--no-behaviours",
            help="Exercise cursor, retry, conflict and coverage behaviours in a sandbox "
            "(the default), or not",
        ),
    ] = True,
    fail_unverified: Annotated[
        bool, typer.Option("--fail-unverified", help="Exit 1 on UNVERIFIED as well as on FAIL")
    ] = False,
    range_from: RangeFromOpt = None,
    range_to: RangeToOpt = None,
    day: DateOpt = None,
    last: LastOpt = None,
    as_json: VerifyJson = False,
) -> None:
    """Check a dataset's producers over one source window, read only.

    Reports four results separately - schema, behaviour, cross-mode equivalence and
    completeness - each PASS, FAIL or UNVERIFIED, with the dataset, scope, bounds and
    the basis it rests on. A dataset with one producer is verified without a second;
    its cross-mode result is UNVERIFIED. Behaviours run on fixtures in a throwaway
    directory and never against a live source or a running process.
    Exits 1 on FAIL (and with --fail-unverified on UNVERIFIED).
    e.g. `uqs job check demo_deals --date 2026-09-11 --scope sym=EURUSD`
    """
    try:
        window = timerange.resolve(range_from, range_to, day, last)
        report = dataset_verify.verify(
            _paths(),
            dataset,
            dataset_verify.parse_scope(scope or []),
            window,
            behaviours=behaviours,
            gateway=gateway,
        )
    except UqsError as exc:
        _die(exc)
    worst = dataset_verify.worst(report)
    failed = (
        worst == "FAIL" or (fail_unverified and worst == "UNVERIFIED") or not report["readonly"]
    )
    if as_json:
        emit({"ok": not failed, **_utc_bounds(report), "worst": worst})
    else:
        render(report)
    if failed:
        raise typer.Exit(code=1)
