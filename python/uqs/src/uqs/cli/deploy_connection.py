"""How a `uqs deploy` command names the server it reaches (#1736): a TARGET
from deploy_targets.toml, or - for a server no target declares - the
connection flags. One set of flags, declared once here, so `push`, `status`,
`rollback` and `prune` spell and explain them identically.

A target is declared once (`uqs target add`, or a push registers it), and
every command that reaches a server takes it as a positional TARGET. The
flags stay as the "no target declared" escape; given beside a TARGET, a flag
wins over the target's value, as it does for push's other options.
"""

from __future__ import annotations

from typing import Annotated

import typer

from uqs.deploy import control
from uqs.deploy.config import DeployError
from uqs.logger import get_logger
from uqs.paths import UqsError, repo_root

log = get_logger()

#: The flags that say where a server is and how to reach it, as
#: make_config's parameter names.
CONNECTION = ("host", "dest", "remote_user", "connect_timeout")

Target = Annotated[
    str | None,
    typer.Argument(
        metavar="TARGET",
        help="A target in deploy_targets.toml, or a server pushed to from here "
        "(`uqs deploy history`); for one neither knows, give --host and --dest",
        show_default=False,
    ),
]
Host = Annotated[
    str | None,
    typer.Option("--host", help="ssh destination, as your ssh config knows it; overrides TARGET's"),
]
Dest = Annotated[
    str | None,
    typer.Option("--dest", help="Absolute directory on the server; overrides TARGET's"),
]
RemoteUser = Annotated[
    str | None,
    typer.Option(
        "--remote-user",
        help="Run the steps as this account through `sudo -n -iu`; ssh and scp stay the "
        "--host login. Overrides TARGET's",
    ),
]
ConnectTimeout = Annotated[
    int, typer.Option("--connect-timeout", help="ssh/scp, seconds; overrides TARGET's")
]
DEFAULT_CONNECT_TIMEOUT = control.DEFAULT_CONNECT_TIMEOUT
#: A push to a server no target names declares one (deploy/history.py), as a
#: change `uqs target log` records (#1756); this says not to.
NoRegister = Annotated[
    bool,
    typer.Option(
        "--no-register",
        help="Do not declare a target for a server no target names (by default a "
        "successful push adds one, through `uqs target add`'s checks and change log)",
    ),
]


def on_command_line(ctx: typer.Context, name: str) -> bool:
    """Was option `name` given on the command line, rather than defaulted?
    By the source's name: typer carries its own click, whose ParameterSource
    is not click.core's, so an identity check against that never matches."""
    source = ctx.get_parameter_source(name)
    return source is not None and source.name == "COMMANDLINE"


def server(ctx: typer.Context, target: str | None, **flags) -> dict:
    """{host, dest, remote_user, connect_timeout} for a command that reaches
    one server: TARGET's, with each connection flag given on the command line
    over it; with no TARGET, the flags alone, which must then name the server."""
    if target is None:
        absent = [f"--{k}" for k in ("host", "dest") if flags[k] is None]
        if absent:
            raise DeployError(
                "arguments",
                f"give a TARGET, or {' and '.join(absent)} for a server no target declares",
            )
        return flags
    try:
        r = control.remote_for(target, repo_root())
    except UqsError as exc:
        raise DeployError("arguments", str(exc)) from None
    out = {
        "host": r.host,
        "dest": r.dest,
        "remote_user": r.remote_user,
        "connect_timeout": r.connect_timeout,
    }
    out.update({k: v for k, v in flags.items() if on_command_line(ctx, k)})
    log.info("target {}: {}:{}", r.name, out["host"], out["dest"])
    return out


def renamed_target_option(names: str) -> None:
    """`deploy push --target A,B`, the spelling before #1736: still accepted
    for one release, saying what to type instead."""
    shown = " ".join(n.strip() for n in names.split(",") if n.strip())
    log.warning(
        "--target is renamed: give the targets after the artifact, "
        "`uqs deploy push ARTIFACT {}` - the option goes in the next release",
        shown,
    )
