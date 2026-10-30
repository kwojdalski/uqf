"""The plant tables' declared schemas, as q evaluated them (#1781).

q is the authority on what `src/etl/plant_tables.q` declares. uqs used to
find out by matching `name:([]...)` in the file's TEXT, five times over, so
`name: ([]...)` - which q loads exactly the same - lost the table from every
Python reader while q still had it. Now q is asked once:
scripts/generate/export_plant_schema.q loads plant_tables.q alone (no init.q,
no feed, no worker) and prints each table's columns in order, with q's type
character and attribute, its nested columns' element types and its sort
rule. This module writes that to `src/etl/generated/plant_schema.json`, and
is the one reader of it.

ORDINARY READS NEED NO q. The JSON is committed, ships in every release (it
sits under src/), and a read only hashes the inputs to check it is current:

  * `tree`     - plant_tables.q, the exporter and EXPORTER_VERSION. Checked on
                 every read: a table this tree defines is never answered from
                 metadata for some other plant_tables.q.
  * `vendored` - the starter pack's database.q (q reads $TORQAPPHOME's when it
                 is set, as `.qetl.plant.vendored_path` does). Checked only when
                 a vendored table (quote, trade, packets) is asked for, so a
                 server whose starter pack differs can still read this tree's own.

Missing, unreadable or stale metadata is an error that says how to
regenerate it; never a smaller schema. Regeneration writes a temporary file
and renames it over the old, and refuses when the inputs change while q runs,
so a failure leaves the previous file - which the fingerprint then calls
stale - and never metadata for a code snapshot it was not made from.

    uv run python scripts/generate/derive.py plant-schema           # if stale (q only then)
    uv run python scripts/generate/derive.py plant-schema --force   # regenerate (needs q)
    uv run python scripts/generate/derive.py plant-schema --check   # q must agree (needs q)

(uqs.model.plant_metadata_gen does the writing, and a bundle install calls it
directly; this module only reads.)

WHAT IS NOT HERE: a running database's tables (ask the database - this is
what is DECLARED), and keyed tables (a plant table is unkeyed; the exporter
refuses one rather than dropping it).
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from uqs.generated.q_facts import Q_ATTRIBUTES, Q_TYPE_NAMES
from uqs.paths import DERIVE_SCRIPT, ETL_DIR, TABLES_FILE, TORQAPPHOME_ENV, UqsError, repo_root

#: What q evaluates TABLES_FILE (and the starter pack's database.q) to declare.
PLANT_SCHEMA_FILE = ETL_DIR / "generated" / "plant_schema.json"

#: Bumped when the JSON's shape or the exporter's meaning changes, so every
#: committed file built by an older exporter reads as stale.
EXPORTER_VERSION = 1
#: The q that exports, relative to the tree of the uqs doing the reading.
EXPORTER = Path("scripts/generate/export_plant_schema.q")
#: The vendored starter pack's schema when $TORQAPPHOME is unset - the same
#: default as `.qetl.plant.vendored_path` in plant_tables.q.
VENDORED_DEFAULT = Path("lib/torq-finance-starter-pack/database.q")

#: q's type characters and the type names a definition casts with, as q exports
#: them (uqs.generated.q_facts, #1785); " " is a general list, written `()`.
#: Anything else is refused at export.
Q_TYPES = Q_TYPE_NAMES
GENERAL = " "
#: q's attributes, "" for none.
ATTRIBUTES = ("", *Q_ATTRIBUTES)

#: The command that regenerates it: derive.py's plant-schema target (#1804).
REGENERATE = f"uv run python {DERIVE_SCRIPT} plant-schema"


class SchemaMetadataError(UqsError):
    """The plant schema metadata is missing, stale, invalid or cannot be made."""


@dataclass(frozen=True)
class Column:
    name: str
    #: q's type character, `meta`'s `t`: lower case, or " " for a general list
    type: str
    #: q's attribute, `meta`'s `a`: "", "s", "u", "p" or "g"
    attribute: str


@dataclass(frozen=True)
class Table:
    name: str
    #: the starter pack's own (quote, trade, packets), not this tree's
    vendored: bool
    columns: tuple[Column, ...]
    #: a nested (" ") column's declared element type, `.qetl.plant.nested`'s
    nested: dict[str, str]
    #: the declared TorQ sort rule, `.qetl.plant.sorting`'s: (column, attribute)
    sorting: tuple[tuple[str, str], ...]

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    def definition(self) -> str:
        """The table as one q statement, `name:([]time:`timestamp$(); ...)` - what
        stp1's schema file holds. Rendered from what q evaluated, so it is the
        same whatever spacing or line breaks the source used."""
        cells = []
        for c in self.columns:
            literal = "()" if c.type == GENERAL else f"`{Q_TYPES[c.type]}$()"
            cells.append(f"{c.name}:{'`' + c.attribute + '#' if c.attribute else ''}{literal}")
        return f"{self.name}:([]{'; '.join(cells)})"


# ------------------------------------------------------------------ inputs


def vendored_schema(root: Path) -> Path:
    """The starter-pack database.q the exporter loads for `root`."""
    configured = os.environ.get(TORQAPPHOME_ENV, "").strip()
    return Path(configured) / "database.q" if configured else root / VENDORED_DEFAULT


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "absent"


def _combine(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()


def tree_fingerprint(root: Path) -> str:
    """plant_tables.q at `root`, the exporter and its version."""
    exporter = _sha(repo_root() / EXPORTER)
    return _combine(f"v{EXPORTER_VERSION}", exporter, _sha(root / TABLES_FILE))


def vendored_fingerprint(vendored: Path) -> str:
    """The starter pack's database.q at `vendored`, the exporter and its version."""
    return _combine(f"v{EXPORTER_VERSION}", _sha(repo_root() / EXPORTER), _sha(vendored))


# ------------------------------------------------------------------ reading


def _guidance(root: Path) -> str:
    where = "" if root == repo_root() else f" --root {root}"
    return f"regenerate it with `{REGENERATE}{where}` (needs q: $QCMD, or q on PATH)"


@dataclass(frozen=True)
class Metadata:
    root: Path
    tables: dict[str, Table]
    #: the vendored fingerprint the file was made with
    vendored_print: str

    def own(self) -> dict[str, Table]:
        """This tree's plant tables, in definition order."""
        return {n: t for n, t in self.tables.items() if not t.vendored}

    def vendored(self, schema: Path | None = None) -> dict[str, Table]:
        """The starter pack's tables (default: the pack the exporter would load).

        The pack the metadata was made from answers from it, with no q. Any
        other - a server's own $TORQAPPHOME, say - is not this tree's code, so
        q evaluates it on demand, once per process; without q that is refused,
        naming the command, never answered from the wrong pack's tables."""
        schema = schema or vendored_schema(self.root)
        printed = vendored_fingerprint(schema)
        if printed == self.vendored_print:
            return {n: t for n, t in self.tables.items() if t.vendored}
        if printed not in _OTHER_PACKS:
            from uqs.model import plant_metadata_gen

            try:
                found = plant_metadata_gen.export(self.root, vendored=schema)
            except SchemaMetadataError as exc:
                raise SchemaMetadataError(
                    f"{self.root / PLANT_SCHEMA_FILE} was not generated from {schema}, and q "
                    f"could not evaluate it: {exc}; {_guidance(self.root)}"
                ) from None
            plant_metadata_gen._check(found)
            _OTHER_PACKS[printed] = _parse(json.dumps({"tables": {
                t.pop("name"): t for t in found["tables"] if t["vendored"]
            }}))[1]  # fmt: skip
        return _OTHER_PACKS[printed]

    def table(self, name: str) -> Table:
        """One of this tree's own tables, or a refusal naming them."""
        own = self.own()
        if name not in own:
            raise KeyError(
                f"{name!r} is not defined in {TABLES_FILE.name} - it has {', '.join(sorted(own))}"
            )
        return own[name]


def _parse(text: str) -> tuple[dict, dict[str, Table]]:
    data = json.loads(text)
    tables = {}
    for name, t in data["tables"].items():
        cols = tuple(Column(c["name"], c["type"], c["attribute"]) for c in t["columns"])
        sort = tuple((c, a) for c, a in t["sorting"])
        tables[name] = Table(name, bool(t["vendored"]), cols, dict(t["nested"]), sort)
    return data, tables


_CACHE: dict[tuple, Metadata] = {}
#: Starter packs other than the metadata's, as q evaluated them: fingerprint -> tables.
_OTHER_PACKS: dict[str, dict[str, Table]] = {}


def _stamp(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


def load(root: Path | None = None) -> Metadata:
    """The metadata for the tree at `root` (default: this uqs's), checked
    current against that tree's plant_tables.q - or a refusal saying how to
    regenerate it. Cached while none of its inputs change on disk."""
    root = (root or repo_root()).resolve()
    path = root / PLANT_SCHEMA_FILE
    key = (root, _stamp(path), _stamp(root / TABLES_FILE), _stamp(repo_root() / EXPORTER))
    if (hit := _CACHE.get(key)) is not None:
        return hit
    if not path.is_file():
        raise SchemaMetadataError(f"no plant schema metadata at {path} - {_guidance(root)}")
    try:
        data, tables = _parse(path.read_text())
        version, prints = data["exporter_version"], data["fingerprint"]
        recorded_tree, recorded_vendored = prints["tree"], prints["vendored"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SchemaMetadataError(f"{path} is not valid ({exc!r}) - {_guidance(root)}") from None
    if version != EXPORTER_VERSION:
        raise SchemaMetadataError(
            f"{path} was made by exporter version {version}, this uqs reads "
            f"{EXPORTER_VERSION} - {_guidance(root)}"
        )
    if recorded_tree != tree_fingerprint(root):
        raise SchemaMetadataError(
            f"{path} is stale: {root / TABLES_FILE} (or the exporter) changed since it "
            f"was generated - {_guidance(root)}"
        )
    found = Metadata(root, tables, recorded_vendored)
    _CACHE[key] = found
    return found


def is_current(root: Path | None = None) -> bool:
    try:
        load(root)
    except SchemaMetadataError:
        return False
    return True
