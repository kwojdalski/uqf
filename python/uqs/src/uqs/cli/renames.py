"""Old spellings are refused, never kept (#2239, #2752).

This repository keeps no compatibility: a renamed option's old spelling is a
usage error that names the new one (`refused()`), so nothing has two spellings
even for a release. The one-release window renames once had (#1738 rule 6) is
over for every one of them; cli/rename_expiry.py and its test hold the command
tree to having no hidden old spelling left.
"""

from __future__ import annotations

from collections.abc import Mapping

from typer._click.exceptions import UsageError
from typer.core import TyperCommand

#: "Partition" is an HDB date partition (#2239), as kdb+ has it; the coverage
#: slice a bounded worker fills, which `data diff` and `job new` called
#: `--partition`, is `--slice`. Refused, not rewritten (`refused()`).
SLICE = {
    "--partition": "`--partition` is now `--slice` (the coverage slice); a date partition "
    "is `--date` (#2239)",
}
SLICE_COLUMN = {
    "--partition-column": "`--partition-column` is now `--slice-column`, the column holding "
    "--slice (#2239)",
}


def refused(
    spellings: Mapping[str, str],
    mark: type[TyperCommand] = TyperCommand,
    *,
    extra_argument: str | None = None,
) -> type[TyperCommand]:
    """`cls=` for a command an old spelling is REFUSED on, not rewritten: this
    repository keeps no compatibility (#2239), so `--partition` on `data diff`
    is an error that names `--slice`, never a second way to say it. `spellings`
    maps each old option (`--partition`, or `--partition=VALUE`) to the
    message, which names the new spelling. `extra_argument` is the message
    for a positional the command no longer takes (`data migrate TABLE DATE`,
    now `--date`), given in place of Click's "unexpected extra argument".
    Nothing after `--` is an option. `mark` is the command's safety class
    (`ReadOnly`, `ChangesState`, `ConfirmsOnServer`), so the effect it states
    carries over."""

    class Refused(TyperCommand):
        #: old spelling -> its refusal, read by the tests
        refusals: dict[str, str] = {}

        def parse_args(self, ctx, args):
            for arg in [] if ctx.resilient_parsing else args:
                if arg == "--":
                    break
                message = spellings.get(arg.partition("=")[0])
                if message is not None:
                    raise UsageError(message, ctx=ctx)
            try:
                return super().parse_args(ctx, args)
            except UsageError as exc:
                if extra_argument and "unexpected extra argument" in exc.message:
                    raise UsageError(extra_argument, ctx=ctx) from None
                raise

    Refused.refusals = dict(spellings)
    return Refused if mark is TyperCommand else type("Refused", (Refused, mark), {})
