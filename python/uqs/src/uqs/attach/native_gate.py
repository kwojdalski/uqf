"""The start gate, for a publisher a native TorQ start launches (#1845, #1441).

`uqs start` refuses an attachment's publisher unless it is verified, started
under its stack, and - where a restart could have lost them - its tables are
still on the tickerplant (stack/attach_state.py, live.py). A site's own
`torq.sh start all` never asks uqs. So the registered row starts the publisher
through a launcher (native_row.py) whose first command is this module: it
exits 1, and q is never started, unless

  * the attachment's manifest says `verified` (anything else, or a manifest
    that cannot be read, refuses - as the uqs gate does, #1442);
  * the start is under the attachment's stack (`-stackid`, which torq.sh
    passes from KDBBASEPORT);
  * the tickerplant answers and holds every attached table among its tables
    and its pub/sub tables - asked ALWAYS here, not only for a runtime-only or
    remote registration: a native `start all` starts the tickerplant at the
    same moment, and rows published before it has reloaded the schema block
    are discarded without a word. So it is asked again, every
    `INTERVAL_S`, for up to `--wait` seconds, while the stack comes up; a
    stack that never answers refuses, since an answer nobody got is not a yes.

The stack's password comes from the variable the attachment names, in the
environment torq.sh runs in; a variable that is not set refuses at once.
What it says goes to stderr, which torq.sh writes to the process's log.

    python -m uqs.attach.native_gate NAME --state-dir DIR --stack-id ID [--wait S]
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable
from pathlib import Path

from uqs.attach import config, live
from uqs.attach.manifest import Manifest
from uqs.attach.remote import Ipc
from uqs.paths import UqsError

#: How long a native start waits for the tickerplant to hold the tables.
WAIT_S = 120.0
INTERVAL_S = 2.0


def check(
    name: str,
    stack_id: str,
    *,
    state_dir: Path | None = None,
    ipc: Ipc | None = None,
    wait: float = WAIT_S,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> str:
    """Refuse (UqsError) the native start of attachment `name`'s publisher
    unless every condition in the module docstring holds; say what passed."""
    att = config.load(name, state_dir)
    proc, nothing = att.publisher.procname, "Nothing was started."
    try:
        state = Manifest.load(name, state_dir).state
    except UqsError as exc:
        raise UqsError(f"{proc}: the manifest of attachment {name} cannot be read ({exc}). "
                       f"{nothing}") from None  # fmt: skip
    if state != "verified":
        raise UqsError(
            f"{proc} is the publisher of attachment {name} (stack {att.stack.id}), which is "
            f"{state}: a native start may start it once `uqs job attachment add {name}` has "
            f"verified it. {nothing}"
        )
    if stack_id != str(att.stack.id):
        raise UqsError(
            f"{proc} belongs to attachment {name} on stack {att.stack.id}, and this start is "
            f"under -stackid {stack_id or '(none)'}: it would look for the wrong discovery. "
            f"{nothing}"
        )
    if ipc is None:
        try:
            ipc = live.ipc_for(att)
        except UqsError as exc:
            raise UqsError(f"{proc}: {exc} in the environment of this start. {nothing}") from None
    deadline = clock() + wait
    while True:
        try:
            gone, where = live.missing(att, ipc)
        except UqsError as exc:
            reason = str(exc)
        else:
            if not gone:
                return f"{proc}: attachment {name} is verified and {where} holds its tables"
            reason = f"{', '.join(gone)} not registered on {where}"
        if clock() >= deadline:
            raise UqsError(
                f"{proc} is the publisher of attachment {name}, and its tables are not ready on "
                f"stack {att.stack.id} after {wait:g}s: {reason}. Re-apply with `uqs job "
                f"attachment add {name}` once the stack is up. {nothing}"
            )
        sleep(INTERVAL_S)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m uqs.attach.native_gate")
    parser.add_argument("name")
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--stack-id", default="")
    parser.add_argument("--wait", type=float, default=WAIT_S)
    args = parser.parse_args(argv)
    try:
        said = check(args.name, args.stack_id, state_dir=args.state_dir, wait=args.wait)
    except UqsError as exc:
        print(f"uqs start gate: refused: {exc}", file=sys.stderr)
        return 1
    print(f"uqs start gate: {said}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
