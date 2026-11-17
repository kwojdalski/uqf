"""Sidecar bundles: a versioned folder of jobs AND the tree additions they
need, installed as one unit (#800).

A plain sidecar (stack/install.py) carries job files only. A job that writes
a table the plant does not define, or that a desk should find in the catalog,
also needs lines in src/etl/plant_tables.q and scripts/processes/uqs_catalog.q
- and installing those by hand on every server is how a release and a
workstation drift apart. A bundle declares them beside its jobs:

    <bundle>/
      bundle.json              {"name": "piggybank", "version": "1.4.0"}
      *.q                      sources, workers, streaming jobs - placed by
                               what each declares, exactly as stack/install.py
      tables.q                 optional: `name:([]...)` plant tables, and
                               `nested[...]` / `sorting[...]` lines, one each
      catalog.q                optional: `.qcat.describe[`table]:"..."` entries,
                               and `.qcat.unbounded[`table]:"..."` reasons a
                               table of its own is browsable without a policy
      process_overrides.csv    optional: procname,field,value for the
                               bundle's OWN processes
      *_feed.py, *_streamer.py optional: its connectors, the publishers outside
                               the plant that feed its tables (stack/
                               bundle_connectors.py) - copied in with the q

INSTALLING IS IDEMPOTENT. The additions go into the tree files as one block
per bundle between `/ BEGIN bundle <name>` and `/ END bundle <name>`, and a
reinstall replaces that block rather than appending another. What a bundle
put in the tree is recorded in src/etl/installed_bundles.json (the ledger),
so an upgrade may replace and remove ITS OWN files and override rows - and
nothing else: a job file, table, catalog entry or override another bundle or
the tree already owns is a conflict, refused before anything is written.

COPY ONLY. A bundle is what a release carries to a server, and a symlink back
to a workstation checkout is exactly what does not travel.

What each addition file may hold is stack/bundle_additions.py's.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from uqs.model import q_support
from uqs.model.declarations import read_file
from uqs.model.pipeline import PipelineKind
from uqs.paths import BUNDLE_LEDGER, CATALOG_FILE, TABLES_FILE
from uqs.stack import bundle_additions, bundle_connectors, bundle_shadowing
from uqs.stack import install as stack_install
from uqs.stack import overrides as override_files
from uqs.stack.bundle_blocks import BundleError, with_block, without_block
from uqs.stack.install import Item, Kind, Mode, Status

MANIFEST = "bundle.json"
#: Where the tree records which bundle owns what, relative to its root.
LEDGER = BUNDLE_LEDGER
OVERRIDES_FILE = override_files.BUNDLE_FILE

#: What a bundle may be called: its manifest name, and a new bundle's folder.
NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}")
_MANIFEST_KEYS = frozenset({"name", "version", "description"})


@dataclass(frozen=True)
class Bundle:
    root: Path
    name: str
    version: str
    description: str = ""

    def addition(self, name: str) -> Path | None:
        path = self.root / name
        return path if path.is_file() else None


def is_bundle(folder: Path) -> bool:
    return (folder / MANIFEST).is_file()


def read_bundle(folder: Path) -> Bundle:
    """The bundle `folder` holds, its manifest checked; or why not."""
    path = folder / MANIFEST
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise BundleError(f"{path}: not a readable JSON manifest ({exc})") from None
    if not isinstance(data, dict):
        raise BundleError(f"{path}: must be a JSON object")
    if unknown := sorted(set(data) - _MANIFEST_KEYS):
        raise BundleError(f"{path}: unknown key(s) {', '.join(unknown)}")
    name, version = data.get("name"), data.get("version")
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise BundleError(f"{path}: name must be lower_snake_case, e.g. piggybank")
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        raise BundleError(f"{path}: version must be a string such as 1.4.0")
    return Bundle(folder.resolve(), name, version, str(data.get("description", "")))


# ------------------------------------------------------------------ ledger


def read_ledger(root: Path) -> dict[str, dict]:
    path = root / LEDGER
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())
    except ValueError as exc:
        raise BundleError(f"{LEDGER} is not JSON ({exc}) - fix or remove it by hand") from None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def revision(folder: Path) -> dict[str, object] | None:
    """The bundle's git revision and whether it had uncommitted changes, or
    None when it is not a git checkout."""
    try:
        head = subprocess.run(
            ["git", "-C", str(folder), "rev-parse", "HEAD"], capture_output=True, text=True
        )
        dirty = subprocess.run(
            ["git", "-C", str(folder), "status", "--porcelain", "--", "."],
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if head.returncode or dirty.returncode:
        return None
    return {"commit": head.stdout.strip(), "dirty": bool(dirty.stdout.strip())}


# ------------------------------------------------------------------- plan


@dataclass
class Plan:
    bundle: Bundle
    jobs: list[Item]
    tests: list[Path]
    tables: list[str]
    table_body: list[str]
    catalog_body: list[str]
    overrides: list[tuple[str, str, str]]
    #: tree files the previous install of this bundle owned and this one does not
    stale: list[Path] = field(default_factory=list)
    #: the bundle's connector files (stack/bundle_connectors.py), copied on install
    connectors: list[Path] = field(default_factory=list)


def _owned(root: Path, ledger: dict[str, dict], name: str) -> set[Path]:
    return {root / rel for rel in ledger.get(name, {}).get("files", {})}


def plan(bundle: Bundle, root: Path) -> Plan:
    """Everything installing `bundle` into the tree at `root` would do, with
    every refusal raised here - before a single file is written."""
    ledger = read_ledger(root)
    mine = _owned(root, ledger, bundle.name)
    others = {p for name in ledger if name != bundle.name for p in _owned(root, ledger, name)}
    items, tests = [], []
    for item in stack_install.plan(bundle.root, root):
        if item.source.parent == bundle.root and item.source.name in bundle_additions.ADDITIONS:
            continue
        if item.reason.startswith("a q test"):
            tests.append(item.source)
            continue
        if item.status is Status.SKIPPED or item.destination is None:
            raise BundleError(f"{item.source.relative_to(bundle.root)}: {item.reason}")
        if item.destination in others:
            raise BundleError(f"{item.destination.relative_to(root)} belongs to another bundle")
        if item.status is Status.CONFLICT and item.destination not in mine:
            raise BundleError(
                f"{item.destination.relative_to(root)} exists, differs, and was not installed by "
                f"bundle {bundle.name} - remove it or rename the bundle's file"
            )
        items.append(item)
    if not items:
        raise BundleError(f"bundle {bundle.name} declares no source, worker or streaming job")

    known = bundle_shadowing.known_tables(root, bundle.name)
    table_body, tables = bundle_additions.table_lines(bundle, known)
    catalog_text = without_block((root / CATALOG_FILE).read_text(), bundle.name, str(CATALOG_FILE))
    catalog_body = bundle_additions.catalog_lines(
        bundle, tables, bundle_additions.described(catalog_text)
    )
    procnames = {
        d.procname
        for i in items
        if i.kind in (Kind.WORKER, Kind.STREAMING)
        for d in read_file(i.source)
        if d.kind is not PipelineKind.BACKFILL
    }
    overrides = bundle_additions.override_rows(bundle, procnames)
    stale = sorted(p for p in mine if p not in {i.destination for i in items} and p.suffix == ".q")
    bundle_shadowing.refuse(root, items, stale)
    feeds = bundle_connectors.check(bundle.name, bundle.root, root, tables)
    return Plan(bundle, items, tests, tables, table_body, catalog_body, overrides, stale, feeds)


#: An untracked file at the root of a separate clone that declares it an
#: instance bundles may be installed into (#2732).
INSTANCE_MARKER = ".uqf-instance"


def refuse_development_checkout(root: Path) -> None:
    """Refuse to install into a git checkout of this repository (#2732).

    An install writes tracked files, so the checkout drifts from master and
    every session sharing it inherits the bundle. A linked worktree (`.git`
    is a file) is never an instance; a separate clone is one only when it
    holds INSTANCE_MARKER. A tree with no `.git` - a staged build, CI's
    throwaway copies - is not a checkout and is never refused."""
    git = root / ".git"
    if not git.exists() or (git.is_dir() and (root / INSTANCE_MARKER).is_file()):
        return
    raise BundleError(
        f"{root} is a development checkout: installing a bundle writes tracked files "
        "and every session sharing the checkout inherits it. Test a bundle with "
        "`uv run python scripts/dev/bundle_suite.py <bundle>`, ship one with "
        "`uqs deploy build --bundle <bundle>`, or run one from a separate clone "
        f"(`git clone <repo> <dir> && touch <dir>/{INSTANCE_MARKER}`)"
    )


def install(p: Plan, root: Path, runtime: str | None = None) -> dict:
    """Write the plan into the tree at `root`; return the ledger entry.

    `runtime` joins the runtimes the bundle belongs to (#852): its jobs and
    tables are in those runtimes' stacks and in no other's
    (model/runtime_members.py). An entry without `runtimes`, from before,
    belongs to every runtime that has all of this tree's pipelines."""
    ledger = read_ledger(root)
    previous = ledger.get(p.bundle.name, {})
    # Overrides first: they are the one addition that can still conflict
    # with what an operator set since, and nothing else is written yet.
    override_files.merge_bundle(root, p.overrides, previous.get("overrides", []), p.bundle.name)
    for item in p.jobs:
        stack_install.install(item, Mode.COPY, overwrite=item.status is Status.CONFLICT)
    for stale in p.stale:
        stale.unlink(missing_ok=True)
    for rel, body in ((TABLES_FILE, p.table_body), (CATALOG_FILE, p.catalog_body)):
        path = root / rel
        path.write_text(with_block(path.read_text(), p.bundle.name, body, str(rel)))
    entry = {
        "version": p.bundle.version,
        "revision": revision(p.bundle.root),
        "files": {
            str(i.destination.relative_to(root)): _sha256(i.destination)
            for i in p.jobs
            if i.destination
        },
        "jobs": jobs(p),
        "tables": p.tables,
        "overrides": [list(row) for row in p.overrides],
        "connectors": bundle_connectors.install(
            p.bundle.name, p.connectors, root, previous.get("connectors", [])
        ),
    }
    if runtime is not None or "runtimes" in previous:
        entry["runtimes"] = sorted({*previous.get("runtimes", []), *([runtime] if runtime else [])})
    ledger[p.bundle.name] = entry
    (root / LEDGER).write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n")
    bundle_shadowing.evaluate_tables(root, p.bundle.name)
    return entry


def jobs(p: Plan) -> list[dict[str, str]]:
    """Each installed job's identity: name, kind, procname and file, and the q
    interpreters it runs on when it declares them (its `q` key, #2747) - what
    a release's preflight holds the server's q to."""
    found = []
    for item in p.jobs:
        if item.kind in (Kind.SOURCE, Kind.REACTION):
            assert item.destination is not None
            found.append({"kind": item.kind.value, "file": item.destination.name})
            continue
        for d in read_file(item.source):
            job = {
                "name": d.name,
                "kind": "worker" if d.kind is PipelineKind.BACKFILL else "streaming",
                "procname": d.procname,
                "file": item.source.name,
            }
            if d.q:
                job["q"] = q_support.text(d.q)
            found.append(job)
    return found
