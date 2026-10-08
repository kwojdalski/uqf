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
      tables.q                 optional: `name:([]...)` plant tables and
                               `nested[...]` contracts, one per line
      catalog.q                optional: `.qcat.describe[`table]:"..."` entries
      process_overrides.csv    optional: procname,field,value for the
                               bundle's OWN processes

INSTALLING IS IDEMPOTENT. The additions go into the tree files as one block
per bundle between `/ BEGIN bundle <name>` and `/ END bundle <name>`, and a
reinstall replaces that block rather than appending another. What a bundle
put in the tree is recorded in src/etl/installed_bundles.json (the ledger),
so an upgrade may replace and remove ITS OWN files and override rows - and
nothing else: a job file, table, catalog entry or override another bundle or
the tree already owns is a conflict, refused before anything is written.

COPY ONLY. A bundle is what a release carries to a server, and a symlink back
to a workstation checkout is exactly what does not travel.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from uqs.model.declarations import read_file
from uqs.model.pipeline import PipelineKind
from uqs.paths import CATALOG_FILE, PACKAGE_DIR, TABLES_FILE
from uqs.stack import install as stack_install
from uqs.stack.bundle_blocks import BundleError, with_block, without_block
from uqs.stack.install import Item, Kind, Mode, Status

MANIFEST = "bundle.json"
TABLES = "tables.q"
CATALOG = "catalog.q"
OVERRIDES = "process_overrides.csv"
#: The bundle's own addition files: read by this module, never placed as jobs.
ADDITIONS = (TABLES, CATALOG, OVERRIDES)
#: Where the tree records which bundle owns what, relative to its root.
LEDGER = Path("src/etl/installed_bundles.json")
OVERRIDES_FILE = PACKAGE_DIR / "process_overrides.csv"

_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}")
_MANIFEST_KEYS = frozenset({"name", "version", "description"})
#: The same `name:([]...)` convention uqs.model.schemas reads the plant with.
_TABLE = re.compile(r"^([a-z_][a-z0-9_]*):\(\[\]")
_NESTED = re.compile(r"^nested\[`([a-z_][a-z0-9_]*);")
_DESCRIBE = re.compile(r"^\.qcat\.describe\[`([a-z_][a-z0-9_]*)\]:")


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
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise BundleError(f"{path}: name must be lower_snake_case, e.g. piggybank")
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        raise BundleError(f"{path}: version must be a string such as 1.4.0")
    return Bundle(folder.resolve(), name, version, str(data.get("description", "")))


# ------------------------------------------------------------- additions


def _lines(path: Path | None) -> list[str]:
    return path.read_text().rstrip("\n").splitlines() if path else []


def _refuse_block_comment(line: str, where: str) -> None:
    # A line holding only `/` (or `\`) opens a q block comment that swallows
    # everything after it - in the tree file, not just the bundle's block.
    if line.strip() in ("/", "\\"):
        raise BundleError(f"{where}: a line of only {line.strip()!r} opens a q block comment")


def table_lines(bundle: Bundle, known: set[str]) -> tuple[list[str], list[str]]:
    """tables.q's lines and the tables it defines. `known` is every table
    the tree already defines outside this bundle's block."""
    path = bundle.addition(TABLES)
    lines, tables = _lines(path), []
    for n, line in enumerate(lines, 1):
        where = f"{bundle.name}/{TABLES}:{n}"
        _refuse_block_comment(line, where)
        if not line.strip() or line.startswith("/ "):
            continue
        if m := _TABLE.match(line):
            if m.group(1) in known:
                raise BundleError(f"{where}: table {m.group(1)} is already defined in the tree")
            if m.group(1) in tables:
                raise BundleError(f"{where}: table {m.group(1)} is defined twice")
            tables.append(m.group(1))
        elif m := _NESTED.match(line):
            if m.group(1) not in tables:
                raise BundleError(
                    f"{where}: nested[] for {m.group(1)}, which this bundle does not define "
                    "(above this line)"
                )
        else:
            raise BundleError(
                f"{where}: expected `name:([]...)`, `nested[...]` or a `/ ` comment, one per line"
            )
    return lines, tables


def catalog_lines(bundle: Bundle, tables: list[str], described: set[str]) -> list[str]:
    """catalog.q's lines, every bundle table described exactly once and
    nothing the tree already describes described again."""
    lines, seen = _lines(bundle.addition(CATALOG)), []
    for n, line in enumerate(lines, 1):
        where = f"{bundle.name}/{CATALOG}:{n}"
        _refuse_block_comment(line, where)
        if not line.strip() or line.startswith("/ ") or line[:1] in " \t":
            continue
        m = _DESCRIBE.match(line)
        if not m:
            raise BundleError(f'{where}: expected .qcat.describe[`table]:"..." (fully qualified)')
        if m.group(1) in described or m.group(1) in seen:
            raise BundleError(f"{where}: {m.group(1)} is already described")
        seen.append(m.group(1))
    if missing := [t for t in tables if t not in seen]:
        raise BundleError(
            f"{bundle.name}: {', '.join(missing)} defined in {TABLES} but not described in "
            f"{CATALOG} - the catalog refuses a table that is neither described nor hidden"
        )
    return lines


def override_rows(bundle: Bundle, procnames: set[str]) -> list[tuple[str, str, str]]:
    """process_overrides.csv's rows: the bundle's OWN processes only."""
    from uqs.model.pipelines import PROCESS_CSV_FIELDS

    path = bundle.addition(OVERRIDES)
    if path is None:
        return []
    reader = csv.DictReader(io.StringIO(path.read_text()))
    if reader.fieldnames != ["procname", "field", "value"]:
        raise BundleError(f"{bundle.name}/{OVERRIDES}: the header must be procname,field,value")
    rows: dict[tuple[str, str], str] = {}
    for n, row in enumerate(reader, 2):
        where = f"{bundle.name}/{OVERRIDES}:{n}"
        proc, fld, value = row["procname"], row["field"], row["value"] or ""
        if proc not in procnames:
            raise BundleError(f"{where}: {proc} is not one of this bundle's processes")
        if fld not in PROCESS_CSV_FIELDS or fld in ("procname", "port"):
            raise BundleError(f"{where}: {fld!r} is not a process.csv field a bundle may set")
        if "," in value or "\n" in value:
            raise BundleError(f"{where}: torq.sh reads process.csv unquoted - no comma in {fld}")
        if (proc, fld) in rows:
            raise BundleError(f"{where}: {proc} {fld} is set twice")
        rows[(proc, fld)] = value
    return [(p, f, v) for (p, f), v in rows.items()]


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
        if item.source.parent == bundle.root and item.source.name in ADDITIONS:
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

    tables_text = (root / TABLES_FILE).read_text()
    outside = without_block(tables_text, bundle.name, str(TABLES_FILE))
    known = {m.group(1) for ln in outside.splitlines() if (m := _TABLE.match(ln))}
    table_body, tables = table_lines(bundle, known)
    catalog_text = without_block((root / CATALOG_FILE).read_text(), bundle.name, str(CATALOG_FILE))
    described = {m.group(1) for ln in catalog_text.splitlines() if (m := _DESCRIBE.match(ln))}
    described |= set(re.findall(r"^\s*describe\[`([a-z_][a-z0-9_]*)\]", catalog_text, re.M))
    catalog_body = catalog_lines(bundle, tables, described)
    procnames = {
        d.procname
        for i in items
        if i.kind is not Kind.SOURCE
        for d in read_file(i.source)
        if d.kind is not PipelineKind.BACKFILL
    }
    overrides = override_rows(bundle, procnames)
    installed = {i.destination for i in items}
    stale = sorted(p for p in mine if p not in installed and p.suffix == ".q")
    return Plan(bundle, items, tests, tables, table_body, catalog_body, overrides, stale)


def _merge_overrides(root: Path, p: Plan, previous: list[list[str]]) -> None:
    path = root / OVERRIDES_FILE
    rows: dict[tuple[str, str], str] = {}
    if path.is_file():
        for row in csv.DictReader(io.StringIO(path.read_text())):
            rows[(row["procname"], row["field"])] = row["value"]
    for proc, fld, _value in previous:
        rows.pop((proc, fld), None)
    for proc, fld, value in p.overrides:
        if rows.get((proc, fld), value) != value:
            raise BundleError(
                f"{OVERRIDES_FILE}: {proc} {fld} is already {rows[(proc, fld)]!r}, "
                f"bundle {p.bundle.name} sets {value!r}"
            )
        rows[(proc, fld)] = value
    if not rows and not path.is_file():
        return
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["procname", "field", "value"])
    writer.writerows([proc, fld, value] for (proc, fld), value in rows.items())
    path.write_text(out.getvalue())


def install(p: Plan, root: Path) -> dict:
    """Write the plan into the tree at `root`; return the ledger entry."""
    ledger = read_ledger(root)
    previous = ledger.get(p.bundle.name, {})
    # Overrides first: they are the one addition that can still conflict
    # with what an operator set since, and nothing else is written yet.
    _merge_overrides(root, p, previous.get("overrides", []))
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
    }
    ledger[p.bundle.name] = entry
    (root / LEDGER).write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n")
    return entry


def jobs(p: Plan) -> list[dict[str, str]]:
    """Each installed job's identity: name, kind, procname and file."""
    found = []
    for item in p.jobs:
        if item.kind is Kind.SOURCE:
            assert item.destination is not None
            found.append({"kind": "source", "file": item.destination.name})
            continue
        for d in read_file(item.source):
            found.append(
                {
                    "name": d.name,
                    "kind": "worker" if d.kind is PipelineKind.BACKFILL else "streaming",
                    "procname": d.procname,
                    "file": item.source.name,
                }
            )
    return found
