"""Write `src/etl/generated/plant_schema.json`: ask q, then rename into place (#1781).

The reader, the fingerprints and the shapes are uqs.model.plant_metadata's;
this is the half that needs q. scripts/generate/export_plant_schema.q loads
plant_tables.q alone - no init.q, no feed, no worker, no port - on KDB-X or
PeachQ, and prints JSON; this checks it, renders it with the fingerprints of
the inputs it was made from, and writes it through a temporary file and an
atomic rename. It refuses when the inputs change while q runs, and a failure
leaves the previous file in place - which the fingerprint then calls stale.
derive.py's plant-schema target is the command; a bundle install calls
ensure() itself.

    uv run python scripts/generate/derive.py plant-schema           # ensure(): regenerate if stale
    uv run python scripts/generate/derive.py plant-schema --force   # write(): regenerate (needs q)
    uv run python scripts/generate/derive.py plant-schema --check   # generate(): compare (needs q)
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from uqs.model.plant_metadata import (
    ATTRIBUTES,
    EXPORTER,
    EXPORTER_VERSION,
    GENERAL,
    PLANT_SCHEMA_FILE,
    Q_TYPES,
    SchemaMetadataError,
    is_current,
    tree_fingerprint,
    vendored_fingerprint,
    vendored_schema,
)
from uqs.paths import TABLES_FILE, TORQAPPHOME_ENV, repo_root


def export(root: Path, vendored: Path | None = None) -> dict:
    """Run the exporter over the tree at `root` - beside the starter pack
    `vendored` (a database.q) when given; return what q printed."""
    from uqs.interpreter import q_interpreter

    env = os.environ.copy()
    if vendored is not None:
        env[TORQAPPHOME_ENV] = str(vendored.parent)
    q = q_interpreter(env)
    if q is None:
        raise SchemaMetadataError(
            f"cannot generate {root / PLANT_SCHEMA_FILE}: no q interpreter - set $QCMD, "
            "or put q on PATH"
        )
    env.setdefault("QHOME", str(Path.home() / ".kx"))
    try:
        r = subprocess.run(
            [str(q), str(repo_root() / EXPORTER), "-q"],
            cwd=root,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SchemaMetadataError(f"the plant schema exporter could not run: {exc}") from None
    lines = r.stdout.strip().splitlines()
    if r.returncode or not lines:
        said = (r.stderr.strip() or r.stdout.strip())[-2000:]
        raise SchemaMetadataError(
            f"q could not load {root / TABLES_FILE} to export its schemas "
            f"(exit {r.returncode}):\n{said}"
        )
    try:
        return json.loads(lines[-1])
    except ValueError as exc:
        raise SchemaMetadataError(f"the plant schema exporter printed no JSON: {exc}") from None


def _check(found: dict) -> None:
    """Refuse what the metadata cannot represent, naming it."""
    seen: set[str] = set()
    for t in found["tables"]:
        if t["name"] in seen:
            raise SchemaMetadataError(f"table {t['name']} is exported twice")
        seen.add(t["name"])
        for c in t["columns"]:
            where = f"{t['name']}.{c['name']}"
            if c["type"] not in Q_TYPES and c["type"] != GENERAL:
                raise SchemaMetadataError(
                    f"{where} has q type {c['type']!r}, which the schema metadata does not "
                    "carry: declare a list-per-row column as () and give its element type "
                    "with .qetl.plant.nested"
                )
            if c["attribute"] not in ATTRIBUTES:
                raise SchemaMetadataError(f"{where} has unknown attribute {c['attribute']!r}")


def render(root: Path, found: dict, vendored: Path) -> str:
    """The metadata file: one line per table, so a diff names the table."""
    head = {
        "exporter_version": EXPORTER_VERSION,
        "fingerprint": {
            "tree": tree_fingerprint(root),
            "vendored": vendored_fingerprint(vendored),
        },
    }
    out = ["{"]
    for k, v in head.items():
        out.append(f"  {json.dumps(k)}: {json.dumps(v)},")
    out.append('  "tables": {')
    rows = []
    for t in found["tables"]:
        body = {
            "vendored": t["vendored"],
            "columns": [
                {"name": c["name"], "type": c["type"], "attribute": c["attribute"]}
                for c in t["columns"]
            ],
            "nested": t["nested"] or {},
            "sorting": [list(p) for p in t["sorting"]],
        }
        rows.append(f"    {json.dumps(t['name'])}: {json.dumps(body)}")
    out.append(",\n".join(rows))
    out.append("  }")
    out.append("}")
    return "\n".join(out) + "\n"


def generate(root: Path | None = None) -> str:
    """The metadata `root`'s tree should have, made by q now. Refused when
    the inputs change while q runs: it would describe neither snapshot."""
    root = (root or repo_root()).resolve()
    vendored = vendored_schema(root)
    before = (tree_fingerprint(root), vendored_fingerprint(vendored))
    found = export(root)
    _check(found)
    if (tree_fingerprint(root), vendored_fingerprint(vendored)) != before:
        raise SchemaMetadataError(
            f"{root / TABLES_FILE} changed while its schemas were exported - run again"
        )
    return render(root, found, vendored)


def write(root: Path | None = None) -> bool:
    """Regenerate `root`'s metadata through a temporary file and an atomic
    rename; True when it changed. A failure leaves the previous file."""
    root = (root or repo_root()).resolve()
    text = generate(root)
    path = root / PLANT_SCHEMA_FILE
    if path.is_file() and path.read_text() == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return True


def ensure(root: Path | None = None, *, create: bool = True) -> bool:
    """Regenerate only if stale - what a bundle install or a scaffold calls
    after changing plant_tables.q. True when it wrote. Needs q only then.

    `create=False` leaves a tree that carries no metadata at all as it is
    (a stand-in tree, not a checkout or a release): its readers already
    refuse, naming the command, and there is nothing current to keep."""
    root = (root or repo_root()).resolve()
    if is_current(root) or not (create or (root / PLANT_SCHEMA_FILE).is_file()):
        return False
    return write(root)
