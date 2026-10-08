"""Which columns `uqs summary` shows, and which one it sorts by.

Split out of cli/summary.py, which had reached the package's 400-line module
limit when `--sort` was added. Both turn what the reader typed into the
summary's own column spellings, and refuse an unknown one naming the real
ones, before any slow step runs.
"""

from __future__ import annotations

from uqs.cli.shared import _die
from uqs.paths import UqsError
from uqs.stack.listing import (
    SUMMARY_ALL_COLUMNS,
    SUMMARY_BATCH_COLUMNS,
    SUMMARY_COLUMNS,
    SUMMARY_PROBE_COLUMNS,
)


def resolve_columns(requested: str | None) -> list[str]:
    """The columns to render, from a comma-separated `--columns` value.

    The graph columns are ON by default. They were opt-in first, on the
    grounds that nine columns do not fit an eighty-column terminal - which is
    true, and was still the wrong trade: a column nobody knows about answers
    nothing, and "what feeds this" is the question that follows "is it
    running" almost every time. A reader on a narrow terminal can say
    `--columns status`; a reader who never learns the columns exist has no
    such move.
    """
    if not requested:
        return list(SUMMARY_ALL_COLUMNS)
    if requested.strip().lower() == "all":
        return list(SUMMARY_ALL_COLUMNS)
    if requested.strip().lower() == "status":
        return list(SUMMARY_COLUMNS + SUMMARY_PROBE_COLUMNS + SUMMARY_BATCH_COLUMNS)
    wanted = [c.strip() for c in requested.split(",") if c.strip()]
    known = {c.lower(): c for c in SUMMARY_ALL_COLUMNS}
    resolved, unknown = [], []
    for column in wanted:
        match = known.get(column.lower())
        if match is None:
            unknown.append(column)
        elif match not in resolved:
            resolved.append(match)
    if unknown:
        _die(
            UqsError(
                f"unknown summary column(s): {', '.join(unknown)}. "
                f"Available: {', '.join(sorted(SUMMARY_ALL_COLUMNS))}, "
                "or `all` / `status`"
            )
        )
    return resolved


def resolve_sort_column(requested: str | None) -> str | None:
    """The column `--sort` names, spelled as the summary spells it - or None.

    Checked here, before the listing and the probe run, so a typo costs
    nothing rather than a full summary followed by an error.
    """
    if not requested:
        return None
    match = {c.lower(): c for c in SUMMARY_ALL_COLUMNS}.get(requested.strip().lower())
    if match is None:
        _die(
            UqsError(
                f"cannot sort by {requested!r}: no such summary column. "
                f"Available: {', '.join(sorted(SUMMARY_ALL_COLUMNS))}"
            )
        )
    return match
