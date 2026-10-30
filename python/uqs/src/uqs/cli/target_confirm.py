"""Whether `uqs --target NAME <command>` may change the server, so asks first
(#956, #1780).

Every command states what it may do where it is declared: its Click class
carries a `safety.Effect` - read-only, confirms-on-server, or changes-state -
and an alias takes its new name's (`renames.alias_command`). This reads that
mark from the local CLI tree, so a new command is classified by declaring it,
and one nobody marked asks: `test_every_command_is_marked` names it.

It lives here, not in deploy/control.py with the rest of --target, because it
reads the CLI tree, and deploy/ is below cli/ in the layering.
"""

from __future__ import annotations

import functools
from collections.abc import Sequence
from typing import Any

from uqs.cli.safety import Effect
from uqs.deploy import control
from uqs.paths import UqsError

#: The marks under which a command does not ask here: it cannot change the
#: server, or it asks there, after showing its plan.
_NO_ASK = (Effect.READ_ONLY, Effect.CONFIRMS_ON_SERVER)


@functools.cache
def root_command() -> Any:
    """The local CLI tree, as Click sees it."""
    import typer

    from uqs.cli.entry import app

    return typer.main.get_command(app)


def subcommands(cmd: Any) -> dict[str, Any]:
    """A group's commands by name; a leaf has none."""
    return dict(getattr(cmd, "commands", None) or {})


def find(path: Sequence[str], root: Any = None) -> Any:
    """The command at `path` in the tree, or None."""
    cmd = root if root is not None else root_command()
    for word in path:
        cmd = subcommands(cmd).get(word)
        if cmd is None:
            return None
    return cmd


def effect_of(cmd: Any, root: Any = None) -> Effect | None:
    """`cmd`'s mark: an alias's is its new name's, so the two cannot differ."""
    seen = set()
    while (alias := getattr(cmd, "alias_of", None)) is not None and alias not in seen:
        seen.add(alias)
        cmd = find(alias, root)
    return getattr(cmd, "effect", None)


def unmarked(root: Any = None) -> list[str]:
    """Every command, hidden or not, that carries no mark."""
    out: list[str] = []
    root = root if root is not None else root_command()

    def walk(cmd: Any, path: tuple[str, ...]) -> None:
        subs = subcommands(cmd)
        if not subs and path:
            if effect_of(cmd, root) is None:
                out.append(" ".join(path))
        for name, sub in subs.items():
            walk(sub, (*path, name))

    walk(root, ())
    return out


def _leaf(words: Sequence[str]) -> tuple[Any, tuple[str, ...]]:
    """The command `words` name, and its path: the first leaf along them."""
    cmd, path = root_command(), ()
    for word in words:
        subs = subcommands(cmd)
        if not subs:
            break
        cmd, path = subs.get(word), (*path, word)
        if cmd is None:
            return None, path
    return (None if subcommands(cmd) else cmd), path


def changes_the_server(args: Sequence[str]) -> bool:
    """Whether `args` may change the server. Help never does, nor a dry run -
    every command that changes state takes --dry-run/-n (#1732) - but a
    backfill's, which starts a process to fetch; an unmarked or unknown
    command is assumed to."""
    if "--help" in args or "-h" in args:
        return False
    words = control.command_words(args)
    if not words:
        return False
    if ("--dry-run" in args or "-n" in args) and words[:2] != ("job", "backfill"):
        return False
    if words[:1] in (("status",), ("summary",)) and ("-i" in args or "--interactive" in args):
        return True  # its browser starts, stops and restarts processes with single keys
    cmd, _ = _leaf(words)
    return cmd is None or effect_of(cmd) not in _NO_ASK


def confirmed_on_server(args: Sequence[str], yes: bool) -> list[str]:
    """`args` for a confirms-on-server command under --target: --attach and
    --hdb refused (each names an HDB, and --target named the release's own),
    and the global --yes passed on, so one --yes answers the server's question
    (#1755)."""
    args = list(args)
    cmd, _ = _leaf(control.command_words(args))
    if cmd is None or effect_of(cmd) is not Effect.CONFIRMS_ON_SERVER:
        return args
    if any(a.partition("=")[0] in ("--attach", "--hdb") for a in args):
        raise UqsError("give one of --target, --attach and --hdb: each names an HDB")
    if yes and "--apply" in args and not {"--yes", "-y"} & set(args):
        args.append("--yes")
    return args
