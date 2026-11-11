"""The option every stop command shares, declared once (#2689).

`uqs stop`, `uqs job receiver stop`, `uqs job attachment remove` and `uqs
connector stop` all stop a process by the one policy (uqs/process_stop.py):
SIGTERM, wait until it is confirmed gone, and report one still running rather
than call it stopped. `--kill` is the same escalation on each of them, so it is
written here and held to one meaning by tests/test_stop_options.py.
"""

from __future__ import annotations

from typing import Annotated

import typer

KillOpt = Annotated[
    bool,
    typer.Option(
        "--kill",
        help="SIGKILL whatever is still running after SIGTERM and its grace - for a "
        "process that ignores a normal stop. The last resort: no clean shutdown, and a "
        "tickerplant may leave its log's last message torn",
    ),
]
