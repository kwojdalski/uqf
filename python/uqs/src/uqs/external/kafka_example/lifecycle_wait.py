"""Waiting for a process this process started to be gone.

A child that has exited is a zombie until its parent reaps it, and a zombie
still answers `kill(pid, 0)`: a wait that only signals sees the process as
alive until its deadline. When the verifier started the process itself (one
`verify all` run does) it must `waitpid` too; when another process did, there is
nothing to reap and `waitpid` says so.
"""

from __future__ import annotations

import os
import time


def gone(pid: int) -> bool:
    """Whether `pid` no longer exists, reaping it first if it is this process's child."""
    try:
        done, _ = os.waitpid(pid, os.WNOHANG)
        if done:
            return True
    except ChildProcessError:
        pass  # not ours to reap
    try:
        os.kill(pid, 0)
    except OSError:
        return True
    return False


def wait_gone(pid: int, timeout: float, interval: float = 0.2) -> bool:
    """True once `pid` is gone, False if it is still there after `timeout` seconds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if gone(pid):
            return True
        time.sleep(interval)
    return gone(pid)
