"""One option or argument per concept, carrying the concept's completer (#1884).

An attachment, a target, a job, a table, a source or a component used to be
declared afresh by each command, so whether TAB offered anything depended on
the command, not the value: `job attachment show` completed attachments and
every `--attach` did not. Each concept is declared here once - its name, its
metavar and its completer - and a command adds only its own help.
test_cli_conventions.py holds every option and argument of a concept to its
completer, so a new `--attach` that forgets one fails there.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

import typer

from uqs.cli import completion
from uqs.cli.choices import alternatives
from uqs.deploy.components import COMPONENTS


def attach_opt(help: str, *, required: bool = False) -> Any:  # noqa: A002 - typer's name
    """`--attachment NAME`: a saved attachment."""
    option = typer.Option(
        "--attachment", metavar="ATTACHMENT", help=help, autocompletion=completion.attachments
    )
    return Annotated[str, option] if required else Annotated[str | None, option]


AttachmentArg = Annotated[
    str,
    typer.Argument(
        metavar="ATTACHMENT",
        help="The attachment's name, e.g. piggybank-uat",
        autocompletion=completion.attachments,
    ),
]

#: What every TARGET argument takes.
TARGET_HELP = (
    "A target in deploy_targets.toml, or a server pushed to from here (`uqs deploy history`)"
)


def target_arg(
    help: str = TARGET_HELP,  # noqa: A002
    *,
    latest: bool = False,
    flags: bool = False,
) -> Any:
    """A TARGET argument. `latest`: optional, and without it the most recent
    deployment - what every command that reads a server falls back to. `flags`:
    optional only because the connection flags may name the server instead -
    a command that changes a server falls back to nothing (#2243), so its help
    claims no default."""
    if latest and flags:
        raise ValueError("a TARGET falls back to the latest deployment or to nothing, not both")
    if latest:
        help = f"{help}; default: the most recent deployment"
    argument = typer.Argument(
        metavar="TARGET", help=help, autocompletion=completion.targets, show_default=False
    )
    return Annotated[str | None, argument] if latest or flags else Annotated[str, argument]


#: A TARGET a command needs named: one it changes.
TargetArg = target_arg()
#: A TARGET a command may leave out, for the most recent deployment.
LatestTargetArg = target_arg(latest=True)


def target_opt(flag: str, help: str) -> typer.models.OptionInfo:  # noqa: A002
    """An option naming a target (`uqs --target`, `query --tunnel`)."""
    return typer.Option(flag, metavar="TARGET", help=help, autocompletion=completion.targets)


def job_arg(
    help: str,  # noqa: A002
    complete: Callable[..., Any] = completion.jobs,
    *,
    required: bool = True,
) -> Any:
    """A JOB argument; `complete` narrows it - streaming jobs, bounded workers."""
    argument = typer.Argument(metavar="JOB", help=help, autocompletion=complete)
    return Annotated[str, argument] if required else Annotated[str | None, argument]


def table_arg(help: str) -> Any:  # noqa: A002
    """A TABLE argument: one plant table."""
    return Annotated[
        str, typer.Argument(metavar="TABLE", help=help, autocompletion=completion.plant_table)
    ]


def tables_opt(flag: str, help: str) -> Any:  # noqa: A002
    """A comma-separated list of plant tables."""
    option = typer.Option(
        flag, metavar="TABLE,...", help=help, autocompletion=completion.plant_tables
    )
    return Annotated[str | None, option]


def profile_opt(help: str, *, required: bool = False) -> Any:  # noqa: A002
    """`--profile`: a start profile."""
    option = typer.Option("--profile", help=help, autocompletion=completion.profiles)
    return Annotated[str, option] if required else Annotated[str | None, option]


SourceArg = Annotated[
    str,
    typer.Argument(
        metavar="SOURCE",
        help="The source, as src/etl/sources/<source>.q names it",
        autocompletion=completion.sources,
    ),
]
SourcesArg = Annotated[
    list[str],
    typer.Argument(
        metavar="SOURCE...", help="The sources to check, by name", autocompletion=completion.sources
    ),
]

#: What each bundled component is, where its name does not say.
COMPONENT_NOTES = {"q": "PeachQ", "python": "CPython"}
ComponentArg = Annotated[
    str,
    typer.Argument(
        metavar="COMPONENT",
        help=f"The component to build: {alternatives(COMPONENTS, COMPONENT_NOTES)}",
        autocompletion=completion.choices(*COMPONENTS),
    ),
]
