"""The text a scaffolded job is made of: q files, and one Python entry.

Split from scaffold/jobs.py, which crossed the 400-line threshold this package
holds itself to. The seam is real rather than convenient: everything here
PRODUCES TEXT and follows the shape of a real job - change it when
`.qstream.register` grows a field, or when a source declaration gains one.
scaffold/jobs.py plans and writes, and changes when what a job NEEDS changes.

Every template is deliberately unfinished in the same way: the handler
throws, and the generated test fails. The exceptions are the two places
where being unfinished would break the tree rather than the build - see
`_source_body`'s fixture.
"""

from __future__ import annotations

#: Columns every plant table carries, and how. `time` first because `.u.upd`
#: stamps it (invariant 1) and every consumer reads it positionally; `sym`
#: grouped because every table in uqs_tables.q groups it and a missing
#: `g#` is a silent performance cliff rather than an error.
TIME_COLUMN = "time"
GROUPED = {"sym"}

#: q type names accepted in a --columns spec, mapped to the empty-column
#: literal that `uqs_tables.q` spells them with. `list` is the general
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


def test_stub(name: str, namespace: str, what: str, *, driver: bool = False) -> str:
    """A test that FAILS until the job is written.

    The whole point of the scaffold is to leave something red. A passing stub
    would make "I scaffolded it" and "it works" look identical from the
    outside, which is the state a scaffold should make impossible.

    `driver` adds the `contract_driver` tests/q/test_job_output_contracts.q
    looks for in this namespace: a job that subscribes and publishes has no
    output check until it has one. It throws until it is written.
    """
    contract = (
        f"""
/ SCAFFOLDED. What test_job_output_contracts.q drives {name} with, so every
/ table it publishes is held to its plant table by name, order and type.
/ Push one batch {name} acts on through .qsub.{name}.on_batch - built from
/ the rows this file's own tests use - and call its timer if it publishes on one.
contract_driver:{{[]
    '"{name}: write .{namespace}.contract_driver - see tests/q/test_job_output_contracts.q"}}
"""
        if driver
        else ""
    )
    return f"""/ test_{name}.q - {what} (.{namespace}).
/ .
/ SCAFFOLDED, AND FAILING ON PURPOSE. Replace the assertion below with what
/ {name} should actually do with a batch. A scaffold that left a passing test
/ behind would make "generated" and "implemented" indistinguishable.

\\d .{namespace}

test_{name}_is_implemented:{{[t]
    .qunit.assertTrue[0b;
        "{name} has not been implemented yet - write this test, then the job"]}}
{contract}
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

/ Parameterised, NEVER concatenated (FE-14, via src/etl/core/source_contract.q).
/ The bounds are arguments to a functional select evaluated on the remote
/ side, so no caller value is ever spliced into query text.
/ .
/ Half-open [range_from;range_to) - ETL-08, a DIFFERENT rule: >= on the lower
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


def worker_body(worker: str, src: str, dataset: str, width: str, proc: str) -> str:
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

/ `procname` is the process that runs this worker - the process registry is
/ read from this declaration, so there is no entry to add anywhere else.
.qbw.define[`{worker};
    `source`dataset`width`transform`facts`procname`note!
        (`{src};`{dataset};{width};`{src}_passthrough;.qwrk.{worker}.facts;
         `{proc};
         "SCAFFOLDED: bounded - say what this backfill is for")];
"""


def table_definition(table: str, columns: list[tuple[str, str]]) -> str:
    """One `name:([]...)` line, in uqs_tables.q's own shape."""
    body = "; ".join(f"{col}:{literal}" for col, literal in columns)
    return f"{table}:([]{body})"
