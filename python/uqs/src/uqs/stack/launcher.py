"""Which TorQ launcher `uqs` runs: `$TORQHOME/torq.sh`, or a site's own.

Some sites keep TorQ's pieces apart - the core (`torq.q`) in one directory and
a site-managed `torq.sh` in another, to be used exactly as supplied.
`UQS_TORQ_LAUNCHER` names that script. TORQHOME still names the core, and the
launcher runs under the same generated SETENV and TORQPROCESSES as the
vendored one would, so it starts this tree's process configuration.
"""

from __future__ import annotations

import os
from pathlib import Path

from uqs.paths import UqsError, UqsPaths

#: An absolute path to an executable TorQ launcher, used instead of
#: `$TORQHOME/torq.sh`. Unset or empty: `$TORQHOME/torq.sh`.
LAUNCHER_ENV = "UQS_TORQ_LAUNCHER"


def torq_launcher(paths: UqsPaths) -> Path:
    """The launcher to run - refused, naming the variable, when it is set to
    anything but an absolute path to an executable file."""
    value = os.environ.get(LAUNCHER_ENV, "")
    if not value:
        return paths.torqhome / "torq.sh"
    launcher = Path(value)
    if not launcher.is_absolute():
        raise UqsError(f"{LAUNCHER_ENV}={value!r} must be an absolute path")
    if not launcher.is_file() or not os.access(launcher, os.X_OK):
        raise UqsError(
            f"{LAUNCHER_ENV}={value!r} is not an executable file - point it at the "
            "site's torq.sh, or unset it to use $TORQHOME/torq.sh"
        )
    return launcher
