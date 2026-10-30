"""Whether `uqs --target NAME <command>` may change the server, so asks first
(#956, #1780).

Every command states what it may do where it is declared: its Click class
carries a `safety.Effect` - read-only, confirms-on-server, or changes-state -
and an alias takes its new name's (`renames.alias_command`). This reads that
mark from the local CLI tree, so a new command is classified by declaring it,
and one nobody marked asks: `test_every_command_is_marked` names it.

The command line is parsed by the command's own Click parser, as the server's
uqs will parse it, never scanned for words: `--reason --dry-run` gives
--reason a value and is no dry run. The parse runs no callback - help is not
printed, nothing is logged - and anything it cannot resolve or split into
options and arguments asks: an unknown command (the server's uqs may be
newer), an unknown option, an option missing its value, a word too many. A
local parse can only skip the question for a command and options this
version knows; if the server's differs, it refuses the option before doing
anything. A value that is missing or invalid is not a doubt about what the
words mean: the server refuses it the same way, so it does not ask here.

It lives here, not in deploy/control.py with the rest of --target, because it
reads the CLI tree, and deploy/ is below cli/ in the layering.
"""

from __future__ import annotations

import functools
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from uqs.cli.renames import QUIET_KEY, _rewrite_renamed
from uqs.cli.safety import Effect
from uqs.paths import UqsError

#: The marks under which a command does not ask here: it cannot change the
#: server, or it asks there, after showing its plan.
_NO_ASK = (Effect.READ_ONLY, Effect.CONFIRMS_ON_SERVER)
#: Its dry run still starts a process, to fetch (#1732).
_DRY_RUN_STARTS = ("job", "backfill")
#: Its --interactive browser starts, stops and restarts processes with single keys.
_INTERACTIVE_ACTS = ("status",)


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


def canonical(cmd: Any, root: Any = None) -> tuple[Any, tuple[str, ...] | None]:
    """(the command an alias stands for, its path), or `cmd` itself and None."""
    path, seen = None, set()
    while (alias := getattr(cmd, "alias_of", None)) is not None and alias not in seen:
        seen.add(alias)
        path, cmd = alias, find(alias, root)
    return cmd, path


def effect_of(cmd: Any, root: Any = None) -> Effect | None:
    """`cmd`'s mark: an alias's is its new name's, so the two cannot differ."""
    return getattr(canonical(cmd, root)[0], "effect", None)


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


@dataclass(frozen=True)
class Parsed:
    """A command line as the command's own parser reads it."""

    #: the command's path, an alias's as its new name's
    path: tuple[str, ...]
    #: the command it names; None when it names a group and no command in it
    command: Any
    #: the command's context: its parameters, parsed
    ctx: Any
    #: whether --help (or the root's --version) was given, at any level
    help: bool = False

    def given(self, option: str) -> Any:
        """The parsed value of the parameter that declares `option` (`--dry-run`),
        by its flag, not its name: `start` calls its --dry-run `print_only`."""
        for param in getattr(self.ctx.command, "params", ()):
            if option in (*param.opts, *param.secondary_opts):
                return self.ctx.params.get(param.name)
        return None


def _tokenizes(cmd: Any, ctx: Any, args: list[str]) -> None:
    """Raise unless `args` split into `cmd`'s options and arguments without an
    error: an unknown option, an option missing its value, a word too many.
    The resilient parse swallows these and stops where they happen, so a word
    after one could be read as a flag the server reads as a value. Values are
    not converted here: a missing or invalid one the server refuses itself."""
    renames = getattr(cmd, "renames", None)
    if renames is not None:  # renames.renamed: as its own parse_args does
        args = _rewrite_renamed(list(args), renames, ctx.command_path, None, None, quiet=True)
    probe = cmd.context_class(
        cmd, info_name=ctx.info_name, parent=ctx.parent, resilient_parsing=False
    )  # a child inherits its parent's resilience unless told: the parent's is True
    _, extra, _ = cmd.make_parser(probe).parse_args(list(args))
    if extra and not subcommands(cmd) and not probe.allow_extra_args:
        raise UqsError(f"unexpected words: {' '.join(extra)}")


def _walk(args: Sequence[str]) -> list[Any]:
    """The contexts `args` parse into, root first, parsed resiliently - no
    callback runs, so --help is read, not printed - and each level's words
    checked to tokenize. Raises for anything that does not parse."""
    root = root_command()
    ctx = root.make_context("uqs", list(args), resilient_parsing=True)
    ctx.meta[QUIET_KEY] = True  # renames.renamed: rewrite old spellings, say nothing
    _tokenizes(root, ctx, list(args))
    contexts, cmd = [ctx], root
    while subcommands(cmd):
        rest = [*ctx._protected_args, *ctx.args]
        if not rest:
            break
        name, sub, rest = cmd.resolve_command(ctx, rest)
        if sub is None:
            raise UqsError(f"no command {name!r} here")
        # a copy: parsing consumes the list it is given
        ctx = sub.make_context(name, list(rest), parent=ctx, resilient_parsing=True)
        _tokenizes(sub, ctx, rest)
        contexts.append(ctx)
        cmd = sub
    return contexts


def _asked_for(ctx: Any, name: str) -> bool:
    """Whether the command line gave `name` - --help shows in no params."""
    return getattr(ctx.get_parameter_source(name), "name", None) == "COMMANDLINE"


def parse(args: Sequence[str]) -> Parsed | None:
    """`args` parsed by the local CLI tree, or None if they do not: an
    unknown command, an unknown option, an option missing its value, a word
    the command does not take."""
    try:
        contexts = _walk(args)
    except Exception:
        return None
    root, leaf = contexts[0], contexts[-1]
    asked_help = any(_asked_for(c, "help") for c in contexts) or _asked_for(root, "version")
    path = tuple(c.info_name for c in contexts[1:])
    command, alias = canonical(leaf.command)
    if subcommands(leaf.command):
        command = None
    return Parsed(alias or path, command, leaf, asked_help)


def changes_the_server(args: Sequence[str]) -> bool:
    """Whether `args` may change the server, read from the parsed command:
    never with help, nor a dry run - every command that changes state takes
    --dry-run/-n (#1732) - but a backfill's, which starts a process to fetch;
    a read-only or confirms-on-server command does not, except `status
    --interactive`; anything else, or anything that does not parse, may."""
    p = parse(args)
    if p is None:
        return True
    if p.help or not p.path:
        return False
    if p.command is None:
        return True
    if p.given("--dry-run") is True and p.path != _DRY_RUN_STARTS:
        return False
    if p.path == _INTERACTIVE_ACTS and p.given("--interactive"):
        return True
    return effect_of(p.command) not in _NO_ASK


def confirmed_on_server(args: Sequence[str], yes: bool) -> list[str]:
    """`args` for a confirms-on-server command under --target: --attach and
    --hdb refused (each names an HDB, and --target named the release's own),
    and the global --yes passed on, so one --yes answers the server's question
    (#1755)."""
    args = list(args)
    p = parse(args)
    if p is None or p.help or effect_of(p.command) is not Effect.CONFIRMS_ON_SERVER:
        return args
    if p.given("--attach") is not None or p.given("--hdb") is not None:
        raise UqsError("give one of --target, --attach and --hdb: each names an HDB")
    if yes and p.given("--apply") and not p.given("--yes"):
        args.append("--yes")
    return args
