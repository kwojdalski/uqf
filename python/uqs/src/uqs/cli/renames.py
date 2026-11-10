"""Old spellings kept for one release after a rename (#1738 rule 6).

A renamed option keeps parsing under its old name through `renamed()`, the
`cls=` of its command, which rewrites the old spelling to the new one before
Typer parses anything - so the old name is in no --help and no signature, and
the conventions test (test_cli_conventions.py) sees only the new one. A
renamed command keeps its old path through `alias_command()`, hidden. Both log
the new spelling every time the old one is used. The release after removes
the row or the alias. Each old spelling's window is a row in
rename_expiry.py, which a test holds to that one release (#1796).

A rename whose new spelling depends on the old one's value (`job install
--mode symlink` -> `--link`) cannot be rewritten by name: it keeps the old
option as a hidden one, `old_spelling()`, which the command reads and warns
about itself.

A rename from #2237 on keeps NO old spelling: `refused()` makes the old one a
usage error naming the new one, so nothing has two spellings even for a
release.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import typer
from typer._click.exceptions import UsageError
from typer.core import TyperCommand

from uqs.logger import get_logger

log = get_logger()

#: ctx.meta's flag for a parse that only reads the arguments (`uqs --target`
#: deciding whether to ask, #1780): old spellings rewritten, nothing warned.
QUIET_KEY = "uqs.renames.quiet"


#: An old bypass FLAG - `--allow-dirty`, taking no value - now `--override
#: NAME --reason TEXT` (#1852): `{"--allow-dirty": bypass_flag("allow-dirty")}`.
#: Rewritten to `--override allow-dirty`; with no `--reason` given, one saying
#: the old flag recorded none is supplied, so the old spelling still works for
#: its one release and the record says how it was asked for.
def bypass_flag(name: str) -> tuple[str, str, str]:
    return ("--override", name, "")


def legacy_reason(old: str) -> str:
    """The --reason recorded for an old bypass flag given without one."""
    return f"given as {old}, which records no reason"


#: `--attach NAME` was a verb naming a noun: the saved attachment is
#: `--attachment NAME` on every command that reads or writes one (#1855).
ATTACHMENT = {"--attach": "--attachment"}


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


def warn_renamed(old: str, new: str) -> None:
    log.warning("`{}` is renamed `{}`; the old spelling goes in the next release", old, new)


def warn_secret_argv(old: str, new: str) -> None:
    """The warning for an old spelling that takes a secret inline (#1853):
    renamed, and why the old one is a risk while it lasts."""
    log.warning(
        "`{}` puts a secret on the command line, where ps and shell history show it; "
        "export it and use `{}` - the old spelling goes in the next release",
        old,
        new,
    )


def _rewrite_renamed(
    args: list[str], renames: Mapping[str, str | tuple[str, ...]], quiet: bool = False
) -> list[str]:
    """`args` with every old option spelling in `renames` replaced by its new
    one, each warned about once. Stops at `--`, after which nothing is an
    option. `quiet` rewrites without a warning: a parse that only reads the
    arguments, as completion and `uqs --target` do (#1780)."""
    out: list[str] = []
    flags: list[str] = []
    warned: set[str] = set(renames) if quiet else set()  # all warned: none is
    head = args[: args.index("--")] if "--" in args else args
    has_reason = any(a == "--reason" or a.startswith("--reason=") for a in head)
    rest = iter(args)
    for arg in rest:
        if arg == "--":
            return out + _reason(flags, has_reason) + [arg, *rest]
        name, eq, value = arg.partition("=")
        new = renames.get(name) if name.startswith("--") else None
        if isinstance(new, tuple):  # bypass_flag(NAME)
            _warn_once(warned, name, f"{new[0]} {new[1]} --reason REASON")
            out += list(new[:2])
            flags.append(name)
        elif isinstance(new, str):
            _warn_once(warned, name, new)
            out.append(new + eq + value)
        else:
            out.append(arg)
    return out + _reason(flags, has_reason)


def _reason(flags: Sequence[str], has_reason: bool) -> list[str]:
    """For old bypass flags, which took no reason (#1852), a --reason saying
    so - unless the command line gives its own, which then covers them."""
    if not flags or has_reason:
        return []
    return ["--reason", legacy_reason(", ".join(flags))]


def _warn_once(warned: set[str], old: str, new: str) -> None:
    if old not in warned:
        warned.add(old)
        warn_renamed(old, new)


def renamed(
    renames: Mapping[str, str | tuple[str, ...]], mark: type[TyperCommand] = TyperCommand
) -> type[TyperCommand]:
    """`cls=` for a command whose options were renamed: the old spellings in
    `renames` (old -> new) still parse, hidden from --help, each logging its
    new spelling. A `bypass_flag(NAME)` names an old bypass FLAG, which
    takes no value: `--allow-dirty` -> `--override allow-dirty` (#1852). `mark`
    is the command's class from safety (`ReadOnly`, `ChangesState`,
    `ConfirmsOnServer`), so the effect it states carries over;
    an alias leaves it out and takes its new name's (`alias_command`)."""

    class Renamed(TyperCommand):
        #: old spelling -> new, read by the tests
        renames: dict[str, str | tuple[str, ...]] = {}

        def parse_args(self, ctx, args):
            quiet = ctx.resilient_parsing or ctx.meta.get(QUIET_KEY, False)
            args = _rewrite_renamed(list(args), renames, quiet)
            return super().parse_args(ctx, args)

    Renamed.renames = dict(renames)
    # The mark as a second base, after Renamed: its `effect`, Renamed's parse.
    return Renamed if mark is TyperCommand else type("Renamed", (Renamed, mark), {})


def alias_command(group: typer.Typer, old: str, new: str, fn: Callable, **kwargs) -> None:
    """Register `fn` on `group` under its old name `old` too, hidden from
    --help, logging `new` - the full new spelling - each time it is used. The
    alias takes the new command's mark (safety.Effect): its class names the
    new command's path, `alias_of`, which `effect_of` follows, so an alias is
    classified as its new name is with nothing to keep in step."""

    @functools.wraps(fn)
    def _alias(*args, **kw):
        warn_renamed(old, new)
        return fn(*args, **kw)

    base = kwargs.pop("cls", TyperCommand)
    path = tuple(new.split()[1:])  # `uqs job runs list --unfinished` -> job runs list
    path = path[: next((i for i, w in enumerate(path) if w.startswith("-")), len(path))]
    cls = type(base.__name__, (base,), {"alias_of": path})
    group.command(old, hidden=True, cls=cls, **kwargs)(_alias)


def old_spelling(new: str, *old: str) -> Any:
    """The hidden option that keeps `old` parsing for one release, where the
    new spelling depends on its value and so `renamed()` cannot map it; the
    command reads it and calls `warn_renamed` with the spelling to use."""
    return typer.Option(*old, hidden=True, help=f"Renamed: {new}")


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
    Nothing after `--` is an option. `mark` is the command's safety class, as
    for `renamed()`; the two compose: `renamed(R, refused(S, ChangesState))`."""

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
