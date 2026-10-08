#!/usr/bin/env python3
"""Refuse a commit that would make an operator's bundle part of the tree (#881).

`uqs runtime prepare` installs a bundle by writing a `/ BEGIN bundle <name>`
block into tracked q files (src/etl/plant_tables.q,
scripts/processes/uqs_catalog.q). The bundle's declaration,
runtime_bundles.json, is gitignored as the operator's and never this tree's,
but nothing stopped its effects being committed: after a commit they are
tree content for everyone, in every runtime's schema.

This reads what would be committed - the index, through `git grep --cached` -
so a block present only in the working tree, where prepare is allowed to
put it, does not fail a commit of other files.

Run directly, or via the pre-commit hook. Exits 1 on any block.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def staged_blocks() -> list[str]:
    """`path:line: text` for each bundle marker in the index's q files."""
    r = subprocess.run(
        ["git", "grep", "--cached", "-n", "-E", r"^/ (BEGIN|END) bundle ", "--", "*.q"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    # 0 = found, 1 = none; anything else is git failing, never a clean pass
    if r.returncode not in (0, 1):
        raise SystemExit(f"check_bundle_blocks: git grep failed: {r.stderr.strip()}")
    return [line for line in r.stdout.splitlines() if line.strip()]


def main() -> int:
    found = staged_blocks()
    if not found:
        print("check_bundle_blocks: no bundle blocks in tracked q files")
        return 0
    print(
        "check_bundle_blocks: tracked q files carry bundle blocks - an operator's\n"
        "installed bundle, which `uqs runtime prepare` writes into the working tree\n"
        "and which must not become tree content:\n"
    )
    for line in found:
        print(f"  {line}")
    print(
        "\nUnstage those hunks (the blocks stay in your working tree), or uninstall\n"
        "the bundle before committing."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
