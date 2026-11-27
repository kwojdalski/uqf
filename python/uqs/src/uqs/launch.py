"""The `uqs` script: `uqs cron run`'s not-due check, then the command line (#2987).

`uqs.cli` loads every command family, and its `main` loads `.env` and `.envrc`
before any command runs. A crontab line that fires every hour or minute for an
entry in another zone (uqs.cron.schedule) is mostly not due, so that check runs
here first, on the words alone, and exits 0 before any of it loads.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime


def main() -> None:
    """Entry point for the `uqs` script and `python -m uqs`."""
    now = datetime.now(UTC)
    if sys.argv[1:3] == ["cron", "run"]:
        from uqs.cron import guard

        guard.exit_if_not_due(sys.argv[1:], now)
    from uqs.cli import main as cli_main

    cli_main()
