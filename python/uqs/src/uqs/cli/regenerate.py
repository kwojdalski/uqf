"""After a scaffold writes: regenerate what it made stale.

Shared by every command that edits the tree - `uqs job new`, `uqs job remove`
and `uqs job install` - so it lives beside them rather than inside one, which
is where it was until `job new` grew past this package's module budget.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from uqs.cli.safety import dry_run_note
from uqs.cli.shared import _die, console
from uqs.interpreter import q_interpreter
from uqs.paths import DERIVE_SCRIPT, UqsError
from uqs.scaffold import write
from uqs.scaffold.plan import ScaffoldPlan

#: What a scaffold makes stale, checked in CI with --check: the plant schema
#: metadata a new or removed table changes (needs q, #1781) - FIRST, since the
#: next one reads it - and the registry's derived files (processes.md,
#: src/etl/generated/pipeline_dag.q). docs/man.q is not here: it reads the
#: qDoc blocks when it loads, so a scaffold's new comments are in it with
#: nothing to regenerate. Each is the arguments after `python`, the script's
#: path relative to the tree's root.
#: The contract surface, which a scaffold makes stale too but which needs q.
_CONTRACT_SURFACE = (str(DERIVE_SCRIPT), "contract-surface")
#: What else needs q, in order (#2461): q_facts.py, which records a new
#: source's transport and a worker's dataset - `uqs config sources stub` reads
#: it - then tableproperties.csv, which reads q_facts.py for getdata's table
#: list, then the contract surface.
_WITH_Q: tuple[tuple[str, ...], ...] = (
    (str(DERIVE_SCRIPT), "q-facts"),
    (str(DERIVE_SCRIPT), "table-properties"),
    _CONTRACT_SURFACE,
)
_DERIVED: tuple[tuple[str, ...], ...] = (
    (str(DERIVE_SCRIPT), "plant-schema"),
    (str(DERIVE_SCRIPT), "operational-docs"),
)


def derived_command(derived: tuple[str, ...]) -> str:
    """One of _DERIVED as a contributor types it."""
    return " ".join(("uv run python", *derived))


def _regenerate_derived(repo_root: Path) -> list[subprocess.CompletedProcess[str]]:
    """Rewrite every derived file a scaffold makes stale, and return each run.

    A scaffold that wrote its files and stopped there left the build red on
    files nobody is meant to edit. SUBPROCESSES rather than calls: this process
    imported the registry before the scaffold appended to it, so an in-process
    call would regenerate from the old one.
    """
    return [
        subprocess.run(
            [sys.executable, str(repo_root / script), *args],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        for script, *args in _DERIVED
    ]


def _regenerate_with_q(repo_root: Path) -> list[subprocess.CompletedProcess[str]] | None:
    """Run each of _WITH_Q in order, or None when no q is installed.

    Separate from `_regenerate_derived` because these need q: they load the
    tree to read what is defined. Every scaffold adds `.qpipe.job.<name>`
    names, and a new table or source changes q_facts.py and getdata's table
    list, so without this their hooks and test_catalog.q fail on the next
    commit, for a change nobody made by hand.
    """
    if q_interpreter() is None:
        return None
    return [
        subprocess.run(
            [sys.executable, str(repo_root / script), *args],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        for script, *args in _WITH_Q
    ]


def _report(derived: tuple[str, ...], regen: subprocess.CompletedProcess[str]) -> None:
    if regen.returncode == 0:
        console.print(f"[green]regenerated[/] via {' '.join(derived)}")
    else:
        console.print(
            f"[red]could not regenerate[/] - run `{derived_command(derived)}` "
            f"and fix what it reports:\n{regen.stdout}{regen.stderr}"
        )


def report_regeneration(repo_root: Path) -> None:
    """Regenerate everything a tree edit made stale, saying what each run did.

    Not fatal: the edit is already written, and a refusal here (the generator
    verifies every declared edge first) is information about the tree to act
    on, not a reason to pretend the edit did not happen."""
    for derived, regen in zip(_DERIVED, _regenerate_derived(repo_root), strict=True):
        _report(derived, regen)
    with_q = _regenerate_with_q(repo_root)
    if with_q is None:
        steps = ", ".join(f"`{derived_command(d)}`" for d in _WITH_Q)
        console.print(
            f"[yellow]no q on PATH[/] - run {steps} where q is installed, or their "
            "--check hooks fail on the next commit"
        )
        return
    for derived, regen in zip(_WITH_Q, with_q, strict=True):
        _report(derived, regen)


def write_plan(plan: ScaffoldPlan, repo_root: Path, *, dry_run: bool) -> None:
    """Print a plan and, unless --dry-run, write it and regenerate what it
    makes stale. The same for every shape."""
    console.print(plan.render())
    console.print("  then regenerate: " + ", ".join(" ".join(d) for d in (*_DERIVED, *_WITH_Q)))
    if dry_run:
        console.print(dry_run_note())
        return
    try:
        written = write.apply_plan(plan, repo_root)
    except UqsError as exc:
        _die(exc)
        return
    console.print(f"\n[green]scaffolded {len(written)} file(s)[/]")
    report_regeneration(repo_root)
    for note in plan.notes:
        console.print(f"  [yellow]next[/] {note}")
