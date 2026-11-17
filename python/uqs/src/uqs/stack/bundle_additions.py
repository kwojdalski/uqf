"""A sidecar bundle's tree additions (#800): its tables.q, catalog.q and
process_overrides.csv, each read as text and checked before anything is
written.

Each file holds only the lines it may: tables.q `name:([]...)` plant tables
and their `nested[...]`/`sorting[...]` lines, catalog.q a description (or a
hidden reason) for every table the bundle defines and an unbounded reason only
for its own, process_overrides.csv rows for the bundle's OWN processes. None
of them may define, describe or set what the tree already does; a line of
only `/` or `\\` is refused, since it would open a q block comment in the
tree file. stack/bundles.py plans and installs what these return.
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import TYPE_CHECKING

from uqs.stack.bundle_blocks import BundleError

if TYPE_CHECKING:
    from uqs.stack.bundles import Bundle

TABLES = "tables.q"
CATALOG = "catalog.q"
OVERRIDES = "process_overrides.csv"
#: The bundle's own addition files: read by stack/bundles.py, never placed as jobs.
ADDITIONS = (TABLES, CATALOG, OVERRIDES)
#: `name:([]...)` as uqs.model.schemas reads the plant: a bundle writes it bare, the
#: tree holds it qualified (#1411), so both match and `table_lines` qualifies.
_TABLE = re.compile(r"^(?:\.qetl\.plant\.)?([a-z_][a-z0-9_]*):\(\[\]")
_CONTRACT = re.compile(r"^(?:\.qetl\.plant\.)?(nested|sorting)\[`([a-z_][a-z0-9_]*);")
_DESCRIBE = re.compile(r"^\.qcat\.describe\[`([a-z_][a-z0-9_]*)\]:")
#: A raw table a desk should not browse (the input to a fold) is hidden with its
#: reason rather than described, as the tree's own are.
_HIDDEN = re.compile(r"^\.qcat\.hidden\[`([a-z_][a-z0-9_]*)\]:")
#: A bundle cannot add querypolicy.csv rows, so a reason its table is browsable
#: unbounded is how it meets the catalog's policy-or-reason rule (#889).
_UNBOUNDED = re.compile(r"^\.qcat\.unbounded\[`([a-z_][a-z0-9_]*)\]:")


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
        elif m := _CONTRACT.match(line):
            if m.group(2) not in tables:
                raise BundleError(
                    f"{where}: {m.group(1)}[] for {m.group(2)}, which this bundle does not define "
                    "(above this line)"
                )
        else:
            raise BundleError(
                f"{where}: expected `name:([]...)`, `nested[...]`, `sorting[...]` or `/ ` lines"
            )
    return [_qualified(line) for line in lines], tables


def _qualified(line: str) -> str:  # a tables.q line as the tree holds it
    matched = (_TABLE.match(line) or _CONTRACT.match(line)) and not line.startswith(".qetl.")
    return ".qetl.plant." + line if matched else line


def catalog_lines(bundle: Bundle, tables: list[str], described: set[str]) -> list[str]:
    """catalog.q's lines, every bundle table described (or hidden, with a
    reason) exactly once and nothing the tree already describes described
    again. An unbounded reason may name only a table this bundle defines."""
    lines, seen = _lines(bundle.addition(CATALOG)), []
    for n, line in enumerate(lines, 1):
        where = f"{bundle.name}/{CATALOG}:{n}"
        _refuse_block_comment(line, where)
        if not line.strip() or line.startswith("/ ") or line[:1] in " \t":
            continue
        if u := _UNBOUNDED.match(line):
            if u.group(1) not in tables:
                raise BundleError(
                    f"{where}: .qcat.unbounded for {u.group(1)}, which this bundle does not define"
                )
            continue
        m = _DESCRIBE.match(line) or _HIDDEN.match(line)
        if not m:
            raise BundleError(
                f'{where}: expected .qcat.describe[`table]:"...", .qcat.hidden[`table]:"..." or '
                '.qcat.unbounded[`table]:"..." (fully qualified)'
            )
        if m.group(1) in described or m.group(1) in seen:
            raise BundleError(f"{where}: {m.group(1)} is already described")
        if _HIDDEN.match(line) and m.group(1) not in tables:
            raise BundleError(
                f"{where}: .qcat.hidden for {m.group(1)}, which this bundle does not define"
            )
        seen.append(m.group(1))
    if missing := [t for t in tables if t not in seen]:
        raise BundleError(
            f"{bundle.name}: {', '.join(missing)} defined in {TABLES} but neither described nor "
            f"hidden in {CATALOG} - the catalog refuses a table that is neither"
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


def described(catalog_text: str) -> set[str]:
    """The tables the tree's catalog.q text describes or hides, at the top level
    or inside a block of its own."""
    found = {
        m.group(1)
        for ln in catalog_text.splitlines()
        if (m := _DESCRIBE.match(ln) or _HIDDEN.match(ln))
    }
    return found | set(
        re.findall(r"^\s*(?:describe|hidden)\[`([a-z_][a-z0-9_]*)\]", catalog_text, re.M)
    )
