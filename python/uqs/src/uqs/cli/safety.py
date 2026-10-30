"""One safety vocabulary for every uqs command (#1732, #1738 rule 5).

- `--dry-run/-n` on every command that changes state: everything is checked and
  what would change is shown, and nothing is changed.
- `--yes/-y` on every command that asks before acting.
- `--override NAME --reason TEXT` for every bypass of a check: NAME is one of
  the command's named overrides (`uqs override list`), repeatable, and one
  reason covers the overrides given with it. Each is warned about and recorded
  where its command records anything.
- An `Effect` on every command, carried by its Click class (`cls=ReadOnly`,
  `cls=ChangesState`, `cls=ConfirmsOnServer`, or `renamed(..., effect=...)`):
  whether it may change the machine it runs on. `uqs --target` reads it to
  decide whether to ask first (cli/target_confirm.py, #1780), so a command
  states it once, where it is declared, and no list repeats it.

The options are declared here once, so their names, short flags and help are
the same on every command; test_cli_conventions.py holds the commands to them.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated

import typer
from typer.core import TyperCommand

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


def marked(effect: Effect, base: type[TyperCommand] = TyperCommand) -> type[TyperCommand]:
    """`base` carrying `effect`: the `cls=` of a command. Typer wraps the
    command's function, so the mark is on the Click class, which survives."""
    return type(base.__name__, (base,), {"effect": effect})


ReadOnly = marked(Effect.READ_ONLY)
ConfirmsOnServer = marked(Effect.CONFIRMS_ON_SERVER)
ChangesState = marked(Effect.CHANGES_STATE)


def dry_run_note() -> str:
    """The line a dry run ends with."""
    return "[dim]dry run: nothing was changed. Run it again without --dry-run.[/]"


@dataclass(frozen=True)
class Bypass:
    """One named override: the commands that take it, and what it skips."""

    name: str
    commands: tuple[str, ...]
    skips: str


def catalog() -> list[Bypass]:
    """Every named override a command takes, read from where each is enforced."""
    from uqs.attach.overrides import POLICY
    from uqs.deploy import overrides as deploy
    from uqs.stack import start_overrides as start

    rows = [
        Bypass(start.NAMES[field], commands, start.CONSEQUENCES[field])
        for field, commands in start.COMMANDS.items()
    ]
    rows += [Bypass(name, ("deploy push",), skips) for name, skips in deploy.SKIPS.items()]
    rows += [
        Bypass(f"{code}:COMPONENT", ("job attachment add",), policy.risk)
        for code, policy in sorted(POLICY.items())
        if policy.overridable
    ]
    return rows


def given(command: str, names: list[str] | None, reason: str | None) -> dict[str, str]:
    """{override: reason} for the `--override`s given to `command` - refused,
    as a UqsError, for a name `command` does not take, an override without a
    reason, or a reason with no override."""
    names = list(names or ())
    text = (reason or "").strip()
    if not names:
        if reason is not None:
            raise UqsError("--reason says why an --override is acceptable, and none was given")
        return {}
    if not text:
        raise UqsError(f"--override {names[0]} needs --reason: say why the check is bypassed")
    known = sorted(b.name for b in catalog() if command in b.commands)
    for name in names:
        if name not in known:
            raise UqsError(
                f"`uqs {command}` has no override {name!r} - it takes: {', '.join(known)}"
            )
    return dict.fromkeys(names, text)
