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
from uqs.paths import UqsError

#: The old per-override options, each now `--override NAME --reason REASON`
#: (#1732); kept, hidden, for one release.
OLD_SPELLINGS = {f"--{name}": ("--override", name) for name in overrides.SKIPS}


def bypasses(names: list[str] | None, reason: str | None) -> dict[str, str | None]:
    """make_config's override fields - accept_digest, ... - from the
    `--override NAME`s given and their --reason; None for each not given."""
    try:
        given = safety.given("deploy push", names, reason)
    except UqsError as exc:
        raise DeployError("arguments", str(exc)) from exc
    return {name.replace("-", "_"): given.get(name) for name in overrides.SKIPS}


AllowDirty = Annotated[
    bool,
    typer.Option(
        "--allow-dirty", help="Deploy an artifact built from uncommitted changes (refused)"
    ),
]
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
