"""One safety vocabulary for every uqs command (#1732, #1738 rule 5).

- `--dry-run/-n` on every command that changes state: everything is checked and
  what would change is shown, and nothing is changed.
- `--yes/-y` on every command that asks before acting.
- Two write models, one per kind of change (#1854). A command that changes
  this machine writes by default and takes `--dry-run/-n`. A command that
  rewrites data in place - an HDB, a server's release - is a dry run by
  default and writes only with `--apply`, which shows its plan and asks;
  `--yes/-y` answers that question. Such a command takes no `--dry-run`: it
  is what it does anyway.
- `--override NAME --reason TEXT` for every bypass of a check: NAME is one of
  the command's named overrides (`uqs override list`), repeatable, and one
  reason covers the overrides given with it. Each is warned about and recorded
  where its command records anything. A guardrail declared with `skip_flag`
  also gives each of its commands `--skip-<id>`, another spelling of the
  same override (cli/skip_options.py, #1826), which `given` reads too.
- An `Effect` on every command, carried by its Click class (`cls=ReadOnly`,
  `cls=ChangesState` or `cls=ConfirmsOnServer`):
  whether it may change the machine it runs on. `uqs --target` reads it to
  decide whether to ask first (cli/target_confirm.py, #1780), so a command
  states it once, where it is declared, and no list repeats it.

The options are declared here once, so their names, short flags and help are
the same on every command; test_cli_conventions.py holds the commands to them.
"""

from __future__ import annotations

import sys
from enum import StrEnum
from typing import Annotated

import typer
from typer.core import TyperCommand

from uqs.cli import completion, skip_options
from uqs.guardrails import Guardrail, aggregate, spelled
from uqs.logger import get_logger
from uqs.paths import UqsError, UqsUsageError

log = get_logger()

DryRunOpt = Annotated[
    bool,
    typer.Option(
        "--dry-run", "-n", help="Check everything and show what would change; change nothing"
    ),
]
YesOpt = Annotated[bool, typer.Option("--yes", "-y", help="Do not ask before acting")]
#: The `--apply` model (#1854): a dry run unless --apply, which asks first.
ApplyOpt = Annotated[
    bool,
    typer.Option(
        "--apply", help="Make the changes, after asking; without it, a dry run that changes nothing"
    ),
]
ApplyYesOpt = Annotated[
    bool, typer.Option("--yes", "-y", help="With --apply: do not ask before changing anything")
]
OverrideOpt = Annotated[
    list[str] | None,
    typer.Option(
        "--override",
        metavar="NAME",
        help="Bypass the check NAME for this command only (`uqs override list` names them); "
        "repeatable. Needs --reason; warned and recorded",
        autocompletion=completion.overrides,
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
    effect: Effect,
    base: type[TyperCommand] = skip_options.SkipOptions,
    *,
    dry_run_starts: str = "",
    acts_when: str = "",
    own_output: str = "",
    no_dry_run: str = "",
) -> type[TyperCommand]:
    """`base` carrying `effect`: the `cls=` of a command. Typer wraps the
    command's function, so the mark is on the Click class, which survives.
    It takes its registry-generated `--skip-<id>` options (skip_options).

    The rest are what an Effect does not say (#1893), each read by
    `uqs --target` (cli/target_confirm.py) and test_cli_conventions.py, so a
    new command with one of these properties declares it here, once:

    - `dry_run_starts`: why its --dry-run still starts a process - so on a
      --target it asks even then;
    - `acts_when`: the option under which a read-only command changes state
      (`status --interactive` starts and stops processes with single keys);
    - `own_output`: why its output belongs to something else, so --json and
      --dry-run do not apply;
    - `no_dry_run`: why a command that changes state reports, and has no
      --dry-run."""
    if not issubclass(base, skip_options.SkipOptions):
        base = type(base.__name__, (skip_options.SkipOptions, base), {})
    traits = {
        "effect": effect,
        "dry_run_starts": dry_run_starts,
        "acts_when": acts_when,
        "own_output": own_output,
        "no_dry_run": no_dry_run,
    }
    return type(base.__name__, (base,), traits)


ReadOnly = marked(Effect.READ_ONLY)
ConfirmsOnServer = marked(Effect.CONFIRMS_ON_SERVER)
ChangesState = marked(Effect.CHANGES_STATE)


#: The exit code of a command whose question was answered no - or not at all,
#: at an EOF or Ctrl-C (#1885). One code for every command: nothing was
#: changed, and the caller did not get what it asked for.
EXIT_DECLINED = 1


def interactive() -> bool:
    """Whether there is a terminal to ask on."""
    return sys.stdin.isatty()


def ask(question: str, *, default: bool = False) -> bool:
    """A yes/no question, the one way every command asks one (#1885): `[y/N]`
    unless `default` says otherwise, an EOF or Ctrl-C an answer of no, and -
    with no terminal to ask on - a refusal naming --yes, rather than a prompt
    into the void that ended in Click's bare "Aborted!"."""
    if not interactive():
        raise UqsError(
            f"{question.rstrip('?')}? - there is no terminal to ask on: add --yes to go ahead "
            "without asking. Nothing was changed."
        )
    try:
        return typer.confirm(question, default=default)
    except typer.Abort:
        return False


def json_needs_yes(as_json: bool, yes: bool, what: str) -> None:
    """Refuse a command that would ask, under --json without --yes (#2229):
    --json is one document on stdout and nothing interactive, so it never
    prompts. A refusal, exit 1 - nothing was changed, as `ask` refuses with no
    terminal - not a usage error. `what` ends "add --yes to ..."."""
    if as_json and not yes:
        raise UqsError(
            f"--json cannot ask for confirmation: add --yes to {what}. Nothing was changed."
        )


def confirm(question: str, *, yes: bool) -> None:
    """Ask `question` unless `yes`; a no ends the command with EXIT_DECLINED,
    having changed nothing."""
    if yes or ask(question):
        return
    log.warning("not confirmed - nothing was changed")
    raise typer.Exit(code=EXIT_DECLINED)


def choose(question: str, choices: list[str], *, default: str) -> str:
    """One of `choices`, asked on the terminal until one is given; without a
    terminal, refused - give it as an option instead."""
    if not interactive():
        raise UqsError(f"{question}: there is no terminal to ask on - give it as an option")
    shown = f"{question} ({'/'.join(choices)})"
    while True:
        try:
            answer = typer.prompt(shown, default=default)
        except typer.Abort:
            log.warning("not confirmed - nothing was changed")
            raise typer.Exit(code=EXIT_DECLINED) from None
        if answer in choices:
            return answer
        typer.echo(f"{answer!r} is not one of {', '.join(choices)}", err=True)


def dry_run_note(rerun: str = "without --dry-run") -> str:
    """The line every dry run ends with, whatever it found - something to do or
    nothing (#1880). `rerun` is how to act on it: a command whose dry run is its
    default, without --apply, says "with --apply"."""
    return f"[dim]dry run: nothing was changed. Run it again {rerun}.[/]"


def registry() -> tuple[Guardrail, ...]:
    """Every guardrail, from the modules that enforce them, in a fixed order:
    start, deployment, attachment, attached backfill, then (#1852) the forced
    writer, the q conversion and job removal. Gathered here, in the front end,
    because uqs/guardrails.py is a leaf every layer imports and must import
    none."""
    from uqs.attach import backfill_forced as forced
    from uqs.attach import backfill_policy as backfill
    from uqs.attach import overrides as attach
    from uqs.deploy import overrides as deploy
    from uqs.deploy import portable
    from uqs.scaffold import remove_policy as remove
    from uqs.stack import start_overrides as start

    return aggregate(
        [
            tuple(start.GUARDRAILS.values()),
            deploy.GUARDRAILS,
            attach.GUARDRAILS,
            backfill.GUARDRAILS,
            # #1852: the bypasses that were bare flags, each with its owner
            forced.GUARDRAILS,
            portable.GUARDRAILS,
            remove.GUARDRAILS,
        ],
    )


def catalog() -> list[Guardrail]:
    """Every named override a command takes: the overridable guardrails, read
    from where each is enforced (`registry`)."""
    return [g for g in registry() if g.overridable]


def _as_id(name: str, ids: dict[str, str]) -> str:
    """`name`'s guardrail id, with its component: `torq-version:stp1` is
    `TORQ_VERSION:stp1`. An id spelled as it is declared passes as it is."""
    gid, sep, component = name.partition(":")
    return ids.get(spelled(gid), gid) + sep + component


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
        raise UqsUsageError(f"{asked[0][1]} needs --reason: say why the check is bypassed")
    every = {g.id: g for g in registry()}
    ids = {spelled(gid): gid for gid in every}  # torq-version -> TORQ_VERSION (#1884)
    known = sorted(g.name for g in every.values() if g.overridable and command in g.commands)
    asked = [(_as_id(name, ids), spelling) for name, spelling in asked]
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
                f"{spelling}: {spelled(g.id)} is waived for one component - give {g.name}"
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
