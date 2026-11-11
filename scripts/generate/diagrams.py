#!/usr/bin/env python3
"""Render `docs/diagrams/*.d2` to SVG: the `diagrams` target of derive.py.

    python3 scripts/generate/derive.py diagrams                    # render
    python3 scripts/generate/derive.py diagrams --check            # compare
    python3 scripts/generate/derive.py diagrams --check --require  # CI: never skip
    python3 scripts/generate/derive.py diagrams --print-version    # D2_VERSION

An `.svg` committed beside its `.d2` is a derived artefact, and this tree
already knows what happens to those without a gate: `docs/man.q` called
itself generated for months while covering 78 of 348 functions, because
nothing re-ran the generator. So this is the same shape as
the contract-surface check - edit the source, forget the render, and the
build says so.

WHY A BYTE COMPARISON IS SAFE HERE. d2 is deterministic for a given version:
rendering the same `.d2` twice produces identical bytes (verified before this
gate was written), so `--check` can compare the file rather than parse the
SVG. The target renders each source into a temporary directory and hands the
bytes to derive.py, which compares them or writes them.

WHY A VERSION MISMATCH SKIPS RATHER THAN FAILS. Different d2 releases lay out
and emit differently, so the same source renders to different - equally
correct - bytes. A check that failed on that would be reporting the
contributor's d2 version, not a stale diagram, and the fix it implied
(re-render and commit) would start a churn war between two developers on
different versions. So the gate runs only when the renderer matches
D2_VERSION below; otherwise it says which version it wanted and passes.
That policy is `verdict` below, and the target applies it before rendering.

CI is the exception to both skips: it installs the pinned d2 and passes
`--require`, under which an absent or different d2 FAILS. Before that CI never
installed d2, so the skip above was taken on every run and the check was green
over nothing (#1369). `--print-version` lets the workflow read D2_VERSION from
here instead of keeping a second copy of it.

Bumping d2: change D2_VERSION, re-render, and commit the SVG churn in its own
commit so a real diagram change is never buried in it.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# parents[2], not parent.parent: this file sits one level deeper since
# scripts/ was foldered (#241). Getting it wrong does not raise - it
# resolves to scripts/ and the checker reports over an empty tree.
REPO = Path(__file__).resolve().parents[2]
DIAGRAMS = REPO / "docs" / "diagrams"

#: The renderer this repository's committed SVGs were produced with.
D2_VERSION = "v0.9.0"


def installed_version() -> str | None:
    """The `d2` on PATH, or None when there is none."""
    if shutil.which("d2") is None:
        return None
    out = subprocess.run(["d2", "--version"], capture_output=True, text=True, check=False)
    return out.stdout.strip() or None


def sources() -> list[Path]:
    return sorted(DIAGRAMS.glob("*.d2"))


def render(source: Path, target: Path) -> None:
    subprocess.run(["d2", str(source), str(target)], check=True, capture_output=True)


def rendered(source: Path) -> bytes:
    """`source` rendered by d2, as bytes; nothing in the tree is written.

    Rendered under the committed SVG's own name, in a temporary directory,
    as the comparison always was.
    """
    with tempfile.TemporaryDirectory() as tmp:
        fresh = Path(tmp) / source.with_suffix(".svg").name
        render(source, fresh)
        return fresh.read_bytes()


def verdict(count: int, *, require: bool) -> int | None:
    """Whether this machine's d2 may render: None to go ahead, else the exit
    status to stop with - 0 to skip, saying why, or 1 under `require`.

    `count` is the number of sources, for the skip message.
    """
    version = installed_version()
    if require and version != D2_VERSION:
        print(
            f"diagrams: --require needs d2 {D2_VERSION} but found "
            f"{version or 'no d2 on PATH'}; refusing to skip the check",
            file=sys.stderr,
        )
        return 1
    if version is None:
        print(f"diagrams: no d2 on PATH; {count} diagram(s) left as committed")
        return 0
    if version != D2_VERSION:
        print(
            f"diagrams: d2 {version} is installed but the committed SVGs were "
            f"rendered with {D2_VERSION}; skipping rather than reporting a version "
            f"difference as a stale diagram"
        )
        return 0
    return None
