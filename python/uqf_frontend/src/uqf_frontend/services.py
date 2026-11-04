"""The Services view: the deployment targets this machine declares (#1949).

An operator picks a server here from the targets `uqs` already knows, so the
frontend keeps no second registry. The rows are `uqs target list --json`'s,
read by the same function (`uqs.deploy.history.target_rows`) from the same
deploy_targets.toml - or the file $UQS_DEPLOY_TARGETS names - so a target
added with `uqs target add`, or registered by a push, appears here unchanged.

READING IS LOCAL. A target's ssh destination and dest are what the file says,
and "last deployed" is this workstation's deploy_history.json: nothing is
asked of the server, so listing never opens an ssh connection, and a target
that cannot be reached is listed like any other (test_services.py holds the
read to no subprocess and no socket).

A target needs no deployment profile to be listed. `uqs target add` writes
one with only ssh and dest, and the registry's checks accept it - a profile
is for a push, which may give it on the command line.

uqs is imported inside `read`, as control.py imports it: the read path for
/query and /catalog loads nothing of the orchestrator (test_coercion.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from uqf_frontend.config import Settings


@dataclass(frozen=True)
class Registry:
    """What the declaration said: its targets, where it is, and why it could
    not be read, when it could not."""

    targets: list[dict[str, Any]] = field(default_factory=list)
    source: str = ""
    declared: bool = False
    invalid: str | None = None


def root(settings: Settings) -> Path:
    """The repository the declaration is read beside: the stack this
    frontend serves, else the checkout uqs runs from - as `uqs` resolves it."""
    if settings.stack_root is not None:
        return settings.stack_root
    from uqs.paths import repo_root

    return repo_root()


def read(settings: Settings) -> Registry:
    """The declared targets, as `uqs target list --json` reads them.

    A missing file is an empty registry, not an error: no target has been
    added yet. A file the registry refuses - bad TOML, an unknown key, an
    undeclared environment - is reported in `invalid` with uqs's own words,
    which name the line, rather than as a failed request: the operator needs
    the path and the reason to fix it, and a 4xx would hide both."""
    from uqs.cells import Shown
    from uqs.deploy import history, targets
    from uqs.deploy.config import DeployError
    from uqs.paths import UqsError

    where = root(settings)
    path = targets.declaration_path(where)
    if not path.is_file():
        return Registry(source=str(path))
    try:
        rows = history.target_rows(where)
    except (DeployError, UqsError, OSError) as exc:
        return Registry(source=str(path), declared=True, invalid=str(exc))
    except (ValueError, TypeError, AttributeError) as exc:
        # The backstop (#2026): whatever uqs did not turn into its own refusal
        # reading a hand-edited or corrupt file is still that file's problem,
        # and one such file must not blank the view with a 500.
        return Registry(
            source=str(path),
            declared=True,
            invalid=f"{path} or its deploy history could not be read: {exc}",
        )
    out = []
    for row in rows:
        last = row.get("last_deployed")
        out.append(
            {
                "name": row["target"],
                "ssh": row["ssh"],
                "dest": row["dest"],
                "remote_user": row["remote_user"] or None,
                # The column appears only when some target names one.
                "environment": row.get("environment") or None,
                "labels": row["labels"],
                "last_deployed": last.value if isinstance(last, Shown) else last,
            }
        )
    return Registry(targets=out, source=str(path), declared=True)
