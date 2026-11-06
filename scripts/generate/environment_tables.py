"""docs/reference/environment.md's two variable tables, rendered from their declarations (#2169).

The page's prose is hand-written; its tables are not. Each variable is one
row of docs/reference/environment/read.csv or produced.csv - THE place it is
declared, which the env-reference gate reads too (scripts/gates/policy/
env_reference.py: DECLARATIONS) - and this module renders the rows between
markers in the page:

    <!-- panache-ignore-format-start -->
    <!-- BEGIN GENERATED environment-read: ... -->
    ...the table...
    <!-- END GENERATED environment-read -->
    <!-- panache-ignore-format-end -->

The table is written compact (`| a | b |`), one line per variable, with no
column padding and the panache-ignore directives around it. The formatter
realigns a whole padded table when any one row changes, so two branches each
adding a variable conflicted across every line of it; an unpadded row changes
only itself. The rest of the page is still formatted.

Everything outside the markers is left byte for byte as it is. A hand edit
inside them, or a row added to a CSV without regenerating, fails
`derive.py environment-tables --check`. A bare `|` in a cell is escaped here,
so a declaration holds plain text.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RULE = REPO / "scripts" / "gates" / "policy" / "env_reference.py"


def _gate():
    """The env-reference rule, loaded by path: it owns the declaration files'
    names and their one parser."""
    loaded = sys.modules.get("env_reference")
    if loaded is not None and Path(getattr(loaded, "__file__", "") or "").resolve() == RULE:
        return loaded
    spec = importlib.util.spec_from_file_location("env_reference", RULE)
    assert spec and spec.loader, RULE
    module = importlib.util.module_from_spec(spec)
    sys.modules["env_reference"] = module
    spec.loader.exec_module(module)
    return module


@dataclass(frozen=True)
class Table:
    """One generated block: its marker name, declaration file, and how it reads."""

    name: str
    #: The env_reference constant naming the declaration file.
    source: str
    #: (CSV column, heading) in the order the table shows them.
    columns: tuple[tuple[str, str], ...]


TABLES = (
    Table(
        "environment-read",
        "READ_TABLE",
        (
            ("variable", "Variable"),
            ("read_by", "Read by"),
            ("required", "Required"),
            ("unset", "Unset behaviour"),
        ),
    ),
    Table("environment-produced", "PRODUCED_TABLE", (("variable", "Variable"), ("value", "Value"))),
)

IGNORE_START = "<!-- panache-ignore-format-start -->"
IGNORE_END = "<!-- panache-ignore-format-end -->"


def begin(table: Table, source: Path) -> str:
    return (
        f"<!-- BEGIN GENERATED {table.name}: "
        f"`uv run python scripts/generate/derive.py environment-tables` from {source} - "
        "edit that file, not this table -->"
    )


def end(table: Table) -> str:
    return f"<!-- END GENERATED {table.name} -->"


def _cell(value: str) -> str:
    return " ".join(value.replace("|", "\\|").split())


def _line(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def block(table: Table, root: Path | None = None) -> str:
    """The marked block for `table`, from its declaration, ending in a newline."""
    root = root or REPO
    gate = _gate()
    source = getattr(gate, table.source)
    rows = gate.declared_rows(root, source)
    lines = [
        IGNORE_START,
        begin(table, source),
        "",
        _line([heading for _, heading in table.columns]),
        _line(["---"] * len(table.columns)),
        *(_line([_cell(row[col]) for col, _ in table.columns]) for row in rows),
        "",
        end(table),
        IGNORE_END,
    ]
    return "\n".join(lines) + "\n"


class MarkerError(ValueError):
    """The page does not hold a block's markers exactly once, in order."""


def splice(page: str, table: Table, new: str) -> str:
    """`page` with the block for `table` replaced by `new`."""
    first = page.find(IGNORE_START + "\n" + f"<!-- BEGIN GENERATED {table.name}:")
    last = page.find(end(table) + "\n" + IGNORE_END + "\n")
    if first < 0 or last < first or page.count(f"BEGIN GENERATED {table.name}:") != 1:
        raise MarkerError(
            f"docs/reference/environment.md must hold the {table.name} block exactly once: "
            f"a line `{IGNORE_START}` then `<!-- BEGIN GENERATED {table.name}: ... -->`, "
            f"and `{end(table)}` then `{IGNORE_END}`"
        )
    stop = last + len(end(table) + "\n" + IGNORE_END + "\n")
    return page[:first] + new + page[stop:]


def render(root: Path | None = None) -> str:
    """The whole page, its generated blocks current."""
    root = root or REPO
    gate = _gate()
    page = (root / gate.DOC).read_text(encoding="utf-8")
    for table in TABLES:
        page = splice(page, table, block(table, root))
    return page


def doc_path(root: Path | None = None) -> Path:
    return (root or REPO) / _gate().DOC
