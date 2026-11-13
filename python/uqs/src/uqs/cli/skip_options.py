"""`--skip-<id>` options generated from the guardrail registry (#1826, part of #1824).

A guardrail declared with `skip_flag=True` (uqs/guardrails.py) gives every
command in its `commands` an option, with nothing added to the command's
signature or to any list:

  - `--skip-<id>`, a flag, for a guardrail waived for the whole command;
  - `--skip-<id> COMPONENT`, repeatable, for one waived per component.

Its help is the declaration's description, risk and action, and completion
offers it with that help, because Click builds both from the same parameters
it parses (`get_params`). The option is another spelling of `--override`:
`--skip-eod-fence` is `--override eod-fence`, `--skip-torq-version stp1` is
`--override TORQ_VERSION:stp1`. The command body never sees the option. It
calls `safety.given(command, override, reason)`, as it does for `--override`,
and gets one `{name: reason}` for both spellings, checked the same way: one
nonempty `--reason`, a name in the command's scope, an overridable guardrail,
no conflicting requests. `uqs --target` forwards the skip as `--override NAME`
(`forwarded`), which a server's uqs understands even if it predates the flag.

Every command class from safety.marked carries `SkipOptions`, so declaring is
enough. A command that takes a generated option must take `--override` and
`--reason` and read them through `safety.given`. Without both options its
parameters are refused (`test_cli_conventions.py` walks the tree), because a
skip that no body reads would be accepted and then ignored.
"""

from __future__ import annotations

import contextvars
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, TypeGuard

from typer.core import TyperCommand, TyperOption

from uqs.guardrails import Guardrail, spelled

#: ctx.meta's record of the skips a command line gave, as a Request.
META_KEY = "uqs.skip_options.given"
#: The prefix of a generated option's parameter name, which no signature uses.
_PARAM = "uqs_skip__"


@dataclass(frozen=True)
class Request:
    """The generated skips one command line gave."""

    #: the command, as guardrails name it (`deploy push`)
    command: str
    #: (the waiver as --override names it, the spelling given), in order
    given: tuple[tuple[str, str], ...]


#: The Request of the command being invoked, while its body runs.
_CURRENT: contextvars.ContextVar[Request | None] = contextvars.ContextVar(
    "uqs_skip_options", default=None
)


def flag(g: Guardrail) -> str:
    """`g`'s generated option: `--skip-eod-fence`, `--skip-torq-version`."""
    return "--skip-" + spelled(g.id)


def declared(command: str) -> tuple[Guardrail, ...]:
    """The guardrails that give `command` a generated option, in registry order."""
    from uqs.cli import safety  # safety imports this module: read its registry at call time

    return tuple(g for g in safety.registry() if g.skip_flag and command in g.commands)


def _help(g: Guardrail) -> str:
    same = f"--override {g.name}"
    first = g.description.rstrip(".")
    parts = [first[:1].upper() + first[1:]]
    if g.risk and g.risk != g.description:
        parts.append(f"Risk: {g.risk.rstrip('.')}")
    if g.action:
        parts.append(f"Then: {g.action.rstrip('.')}")
    each = "; repeatable, one COMPONENT each" if g.component_scoped else ""
    parts.append(f"Nothing else is waived. The same as {same}{each}")
    parts.append("Needs --reason; warned and recorded")
    return ". ".join(parts)


class GuardrailOption(TyperOption):
    """A generated option: a TyperOption that knows the guardrail it waives."""

    def __init__(self, guardrail: Guardrail, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.guardrail = guardrail


def option(g: Guardrail) -> GuardrailOption:
    """`g`'s generated option, marked with the guardrail it waives."""
    name = _PARAM + g.id.lower().replace("-", "_")
    if g.component_scoped:
        return GuardrailOption(
            g, param_decls=[name, flag(g)], multiple=True, metavar="COMPONENT", help=_help(g)
        )
    return GuardrailOption(
        g, param_decls=[name, flag(g)], is_flag=True, default=False, help=_help(g)
    )


#: The options built for each (command, its declared guardrails): one object
#: each, because Click orders what it parses by the parameters' identity.
_BUILT: dict[tuple[str, tuple[Guardrail, ...]], tuple[GuardrailOption, ...]] = {}


def options_for(command: str) -> tuple[GuardrailOption, ...]:
    """`command`'s generated options - refusing two guardrails that would
    spell one flag (`TORQ_VERSION` and `torq-version`)."""
    gs = declared(command)
    key = (command, gs)
    if key not in _BUILT:
        flags: dict[str, str] = {}
        for g in gs:
            other = flags.setdefault(flag(g), g.id)
            if other != g.id:
                raise ValueError(
                    f"guardrails {other!r} and {g.id!r} would both be {flag(g)} on `uqs {command}`"
                )
        _BUILT[key] = tuple(option(g) for g in gs)
    return _BUILT[key]


def is_generated(param: object) -> TypeGuard[GuardrailOption]:
    """Whether `param` is a generated option, not one a signature declares."""
    return isinstance(param, GuardrailOption)


def command_of(cmd: Any, ctx: Any) -> str:
    """The command `cmd` runs, as guardrails name it: its words below the root."""
    words: list[str] = []
    while ctx is not None and ctx.parent is not None:
        words.append(ctx.info_name)
        ctx = ctx.parent
    return " ".join(reversed(words))


def generated(cmd: Any, command: str) -> tuple[GuardrailOption, ...]:
    """The generated options of `cmd`, the command `command` names - refusing
    one it already spells, and any on a command that takes no --override and
    --reason for them to become."""
    opts = options_for(command) if command else ()
    own = {o for p in cmd.params for o in (*p.opts, *p.secondary_opts)}
    for opt in opts:
        g = opt.guardrail
        if flag(g) in own:
            raise ValueError(
                f"`uqs {command}` already has {flag(g)}: guardrail {g.id} cannot add it"
            )
        if not {"--override", "--reason"} <= own:
            raise ValueError(
                f"guardrail {g.id} gives `uqs {command}` {flag(g)}, but that command takes no "
                "--override NAME --reason TEXT for it to become"
            )
    return opts


class SkipOptions(TyperCommand):
    """A command class taking its generated options: in its help, its
    completion and its parse, and handed to `safety.given`, never to the body."""

    def _generated(self, ctx: Any) -> tuple[GuardrailOption, ...]:
        return generated(self, command_of(self, ctx))

    def get_params(self, ctx: Any) -> list[Any]:
        params = super().get_params(ctx)
        extra = self._generated(ctx)
        if not extra:
            return params
        n = len(self.params)  # before the help option, which Click appends
        return [*params[:n], *extra, *params[n:]]

    def parse_args(self, ctx: Any, args: list[str]) -> list[str]:
        rest = super().parse_args(ctx, args)
        given: list[tuple[str, str]] = []
        for opt in self._generated(ctx):
            g, value = opt.guardrail, ctx.params.pop(opt.name, None)
            if g.component_scoped:
                given += [(f"{g.id}:{c}", f"{flag(g)} {c}") for c in value or ()]
            elif value:
                given.append((g.id, flag(g)))
        ctx.meta[META_KEY] = Request(command_of(self, ctx), tuple(given))
        return rest

    def invoke(self, ctx: Any) -> Any:
        token = _CURRENT.set(ctx.meta.get(META_KEY))
        try:
            return super().invoke(ctx)
        finally:
            _CURRENT.reset(token)


def requested(command: str, names: Sequence[str] | None) -> list[tuple[str, str]]:
    """(waiver, how it was spelled) for each `--override` in `names`, then each
    generated skip given to `command` while its body runs - unchecked:
    safety.given checks them."""
    out = [(n, f"--override {n}") for n in names or ()]
    current = _CURRENT.get()
    if current is not None and current.command == command:
        out += current.given
    return out


def forwarded(args: Sequence[str]) -> list[str]:
    """`args` with each generated skip spelled `--override NAME`, as `uqs
    --target` sends them to a server whose uqs may predate the flag. Parsed by
    the local tree as target_confirm does; a line it cannot parse is sent as it
    is, for the server to refuse. A word another option takes as its value is
    left alone, and nothing after `--` is touched."""
    from uqs.cli import target_confirm  # it imports safety, which imports this

    p = target_confirm.parse(args)
    if p is None or p.command is None:
        return list(args)
    ctx = p.ctx
    generated = {
        flag(o.guardrail): o.guardrail for o in ctx.command.get_params(ctx) if is_generated(o)
    }
    if not generated:
        return list(args)
    takes_value: set[str] = set()
    level = ctx
    while level is not None:
        for param in level.command.get_params(level):
            if param.param_type_name == "option" and not param.is_flag and not param.count:
                takes_value.update(param.opts)
        level = level.parent
    out: list[str] = []
    words = iter(args)
    for word in words:
        if word == "--":
            out += [word, *words]
            break
        name, eq, value = word.partition("=")
        g = generated.get(name)
        if g is not None and g.component_scoped:
            out += ["--override", f"{g.id}:{value if eq else next(words, '')}"]
            continue
        if g is not None and not eq:
            out += ["--override", g.id]
            continue
        out.append(word)
        if name in takes_value and not eq:
            out += [w for w in (next(words, None),) if w is not None]
    return out
