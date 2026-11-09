"""Which columns `uqs status` shows, and which one it sorts by.

Split out of cli/summary.py, which had reached the package's 400-line module
limit when `--sort` was added. Both turn what the reader typed into the
summary's own column spellings, and refuse an unknown one naming the real
ones, before any slow step runs.
"""

from __future__ import annotations

from uqs.cli.choices import alternatives
from uqs.cli.json_out import snake
from uqs.cli.shared import _die
from uqs.paths import UqsError, UqsUsageError
from uqs.stack.listing import (
    SUMMARY_ALL_COLUMNS,
    SUMMARY_BATCH_COLUMNS,
    SUMMARY_COLUMNS,
    SUMMARY_PROBE_COLUMNS,
)

#: `uqs status --columns`'s presets, a name for a set of columns - read by the
#: resolver, the help, completion and the refusal alike (#1893). `status` is
#: everything but the graph, narrower for an 80-column terminal.
SUMMARY_PRESETS: dict[str, tuple[str, ...]] = {
    "all": SUMMARY_ALL_COLUMNS,
    "status": SUMMARY_COLUMNS + SUMMARY_PROBE_COLUMNS + SUMMARY_BATCH_COLUMNS,
}
#: The preset shown when `--columns` is not given.
SUMMARY_DEFAULT_PRESET = "all"


def presets_help() -> str:
    """`--columns`'s help: each preset with the columns it shows (#1893)."""

    def shows(columns: tuple[str, ...]) -> str:
        return "every column" if columns == SUMMARY_ALL_COLUMNS else ", ".join(columns)

    notes = {name: shows(columns) for name, columns in SUMMARY_PRESETS.items()}
    return (
        "Comma-separated columns, or a preset: "
        + alternatives(SUMMARY_PRESETS, notes, default=SUMMARY_DEFAULT_PRESET, quote="`")
        + "."
    )


def resolve_columns(requested: str | None) -> list[str]:
    """The columns to render, from a comma-separated `--columns` value.

    The graph columns are ON by default. They were opt-in first, on the
    grounds that the full set does not fit an eighty-column terminal - which is
    true, and was still the wrong trade: a column nobody knows about answers
    nothing, and "what feeds this" is the question that follows "is it
    running" almost every time. A reader on a narrow terminal can say
    `--columns status`; a reader who never learns the columns exist has no
    such move.
    """
    requested = requested or SUMMARY_DEFAULT_PRESET
    preset = SUMMARY_PRESETS.get(requested.strip().lower())
    if preset is not None:
        return list(preset)
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
                f"or {' / '.join(f'`{p}`' for p in SUMMARY_PRESETS)}"
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
    # by its title or by its --json key (#2523); a title wins where they collide
    known = {snake(c).casefold(): c for c in SUMMARY_ALL_COLUMNS}
    known |= {c.casefold(): c for c in SUMMARY_ALL_COLUMNS}
    match = known.get(requested.strip().casefold())
    if match is None:
        _die(
            UqsUsageError(
                f"cannot sort by {requested!r}: no such summary column. "
                f"Available: {', '.join(sorted(SUMMARY_ALL_COLUMNS))}"
            )
        )
    return match
