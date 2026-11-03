"""Which `uqs job new` options shape which kind of job.

An option that shapes one kind is refused on the others rather than ignored:
`--partition` on a streaming job would otherwise be dropped in silence, and
the job scaffolded as though it had been honoured.
"""

from __future__ import annotations

_BACKFILL = ("backfill",)
_STANDING = ("streaming", "normalizer", "connector", "horizon")

#: option -> the kinds it shapes. `--transform` first, as the most common slip.
SHAPES: dict[str, tuple[str, ...]] = {
    "--transform": ("backfill", "streaming"),
    "--transport": _BACKFILL,
    "--partition": _BACKFILL,
    "--partition-column": _BACKFILL,
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
    # connector: the table its publisher writes; backfill: the physical table a
    # new source's adapter reads, beside --raw-columns
    "--raw-table": ("connector", "backfill"),
    "--profile": _STANDING,
    "--unprofiled": _STANDING,
}


#: Kinds renamed, old -> new: a Python publisher outside q is a connector, as
#: `uqs connector` names it; "feed" is a q streaming job that only publishes,
#: and "external" was a third word for the first (#1890).
OLD_KINDS = {"external": "connector"}


def canonical(kind: str) -> str:
    """`--kind`'s value, an old one (OLD_KINDS) named by its new spelling and warned."""
    if kind in OLD_KINDS:
        from uqs.cli.renames import warn_renamed

        warn_renamed(f"--kind {kind}", f"--kind {OLD_KINDS[kind]}")
    return OLD_KINDS.get(kind, kind)


def misplaced(kind: str, used: dict[str, bool]) -> str | None:
    """The refusal for the first option in `used` that does not shape `kind`."""
    for option, kinds in SHAPES.items():
        if used.get(option) and kind not in kinds:
            return f"{option} does not apply to --kind {kind}"
    return None
