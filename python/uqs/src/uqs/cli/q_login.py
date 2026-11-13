"""How `uqs query` and `uqs schema` log in to a q process (#1853).

The user is an option; the password never is - `ps` and shell history would
show it. `--password-env NAME` names the variable holding it, as `job
attachment add`'s does, and with none it is TorQ's own demo password.
"""

from __future__ import annotations

import os
from typing import Annotated

import typer

from uqs.attach.config import DEFAULT_PASSWORD, DEFAULT_USER
from uqs.paths import UqsError

__all__ = ["DEFAULT_PASSWORD", "DEFAULT_USER", "QPasswordEnvOpt", "QUserOpt"]

#: Every --user states its default one way, in its help (#1853).
QUserOpt = Annotated[
    str,
    typer.Option(
        "--user",
        help=f"User to log in to the q process as (default: {DEFAULT_USER})",
        show_default=False,
    ),
]
#: The password is never an argument (#1853): `ps` and shell history would
#: show it. --password-env names the variable holding it, as `job attachment
#: add`'s does.
QPasswordEnvOpt = Annotated[
    str | None,
    typer.Option(
        "--password-env",
        metavar="NAME",
        help="Environment variable holding --user's password (default: TorQ's own demo "
        "credential). The password itself is never printed.",
        show_default=False,
    ),
]


def q_password(password_env: str | None) -> str:
    """The password `query` and `schema` log in with: the variable
    `--password-env` names, else TorQ's demo one (#1853)."""
    if password_env is None:
        return DEFAULT_PASSWORD
    value = os.environ.get(password_env)
    if value is None:
        raise UqsError(
            f"--password-env {password_env}: ${password_env} is not set - export the "
            "password in it first"
        )
    return value
