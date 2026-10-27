#!/usr/bin/env python3
"""Run `qlinter analyze graph` and read its JSON (#1690).

q-lint's file dependency graph - which q file reads a global another defines,
or loads it by a literal path - read without running anything. Two callers:
scripts/gates/check_module_deps.py (the library's module edges) and
scripts/dev/test_impact.py (which q tests a change reaches).

WHICH BINARY. `$UQF_QLINTER` if set, else `qlinter` on PATH. Not `$QLINTER`:
that one is the q-traps hook's, which refuses any release but the one pinned
in .qlinter-version, while the graph needs the release that added it - until
the pin moves past it, the two can be different binaries.

NO SILENT FALL-BACK. A binary without the subcommand raises GraphUnavailable,
naming the binary and the q-lint it needs. 0.14.14 reads `analyze` as a path
to lint and fails on it, so asking for `analyze graph --help` tells the two
apart without guessing from a version string (master still calls itself
0.14.14 until it is released).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

#: Where the graph's binary is named, ahead of PATH.
ENV = "UQF_QLINTER"

#: What a binary has to be to draw the graph - said in every refusal.
NEEDED = (
    "q-lint with `qlinter analyze graph` (kwojdalski/q-lint#115 and #116, merged after "
    "the 0.14.14 release): install the commit .qlinter-graph-rev pins - the one CI's "
    "KDB-X attestation check selects q tests with - out of the way of the pinned "
    "qlinter the q-traps hook needs, `cargo install --git "
    "https://github.com/kwojdalski/q-lint --rev $(cat .qlinter-graph-rev) --locked "
    f"--root ~/.cache/uqf/qlint-graph`, and point ${ENV} at its bin/qlinter; or "
    "install the first release after 0.14.14 once .qlinter-version moves to it"
)


class GraphUnavailable(RuntimeError):
    """The qlinter found cannot draw the dependency graph."""


def binary() -> str:
    """The qlinter to ask: `$UQF_QLINTER`, else the one on PATH."""
    explicit = os.environ.get(ENV)
    if explicit:
        if not Path(explicit).is_file():
            raise GraphUnavailable(
                f"${ENV} names {explicit}, which is not a file. Needed: {NEEDED}"
            )
        return explicit
    found = shutil.which("qlinter")
    if not found:
        raise GraphUnavailable(f"no qlinter: ${ENV} is unset and none is on PATH. Needed: {NEEDED}")
    return found


def available(qlinter: str | None = None) -> bool:
    """Whether `qlinter` (default: binary()) has the `analyze graph` subcommand."""
    try:
        qlinter = qlinter or binary()
    except GraphUnavailable:
        return False
    try:
        result = subprocess.run(
            [qlinter, "analyze", "graph", "--help"], capture_output=True, text=True, timeout=60
        )
    except OSError:
        return False
    return result.returncode == 0 and "--direction" in result.stdout


def require(qlinter: str | None = None) -> str:
    """The binary, or GraphUnavailable saying which q-lint is needed."""
    qlinter = qlinter or binary()
    if not available(qlinter):
        raise GraphUnavailable(f"{qlinter} has no `qlinter analyze graph`. Needed: {NEEDED}")
    return qlinter


def graph(
    paths: list[str],
    *,
    cwd: Path,
    direction: str = "dependencies",
    detail: bool = False,
    symbols: bool = False,
    qlinter: str | None = None,
) -> dict:
    """`qlinter analyze graph` over `paths`, run in `cwd`, as parsed JSON.

    Paths in the result are relative to `cwd`. Raises GraphUnavailable when
    the binary cannot draw the graph, and RuntimeError when it fails on input.
    """
    qlinter = require(qlinter)
    cmd = [qlinter, "analyze", "graph", "--direction", direction]
    if detail:
        cmd.append("--detail")
    if symbols:
        cmd.append("--symbols")
    result = subprocess.run([*cmd, *paths], cwd=cwd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(
            f"qlinter analyze graph failed ({result.returncode}): {result.stderr.strip()}"
        )
    return json.loads(result.stdout)
