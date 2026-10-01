"""Tests for stack/follow.py - what each `uqs logs --multitail` pane runs.

Driven on a thread against real files and a real symlink, re-pointed the way
TorQ's createalias does it (`ln -sf`), because that re-pointing is the whole
reason this exists.
"""

from __future__ import annotations

import io
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path

from uqs.stack.follow import follow


def _repoint(link: Path, target: str) -> None:
    """What `ln -sf target link` does: the name now means a different file."""
    tmp = link.with_suffix(".tmp")
    tmp.symlink_to(target)
    os.replace(tmp, link)


def _until(cond: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


class _Pane:
    """follow() running on a thread, with what it has written so far."""

    def __init__(self, path: Path, lines: int) -> None:
        self.out = io.StringIO()
        self._done = threading.Event()
        self._thread = threading.Thread(
            target=follow,
            args=(path, lines, self.out),
            kwargs={"stop": self._done.is_set, "poll": 0.01},
            daemon=True,
        )
        self._thread.start()

    def lines(self) -> list[str]:
        return self.out.getvalue().splitlines()

    def wait_for(self, line: str) -> None:
        _until(lambda: line in self.lines())

    def stop(self) -> None:
        self._done.set()
        self._thread.join(timeout=5)


def _append(path: Path, text: str) -> None:
    with path.open("a") as f:
        f.write(text)


def test_it_opens_on_the_last_lines_then_follows_appends(tmp_path):
    log = tmp_path / "out_p.log"
    log.write_text("".join(f"old-{i}\n" for i in range(5)))
    pane = _Pane(log, lines=2)
    pane.wait_for("old-4")
    _append(log, "new-1\n")
    pane.wait_for("new-1")
    pane.stop()
    assert pane.lines() == ["old-3", "old-4", "new-1"]


def test_a_repointed_alias_is_followed_into_the_new_file(tmp_path):
    """The bug: a pane stayed on the file the alias used to name."""
    (tmp_path / "out_p_1.log").write_text("run1-a\n")
    alias = tmp_path / "out_p.log"
    alias.symlink_to("out_p_1.log")
    pane = _Pane(alias, lines=10)
    pane.wait_for("run1-a")
    (tmp_path / "out_p_2.log").write_text("run2-a\n")
    _repoint(alias, "out_p_2.log")
    pane.wait_for("run2-a")
    _append(tmp_path / "out_p_2.log", "run2-b\n")
    pane.wait_for("run2-b")
    pane.stop()
    assert pane.lines() == ["run1-a", "run2-a", "run2-b"], "the new run is read from its start"


def test_what_the_old_file_got_before_the_switch_is_not_lost(tmp_path):
    old = tmp_path / "out_p_1.log"
    old.write_text("")
    alias = tmp_path / "out_p.log"
    alias.symlink_to("out_p_1.log")
    pane = _Pane(alias, lines=10)
    time.sleep(0.05)
    # Written and re-pointed within one poll: the last lines of the old run
    # and the switch arrive together.
    _append(old, "run1-last\n")
    (tmp_path / "out_p_2.log").write_text("run2-a\n")
    _repoint(alias, "out_p_2.log")
    pane.wait_for("run2-a")
    pane.stop()
    assert pane.lines() == ["run1-last", "run2-a"]


def test_a_file_that_does_not_exist_yet_is_waited_for(tmp_path):
    log = tmp_path / "out_p.log"
    pane = _Pane(log, lines=10)
    time.sleep(0.05)
    log.write_text("first\n")
    pane.wait_for("first")
    pane.stop()


def test_a_half_written_line_arrives_whole(tmp_path):
    log = tmp_path / "out_p.log"
    log.write_text("")
    pane = _Pane(log, lines=0)
    time.sleep(0.05)
    _append(log, "hal")
    time.sleep(0.1)
    assert pane.lines() == [], "nothing until the line is finished"
    _append(log, "f\n")
    pane.wait_for("half")
    pane.stop()
    assert pane.lines() == ["half"]
