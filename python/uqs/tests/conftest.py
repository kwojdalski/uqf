"""uqs's tests: no q process a test started may outlive the run (#1760).

A receiver (scripts/dev/remote_receiver.q) stops itself only at its -deadline,
so one a test forgot idles on its port long after pytest is gone - three hundred
of them once piled up on a developer's machine. Each test that starts one owns it
(attach_backfill_support.Receivers); this is the backstop that says when one
did not. It looks only under this process's own pytest tmp directory - per
xdist worker, per run - so another session's run, or a stack, never trips it.
"""

from __future__ import annotations

import pytest
from attach_backfill_support import receivers_under, stop_process


@pytest.fixture(scope="session", autouse=True)
def no_receiver_outlives_the_run(tmp_path_factory):
    yield
    root = tmp_path_factory.getbasetemp()
    left = receivers_under(root)
    for pid in left:
        stop_process(pid)
    if left:
        pytest.fail(
            f"remote_receiver.q process(es) {left} started under {root} were still running "
            "at the end of the run (stopped now): a test started a receiver it did not stop"
        )
