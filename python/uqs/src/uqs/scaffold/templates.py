"""The text a scaffolded job is made of: q files, and one Python entry.

Split from scaffold/jobs.py, which crossed the 400-line threshold this package
holds itself to. The seam is real rather than convenient: everything here
PRODUCES TEXT and follows the shape of a real job - change it when
`.qetl.job.stream.define` grows a field, or when a source declaration gains one.
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

#: How a source is reached, as `.qetl.source.transports` lists them. Held to
#: src/etl/core/source_contract.q by test_scaffold.py.
TRANSPORTS = ("ipc", "odbc")

#: What a credential looks like per transport, for the scaffold's own note.
#: A source that knows better declares its own `credential_example`.
CREDENTIAL_SHAPES = {
    "ipc": "host:port of the q process to read from",
    "odbc": "an ODBC connection string",
}


def credential_var(source: str) -> str:
    """The environment variable a live run reads the source's credential from.

    `.qetl.source.credential_var` computes it in q; test_scaffold.py holds
    the two to the same spelling.
    """
    return f"UQF_SOURCE_CRED_{source.upper()}"


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
#: Not empty, because `.qetl.transform.define` refuses a transform whose examples are all
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
/ Push one batch {name} acts on through .qpipe.job.{name}.on_batch - built from
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


#: The query comment and extra declarations that differ by transport. IPC
#: reads a q process with a functional select; ODBC builds SQL whose bounds go
#: through .qetl.io.odbc.literal, as src/etl/sources/duckdb_deals.q does.
_QUERY_NOTES = {
    "ipc": """/ Parameterised, NEVER concatenated (src/etl/core/source_contract.q refuses a string).
/ The bounds are arguments to a functional select evaluated on the remote
/ side, so no caller value is ever spliced into query text.""",
    "odbc": """/ `h` is an ODBC handle from .qetl.io.odbc.open. Build the SELECT with every
/ bound through .qetl.io.odbc.literal - never string concatenation of a raw
/ value - run it with .qetl.io.odbc.run_sql, and return the declared columns
/ and types (src/etl/sources/duckdb_deals.q's sql_for and adapt).""",
}


def _transport_block(src: str, transport: str) -> tuple[str, str, str]:
    """(declarations, extra define keys, extra define values) for a transport.

    IPC is the default `.qetl.source.define` assumes, so it adds nothing.
    """
    if transport == "ipc":
        return "", "", ""
    decls = f"""
transport:`{transport}

/ SCAFFOLDED. What {credential_var(src)} looks like, for the warning a worker
/ logs when none is set - a DuckDB file is a path and a mode, a server needs
/ its host, user and password.
credential_example:"SCAFFOLDED: e.g. DRIVER=...;Database=..."
"""
    return decls, "`transport`credential_example", ";transport;credential_example"


def source_body(src: str, dataset: str, cols: list[tuple[str, str]], transport: str = "ipc") -> str:
    names = [c for c, _ in cols]
    extra_decls, extra_keys, extra_values = _transport_block(src, transport)
    types = "".join(_TYPE_CHARS[literal] for _, literal in cols)
    # The fixture is a real empty table of the declared shape - see its comment.
    fixture_cols = "; ".join(f"{c}:enlist {_SAMPLE_VALUES[lit]}" for c, lit in cols)
    return f"""/ {src}.q - <one line: what this source is> (.qpipe.source.{src}).
/ .
/ SCAFFOLDED. `query` and `fixture` throw until they are written.

\\d .qpipe.source.{src}

source_name:`{src}

/ The columns this adapter READS - not everything the source has. Declaring
/ one the worker never touches means an upstream change to an unused column
/ breaks the run.
columns:`{"`".join(names)}
types:"{types}"

target:`{dataset}

/ The column the window is taken on.
time_column:`{TIME_COLUMN}

/ What identifies a row uniquely. Only correct if the source guarantees it:
/ a source that reuses ids after a purge silently merges unrelated rows.
row_key:`{TIME_COLUMN}

/ A claim, not a default. An unstated zone is the shape of the bug - every
/ later reader assumes UTC while the source hands over local wall-clock time.
tz:`UTC
{extra_decls}
{_QUERY_NOTES[transport]}
/ .
/ Half-open [range_from;range_to) - a DIFFERENT rule: >= on the lower
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
/ .qetl.transform.passthrough call reads this AT LOAD TIME, so a fixture that threw
/ would stop the whole ETL tree from loading - you could not run the suite to
/ see what was unfinished. Empty does not work either: .qetl.transform.define refuses a
/ transform whose examples are all empty. So: one deterministic row of the
/ declared shape, which loads and asserts nothing. Replace it with rows that
/ exercise what this source actually does before trusting a run.
fixture:{{[]
    ([] {fixture_cols})}}

/ Register on load, so the declaration and the implementation cannot drift.
.qetl.source.define[source_name;
    `source`table_name`target`time_column`row_key`columns`types`query`fixture`tz{extra_keys}!
    (source_name;`{dataset};target;time_column;row_key;columns;types;query;fixture;tz{extra_values})];

\\d .
"""


_CHECK_STUB = """
/ SCAFFOLDED. The rows of a batch that must not be published, as a table
/ check/status/detail with one row per offence - empty means it passes
/ (.qetl.job.bounded.no_failures[]). A failing window publishes nothing and
/ records no coverage, so the next run tries it again. Throws until written,
/ so every window fails until it is: see quality_check in
/ src/etl/workers/demo_deals_backfill.q.
quality_check:{{[batch]
    '"{worker}.quality_check: not implemented";
    }}
"""


def worker_body(
    worker: str,
    src: str,
    dataset: str,
    width: str,
    proc: str,
    *,
    partition: str | None = None,
    check: bool = False,
) -> str:
    """The worker file. `partition` scopes it to one slice of its dataset,
    which is what lets a second worker fill the same dataset; `check` adds a
    quality check stub."""
    check_stub = _CHECK_STUB.format(worker=worker) if check else ""
    extra_keys = ("`check" if check else "") + ("`partition" if partition else "")
    extra_values = (f";.qpipe.job.{worker}.quality_check" if check else "") + (
        f";`{partition}" if partition else ""
    )
    return f"""/ {worker}.q - the {src} bounded worker (.qpipe.job.{worker}).
/ .
/ SCAFFOLDED. Mostly a declaration: the lifecycle - windowing, retries,
/ coverage, checkpoints, dry-run - is .qetl.job.bounded's, and none of it belongs here.
/ If you find yourself writing a loop over days, you are rebuilding .qetl.job.bounded.

\\d .qpipe.job.{worker}

/ What this run SAW, beyond its row count. A row count alone reads a partial
/ extract as success. Every aggregate must survive an empty batch - a
/ zero-row window is legal and recorded deliberately.
facts:{{[batch]
    if[0=count batch; :(enlist `window)!enlist "empty window"];
    (enlist `rows)!enlist count batch}}
{check_stub}
\\d .

/ Pass-through until a real transform is needed: the batch is published as
/ fetched. The example tables are what .qetl.transform checks the shape against.
.qetl.transform.passthrough[`{src}_passthrough;`batch;0#.qpipe.source.{src}.fixture[];.qpipe.source.{src}.fixture[]];

/ `procname` is the process that runs this worker - the process registry is
/ read from this declaration, so there is no entry to add anywhere else.
.qetl.job.bounded.define[`{worker};
    `source`dataset`width`transform`facts{extra_keys}`procname`note!
        (`{src};`{dataset};{width};`{src}_passthrough;.qpipe.job.{worker}.facts{extra_values};
         `{proc};
         "SCAFFOLDED: bounded - say what this backfill is for")];
"""


def reaction_body(name: str, dataset: str, writes: list[str], producers: list[str]) -> str:
    """A reaction file: a handler that throws, registered on `dataset`.

    `writes` switches `on` for `on_writing`, which puts the reaction in the job
    graph as a node that writes those tables - a CLAIM, since a handler can
    write anywhere (`derived` is 0b; see .qetl.reaction.on).
    """
    runs_in = ", ".join(producers)
    if writes:
        outputs = f"enlist `{writes[0]}" if len(writes) == 1 else "`" + "`".join(writes)
        register = (
            f".qetl.reaction.on_writing[`{dataset};`{name};{outputs};.qpipe.job.{name}.handler];"
        )
        claim = (
            f"/ on_writing, because this handler writes {', '.join(writes)}: that puts it in\n"
            "/ the job graph, where a cycle is refused at load. It is a CLAIM nothing\n"
            "/ checks - keep it true when you write the handler.\n"
        )
    else:
        register = f".qetl.reaction.on[`{dataset};`{name};.qpipe.job.{name}.handler];"
        claim = ""
    return f"""/ {name}.q - recompute when {dataset} is published (.qpipe.job.{name}).
/ .
/ SCAFFOLDED. The handler throws until it is written.
/ .
/ Runs inside whichever process publishes {dataset} - today {runs_in} - once
/ per published window, after the window's coverage is recorded. It has no
/ process of its own. A failure is recorded in .qetl.reaction.history and
/ never fails the publication; see src/etl/core/react.q.

\\d .qpipe.job.{name}

/ Called with the dataset and the half-open range [range_from;range_to) just
/ published. Recompute exactly what that range changed, keyed by the window,
/ so a re-published window replaces its own rows instead of adding to them.
handler:{{[dataset;range_from;range_to]
    '"{name}: not implemented";
    }}

\\d .

{claim}{register}
"""


def table_definition(table: str, columns: list[tuple[str, str]]) -> str:
    """One `name:([]...)` line, in uqs_tables.q's own shape."""
    body = "; ".join(f"{col}:{literal}" for col, literal in columns)
    return f"{table}:([]{body})"
