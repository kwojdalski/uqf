"""`uqs summary -i`'s row actions: start, stop and restart the highlighted process.

One key each - s, x, r - acting on the row's `Process`. Each runs torq.sh as
`uqs start/stop/restart <procname>` does, with its output captured rather
than drawn over the browser, and reports the last line it printed when it
fails. Stopping is one key like the others: the browser shows what it is
about to act on, and a stopped process is one `s` away from running again.
"""

from __future__ import annotations

from collections.abc import Callable

from uqs.cli.shared import _paths
from uqs.cli.table_browser import RowAction
from uqs.paths import UqsError
from uqs.stack import runtime

#: (key, label, past tense, the stack.runtime function), looked up by name
#: when it runs.
VERBS: tuple[tuple[str, str, str, str], ...] = (
    ("s", "Start", "started", "start"),
    ("x", "Stop", "stopped", "stop"),
    ("r", "Restart", "restarted", "restart"),
)


def act(verb: str, done: str, function: str, port: int, row: dict[str, str]) -> str:
    """Run one lifecycle verb on the row's process; what to tell the user."""
    procname = row.get("Process", "").strip()
    if not procname:
        raise UqsError("this table has no Process column - drop --columns to act on processes")
    run: Callable = getattr(runtime, function)
    result = run(_paths(), procname, base_port=port, capture=True)
    if result.returncode != 0:
        said = (result.stderr or result.stdout or "").strip().splitlines()
        raise UqsError(f"{verb.lower()} {procname} failed: {said[-1] if said else 'no output'}")
    return f"{done} {procname}"


def process_actions(port: int) -> list[RowAction]:
    """The s/x/r actions for a summary on base port `port`."""
    return [
        RowAction(
            key,
            label,
            lambda row, label=label, done=done, fn=fn: act(label, done, fn, port, row),
        )
        for key, label, done, fn in VERBS
    ]
