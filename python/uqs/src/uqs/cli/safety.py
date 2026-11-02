"""One safety vocabulary for every uqs command (#1732, #1738 rule 5).

- `--dry-run/-n` on every command that changes state: everything is checked and
  what would change is shown, and nothing is changed.
- `--yes/-y` on every command that asks before acting.
- `--override NAME --reason TEXT` for every bypass of a check: NAME is one of
  the command's named overrides (`uqs override list`), repeatable, and one
  reason covers the overrides given with it. Each is warned about and recorded
  where its command records anything. A guardrail declared with `skip_flag`
  also gives each of its commands `--skip-<id>`, another spelling of the
  same override (cli/skip_options.py, #1826), which `given` reads too.
- An `Effect` on every command, carried by its Click class (`cls=ReadOnly`,
  `cls=ChangesState`, `cls=ConfirmsOnServer`, or `renamed(..., effect=...)`):
  whether it may change the machine it runs on. `uqs --target` reads it to
  decide whether to ask first (cli/target_confirm.py, #1780), so a command
  states it once, where it is declared, and no list repeats it.

The options are declared here once, so their names, short flags and help are
the same on every command; test_cli_conventions.py holds the commands to them.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

import typer
from typer.core import TyperCommand

from uqs import guardrails
from uqs.cli import skip_options
from uqs.guardrails import Guardrail
from uqs.paths import UqsError

DryRunOpt = Annotated[
    bool,
    typer.Option(
        "--dry-run", "-n", help="Check everything and show what would change; change nothing"
    ),
]
YesOpt = Annotated[bool, typer.Option("--yes", "-y", help="Do not ask before acting")]
OverrideOpt = Annotated[
    list[str] | None,
    typer.Option(
        "--override",
        metavar="NAME",
        help="Bypass the check NAME for this command only (`uqs override list` names them); "
        "repeatable. Needs --reason; warned and recorded",
    ),
]
ReasonOpt = Annotated[
    str | None,
    typer.Option("--reason", help="Why the --override is acceptable: recorded with it"),
]


class Effect(StrEnum):
    """What a command may do to the machine it runs on (#1780)."""

    #: reads, and never changes anything
    READ_ONLY = "read-only"
    #: changes the server only with --apply, and then shows its plan and asks
    #: there - or takes its own --yes (#1755)
    CONFIRMS_ON_SERVER = "confirms-on-server"
    #: may change state: start or stop a process, write a file, run any q
    CHANGES_STATE = "changes-state"


def marked(
    effect: Effect, base: type[TyperCommand] = skip_options.SkipOptions
) -> type[TyperCommand]:
    """`base` carrying `effect`: the `cls=` of a command. Typer wraps the
    command's function, so the mark is on the Click class, which survives.
    It takes its registry-generated `--skip-<id>` options (skip_options)."""
    if not issubclass(base, skip_options.SkipOptions):
        base = type(base.__name__, (skip_options.SkipOptions, base), {})
    return type(base.__name__, (base,), {"effect": effect})


ReadOnly = marked(Effect.READ_ONLY)
ConfirmsOnServer = marked(Effect.CONFIRMS_ON_SERVER)
ChangesState = marked(Effect.CHANGES_STATE)


def dry_run_note() -> str:
    """The line a dry run ends with."""
    return "[dim]dry run: nothing was changed. Run it again without --dry-run.[/]"


def catalog() -> list[Guardrail]:
    """Every named override a command takes: the overridable guardrails, read
    from where each is enforced (uqs/guardrails.py)."""
    return [g for g in guardrails.registry() if g.overridable]


def given(command: str, names: list[str] | None, reason: str | None) -> dict[str, str]:
    """{override: reason} for the `--override`s given to `command`, and the
    `--skip-<id>`s generated for it (skip_options), as one: `--skip-eod-fence`
    is `eod-fence`, `--skip-torq-version stp1` is `TORQ_VERSION:stp1`. Refused,
    as a UqsError: a name `command` does not take - unknown, another command's,
    or not overridable - a component missing where the guardrail is waived per
    component or given where it is not, one guardrail asked for both ways, an
    override without a reason, or a reason with no override. The same waiver
    given twice, by either spelling, is one."""
    asked = skip_options.requested(command, names)
    text = (reason or "").strip()
    if not asked:
        if reason is not None:
            raise UqsError("--reason says why an --override is acceptable, and none was given")
        return {}
    if not text:
        raise UqsError(f"{asked[0][1]} needs --reason: say why the check is bypassed")
    every = {g.id: g for g in guardrails.registry()}
    known = sorted(g.name for g in every.values() if g.overridable and command in g.commands)
    whole = {name: spelling for name, spelling in asked if ":" not in name}
    out: dict[str, str] = {}
    for name, spelling in asked:
        gid, sep, component = name.partition(":")
        g = every.get(gid)
        if g is None or command not in g.commands:
            whose = (
                f" (it is {', '.join(f'`uqs {c}`' for c in g.commands)}'s)" if g is not None else ""
            )
            raise UqsError(
                f"`uqs {command}` has no override {name!r}{whose} - it takes: "
                + (", ".join(known) or "none")
            )
        if not g.overridable:
            raise UqsError(f"{g.id} is never overridden: {g.risk or g.description}")
        if g.component_scoped and (
            not component or component != component.strip() or ":" in component
        ):
            raise UqsError(
                f"{spelling}: {g.id} is waived for one component - give {g.id}:COMPONENT"
            )
        if not g.component_scoped and sep:
            if gid in whole:
                raise UqsError(
                    f"conflicting requests for {g.id}: {whole[gid]} waives it for the whole "
                    f"command, {spelling} for one component - give it once"
                )
            raise UqsError(
                f"{spelling}: {g.id} is waived for the whole command, not one component - "
                f"give --override {g.id}"
            )
        out[name] = text
    return out
