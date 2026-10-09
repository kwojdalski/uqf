"""Which `uqs job new` options shape which kind of job.

An option that shapes one kind is refused on the others rather than ignored:
`--partition` on a streaming job would otherwise be dropped in silence, and
the job scaffolded as though it had been honoured.
"""

from __future__ import annotations

_BACKFILL = ("backfill",)
_STANDING = ("streaming", "normalizer", "external", "horizon")

#: option -> the kinds it shapes. `--transform` first, as the most common slip.
SHAPES: dict[str, tuple[str, ...]] = {
    "--transform": ("backfill", "streaming"),
    "--transport": _BACKFILL,
    "--partition": _BACKFILL,
    "--check": _BACKFILL,
    "--dataset": _BACKFILL,
    "--source": _BACKFILL,
    "--width": _BACKFILL,
    "--twin-of": _BACKFILL,
    "--raw-columns": _BACKFILL,
    "--period": ("streaming",),
    "--horizon": ("horizon",),
    "--poll": ("streaming",),
    "--cursor-fields": ("streaming",),
    # external: the table its publisher writes; backfill: the physical table a
    # new source's adapter reads, beside --raw-columns
    "--raw-table": ("external", "backfill"),
    "--profile": _STANDING,
    "--unprofiled": _STANDING,
}


def misplaced(kind: str, used: dict[str, bool]) -> str | None:
    """The refusal for the first option in `used` that does not shape `kind`."""
    for option, kinds in SHAPES.items():
        if used.get(option) and kind not in kinds:
            return f"{option} does not apply to --kind {kind}"
    return None
