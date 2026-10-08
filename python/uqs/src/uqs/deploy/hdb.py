"""The shared HDB against the release's schema, before anything starts (#870).

Releases share the HDB under the data directory across upgrades. A release
that adds a table or a column meets older partitions without it, and a
partitioned query then fails on the first short one. A release that CHANGES a
column's type meets partitions storing the old one - a migration, which no
fill can make right.

prepare runs the release's own `uqs data hdb-check --json` against the shared
HDB, once its .venv exists, so the release judges the HDB by its own schema:

  - a column whose type changed is always refused, naming each one;
  - missing tables or columns are refused unless --fix-hdb, which runs
    `hdb-check --fix` (additive and idempotent: an empty copy of each missing
    table, each missing column as its type's null) and checks again.

A dry run cannot run the release's code - nothing of it is on the server yet -
so the plan judges a listing of the HDB taken on the server (LIST_PY) against
the shape `uqs deploy build` recorded in the manifest (`hdb_shape`), with
stack/hdb_shape.py's own rule. Types need q and the release, so the plan
says they are checked in prepare.
"""

from __future__ import annotations

import json

from uqs.deploy.config import DeployError, redact
from uqs.deploy.remote import q, script
from uqs.deploy.stages import Deployment
from uqs.runtimes import RUNTIMES
from uqs.stack import hdb_shape

#: The HDB at argv[1] as hdb_shape.listing reads one: {partition: {table:
#: [columns]}}, by the same rules - date-named partitions, a column per file
#: but `.d`, a nested column's `#` file folded into it. {} for no HDB.
LIST_PY = r"""
import json, os, re, sys
root = sys.argv[1]
out = {}
if os.path.isdir(root):
    for p in sorted(os.listdir(root)):
        if not re.match(r"^\d{4}\.\d{2}\.\d{2}$", p) or not os.path.isdir(os.path.join(root, p)):
            continue
        tables = {}
        for t in os.listdir(os.path.join(root, p)):
            d = os.path.join(root, p, t)
            if os.path.isdir(d):
                names = (f[:-1] if f.endswith("#") else f for f in os.listdir(d))
                files = {f for f in names if f != ".d" and os.path.isfile(os.path.join(d, f))}
                tables[t] = sorted(files)
        out[p] = tables
print(json.dumps(out))
"""


def hdb_root(dep: Deployment) -> str:
    """Where the release's processes find the HDB: <data root>/<runtime>/hdb."""
    return f"{dep.cfg.data_root}/{RUNTIMES[dep.runtime].data_dir}/hdb"


def planned(dep: Deployment, manifest: dict) -> str:
    """The dry run's line: what --fix-hdb would fill, from the server's
    listing and the manifest's declared shape."""
    declared = manifest.get("hdb_shape")
    if not declared:
        return "not judged - the artifact records no hdb_shape (built before #870)"
    out = dep.run("preflight", "listing the HDB", f"python3 -c {q(LIST_PY)} {q(hdb_root(dep))}")
    tables, columns = hdb_shape.shape_gaps(json.loads(out.strip().splitlines()[-1]), declared)
    if not tables and not columns:
        return "every partition holds every declared table and column; types checked in prepare"
    n_tables = sum(len(t) for t in tables.values())
    n_cols = sum(len(c) for by in columns.values() for c in by.values())
    parts = len({*tables, *columns})
    return (
        f"{n_tables} missing table(s) and {n_cols} missing column(s) in {parts} partition(s) - "
        + ("filled by --fix-hdb" if dep.cfg.fix_hdb else "refused without --fix-hdb")
        + "; types checked in prepare"
    )


def _check(dep: Deployment, release: str, fix: bool) -> dict:
    argv = "--fix --json" if fix else "--json"
    r = dep.remote.run(
        script(*dep.in_release(release, f".venv/bin/uqs data hdb-check {argv}")),
        dep.cfg.command_timeout,
        "hdb",
    )
    out = r.stdout or ""
    try:
        return json.loads(out[out.index("{") :])
    except ValueError:
        tail = redact(((r.stderr or "") + out).strip()[-400:])
        raise DeployError("hdb", f"the release's hdb-check printed no result: {tail}") from None


def check(dep: Deployment, release: str) -> dict:
    """The release's hdb-check against the shared HDB; refuses what a
    deployment must not start on. Returns what the report records."""
    found = _check(dep, release, fix=False)
    if not found.get("present"):
        return {"hdb": "absent"}
    changed = found.get("type_changes") or []
    if changed:
        named = "; ".join(
            f"{c['partition']} {c['table']}.{c['column']} stored as {c['on_disk']}, "
            f"declared {c['declared']}"
            for c in changed[:10]
        )
        raise DeployError(
            "hdb",
            f"{len(changed)} HDB column(s) changed type - a migration, never filled: {named}",
        )
    missing = {
        "tables": found.get("missing_tables", {}),
        "columns": found.get("missing_columns", {}),
    }
    result = {"hdb": "ok", "types_checked": found.get("types_checked", False)}
    if not missing["tables"] and not missing["columns"]:
        return result
    if not dep.cfg.fix_hdb:
        parts = sorted({*missing["tables"], *missing["columns"]})
        raise DeployError(
            "hdb",
            f"{len(parts)} HDB partition(s) lack tables or columns this release declares "
            f"({', '.join(parts[:6])}{' ...' if len(parts) > 6 else ''}) - pass --fix-hdb to "
            "fill them (additive: missing tables empty, missing columns null)",
        )
    after = _check(dep, release, fix=True)
    if after.get("missing_tables") or after.get("missing_columns"):
        raise DeployError("hdb", "hdb-check --fix left tables or columns missing")
    return {**result, "hdb": "filled", "filled": missing}
