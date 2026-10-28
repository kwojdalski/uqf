"""One fake for `shutil.which`, with `which`'s real signature (#1759).

`shutil` is one module, so patching `x.shutil.which` replaces `which` for
every caller in the process, not just module `x`. A fake that takes one
argument then raises TypeError inside any code that calls
`which(cmd, path=...)` - and a broad `except` above it can turn that into a
wrong answer rather than a failure. Every test that fakes `which` uses this,
and test_which_fakes.py refuses a hand-written one-argument fake.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Mapping

import pytest


def fake_which(
    monkeypatch: pytest.MonkeyPatch,
    found: Mapping[str, str | None] | None = None,
    *,
    other: str | None = None,
) -> None:
    """Make `shutil.which(cmd)` answer `found[cmd]` for a listed name, and
    `other` (None, "not installed", by default) for every other one.

    `fake_which(mp, {"qcon": "/x/qcon"})` finds qcon and nothing else;
    `fake_which(mp, other="/usr/bin/true")` finds everything;
    `fake_which(mp, {"q": None}, other="/usr/bin/true")` everything but q.
    """
    table = dict(found or {})

    def which(cmd, mode=os.F_OK | os.X_OK, path=None):  # noqa: ARG001 - which()'s own signature
        return table.get(os.fspath(cmd), other)

    monkeypatch.setattr(shutil, "which", which)
