"""After a scaffold writes: regenerate what it made stale.

Shared by every command that edits the tree - `uqs job new`, `uqs job remove`
and `uqs job install` - so it lives beside them rather than inside one, which
is where it was until `job new` grew past this package's module budget.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from uqs.cli.shared import _die, console
from uqs.interpreter import q_interpreter
from uqs.paths import (
    CONTRACT_SURFACE_SCRIPT,
    OPERATIONAL_DOCS_SCRIPT,
    UqsError,
)
from uqs.scaffold import write
from uqs.scaffold.plan import ScaffoldPlan

#: What a scaffold makes stale, checked in CI with --check: the registry's
#: derived files (processes.md, src/etl/generated/pipeline_dag.q). docs/man.q
#: is not here: it reads the qDoc blocks when it loads, so a scaffold's new
#: comments are in it with nothing to regenerate.
_DERIVED = (OPERATIONAL_DOCS_SCRIPT,)


def _regenerate_derived(repo_root: Path) -> list[subprocess.CompletedProcess[str]]:
    """Rewrite every derived file a scaffold makes stale, and return each run.

    A scaffold that wrote its files and stopped there left the build red on
    files nobody is meant to edit. SUBPROCESSES rather than calls: this process
    imported the registry before the scaffold appended to it, so an in-process
    call would regenerate from the old one.
    """
    return [
        subprocess.run(
            [sys.executable, str(repo_root / script)],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        for script in _DERIVED
    ]


def _export_contract_surface(repo_root: Path) -> subprocess.CompletedProcess[str] | None:
    """Re-export the contract surface, or None when no q is installed.

    Separate from `_regenerate_derived` because it needs q: it loads the tree
    to read what is defined. Every scaffold adds `.qpipe.job.<name>` names, so
    without this the contract-surface hook fails on the next commit, for a
    change nobody made by hand.
    """
    if q_interpreter() is None:
        return None
    return subprocess.run(
        [sys.executable, str(repo_root / CONTRACT_SURFACE_SCRIPT), "export"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )


def write_plan(plan: ScaffoldPlan, repo_root: Path, *, dry_run: bool) -> None:
    """Print a plan and, unless --dry-run, write it and regenerate what it
    makes stale. The same for every shape."""
    console.print(plan.render())
    console.print(
        f"  then regenerate: {', '.join(str(p) for p in (*_DERIVED, CONTRACT_SURFACE_SCRIPT))}"
    )
    if dry_run:
        console.print("[dim]--dry-run: nothing written[/]")
        return
    try:
        written = write.apply_plan(plan, repo_root)
    except UqsError as exc:
        _die(exc)
        return
    console.print(f"\n[green]scaffolded {len(written)} file(s)[/]")
    # Not fatal: the job's files are already written, and a refusal here (the
    # generator verifies every declared edge first) is information about the
    # tree to act on, not a reason to pretend the scaffold did not happen.
    for script, regen in zip(_DERIVED, _regenerate_derived(repo_root), strict=True):
        if regen.returncode == 0:
            console.print(f"[green]regenerated[/] via {script}")
        else:
            console.print(
                f"[red]could not regenerate[/] - run `python3 {script}` "
                f"and fix what it reports:\n{regen.stdout}{regen.stderr}"
            )
    surface = _export_contract_surface(repo_root)
    if surface is None:
        console.print(
            f"[yellow]no q on PATH[/] - run `uv run python {CONTRACT_SURFACE_SCRIPT} export` where "
            "q is installed, or the contract-surface hook fails on the next commit"
        )
    elif surface.returncode == 0:
        console.print(f"[green]regenerated[/] via {CONTRACT_SURFACE_SCRIPT}")
    else:
        console.print(
            f"[red]could not export the contract surface[/] - the new files may not load in q:\n"
            f"{surface.stdout}{surface.stderr}"
        )
    for note in plan.notes:
        console.print(f"  [yellow]next[/] {note}")
