"""`uqs job new ... --bundle DIR`: a scaffold written into a sidecar bundle.

A bundle (stack/bundles.py) is a folder of job files plus the tables.q and
catalog.q additions they need. Its jobs have the same shape as the tree's, so
they are scaffolded by the same planners - this module only changes WHERE the
plan lands, and WHAT the planners see.

WHAT THEY SEE. A planner reads the tree to refuse what would not load: a table
defined twice, a reaction on a dataset no worker fills, a subscription to a
table the plant does not carry. A bundle job is checked against the tree AND
its own bundle, so `tree_with` builds a scratch copy of what the planners read
and installs the bundle into it with the real installer. A second job in a
bundle then sees the first, exactly as the tree will once both are installed.

WHERE IT LANDS. `into_bundle` keeps the actions a bundle can carry - job
files, their tests, the table and the catalog entry - and drops the ones that
register a job with the TREE: run_tests.q's nsList, test_stack_tables.q, the
process docs, profiles, the example script. Those describe the tree's own
jobs, and installing a bundle never writes them. The bundle's tests run
through `scripts/test.py bundles`, which installs each bundle into a
throwaway copy of the tree.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from uqs.paths import (
    CATALOG_FILE,
    ETL_DIR,
    REACTION_DIR,
    SOURCE_DIR,
    STREAM_DIR,
    TABLES_FILE,
    TEST_DIR,
    WORKER_DIR,
    UqsError,
)
from uqs.scaffold.plan import FileAction, ScaffoldPlan, WriteMode
from uqs.stack import bundles

#: Tree folders whose new files are a bundle's job files.
_JOB_DIRS = (SOURCE_DIR, WORKER_DIR, STREAM_DIR, REACTION_DIR)
#: The version a bundle's manifest starts at.
FIRST_VERSION = "0.1.0"


def _job_files(folder: Path) -> list[Path]:
    """The bundle's .q files that are jobs: not its additions, not its tests."""
    return [
        p
        for p in folder.rglob("*.q")
        if p.name not in bundles.ADDITIONS and not p.name.startswith("test_")
    ]


@contextmanager
def tree_with(bundle_dir: Path, repo_root: Path) -> Iterator[Path]:
    """A scratch root holding what the planners read from `repo_root`, with
    the bundle at `bundle_dir` installed into it. Removed on exit.

    A bundle with no job files yet - a first scaffold - is not installed:
    there is nothing for a planner to see, and the installer refuses a bundle
    that declares no job.
    """
    with tempfile.TemporaryDirectory(prefix="uqs-bundle-view-") as tmp:
        root = Path(tmp)
        shutil.copytree(repo_root / ETL_DIR, root / ETL_DIR)
        (root / CATALOG_FILE).parent.mkdir(parents=True)
        shutil.copy2(repo_root / CATALOG_FILE, root / CATALOG_FILE)
        overrides = repo_root / bundles.OVERRIDES_FILE
        (root / bundles.OVERRIDES_FILE).parent.mkdir(parents=True)
        if overrides.is_file():
            shutil.copy2(overrides, root / bundles.OVERRIDES_FILE)
        if bundles.is_bundle(bundle_dir) and _job_files(bundle_dir):
            try:
                bundle = bundles.read_bundle(bundle_dir)
                bundles.install(bundles.plan(bundle, root), root)
            except bundles.BundleError as exc:
                raise UqsError(f"{bundle_dir} does not install into this tree: {exc}") from None
        yield root


def check_folder(bundle_dir: Path) -> None:
    """Refuse a folder that cannot become a bundle, before planning anything."""
    if bundles.is_bundle(bundle_dir):
        bundles.read_bundle(bundle_dir)  # a bad manifest is refused here
        return
    if bundle_dir.exists() and not bundle_dir.is_dir():
        raise UqsError(f"--bundle {bundle_dir} is a file, not a folder")
    if not bundles.NAME.fullmatch(bundle_dir.name):
        raise UqsError(
            f"--bundle {bundle_dir}: a new bundle is named after its folder, and "
            f"{bundle_dir.name!r} is not lower_snake_case"
        )


def into_bundle(plan: ScaffoldPlan, bundle_dir: Path) -> ScaffoldPlan:
    """`plan`, written into the bundle at `bundle_dir` instead of the tree.

    Paths in the result are `bundle_dir`'s, as given: apply it against the
    directory that path is relative to.
    """
    actions: list[FileAction] = []
    if not bundles.is_bundle(bundle_dir):
        manifest = {"name": bundle_dir.resolve().name, "version": FIRST_VERSION}
        actions.append(FileAction(bundle_dir / bundles.MANIFEST, json.dumps(manifest) + "\n"))
    dropped: list[Path] = []
    for action in plan.actions:
        target = _destination(action, bundle_dir)
        if target is None:
            dropped.append(action.path)
            continue
        if action.mode is WriteMode.APPEND and not target.is_file():
            # The bundle's first table or catalog entry makes the file.
            actions.append(FileAction(target, action.body.lstrip("\n")))
        else:
            actions.append(FileAction(target, action.body, action.mode))
    renamed = {str(TABLES_FILE): str(bundle_dir / bundles.TABLES),
               str(CATALOG_FILE): str(bundle_dir / bundles.CATALOG)}  # fmt: skip
    notes = []
    for note in plan.notes:
        if any(str(p) in note for p in dropped):
            continue
        if ".qcat.hidden" in note:
            # The installer refuses a bundle table its catalog.q leaves out.
            note = note.split(" - or,")[0] + " - a bundle describes every table it defines"
        for old, new in renamed.items():
            note = note.replace(old, new)
        notes.append(note)
    if dropped:
        notes.append(
            "not written - they register a job with the tree, which installing a bundle "
            f"never does: {', '.join(sorted({str(p) for p in dropped}))}"
        )
    notes.append(
        f"see what installing it would do: uqs job install {bundle_dir} --dry-run; "
        "its tests run with: python3 scripts/test.py bundles"
    )
    return ScaffoldPlan(plan.name, actions, notes)


def _destination(action: FileAction, bundle_dir: Path) -> Path | None:
    """Where in the bundle `action` goes, or None when a bundle carries no such thing."""
    path = action.path
    if action.mode is WriteMode.CREATE and (path.parent in _JOB_DIRS or path.parent == TEST_DIR):
        return bundle_dir / path.name
    if path == TABLES_FILE:
        return bundle_dir / bundles.TABLES
    if path == CATALOG_FILE:
        return bundle_dir / bundles.CATALOG
    return None
