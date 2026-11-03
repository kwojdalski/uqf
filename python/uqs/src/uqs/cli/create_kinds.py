"""Which `uqs job new` options shape which kind of job.

An option that shapes one kind is refused on the others rather than ignored:
`--partition` on a streaming job would otherwise be dropped in silence, and
the job scaffolded as though it had been honoured.
"""

from __future__ import annotations

from typing import Annotated, Any

import typer

from uqs.cli import completion
from uqs.cli.choices import alternatives
from uqs.model.kinds import SCAFFOLD_KINDS

#: `--kind` when it is not given.
DEFAULT_KIND = "streaming"
#: What a kind scaffolds, where its name does not say: `--kind`'s help.
KIND_NOTES = {
    "connector": "a Python publisher outside q, plus the q job reshaping what it publishes",
    "horizon": "each event evaluated once --horizon has passed",
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


KindOpt = Annotated[
    str,
    typer.Option(
        "--kind",
        help=alternatives(SCAFFOLD_KINDS, KIND_NOTES, default=DEFAULT_KIND, quote="`"),
        autocompletion=completion.choices(*SCAFFOLD_KINDS),
        callback=canonical,
        show_default=False,
    ),
]

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
    # every kind declares whether `uqs start all` starts it (#1893)
    "--start-with-all": SCAFFOLD_KINDS,
    # a normalizer and a horizon job publish their own NAME; a backfill fills --dataset
    "--publishes": ("streaming", "connector"),
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
NOT_SHAPING = frozenset({"--triggered-by", "--writes", "--bundle", "--dry-run"})


def given_options(ctx: Any, exclude: frozenset[str] = NOT_SHAPING) -> dict[str, bool]:
    """{--option: whether the command line gave it}, for every option of
    `ctx`'s command - read from Click, so an option added to the command is
    checked against SHAPES without a list to add it to (#1893)."""
    return {
        param.opts[0]: getattr(ctx.get_parameter_source(param.name), "name", "") == "COMMANDLINE"
        for param in ctx.command.params
        if param.param_type_name == "option" and param.opts[0] not in exclude
    }
