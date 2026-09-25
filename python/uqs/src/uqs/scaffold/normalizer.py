"""Scaffolding a normalizer: many differently-shaped sources, one canonical table.

Its own module rather than a branch of `jobs.py`: a normalizer is three
declarations in one file - the canonical table, one `.qxf` transform per
source, and the `.qnorm.define` that registers the job - and the transforms
are what make it different. See src/etl/core/normalizer.q for why it is a
kind at all, and src/etl/streaming/marks.q for a finished one.

THE FILE MUST LOAD. `.qxf.define` checks a transform's examples when the
file is loaded, and refuses one with no rows. So every mapping gets a typed
example row - the same sample values a scaffolded source's fixture uses -
and the MAPPING throws instead: the transform suite then fails on it, which
is the red this scaffold is meant to leave, without the tree failing to load.
"""

from __future__ import annotations

import re

from uqs.paths import STREAM_DIR, TABLES_FILE, TEST_DIR, UqsError
from uqs.scaffold.catalog import catalog_actions
from uqs.scaffold.jobs import (
    _STACK_PAGE_NOTE,
    _check_name,
    _expected_table_action,
    _nslist_action,
    test_namespace,
)
from uqs.scaffold.plan import FileAction, ScaffoldPlan, WriteMode
from uqs.scaffold.templates import _SAMPLE_VALUES, TIME_COLUMN, table_definition, test_stub

#: One `name:literal` column inside a `([]...)` definition.
_COLUMN = re.compile(r"^\s*([a-z_][a-z0-9_]*)\s*:\s*(.+?)\s*$", re.IGNORECASE)


def definition_columns(definition: str) -> list[tuple[str, str]]:
    """`t:([]a:`float$(); b:())` as [("a", "`float$()"), ("b", "()")].

    The grouped attribute is dropped: it is a property of the plant's copy,
    and a mapping's declared input carrying it would make every example the
    scaffold writes - which has no attribute - fail `.qxf.define` at load.
    """
    body = definition[definition.index("([]") + 3 : definition.rindex(")")]
    out = []
    for part in body.split(";"):
        if m := _COLUMN.match(part):
            out.append((m.group(1), m.group(2).replace("`g#", "")))
    return out


def _empty(cols: list[tuple[str, str]]) -> str:
    return "([] " + "; ".join(f"{c}:{lit}" for c, lit in cols) + ")"


def _row(cols: list[tuple[str, str]]) -> str:
    return "([] " + "; ".join(f"{c}:enlist {_SAMPLE_VALUES[lit]}" for c, lit in cols) + ")"


def normalizer(
    name: str,
    sources: list[str],
    columns: list[tuple[str, str]],
    source_columns: dict[str, list[tuple[str, str]]],
    *,
    known_tables: set[str],
    procname: str | None = None,
) -> ScaffoldPlan:
    """Plan a normalizer publishing `name` from `sources`.

    `columns` is the canonical table as `parse_columns` reads `--columns`,
    `time` included: the plant's copy keeps it, the normalizer's own output
    drops it, since `.qnorm.define` refuses an output carrying `time`.
    `source_columns` is each source's plant schema, the facts the caller reads
    from the tree - a mapping's input starts as its source's whole table.
    """
    _check_name(name, "normalizer name")
    proc = procname or f"{name}1"
    _check_name(proc, "procname")
    if not sources:
        raise UqsError("--kind normalizer needs --subscribeto: the source tables it normalizes")
    if name in known_tables:
        raise UqsError(
            f"{name!r} is already a plant table - a normalizer owns its canonical table, "
            "so pick a new name"
        )
    missing = [s for s in sources if s not in source_columns]
    if missing:
        raise UqsError(f"no plant definition to read for source(s) {', '.join(missing)}")
    unsampled = sorted({lit for s in sources for _, lit in source_columns[s]} - set(_SAMPLE_VALUES))
    if unsampled:
        raise UqsError(
            f"a source column type has no sample value to scaffold an example with: "
            f"{', '.join(unsampled)} - write this normalizer by hand"
        )
    # Plain `symbol`, not the plant's grouped one: this is the transform's
    # declared output, held strictly against the examples, which carry none -
    # as marks.q's own output does. The plant's copy keeps its attribute.
    output = [(c, lit.replace("`g#", "")) for c, lit in columns if c != TIME_COLUMN]

    sections = []
    transforms = []
    for src in sources:
        cols = source_columns[src]
        xf = f"{name}_from_{src}"
        transforms.append(xf)
        sections.append(
            f"""/ SCAFFOLDED. What the {src} mapping reads - {src}'s whole plant schema.
/ Narrow it to the columns from_{src} uses: a column it never touches still
/ breaks it when upstream changes that column.
{src}:{_empty(cols)}

/ SCAFFOLDED. A {src} batch as canonical {name} rows. Throws until written.
/ @param batch a {src} batch
/ @return canonical {name}
from_{src}:{{[batch]
    '"{name}.from_{src}: not implemented";
    }}
"""
        )
    defines = "\n".join(
        f"""/ SCAFFOLDED example: one {src} row, and the {name} row it should become.
.qxf.define[`{xf};`inputs`output`fn`examples!(
    (enlist `{src})!enlist .qsub.{name}.{src};
    .qsub.{name}.{name};
    .qsub.{name}.from_{src};
    enlist `inputs`expected!(
        (enlist `{src})!enlist {_row(source_columns[src])};
        {_row(output)}))];
"""
        for src, xf in zip(sources, transforms, strict=True)
    )
    if len(sources) == 1:
        mapping = f"(enlist `{sources[0]})!enlist `{transforms[0]}"
    else:
        mapping = "`" + "`".join(sources) + "!`" + "`".join(transforms)
    body = f"""/ {name}.q - the `{name}` normalizer: <one line: the one fact, from every source>
/ (.qsub.{name}).
/ .
/ Reads {", ".join(f"`{s}`" for s in sources)}; publishes `{name}`.
/ .
/ Loaded by src/etl/init.q in any q process: nothing here touches TorQ.

\\d .qsub.{name}

/ Where rows go. A stub until .qstream.wire points it at a tickerplant
/ (the runner) or at a recorder (a test).
publish:.qstream.unwired `{name};

/ The canonical output. No `time`: the plant stamps it.
{name}:{_empty(output)}

{chr(10).join(sections)}
\\d .

{defines}
.qnorm.define[`{name};`procname`output`input`note!(
    `{proc};
    .qsub.{name}.{name};
    {mapping};
    "SCAFFOLDED: say why this exists, and why it does or does not start with the stack")];
"""
    notes = [f"implement .qsub.{name}.from_{s} and its example" for s in sources]
    actions = [
        FileAction(STREAM_DIR / f"{name}.q", body),
        FileAction(
            TABLES_FILE,
            f"\n/ {proc}'s canonical output. <one line: what a row means>\n"
            f"{table_definition(name, columns)}\n",
            mode=WriteMode.APPEND,
        ),
        _expected_table_action(name),
    ]
    actions += catalog_actions(name, columns, notes)
    ns = test_namespace(name)
    actions += [
        FileAction(
            TEST_DIR / f"test_{name}.q",
            test_stub(name, ns, f"the {name} normalizer", driver=True),
        ),
        _nslist_action(ns),
    ]
    notes.append(_STACK_PAGE_NOTE.format(proc=proc))
    notes.append("start it with its producers: " + " ".join(sorted(set(sources))))
    return ScaffoldPlan(name=name, actions=actions, notes=notes)
