#!/usr/bin/env python3
"""Render `docs/diagrams/*.d2` to SVG, and check the committed SVGs are current.

An `.svg` committed beside its `.d2` is a derived artefact, and this tree
already knows what happens to those without a gate: `docs/man.q` called
itself generated for months while covering 78 of 348 functions, because
nothing re-ran the generator. So this is the same shape as
`generate_man_registry.py --check` and `contract_surface.py check` - edit the
source, forget the render, and the build says so.

WHY A BYTE COMPARISON IS SAFE HERE. d2 is deterministic for a given version:
rendering the same `.d2` twice produces identical bytes (verified before this
gate was written), so `--check` can compare the file rather than parse the
SVG.

WHY A VERSION MISMATCH SKIPS RATHER THAN FAILS. Different d2 releases lay out
and emit differently, so the same source renders to different - equally
correct - bytes. A check that failed on that would be reporting the
contributor's d2 version, not a stale diagram, and the fix it implied
(re-render and commit) would start a churn war between two developers on
different versions. So the gate runs only when the renderer matches
D2_VERSION below; otherwise it says which version it wanted and passes.

Bumping d2: change D2_VERSION, re-render, and commit the SVG churn in its own
commit so a real diagram change is never buried in it.
"""

from __future__ import annotations

import argparse
import filecmp
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if a committed .svg differs from a fresh render",
    )
    args = parser.parse_args()

    srcs = sources()
    if not srcs:
        # Refusing to report success over an empty set, the same way
        # check_etl_layering.py does: a moved directory must fail loudly
        # rather than quietly gate nothing.
        print(f"error: no .d2 sources under {DIAGRAMS.relative_to(REPO)}", file=sys.stderr)
        return 1

    version = installed_version()
    if version is None:
        print(f"render_diagrams: no d2 on PATH; {len(srcs)} diagram(s) left as committed")
        return 0
    if version != D2_VERSION:
        print(
            f"render_diagrams: d2 {version} is installed but the committed SVGs were "
            f"rendered with {D2_VERSION}; skipping rather than reporting a version "
            f"difference as a stale diagram"
        )
        return 0

    if not args.check:
        for source in srcs:
            render(source, source.with_suffix(".svg"))
            print(f"render_diagrams: wrote {source.with_suffix('.svg').relative_to(REPO)}")
        return 0

    stale: list[str] = []
    for source in srcs:
        committed = source.with_suffix(".svg")
        if not committed.is_file():
            stale.append(f"{committed.relative_to(REPO)} is missing")
            continue
        with tempfile.TemporaryDirectory() as tmp:
            fresh = Path(tmp) / committed.name
            render(source, fresh)
            # shallow=False: the sizes can match while the content differs,
            # and a same-size edit is exactly the case a diagram gate is for
            # (a renamed box, a re-coloured edge).
            if not filecmp.cmp(fresh, committed, shallow=False):
                stale.append(
                    f"{committed.relative_to(REPO)} is stale - "
                    f"{source.relative_to(REPO)} has changed since it was rendered"
                )

    if stale:
        print("render_diagrams: committed SVGs do not match their sources:", file=sys.stderr)
        for line in stale:
            print(f"  {line}", file=sys.stderr)
        print("\nRun: python3 scripts/generate/render_diagrams.py", file=sys.stderr)
        return 1

    print(f"render_diagrams: {len(srcs)} diagram(s) match their sources (d2 {version})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
