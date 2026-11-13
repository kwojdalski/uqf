"""`uqs deploy push`'s policy options: the requirements a deployment holds an
artifact and a server to, and the named overrides that relax them (#1557,
deploy/overrides.py), given as `--override NAME --reason TEXT` (#1732). Kept
beside cli/deploy.py, which takes each as a parameter, so the push command
stays readable in one sitting."""

from __future__ import annotations

from typing import Annotated

import typer

from uqs.cli import safety
from uqs.deploy import overrides
from uqs.deploy.config import DeployError
from uqs.logger import get_logger
from uqs.paths import UqsError

log = get_logger()


def waived(command: str, names: list[str] | None, reason: str | None) -> dict[str, str]:
    """{override: reason} for `command`'s `--override`s, refused as a
    DeployError (safety.given), each warned about: a bypass is never silent."""
    try:
        given = safety.given(command, names, reason)
    except UqsError as exc:
        raise DeployError("arguments", str(exc)) from exc
    for name, why in given.items():
        log.warning("uqs {}: --override {} ({})", command, name, why)
    return given


def bypasses(names: list[str] | None, reason: str | None) -> dict[str, str | None]:
    """make_config's override fields - accept_digest, allow_dirty, ... - from
    the `--override NAME`s given and their --reason; None for each not given."""
    given = waived("deploy push", names, reason)
    return {name.replace("-", "_"): given.get(name) for name in overrides.SKIPS}


ExpectedSha256 = Annotated[
    str | None,
    typer.Option(
        "--expected-sha256",
        metavar="SHA256",
        help="Refuse an artifact whose sha256 is anything else (usually a target's "
        "expected_sha256)",
    ),
]
QVersion = Annotated[
    str | None,
    typer.Option(
        "--q-version",
        metavar="REQUIREMENT",
        help="The server's q must be this version (4.1) or at least it (>=4.1), as "
        "preflight finds it; usually a target's q_version",
    ),
]
