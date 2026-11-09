"""What a column type IS, decided once: the table every scaffold reads.

A table shape is spelled two ways in this tree - as q, in
src/etl/plant_tables.q, and as the `--schema` mini-language
(`"sym:symbol, px:float"`) - and a scaffold also needs, per type, the `meta`
character a source declares and one sample value for a fixture row. Those
were three dicts kept in step by hand (#353). They are one table here, and
every conversion - spec to columns, q definition to columns, columns back to
either - reads it, so a type added in one place is added everywhere.

The COLUMN is the currency: `(name, literal)`, where `literal` is the empty
column exactly as plant_tables.q writes it - `` `float$() ``, `` `g#`symbol$() ``,
`()` for a vector column. `test_columns.py` holds every table in plant_tables.q
and the vendored database.q to a round trip through these functions.

A source's PHYSICAL table - someone else's, as `--raw-columns` describes it - is
raw_columns.py: it reads this table but follows none of a plant table's rules.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from uqs.generated.q_facts import Q_TYPE_CHARS
from uqs.model import plant_metadata, schemas
from uqs.model.qnames import Q_WORDS, reserved_reason
from uqs.paths import TABLES_FILE, UqsError

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

    #: How `--schema` spells it: `px:float`.
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


def _typed(name: str, sample: str) -> ColumnType:
    """A typed column of q's type `name`: its literal and `meta` character are
    q's (uqs.generated.q_facts, #1785), so only the sample is the scaffold's."""
    return ColumnType(name, f"`{name}$()", Q_TYPE_CHARS[name], sample)


#: The types a scaffold OFFERS - a deliberate subset of q's: each must be one
#: the browser's catalog can type (test_browsable_types.py), and a type left
#: out (byte, real, month, datetime, minute, second, guid) is refused by name.
TYPES: tuple[ColumnType, ...] = (
    _typed("timestamp", "2026.01.01D00:00:00.000000000"),
    _typed("symbol", "`SCAFFOLD"),
    _typed("float", "1.0"),
    _typed("long", "1j"),
    _typed("int", "1i"),
    _typed("short", "1h"),
    _typed("boolean", "0b"),
    _typed("char", '" "'),
    _typed("date", "2026.01.01"),
    _typed("time", "00:00:00.000"),
    _typed("timespan", "0D00:00:01"),
    #: A general column: what a vector-valued table uses (fx_orderbook's bid_prices).
    #: The scaffold's own name - q calls none of its types `list`.
    ColumnType("list", "()", " ", "1 2 3f"),
)

_BY_NAME = {t.name: t for t in TYPES}
_BY_LITERAL = {t.literal: t for t in TYPES}

_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


def type_named(name: str) -> ColumnType | None:
    """The type `--schema` spells `name`, or None for one a scaffold does not offer."""
    return _BY_NAME.get(name)


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
    """A `--schema` string as [(name, literal)].

    `name:type`, with `g#` before the type to group the column
    (`venue:g#symbol`); `sym` is grouped without asking. `time` is prepended
    when absent rather than rejected: every plant table has one, and a table
    scaffolded without it would be refused later by a publish path that
    assumes it. Named, it must be the first column and a timestamp.
    """
    out: list[tuple[str, str]] = []
    names = ", ".join(sorted(t.name for t in TYPES))
    for part in (p.strip() for p in spec.split(",") if p.strip()):
        if ":" not in part:
            raise UqsError(f"column {part!r} must be name:type, e.g. 'px:float'. Types: {names}")
        col, _, qtype = (s.strip() for s in part.partition(":"))
        if not _NAME.fullmatch(col):
            raise UqsError(
                f"column {col!r} must be lower-case, start with a letter, and hold only "
                "letters, digits and underscores"
            )
        refuse_q_word(col, "column")
        grouped = qtype.startswith("g#")
        ctype = _BY_NAME.get(qtype.removeprefix("g#"))
        if ctype is None:
            raise UqsError(f"column {col!r} has unknown type {qtype!r}. Types: {names}")
        if grouped and ctype.literal == "()":
            raise UqsError(f"column {col!r}: a general (list) column cannot carry g#")
        if col in GROUPED_BY_DEFAULT and ctype.name == "symbol":
            grouped = True
        refuse_twice(col, out, "column")
        out.append((col, (GROUP if grouped else "") + ctype.literal))
    if not out:
        raise UqsError("--schema is empty: a published table needs at least one column")
    stamp = (TIME_COLUMN, _BY_NAME["timestamp"].literal)
    if not any(c == TIME_COLUMN for c, _ in out):
        out.insert(0, stamp)
    elif out[0] != stamp:
        # The plant reads `time` positionally and `.u.upd` stamps it as a
        # timestamp: a table that names it later, or as another type, loads
        # and then breaks every consumer (#1574).
        raise UqsError(
            f"column {TIME_COLUMN!r} must come first and be a timestamp "
            f"('{TIME_COLUMN}:timestamp, ...') - or leave it out and it is added"
        )
    return out


def refuse_q_word(col: str, what: str) -> None:
    """Refuse a q keyword as a column name: `([] value:...)` is refused by q
    with `assign`, so the table a scaffold wrote would stop the tree loading
    (#1588)."""
    if col in Q_WORDS:
        raise UqsError(
            f"{what} {col!r} is a q keyword and cannot name a column - "
            f"call it something else, e.g. '{col}_'"
        )


def refuse_twice(col: str, so_far: list[tuple[str, str]], what: str) -> None:
    """Refuse a second `col`: q would load it renamed (`px` then `px1`), so
    the table on disk would not be the one asked for (#1575)."""
    if any(c == col for c, _ in so_far):
        raise UqsError(f"{what} {col!r} is named twice - every column needs its own name")


@dataclass(frozen=True)
class Shape:
    """A plant table's whole shape, as `--schema-from` copies it (#2450).

    The columns are not the whole of it: plant_tables.q also declares, beside
    the table, what each list column holds (`.qetl.plant.nested`) and how the
    table is sorted on disk (`.qetl.plant.sorting`). A copy that kept only the
    columns came back with every element type guessed as "F" and no sort rule,
    so it was a different table from the one it was copied from.
    """

    columns: tuple[tuple[str, str], ...]
    #: (list column, element type character), for each list column the source
    #: declares; one it leaves undeclared is guessed and marked SCAFFOLDED.
    nested: tuple[tuple[str, str], ...] = ()
    #: (column, attribute) in sort order, "" for none: `.qetl.plant.sorting`'s.
    sorting: tuple[tuple[str, str], ...] = ()


#: What a scaffold takes as a table's shape: the `--schema` string, columns
#: already parsed, or a whole `Shape` - which is what `--schema-from` hands
#: over, since the string cannot say every shape (see `columns_spec`) nor
#: carry the declarations beside the table.
Columns = str | list[tuple[str, str]] | Shape


def as_columns(value: Columns) -> list[tuple[str, str]]:
    """`value` as [(name, literal)], parsing it when it is a `--schema` string."""
    if isinstance(value, Shape):
        return list(value.columns)
    return parse_columns(value) if isinstance(value, str) else list(value)


def resolve_shape(
    columns: str | None,
    columns_from: str | None,
    tables: Mapping[str, plant_metadata.Table],
) -> Columns | None:
    """The shape `--schema` or `--schema-from` gives, never both.

    `--schema-from TABLE` copies a plant table's shape exactly from `tables`
    (as q evaluated them): its columns with their attributes, what each list
    column holds and its sort rule - most new jobs publish something shaped
    like something already there.
    """
    if columns_from is None:
        return columns
    if columns is not None:
        raise UqsError("--schema and --schema-from both give the table's shape - pick one")
    if columns_from not in tables:
        raise UqsError(
            f"--schema-from {columns_from!r} is not a plant table - "
            f"one of {', '.join(sorted(tables))}"
        )
    source = tables[columns_from]
    return Shape(
        tuple(table_columns(source.definition())),
        tuple(source.nested.items()),
        tuple(source.sorting),
    )


def columns_spec(columns: list[tuple[str, str]]) -> str:
    """[(name, literal)] as the `--schema` string that parses back to it.

    Exact for every shape plant_tables.q uses. The one it cannot say is a `sym`
    WITHOUT `g#`, since `sym` is grouped by default - the vendored `packets`
    table has one, which is why `--schema-from` passes columns rather than
    this string.
    """
    parts = []
    for col, literal in columns:
        ctype = _type_of(literal)
        grouped = literal.startswith(GROUP)
        if col in GROUPED_BY_DEFAULT and ctype.name == "symbol" and not grouped:
            # Refused rather than written as `sym:symbol`, which would parse
            # back GROUPED - a spec that silently disagreed with its table.
            raise UqsError(f"an ungrouped {col} column has no --schema spelling")
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
    return [
        (name, literal if keep_groups else literal.replace(GROUP, ""))
        for name, literal in schemas.column_pairs(definition)
    ]


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


def plant_runtime_names() -> frozenset[str]:
    """The names plant_tables.q binds to something other than a table - its API
    (`schema`, `shape`, `nested`, ...) and keyed registries - as q evaluated it
    (#1781), so a helper added there is reserved with it (#1597)."""
    return plant_metadata.load().bindings


def plant_table_definition(table: str, columns: list[tuple[str, str]]) -> str:
    """The same line as plant_tables.q holds it: fully qualified from the root,
    since the file opens no `\\d` context (#1411).

    Tables and the plant runtime share `.qetl.plant`, so a table named for one
    of its functions would replace it - refused before anything is written."""
    if (why := reserved_reason(table)) is not None:
        raise UqsError(f"{table!r} cannot be a plant table: it is {why}. Pick another name")
    if table in plant_runtime_names():
        raise UqsError(
            f"{table} is a name the plant runtime uses (.qetl.plant.{table}, in "
            f"{TABLES_FILE}) - a table defined under it would replace it. Pick another name"
        )
    return f".qetl.plant.{table_definition(table, columns)}"


def nested_declaration(table: str, shape: Columns) -> str:
    """The `.qetl.plant.nested` line a table with list columns needs, or "".

    An empty `()` column has no element type, so plant_tables.q declares what
    each one holds beside the table - without it, a preview or poll of a page
    for this table is refused, and test_plant_tables.q's gate fails.

    A `Shape` copied with `--schema-from` carries its source's element types,
    and they are written as they are. Any list column without one - every one
    given by `--schema` - is declared "F", float vectors (what the scaffold's
    own sample `1 2 3f` holds), and only then is the line marked SCAFFOLDED:
    it may hold strings ("C") or symbols ("S") instead.
    """
    known = dict(shape.nested) if isinstance(shape, Shape) else {}
    nested = [col for col, literal in as_columns(shape) if literal == _BY_NAME["list"].literal]
    if not nested:
        return ""
    chars = "".join(known.get(col, "F") for col in nested)
    pairs = (
        f'(enlist `{nested[0]})!enlist "{chars}"'
        if len(nested) == 1
        else "`" + "`".join(nested) + f'!"{chars}"'
    )
    guessed = any(col not in known for col in nested)
    marker = (
        '  / SCAFFOLDED: what each list column holds - "F" floats, "C" strings, "S" symbols'
        if guessed
        else ""
    )
    return f".qetl.plant.nested[`{table};{pairs}];{marker}\n"


def sorting_declaration(table: str, shape: Columns) -> str:
    """The `.qetl.plant.sorting` line a copied table's sort rule needs, or "".

    Only a `Shape` carries one - `--schema` has no spelling for a sort rule,
    and a table without one takes its stack's default rows, as before.
    """
    rule = shape.sorting if isinstance(shape, Shape) else ()
    if not rule:
        return ""
    cols = [c for c, _ in rule]
    atts = [f"`{a}" for _, a in rule]
    pairs = (
        f"(enlist `{cols[0]})!enlist {atts[0]}"
        if len(rule) == 1
        else "`" + "`".join(cols) + "!" + "".join(atts)
    )
    return f".qetl.plant.sorting[`{table};{pairs}];\n"


def plant_declarations(table: str, shape: Columns) -> str:
    """Everything plant_tables.q needs for a new table: its definition line,
    then what its list columns hold and its sort rule, each when it has one."""
    return (
        f"{plant_table_definition(table, as_columns(shape))}\n"
        f"{nested_declaration(table, shape)}{sorting_declaration(table, shape)}"
    )
