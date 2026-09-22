"""The text a scaffolded job is made of: q files, and one Python entry.

Split from scaffold.py, which crossed the 400-line threshold this package
holds itself to. The seam is real rather than convenient: everything here
PRODUCES TEXT and follows the shape of a real job - change it when
`.qstream.register` grows a field, or when a source declaration gains one.
scaffold.py plans and writes, and changes when what a job NEEDS changes.

Every template is deliberately unfinished in the same way: the handler
throws, and the generated test fails. The exceptions are the two places
where being unfinished would break the tree rather than the build - see
`_source_body`'s fixture.
"""

from __future__ import annotations

#: Columns every plant table carries, and how. `time` first because `.u.upd`
#: stamps it (invariant 1) and every consumer reads it positionally; `sym`
#: grouped because every table in uqf_stack_tables.q groups it and a missing
#: `g#` is a silent performance cliff rather than an error.
TIME_COLUMN = "time"
GROUPED = {"sym"}

#: q type names accepted in a --columns spec, mapped to the empty-column
#: literal that `uqf_stack_tables.q` spells them with. `list` is the general
#: column a vector-valued table uses (see wide_book's bid_prices).
Q_TYPES = {
    "timestamp": "`timestamp$()",
    "symbol": "`symbol$()",
    "float": "`float$()",
    "long": "`long$()",
    "int": "`int$()",
    "short": "`short$()",
    "boolean": "`boolean$()",
    "char": "`char$()",
    "date": "`date$()",
    "time": "`time$()",
    "timespan": "`timespan$()",
    "list": "()",
}

_TYPE_CHARS = {
    "`timestamp$()": "p",
    "`symbol$()": "s",
    "`g#`symbol$()": "s",
    "`float$()": "f",
    "`long$()": "j",
    "`int$()": "i",
    "`short$()": "h",
    "`boolean$()": "b",
    "`char$()": "c",
    "`date$()": "d",
    "`time$()": "t",
    "`timespan$()": "n",
    "()": " ",
}


#: One value per column type, for the single row a scaffolded fixture carries.
#: Not empty, because `.qxf.define` refuses a transform whose examples are all
#: empty - "at least one must carry rows" - and not random, because a fixture
#: that changes between runs makes a failing assertion impossible to attribute.
_SAMPLE_VALUES = {
    "`timestamp$()": "2026.01.01D00:00:00.000000000",
    "`symbol$()": "`SCAFFOLD",
    "`g#`symbol$()": "`SCAFFOLD",
    "`float$()": "1.0",
    "`long$()": "1j",
    "`int$()": "1i",
    "`short$()": "1h",
    "`boolean$()": "0b",
    "`char$()": '" "',
    "`date$()": "2026.01.01",
    "`time$()": "00:00:00.000",
    "`timespan$()": "0D00:00:01",
    "()": "1 2 3f",
}


def test_stub(name: str, namespace: str, what: str) -> str:
    """A test that FAILS until the job is written.

    The whole point of the scaffold is to leave something red. A passing stub
    would make "I scaffolded it" and "it works" look identical from the
    outside, which is the state a scaffold should make impossible.
    """
    return f"""/ test_{name}.q - {what} (.{namespace}).
/ .
/ SCAFFOLDED, AND FAILING ON PURPOSE. Replace the assertion below with what
/ {name} should actually do with a batch. A scaffold that left a passing test
/ behind would make "generated" and "implemented" indistinguishable.

\\d .{namespace}

test_{name}_is_implemented:{{[t]
    .qunit.assertTrue[0b;
        "{name} has not been implemented yet - write this test, then the job"]}}

\\d .
"""


def source_body(src: str, dataset: str, cols: list[tuple[str, str]]) -> str:
    names = [c for c, _ in cols]
    types = "".join(_TYPE_CHARS[literal] for _, literal in cols)
    # The fixture is a real empty table of the declared shape - see its comment.
    fixture_cols = "; ".join(f"{c}:enlist {_SAMPLE_VALUES[lit]}" for c, lit in cols)
    return f"""/ {src}.q - <one line: what this source is> (.qfeed.{src}).
/ .
/ SCAFFOLDED. `query` and `fixture` throw until they are written.

\\d .qfeed.{src}

source_name:`{src}

/ The columns this adapter READS - not everything the source has. Declaring
/ one the worker never touches means an upstream change to an unused column
/ breaks the run.
fields:`{"`".join(names)}
types:"{types}"

target:`{dataset}

/ The column the window is taken on.
time_field:`{TIME_COLUMN}

/ What identifies a row uniquely. Only correct if the source guarantees it:
/ a source that reuses ids after a purge silently merges unrelated rows.
row_key:`{TIME_COLUMN}

/ A claim, not a default. An unstated zone is the shape of the bug - every
/ later reader assumes UTC while the source hands over local wall-clock time.
tz:`UTC

/ Parameterised, NEVER concatenated (ETL-08). The bounds are arguments to a
/ functional select evaluated on the remote side, so no caller value is ever
/ spliced into query text. Half-open [range_from;range_to): >= on the lower
/ bound and < on the upper, so a boundary row is published exactly once.
query:{{[h;range_from;range_to]
    '"{src}.query: not implemented";
    }}

/ Must satisfy the same contract as the live source, and be DETERMINISTIC:
/ a fixture that changes between runs makes a failing assertion impossible
/ to attribute. Used when no credential is configured - a stated demo path,
/ never a fallback for a failed connection.
/ .
/ SCAFFOLDED, and deliberately neither a throw nor empty. The worker's
/ .qxf.passthrough call reads this AT LOAD TIME, so a fixture that threw
/ would stop the whole ETL tree from loading - you could not run the suite to
/ see what was unfinished. Empty does not work either: .qxf.define refuses a
/ transform whose examples are all empty. So: one deterministic row of the
/ declared shape, which loads and asserts nothing. Replace it with rows that
/ exercise what this source actually does before trusting a run.
fixture:{{[]
    ([] {fixture_cols})}}

/ Register on load, so the declaration and the implementation cannot drift.
.qsrc.register[source_name;
    `source`table`target`time_field`row_key`fields`types`query`fixture`tz!
    (source_name;`{dataset};target;time_field;row_key;fields;types;query;fixture;tz)];

\\d .
"""


def worker_body(worker: str, src: str, dataset: str, width: str) -> str:
    return f"""/ {worker}.q - the {src} bounded worker (.qwrk.{worker}).
/ .
/ SCAFFOLDED. Mostly a declaration: the lifecycle - windowing, retries,
/ coverage, checkpoints, dry-run - is .qbw's, and none of it belongs here.
/ If you find yourself writing a loop over days, you are rebuilding .qbw.

\\d .qwrk.{worker}

/ What this run SAW, beyond its row count. A row count alone reads a partial
/ extract as success. Every aggregate must survive an empty batch - a
/ zero-row window is legal and recorded deliberately (ETL-07).
facts:{{[batch]
    if[0=count batch; :(enlist `window)!enlist "empty window"];
    (enlist `rows)!enlist count batch}}

\\d .

/ Pass-through until a real transform is needed: the batch is published as
/ fetched. The example tables are what .qxf checks the shape against.
.qxf.passthrough[`{src}_passthrough;`batch;0#.qfeed.{src}.fixture[];.qfeed.{src}.fixture[]];

.qbw.define[`{worker};
    `source`dataset`width`transform`facts!
        (`{src};`{dataset};{width};`{src}_passthrough;.qwrk.{worker}.facts)];
"""


def registry_entry_streaming(name: str, proc: str, is_feed: bool, publishes: str | None) -> str:
    """The Pipeline entry, as text to APPEND to PIPELINES.

    Appended, never inserted, for the reason registry.py states itself:
    offsets are allocated in list order, so an entry above an existing one
    renumbers every process after it onto other processes' ports.

    `subscribes`/`publishes` defer to the q declaration (FROM_DECLARATION) -
    the job file has just been written with both, and restating them here is
    the duplication #311 removed. `schema` is not set at all: it derives from
    `table`.
    """
    kind = "PipelineKind.FEED" if is_feed else "PipelineKind.ETL"
    lines = [
        "    Pipeline(",
        f'        procname="{proc}",',
        "        script=STREAM_RUNNER_SCRIPT,",
        f"        kind={kind},",
        "        loads_qpipe=True,",
    ]
    if not is_feed:
        lines.append("        subscribes=FROM_DECLARATION,")
    if publishes:
        lines.append(f'        table="{publishes}",')
    lines += [
        '        startwithall="0",',
        '        note="SCAFFOLDED: say why this exists, and why it does or does not '
        'start with the stack",',
        "    ),",
    ]
    return "\n".join(lines) + "\n"


def registry_entry_backfill(proc: str, worker: str) -> str:
    """The Pipeline entry for a backfill process.

    `worker=` is what joins this process to the .qbw worker it runs. Without
    it the link exists only at runtime, through UQF_BACKFILL_WORKER, and a
    fully declared worker with no process is invisible to every grep - which
    is how two of them ended up unrunnable (#283).
    """
    return (
        "    Pipeline(\n"
        f'        procname="{proc}",\n'
        '        script="processes/torq_backfill.q",\n'
        "        kind=PipelineKind.BACKFILL,\n"
        f'        worker="{worker}",\n'
        '        startwithall="0",\n'
        '        note="SCAFFOLDED: bounded - runs a window range and exits, so it must '
        'not start with the stack",\n'
        "    ),\n"
    )


def table_definition(table: str, columns: list[tuple[str, str]]) -> str:
    """One `name:([]...)` line, in uqf_stack_tables.q's own shape."""
    body = "; ".join(f"{col}:{literal}" for col, literal in columns)
    return f"{table}:([]{body})"
