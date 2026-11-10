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

from uqs.cli.concepts import TARGET_HELP, target_arg
from uqs.cli.vocabulary import duration
from uqs.deploy import config, control, target_keys, targets
from uqs.deploy.config import DeployError
from uqs.logger import get_logger
from uqs.paths import UqsError, repo_root

log = get_logger()

#: The flags that say where a server is and how to reach it, as
#: make_config's parameter names.
CONNECTION = ("ssh", "dest", "remote_user", "connect_timeout")

#: `deploy status`'s: it only reads a server, so without one it reads the most recent deployment's,
#: as `target check` does.
Target = target_arg(f"{TARGET_HELP}; for one neither knows, give --ssh and --dest", latest=True)
#: `deploy rollback`'s and `prune`'s: they change a server, so it is named - as a TARGET or by
#: --ssh and --dest - and never guessed from whichever was deployed last (#2243).
NamedTarget = target_arg(f"{TARGET_HELP}; or give --ssh and --dest instead", flags=True)
#: `--ssh`, as `uqs target add` names it; `--host` until #1768, where every
#: other command's --host is a q host. The old spelling parses for one release.
Ssh = Annotated[
    str | None,
    typer.Option(
        "--ssh",
        help="The ssh destination, as your ssh config knows it: user@host or a Host "
        "alias; overrides TARGET's",
    ),
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
        "--ssh login. Overrides TARGET's",
    ),
]
ConnectTimeout = Annotated[
    int,
    duration(
        "--connect-timeout",
        integer=True,
        help="How long (a DURATION) ssh/scp may take to connect; overrides TARGET's",
    ),
]
#: None unless given, so neither overrides TARGET's value nor restates
#: make_config's default, which is the only one (#1892); a DURATION (#1857).
CommandTimeout = Annotated[
    int | None,
    duration(
        "--command-timeout",
        integer=True,
        help=f"How long (a DURATION) each remote step may take, default "
        f"{config.DEFAULT_COMMAND_TIMEOUT}s; overrides TARGET's",
        show_default=False,
    ),
]


def verify_timeout_option(integer: bool = False) -> typer.models.OptionInfo:
    """`--verify-timeout`: one readiness wait, on `deploy push`, `rollback` and
    `check` - which called it `--deadline` until #1857."""
    return duration(
        "--verify-timeout",
        integer=integer,
        help=f"How long (a DURATION) the processes have to become ready, default "
        f"{config.DEFAULT_VERIFY_TIMEOUT}s; overrides the target's",
        show_default=False,
    )


VerifyTimeout = Annotated[int | None, verify_timeout_option(integer=True)]
#: Whether a source with no credential may run on its fixture (#1857): the
#: same pair on `job backfill`, which defaults to live, and `deploy push` and
#: `check`, which default to the fixture - the default is each command's.
FixtureOpt = Annotated[
    bool,
    typer.Option(
        "--fixture/--live",
        help="Let a source with no credential run on its fixture, or (--live) refuse it",
    ),
]
DEFAULT_CONNECT_TIMEOUT = config.DEFAULT_CONNECT_TIMEOUT
#: A push to a server no target names declares one (deploy/history.py), as a
#: change `uqs target log` records (#1756); this says not to.
Register = Annotated[
    bool,
    typer.Option(
        "--register/--no-register",
        help="Declare a target for a server no target names, through `uqs target add`'s "
        "checks and change log (the default), or not",
    ),
]


def on_command_line(ctx: typer.Context, name: str) -> bool:
    """Was option `name` given on the command line, rather than defaulted?
    By the source's name: typer carries its own click, whose ParameterSource
    is not click.core's, so an identity check against that never matches."""
    source = ctx.get_parameter_source(name)
    return source is not None and source.name == "COMMANDLINE"


def server(ctx: typer.Context, target: str | None, *, unnamed: str | None = None, **flags) -> dict:
    """make_config's keyword arguments for a command that reaches one server:
    every push option TARGET sets - its connection and its timeouts alike -
    with each flag given on the command line over it; with no TARGET, the
    flags alone, which must then name the server. A flag left unset (None)
    is left out, so make_config's default applies.

    With neither a TARGET nor a connection flag, a command that only reads the
    server (`unnamed` None) reads the most recent deployment's, as `target
    check` does, refused (1) when there is none; one that changes it is
    refused as a usage error (2) saying `unnamed` - never guessed (#2243)."""
    if target is None:
        absent = [target_keys.flag(k) for k in ("ssh", "dest") if flags[k] is None]
        if not absent:
            return {k: v for k, v in flags.items() if v is not None}
        if len(absent) == 1:  # half a server: say which half is missing
            raise DeployError(
                "arguments", f"give a TARGET, or {absent[0]} for a server no target declares"
            )
        if unnamed is not None:
            raise DeployError(
                "arguments", f"{unnamed}, or --ssh and --dest for a server no target declares"
            )
        # nothing names it, and this only reads it: the most recent deployment's, below
    try:
        label, settings = control.settings_for(target, repo_root())
        r = control.remote_from(label, settings)
    except UqsError as exc:  # an unknown name: a refusal (1), not a usage error (#1885)
        raise DeployError("target", str(exc)) from None
    out = targets.push_options(label, settings)
    out.update(ssh=r.host, dest=r.dest, remote_user=r.remote_user)
    out.update({k: v for k, v in flags.items() if on_command_line(ctx, k)})
    log.info("target {}: {}:{}", r.name, out["ssh"], out["dest"])
    return out


LiveCheckTimeout = Annotated[
    int,
    duration(
        "--live-check-timeout",
        integer=True,
        help="How long (a DURATION) every live check together may take",
    ),
]
SmokeTimeout = Annotated[
    int,
    duration(
        "--smoke-timeout", integer=True, help="How long (a DURATION) the offline check may take"
    ),
]
Soak = Annotated[
    int | None,
    duration(
        "--soak",
        integer=True,
        help="After verify, let data flow this long (a DURATION); then a streaming job that is "
        "failing, or never beat, rolls the deployment back before activation",
    ),
]


def config_options(params: dict) -> dict:
    """`deploy push`'s parameters as make_config takes them (#1857): `live`
    refuses a fixture (the option is `--fixture/--live`), a server path is
    text, and `--register` and `--json` are choices about this machine, not
    the push."""
    options = dict(params)
    options.pop("register", None)
    options.pop("as_json", None)
    options["live"] = not options.pop("fixture")
    for name in ("odbc_home", "q_home"):
        options[name] = None if options[name] is None else str(options[name])
    return options
