#!/usr/bin/env python3
"""Generated files merge themselves: a git merge driver, and the regeneration after it (#1965).

The files scripts/generate/derive.py writes are never resolved by hand - the
right answer to a conflict in one is always "regenerate". Yet they stopped
merges again and again: on 2026-11-03 alone, about six times, in
src/etl/generated/load_plan.q, docs/reference/surfaces/current/*.csv,
src/etl/generated/plant_schema.json and docs/diagrams/*.svg.

.gitattributes gives those paths `merge=uqf-generated`. Three parts:

  driver      git calls it for a generated file both sides changed. It cannot
              regenerate there - the tree is half merged, and the generators
              read the whole tree - so it keeps this side's file (%A, already
              in place), records the path, and exits 0: the merge does not
              stop.
  regenerate  the post-merge and post-rewrite hook (.pre-commit-config.yaml,
              `regenerate-merged`): after the merge or rebase completes, it runs
              derive.py for each target a recorded path belongs to and leaves
              the result in the working tree, to commit. A target that cannot
              run here (contract-surface and q-facts need KDB-X, diagrams the
              pinned d2) is named with the command to run.
  install     registers the driver in this clone's git config. git reads merge
              drivers only from config, never from the tree, so each clone
              needs it once; install.sh --dev runs it. A clone without it merges
              these files as text, as before - CI and Kodiak never merge
              locally, so they need nothing.

Nothing stale reaches master unnoticed: the file the driver kept is checked
by the gates that always checked it. operational-docs and rendered-diagrams
run in CI on every file; contract-surface, q-facts and plant-schema are
KDB-X gates whose scopes take the generated file, so a branch that changed one
needs them in its attestation (scripts/dev/kdbx_attestation.py).

    python3 scripts/dev/merge_generated.py install
    python3 scripts/dev/merge_generated.py regenerate [--dry-run]
    python3 scripts/dev/merge_generated.py driver %O %A %B %P     # git's call

Standard library only: git's merge driver runs it with the system python3,
so it must stay parseable by an older one; the regenerate-merged hook runs it
as `uv run python` (#2449).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
NAME = "uqf-generated"
DRIVER = "python3 scripts/dev/merge_generated.py driver %O %A %B %P"
#: Where the driver records the paths it kept, under this worktree's git dir.
RECORD = "uqf-generated-merged"

#: derive.py's target for each generated path, in the order they are rerun:
#: q-facts first, since table-properties and the docs read the facts. Every
#: path .gitattributes names must match one (test_merge_generated.py holds them
#: equal).
TARGETS: tuple[tuple[str, str], ...] = (
    ("q-facts", r"^python/(uqs/src/uqs/generated|uqf_airflow_provider/src/[^/]+)/q_facts\.py$"),
    ("plant-schema", r"^src/etl/generated/plant_schema\.json$"),
    ("operational-docs", r"^src/etl/generated/(load_plan|pipeline_dag)\.q$"),
    ("operational-docs", r"^docs/reference/processes\.md$"),
    ("contract-surface", r"^docs/reference/surfaces/current/[^/]+\.csv$"),
    ("diagrams", r"^docs/diagrams/[^/]+\.svg$"),
)


def git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def record_file(cwd: Path | None = None) -> Path:
    """This worktree's record of the paths the driver kept."""
    path = Path(git("rev-parse", "--git-path", RECORD, cwd=cwd))
    return path if path.is_absolute() else (cwd or Path.cwd()) / path


def target_of(path: str) -> str | None:
    """derive.py's target that writes `path`, or None."""
    return next((t for t, rx in TARGETS if re.match(rx, path)), None)


def driver(ancestor: str, ours: str, theirs: str, path: str) -> int:
    """Keep ours (git left it at %A), record `path`, never stop the merge."""
    del ancestor, ours, theirs  # %A already holds the side kept
    with record_file().open("a") as out:
        out.write(path + "\n")
    print(
        f"{NAME}: kept this side of {path}; it is regenerated after the merge "
        f"(derive.py {target_of(path) or '?'})",
        file=sys.stderr,
    )
    return 0


def regenerate(dry_run: bool = False, cwd: Path | None = None) -> int:
    """Rerun derive.py for each target the recorded paths belong to; always 0 -
    a post-merge hook cannot undo the merge, and the gates still check."""
    record = record_file(cwd)
    if not record.is_file():
        return 0
    paths = sorted({p for p in record.read_text().splitlines() if p})
    wanted = {target_of(p) for p in paths} - {None}
    targets = list(dict.fromkeys(t for t, _ in TARGETS if t in wanted))
    print(
        f"{NAME}: {len(paths)} generated file(s) were merged by keeping one side: "
        f"{', '.join(paths)}"
    )
    failed = []
    for target in targets:
        cmd = ["uv", "run", "python", "scripts/generate/derive.py", target]
        print(f"{NAME}: {' '.join(cmd)}", flush=True)
        if dry_run:
            continue
        r = subprocess.run(cmd, cwd=cwd or REPO, capture_output=True, text=True, check=False)
        if r.returncode:
            failed.append(target)
            tail = "\n".join((r.stdout + r.stderr).strip().splitlines()[-5:])
            print(f"{NAME}: {target} did not regenerate here:\n{tail}", file=sys.stderr)
    if not dry_run:
        record.unlink()
    changed = git("status", "--porcelain", "--", *paths, cwd=cwd or REPO) if paths else ""
    if changed:
        print(f"{NAME}: regenerated - commit these:\n{changed}")
    for target in failed:
        print(
            f"{NAME}: still to do - uv run python scripts/generate/derive.py {target}; "
            "its --check gate fails until it is current",
            file=sys.stderr,
        )
    return 0


def install(cwd: Path | None = None) -> int:
    """Register the driver in this clone's config (shared by its worktrees)."""
    git(
        "config",
        f"merge.{NAME}.name",
        "uqf generated file: keep one side, regenerate after",
        cwd=cwd or REPO,
    )
    git("config", f"merge.{NAME}.driver", DRIVER, cwd=cwd or REPO)
    print(f"{NAME}: merge driver registered (git config merge.{NAME}.driver)")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) == 5 and args[0] == "driver":
        return driver(*args[1:])
    if args in (["regenerate"], ["regenerate", "--dry-run"]):
        return regenerate(dry_run=len(args) == 2)
    if args == ["install"]:
        return install()
    print(__doc__.split("\n\n")[-2], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
