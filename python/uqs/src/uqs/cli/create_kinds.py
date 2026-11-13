"""Which `uqs job new` options shape which kind of job.

An option that shapes one kind is refused on the others rather than ignored:
`--slice` on a streaming job would otherwise be dropped in silence, and
the job scaffolded as though it had been honoured.
"""

from __future__ import annotations

from typing import Any

from uqs.cli.choices import ChoiceOpt
from uqs.model.kinds import SCAFFOLD_KINDS

#: `--kind` when it is not given.
DEFAULT_KIND = "streaming"
#: What a kind scaffolds, where its name does not say: `--kind`'s help.
KIND_NOTES = {
    "connector": "a Python publisher outside q, plus the q job reshaping what it publishes",
    "horizon": "each event evaluated once --horizon has passed",
}

KindOpt = ChoiceOpt(
    SCAFFOLD_KINDS, "--kind", "{values}", notes=KIND_NOTES, default=DEFAULT_KIND, required=True
)

_BACKFILL = ("backfill",)
_STANDING = ("streaming", "normalizer", "connector", "horizon")

#: option -> the kinds it shapes. `--transform` first, as the most common slip.
SHAPES: dict[str, tuple[str, ...]] = {
    "--transform": ("backfill", "streaming"),
    "--transport": _BACKFILL,
    "--slice": _BACKFILL,
    "--slice-column": _BACKFILL,
    "--check": _BACKFILL,
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
    # every kind declares whether `uqs start all` starts it (#1893)
    "--start-with-all": SCAFFOLD_KINDS,
    # a normalizer and a horizon job publish their own NAME; a backfill fills its one output
    "--outputs": ("streaming", "connector", "backfill"),
    # a backfill reads its source, a connector job --raw-table
    "--subscribe-to": ("streaming", "normalizer", "horizon"),
}


def for_kinds(option: str) -> str:
    """The " (backfill)" that ends `option`'s help: the kinds SHAPES lets it
    shape, read from SHAPES so the help cannot name others (#1893). Nothing
    for an option every kind takes."""
    kinds = SHAPES[option]
    return "" if set(kinds) == set(SCAFFOLD_KINDS) else f" ({', '.join(kinds)})"


def misplaced(kind: str, used: dict[str, bool]) -> str | None:
    """The refusal for the first option in `used` that does not shape `kind`."""
    for option, kinds in SHAPES.items():
        if used.get(option) and kind not in kinds:
            return f"{option} does not apply to --kind {kind}"
    return None


#: The options that are not a job's shape: a reaction's own, and how to write.
NOT_SHAPING = frozenset({"--triggered-by", "--bundle", "--dry-run"})


def given_options(ctx: Any, exclude: frozenset[str] = NOT_SHAPING) -> dict[str, bool]:
    """{--option: whether the command line gave it}, for every option of
    `ctx`'s command - read from Click, so an option added to the command is
    checked against SHAPES without a list to add it to (#1893)."""
    return {
        param.opts[0]: getattr(ctx.get_parameter_source(param.name), "name", "") == "COMMANDLINE"
        for param in ctx.command.params
        if param.param_type_name == "option" and param.opts[0] not in exclude
    }
