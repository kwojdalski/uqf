"""`uqs job new ... --bundle DIR`: the CLI half of scaffolding into a bundle.

Apart from cli/create.py, which is at the module budget. The planning half -
what the planners see and where the plan lands - is scaffold/bundle.py.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from uqs.cli.shared import _die, console
from uqs.paths import UqsError
from uqs.scaffold import bundle as bundle_mod
from uqs.scaffold import write
from uqs.scaffold.plan import ScaffoldPlan


@contextmanager
def planning_root(repo_root: Path, bundle: Path | None) -> Iterator[Path]:
    """The root a planner reads: the tree itself, or for --bundle a scratch
    copy of it with the bundle installed (scaffold/bundle.py's tree_with)."""
    if bundle is None:
        yield repo_root
        return
    bundle_mod.check_folder(bundle)
    with bundle_mod.tree_with(bundle, repo_root) as root:
        yield root


def refuse_tree_only(options: dict[str, bool]) -> None:
    """Options that write into the tree's own registrations mean nothing for a
    bundle job, so they are refused rather than dropped."""
    for option in (o for o, used in options.items() if used):
        raise UqsError(
            f"{option} does not apply with --bundle: a bundle's processes join a stack "
            "through runtime_bundles.json and `uqs deploy push --jobs`, never a profile"
        )


def write_bundle_plan(plan: ScaffoldPlan, bundle: Path, *, dry_run: bool) -> None:
    """Print `plan` redirected into `bundle` and, unless --dry-run, write it.

    Nothing is regenerated: the tree's derived files describe the tree's
    jobs, and a bundle job is not one until it is installed.
    """
    plan = bundle_mod.into_bundle(plan, bundle)
    console.print(plan.render())
    if dry_run:
        console.print("[dim]--dry-run: nothing written[/]")
        return
    try:
        written = write.apply_plan(plan, Path.cwd())
    except UqsError as exc:
        _die(exc)
        return
    console.print(f"\n[green]scaffolded {len(written)} file(s) into {bundle}[/]")
    for note in plan.notes:
        console.print(f"  [yellow]next[/] {note}")
