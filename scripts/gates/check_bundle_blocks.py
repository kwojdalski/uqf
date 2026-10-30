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

The blocks are one of the three things an install writes (#926). It also
copies the bundle's job files under src/etl/, and its Python connectors under
src/etl/connectors/<bundle>/ (#1776), and records both in the ledger,
src/etl/installed_bundles.json. Neither carries a marker, so the ledger in the
working tree is what says which files are a bundle's: a commit staging the
ledger, or any file it lists, is refused too. The process overrides file is
host state and may also hold bundle rows; staging any of it is refused.

Run directly, or via the pre-commit hook. Exits 1 on any block or file.
"""

from __future__ import annotations

import json
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


#: Where an install records what it put in the tree (uqs.paths.BUNDLE_LEDGER).
LEDGER = "src/etl/installed_bundles.json"
OVERRIDES = "python/uqs/process_overrides.csv"
BUNDLE_OVERRIDES = "python/uqs/bundle_overrides.csv"


def staged_bundle_files() -> list[str]:
    """Staged bundle files, ledger or host overrides: additions or changes."""
    r = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode:
        raise SystemExit(f"check_bundle_blocks: git diff failed: {r.stderr.strip()}")
    staged = set(r.stdout.split())
    found = [f"{OVERRIDES} (host and bundle overrides)"] if OVERRIDES in staged else []
    if BUNDLE_OVERRIDES in staged:
        found.append(f"{BUNDLE_OVERRIDES} (installed bundle overrides)")
    ledger_path = REPO / LEDGER
    if not ledger_path.is_file():
        if LEDGER in staged:
            found.append(f"{LEDGER} (the bundle ledger)")
        return found
    try:
        ledger = json.loads(ledger_path.read_text())
    except ValueError:
        ledger = {}
    # A bundle's job files, and its connectors' copies (#1776), are both the
    # bundle's: neither may become repository content.
    owners = {
        f: name
        for name, entry in ledger.items()
        for f in [*entry.get("files", {}), *entry.get("connectors", [])]
    }
    found += [f"{f} (bundle {owners[f]})" for f in sorted(staged) if f in owners]
    if LEDGER in staged:
        found.append(f"{LEDGER} (the bundle ledger)")
    return found


def main() -> int:
    found = staged_blocks()
    files = staged_bundle_files()
    if not found and not files:
        print("check_bundle_blocks: no bundle blocks or bundle files staged")
        return 0
    if files:
        print(
            "check_bundle_blocks: staged files contain host or installed bundle state "
            "that must not become repository content:\n"
        )
        for line in files:
            print(f"  {line}")
        print(
            "\nUnstage them (git restore --staged <path>), or remove the bundle first "
            "(uqs job remove).\n"
        )
    if not found:
        return 1
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
