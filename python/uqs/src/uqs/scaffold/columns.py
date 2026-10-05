"""What a column type IS, decided once: the table every scaffold reads.

A table shape is spelled two ways in this tree - as q, in
src/etl/plant_tables.q, and as the `--columns` mini-language
(`"sym:symbol, px:float"`) - and a scaffold also needs, per type, the `meta`
character a source declares and one sample value for a fixture row. Those
were three dicts kept in step by hand (#353). They are one table here, and
every conversion - spec to columns, q definition to columns, columns back to
either - reads it, so a type added in one place is added everywhere.

The COLUMN is the currency: `(name, literal)`, where `literal` is the empty
column exactly as plant_tables.q writes it - `` `float$() ``, `` `g#`symbol$() ``,
`()` for a vector column. `test_columns.py` holds every table in plant_tables.q
and the vendored database.q to a round trip through these functions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from uqs.paths import UqsError

#: The column every plant table leads with: `.u.upd` stamps it, and every
#: consumer reads it positionally.
TIME_COLUMN = "time"

#: The grouped attribute, as a literal prefix. `sym` gets it without asking,
#: because every table in plant_tables.q groups sym and a missing `g#` is a
#: silent performance cliff rather than an error.
GROUP = "`g#"
GROUPED_BY_DEFAULT = {"sym"}


@dataclass(frozen=True)
class ColumnType:
    """One q column type, everything a scaffold needs to know about it."""

    #: How `--columns` spells it: `px:float`.
    name: str
    #: The empty column, as plant_tables.q writes it.
    literal: str
    #: The `meta` character a source's `types` string declares. Note `j` for a
    #: long, not `l`: the first draft of demo_events.q wrote "l" and was
    #: refused at registration, correctly.
    char: str
    #: One deterministic value, for the single row a scaffolded fixture
    #: carries - not empty, because `.qetl.transform.define` refuses examples
    #: that are all empty, and not random, because a fixture that changes
    #: between runs makes a failing assertion impossible to attribute.
    sample: str


TYPES: tuple[ColumnType, ...] = (
    ColumnType("timestamp", "`timestamp$()", "p", "2026.01.01D00:00:00.000000000"),
    ColumnType("symbol", "`symbol$()", "s", "`SCAFFOLD"),
    ColumnType("float", "`float$()", "f", "1.0"),
    ColumnType("long", "`long$()", "j", "1j"),
    ColumnType("int", "`int$()", "i", "1i"),
    ColumnType("short", "`short$()", "h", "1h"),
    ColumnType("boolean", "`boolean$()", "b", "0b"),
    ColumnType("char", "`char$()", "c", '" "'),
    ColumnType("date", "`date$()", "d", "2026.01.01"),
    ColumnType("time", "`time$()", "t", "00:00:00.000"),
    ColumnType("timespan", "`timespan$()", "n", "0D00:00:01"),
    #: A general column: what a vector-valued table uses (quotes' bid_prices).
    ColumnType("list", "()", " ", "1 2 3f"),
)

_BY_NAME = {t.name: t for t in TYPES}
_BY_LITERAL = {t.literal: t for t in TYPES}

#: One `name:literal` column inside a `([]...)` definition.
_COLUMN = re.compile(r"^\s*([a-z_][a-z0-9_]*)\s*:\s*(.+?)\s*$", re.IGNORECASE)
_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


def is_known(literal: str) -> bool:
    """Whether `literal` - grouped or not - is a type this table describes."""
    return literal.removeprefix(GROUP) in _BY_LITERAL


def _type_of(literal: str) -> ColumnType:
    found = _BY_LITERAL.get(literal.removeprefix(GROUP))
    if found is None:
        raise UqsError(
            f"column type {literal} is not one a scaffold knows - "
            f"{', '.join(t.literal for t in TYPES)}"
        )
    return found


def type_char(literal: str) -> str:
    """The `meta` character for a column's literal; an attribute changes nothing."""
    return _type_of(literal).char


def sample_value(literal: str) -> str:
    """The fixture value for a column's literal."""
    return _type_of(literal).sample


def parse_columns(spec: str) -> list[tuple[str, str]]:
    """A `--columns` string as [(name, literal)].

    `name:type`, with `g#` before the type to group the column
    (`venue:g#symbol`); `sym` is grouped without asking. `time` is prepended
    when absent rather than rejected: every plant table has one, and a table
    scaffolded without it would be refused later by a publish path that
    assumes it.
    """
    out: list[tuple[str, str]] = []
    names = ", ".join(sorted(t.name for t in TYPES))
    for part in (p.strip() for p in spec.split(",") if p.strip()):
        if ":" not in part:
            raise UqsError(f"column {part!r} must be name:type, e.g. 'value:float'. Types: {names}")
        col, _, qtype = (s.strip() for s in part.partition(":"))
        if not _NAME.match(col):
            raise UqsError(
                f"column {col!r} must be lower-case, start with a letter, and hold only "
                "letters, digits and underscores"
            )
        grouped = qtype.startswith("g#")
        ctype = _BY_NAME.get(qtype.removeprefix("g#"))
        if ctype is None:
            raise UqsError(f"column {col!r} has unknown type {qtype!r}. Types: {names}")
        if grouped and ctype.literal == "()":
            raise UqsError(f"column {col!r}: a general (list) column cannot carry g#")
        if col in GROUPED_BY_DEFAULT and ctype.name == "symbol":
            grouped = True
        out.append((col, (GROUP if grouped else "") + ctype.literal))
    if not out:
        raise UqsError("--columns is empty: a published table needs at least one column")
    if not any(c == TIME_COLUMN for c, _ in out):
        out.insert(0, (TIME_COLUMN, _BY_NAME["timestamp"].literal))
    return out


#: What a scaffold takes as a table's shape: the `--columns` string, or
#: columns already parsed - which is what `--columns-from` hands over, since
#: the string cannot say every shape (see `columns_spec`).
Columns = str | list[tuple[str, str]]


def as_columns(value: Columns) -> list[tuple[str, str]]:
    """`value` as [(name, literal)], parsing it when it is a `--columns` string."""
    return parse_columns(value) if isinstance(value, str) else list(value)


def resolve_shape(
    columns: str | None, columns_from: str | None, definitions: dict[str, str]
) -> Columns | None:
    """The shape `--columns` or `--columns-from` gives, never both.

    `--columns-from TABLE` copies a plant table's columns exactly, attributes
    included, from `definitions` ({table: its q line}) - most new jobs publish
    something shaped like something already there.
    """
    if columns_from is None:
        return columns
    if columns is not None:
        raise UqsError("--columns and --columns-from both give the table's shape - pick one")
    if columns_from not in definitions:
        raise UqsError(
            f"--columns-from {columns_from!r} is not a plant table - "
            f"one of {', '.join(sorted(definitions))}"
        )
    return table_columns(definitions[columns_from])


def columns_spec(columns: list[tuple[str, str]]) -> str:
    """[(name, literal)] as the `--columns` string that parses back to it.

    Exact for every shape plant_tables.q uses. The one it cannot say is a `sym`
    WITHOUT `g#`, since `sym` is grouped by default - the vendored `packets`
    table has one, which is why `--columns-from` passes columns rather than
    this string.
    """
    parts = []
    for col, literal in columns:
        ctype = _type_of(literal)
        grouped = literal.startswith(GROUP)
        if col in GROUPED_BY_DEFAULT and ctype.name == "symbol" and not grouped:
            # Refused rather than written as `sym:symbol`, which would parse
            # back GROUPED - a spec that silently disagreed with its table.
            raise UqsError(f"an ungrouped {col} column has no --columns spelling")
        explicit = grouped and col not in GROUPED_BY_DEFAULT
        parts.append(f"{col}:{'g#' if explicit else ''}{ctype.name}")
    return ", ".join(parts)


def definition_columns(definition: str, *, keep_groups: bool = False) -> list[tuple[str, str]]:
    """`t:([]a:`float$(); b:())` as [("a", "`float$()"), ("b", "()")].

    LENIENT: a literal no ColumnType describes comes back as written, for the
    caller to refuse with its own reason - the normalizer does. The grouped
    attribute is dropped unless `keep_groups`: it is a property of the plant's
    copy, and a normalizer mapping's declared input carrying it would make
    every example the scaffold writes - which has no attribute - fail
    `.qetl.transform.define` at load.
    """
    body = definition[definition.index("([]") + 3 : definition.rindex(")")]
    out = []
    for part in body.split(";"):
        if m := _COLUMN.match(part):
            literal = m.group(2) if keep_groups else m.group(2).replace(GROUP, "")
            out.append((m.group(1), literal))
    return out


def table_columns(definition: str) -> list[tuple[str, str]]:
    """A table's columns exactly, attributes kept - STRICT: a type this
    module does not describe is refused, because a scaffold would otherwise
    write a source it cannot declare types for."""
    cols = definition_columns(definition, keep_groups=True)
    for _, literal in cols:
        _type_of(literal)
    return cols


def table_definition(table: str, columns: list[tuple[str, str]]) -> str:
    """One `name:([]...)` line, in plant_tables.q's own shape."""
    body = "; ".join(f"{col}:{literal}" for col, literal in columns)
    return f"{table}:([]{body})"


def nested_declaration(table: str, columns: list[tuple[str, str]]) -> str:
    """The `.qetl.plant.nested` line a table with list columns needs, or "".

    An empty `()` column has no element type, so plant_tables.q declares what
    each one holds beside the table - without it, a preview or poll of a page
    for this table is refused, and test_plant_tables.q's gate fails. Each list
    column is declared "F", float vectors - what the scaffold's own sample
    (`1 2 3f`) holds - and marked SCAFFOLDED, since a column copied with
    `--columns-from` may hold strings ("C") or symbols ("S") instead.
    """
    nested = [col for col, literal in columns if literal == _BY_NAME["list"].literal]
    if not nested:
        return ""
    chars = "F" * len(nested)
    pairs = (
        f'(enlist `{nested[0]})!enlist "{chars}"'
        if len(nested) == 1
        else "`" + "`".join(nested) + f'!"{chars}"'
    )
    return (
        f"nested[`{table};{pairs}];"
        '  / SCAFFOLDED: what each list column holds - "F" floats, "C" strings, "S" symbols\n'
    )
