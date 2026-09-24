"""`uqs backfill`: run a bounded worker over a range.

Its own command rather than `uqs start <process>`, because a backfill takes
arguments no other process does - which worker, which source release, which
window - and they reach the process as flags on its start line. See
stack/backfill.py.
"""

from __future__ import annotations

from typing import Annotated

import typer

from uqs.cli import completion
from uqs.cli.shared import PortOpt, _debug_requested, _die, _paths, app
from uqs.model.registry import DEFAULT_BASE_PORT
from uqs.paths import UqsError
from uqs.stack import backfill as stack_backfill

_BOUND_HELP = (
    "A date or datetime: 2026-09-13, 2026-09-13T06:00, 2026-09-13T06:00+02:00, "
    "or a q timestamp such as 2026.09.13D06:00. No offset means UTC"
)


@app.command()
def backfill(
    ctx: typer.Context,
    worker: Annotated[
        str,
        typer.Argument(
            help="The bounded worker to run, e.g. demo_deals_backfill",
            autocompletion=completion.backfill_workers,
        ),
    ],
    version: Annotated[
        str, typer.Option("--version", help="The source_version to record coverage under")
    ],
    range_from: Annotated[
        str, typer.Option("--from", help=f"Inclusive start of the range. {_BOUND_HELP}")
    ],
    range_to: Annotated[
        str, typer.Option("--to", help=f"Exclusive end of the range. {_BOUND_HELP}")
    ],
    port: PortOpt = DEFAULT_BASE_PORT,
    debug: Annotated[
        bool,
        typer.Option(
            "--debug",
            help="Log at DEBUG inside the backfill process too: parsed flags, the "
            "worker's declaration, every window, each stage's timing",
        ),
    ] = False,
) -> None:
    """Run a bounded worker over [--from, --to), recording coverage under --version.

    All three are required: a backfill that guessed a range would publish the
    wrong window and record it as covered. The process registers with
    discovery, so the fleet has to be up, and it exits when the range is
    done - follow it with `uqs logs <process> -f`.

    e.g. `uqs backfill demo_deals_backfill --version v1 --from 2026-09-13 --to 2026-09-15`

    `--debug` (or `uqs --debug backfill ...`) starts the process with
    `-verbose`, so its log - `uqs logs <process>` - carries DBG lines.
    """
    try:
        result = stack_backfill.start(
            _paths(),
            worker,
            version,
            stack_backfill.parse_bound("--from", range_from),
            stack_backfill.parse_bound("--to", range_to),
            base_port=port,
            verbose=_debug_requested(ctx, debug),
        )
    except UqsError as exc:
        _die(exc)
        return
    raise typer.Exit(code=result.returncode)
