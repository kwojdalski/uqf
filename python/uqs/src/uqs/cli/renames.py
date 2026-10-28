"""Old spellings kept for one release after a rename (#1738 rule 6).

A renamed option keeps parsing under its old name through `renamed()`, the
`cls=` of its command, which rewrites the old spelling to the new one before
Typer parses anything - so the old name is in no --help and no signature, and
the conventions test (test_cli_conventions.py) sees only the new one. A
renamed command keeps its old path through `alias_command()`, hidden. Both log
the new spelling every time the old one is used. The release after removes
the row or the alias.

A rename whose new spelling depends on the old one's value (`job install
--mode symlink` -> `--link`) cannot be rewritten by name: it keeps the old
option as a hidden one, `old_spelling()`, which the command reads and warns
about itself.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Mapping
from typing import Any

import typer
from typer.core import TyperCommand

from uqs.logger import get_logger

log = get_logger()

#: Spellings a renamed option or command keeps for one release (#1738 rule
#: 6), every one logging the new spelling when it is used. A rename adds a row
#: here or a `renamed(...)` on its command; the release after removes both.
RENAMED_GLOBAL_FLAGS = frozenset({"--debug"})

#: ctx.meta's list of the values an old option gave a positional (`--proc
#: rdb1` -> PROC rdb1), so a command can tell a named PROC from a guessed one.
MOVED_KEY = "uqs.renames.moved"

#: The new spelling of a flag that is gone because it did nothing (`job preview
#: --dry-run`: a preview is always dry), so the old one is accepted and ignored.
DROPPED = ""

#: `--port` meant the stack's base port on most commands; it is `--base-port`
#: now, and `--port` is only ever one process's port (#1729).
BASE_PORT = {"--port": "--base-port"}


def warn_renamed(old: str, new: str) -> None:
    log.warning("`{}` is renamed `{}`; the old spelling goes in the next release", old, new)


def _rewrite_renamed(
    args: list[str],
    renames: Mapping[str, str | tuple[str, str]],
    command_path: str,
    root: typer.Context | None,
    moved: list[str] | None = None,
) -> list[str]:
    """`args` with every old option spelling in `renames` replaced by its new
    one, each warned about once. Stops at `--`, after which nothing is an
    option. `--debug` given after a command moves to the global flag. An old
    option whose new spelling is a positional (`--proc` -> `PROC`) gives its
    value as the first word, where the command's first positional is."""
    out: list[str] = []
    front: list[str] = []
    reasons: list[str] = []
    warned: set[str] = set()
    rest = iter(args)
    for arg in rest:
        if arg == "--":
            return front + out + _reason(reasons) + [arg, *rest]
        name, eq, value = arg.partition("=")
        bypass = renames.get(name)
        if isinstance(bypass, tuple) and name.startswith("--"):
            _warn_once(warned, name, f"{' '.join(bypass)} --reason REASON")
            out += list(bypass)
            reasons.append(value if eq else next(rest, ""))
            continue
        if name in RENAMED_GLOBAL_FLAGS and name in renames:
            command = command_path.partition(" ")[2]
            _warn_once(warned, f"uqs {command} {name}", f"uqs {name} {command}")
            if root is not None and isinstance(root.obj, dict):
                root.obj["debug"] = True
            # Through shared, as the global flag's callback does, so a test
            # that stubs the one stubs the other.
            from uqs.cli import shared

            shared.configure_logging(component="uqs", level="DEBUG")
            continue
        new = renames.get(name)
        if new == DROPPED and name.startswith("--"):
            log.warning("`{}` does nothing here and goes in the next release; drop it", name)
            continue
        if isinstance(new, str) and name.startswith("--") and not new.startswith("-"):
            _warn_once(warned, name, f"{command_path} {new}")
            front.append(value if eq else next(rest, ""))
            if moved is not None:
                moved.append(front[-1])
            continue
        if isinstance(new, str) and name.startswith("--"):
            _warn_once(warned, name, new)
            out.append(new + eq + value)
            continue
        short = next((o for o in renames if not o.startswith("--") and arg.startswith(o)), None)
        if short is not None:
            long = str(renames[short])
            _warn_once(warned, short, long)
            out.append(long)
            if len(arg) > len(short):  # `-n20`: the value attached to the flag
                out.append(arg[len(short) :])
            continue
        out.append(arg)
    return front + out + _reason(reasons)


def _reason(reasons: list[str]) -> list[str]:
    """The one --reason the old per-override options' reasons become (#1732)."""
    return ["--reason", "; ".join(reasons)] if reasons else []


def _warn_once(warned: set[str], old: str, new: str) -> None:
    if old not in warned:
        warned.add(old)
        warn_renamed(old, new)


def renamed(renames: Mapping[str, str | tuple[str, str]]) -> type[TyperCommand]:
    """`cls=` for a command whose options were renamed: the old spellings in
    `renames` (old -> new) still parse, hidden from --help, each logging its
    new spelling. A short old spelling (`-n`) matches only as its own word or
    with its value attached (`-n20`). `--debug` -> `--debug` moves a
    per-command `--debug` to the global one, and a new spelling with no dash
    names the first positional (`--proc` -> `PROC`). A pair names a bypass:
    `--skip-q-version-check R` -> `--override q-version-check`, its value the
    --reason (#1732)."""

    class Renamed(TyperCommand):
        #: old spelling -> new, read by the tests
        renames: dict[str, str | tuple[str, str]] = {}

        def parse_args(self, ctx, args):
            root = ctx.find_root()
            path = ctx.command_path
            moved = ctx.meta.setdefault(MOVED_KEY, [])
            args = _rewrite_renamed(list(args), renames, path, root, moved)
            return super().parse_args(ctx, args)

    Renamed.renames = dict(renames)
    return Renamed


def alias_command(group: typer.Typer, old: str, new: str, fn: Callable, **kwargs) -> None:
    """Register `fn` on `group` under its old name `old` too, hidden from
    --help, logging `new` - the full new spelling - each time it is used."""

    @functools.wraps(fn)
    def _alias(*args, **kw):
        warn_renamed(old, new)
        return fn(*args, **kw)

    group.command(old, hidden=True, **kwargs)(_alias)


def old_spelling(new: str, *old: str) -> Any:
    """The hidden option that keeps `old` parsing for one release, where the
    new spelling depends on its value and so `renamed()` cannot map it; the
    command reads it and calls `warn_renamed` with the spelling to use."""
    return typer.Option(*old, hidden=True, help=f"Renamed: {new}")
