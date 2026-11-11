"""uqs's tests: no q process a test started may outlive the run (#1760).

A receiver (scripts/jobs/attached_receiver.q) stops itself only at its -deadline,
so one a test forgot idles on its port long after pytest is gone - three hundred
of them once piled up on a developer's machine. Each test that starts one owns it
(attach_backfill_support.Receivers); this is the backstop that says when one
did not. It looks only under this process's own pytest tmp directory - per
xdist worker, per run - so another session's run, or a stack, never trips it.
"""

from __future__ import annotations

import pytest
from attach_backfill_support import receivers_under, stop_process


@pytest.fixture(autouse=True)
def own_lifecycle_lock(tmp_path, monkeypatch):
    """Each test's own stack lifecycle lock file (#2028), under its tmp_path.

    Many tests run a lifecycle command against the checkout's own paths, so
    they all took one lock file, `output/uqs/.lifecycle.lock`: one xdist worker
    holding it made another's frontend route - which refuses rather than waits
    - raise StackBusy, and a developer's running `uqs start` could do the same.
    The lock itself is unchanged: only where a test's lock lives moves, so a
    test of the lock (test_generated_files.py) still contends within itself.
    """
    from uqs.stack import generated

    lock = tmp_path / generated.LOCK_NAME
    monkeypatch.setattr(generated, "lock_path", lambda paths: lock)


@pytest.fixture(autouse=True)
def no_live_port_or_start_checks(monkeypatch):
    """A start's checks that read the machine itself (#2180) - who listens on a
    port (alive.listeners), and whether what a start launched stayed up
    (start_check) - see nothing here, so a test that fakes the launcher is not
    failed by a developer's own stack, or by the processes it never really
    started. test_start_check.py puts the real ones back.
    """
    from uqs.stack import alive, start_check

    monkeypatch.setattr(alive, "listeners", lambda ports, timeout=None: {})
    monkeypatch.setattr(start_check, "snapshot", lambda paths, base_port: {})
    monkeypatch.setattr(start_check, "confirm", lambda *_a, **_k: [])


@pytest.fixture(scope="session", autouse=True)
def no_receiver_outlives_the_run(tmp_path_factory):
    yield
    root = tmp_path_factory.getbasetemp()
    left = receivers_under(root)
    for pid in left:
        stop_process(pid)
    if left:
        pytest.fail(
            f"attached_receiver.q process(es) {left} started under {root} were still running "
            "at the end of the run (stopped now): a test started a receiver it did not stop"
        )


@pytest.fixture
def log_text():
    """What the command logged, as text: a loguru sink of its own. The console
    sink is stderr (#1881), bound when logging was configured - not the stream
    an in-process CliRunner swaps in - so a result's output no longer holds it."""
    from loguru import logger

    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(str(m)), level="TRACE", format="{message}")
    yield lambda: "".join(lines)
    logger.remove(sink)


@pytest.fixture
def terminal(monkeypatch):
    """A terminal to ask on: a command's question reads the runner's input.
    Without one, `safety.ask` refuses, naming --yes (#1885)."""
    from uqs.cli import safety

    monkeypatch.setattr(safety, "interactive", lambda: True)
