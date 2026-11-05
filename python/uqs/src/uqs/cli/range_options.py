"""The time-range options every uqs command that takes a range shares (#1735).

One spelling everywhere - --from/--to, --date or --last - each resolved by
uqs.timerange.resolve. Their own module, not cli/shared.py, so shared.py keeps
to its line budget (test_module_split.py).
"""

from __future__ import annotations

from typing import Annotated

import typer

from uqs.cli.vocabulary import duration
from uqs.timerange import BOUND_FORMS, DAY_FORMS

#: A time range, the one way every command takes it (#1735): --from/--to,
#: --date or --last, resolved by uqs.timerange.resolve.
RangeFromOpt = Annotated[
    str | None,
    typer.Option(
        "--from", help=f"Start of the time range, included. A date or a timestamp: {BOUND_FORMS}"
    ),
]
RangeToOpt = Annotated[
    str | None,
    typer.Option("--to", help="End of the time range, exclusive. A date or a timestamp, as --from"),
]
DateOpt = Annotated[
    str | None,
    typer.Option(
        "--date",
        help=f"One day, {DAY_FORMS}: the same as --from D --to D+1",
    ),
]
LastOpt = Annotated[
    float | None,
    duration(
        "--last",
        help="Up to now, a DURATION - the same as --from now-DURATION --to now",
    ),
]
