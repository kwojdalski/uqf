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

from uqs.model import transports
from uqs.scaffold.columns import TIME_COLUMN, raw_contract, sample_value, type_char
from uqs.scaffold.transform import worker_transform


def credential_var(source: str) -> str:
    """The environment variable a live run reads the source's credential from.

    `.qetl.source.credential_var` computes it in q; test_scaffold.py holds
    the two to the same spelling.
    """
    return f"UQF_SOURCE_CRED_{source.upper()}"


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


def _transport_block(src: str, transport: str) -> tuple[str, str, str]:
    """(declarations, extra define keys, extra define values) for a transport.

    The default transport is what `.qetl.source.define` assumes, so it adds
    nothing. The example is the transport's own, from the contract surface.
    """
    if transports.get(transport).default:
        return "", "", ""
    decls = f"""
transport:`{transport}

/ SCAFFOLDED. What {credential_var(src)} looks like, for the warning a worker
/ logs when none is set - a DuckDB file is a path and a mode, a server needs
/ its host, user and password, a local HDB is a directory.
credential_example:"SCAFFOLDED: e.g. {transports.get(transport).example}"
"""
    return decls, "`transport`credential_example", ";transport;credential_example"


def _raw_block(raw: tuple[str, list[tuple[str, str]]] | None) -> tuple[str, str, str]:
    """(declaration, define key, define value) for a raw-input contract."""
    if raw is None:
        return "", "", ""
    table, cols = raw
    decl = f"""
/ What the adapter READS from the physical table, apart from what it returns
/ (columns/types above): `uqs config sources check` holds {table}'s live
/ metadata to this, and the query's rows to columns/types. A general column
/ accepts any type. Mapping one to the other is the query's job.
raw:{raw_contract(table, cols)}
"""
    return decl, "`raw", ";raw"


def source_body(
    src: str,
    dataset: str,
    cols: list[tuple[str, str]],
    transport: str | None = None,
    raw: tuple[str, list[tuple[str, str]]] | None = None,
) -> str:
    """The source file. `raw` is (physical table, its columns) for an adapter
    whose output is not its input: declared, not guessed, since only the
    author knows the mapping."""
    transport = transport or transports.default()
    names = [c for c, _ in cols]
    extra_decls, extra_keys, extra_values = _transport_block(src, transport)
    raw_decl, raw_key, raw_value = _raw_block(raw)
    extra_decls += raw_decl
    extra_keys += raw_key
    extra_values += raw_value
    types = "".join(type_char(literal) for _, literal in cols)
    # The fixture is a real empty table of the declared shape - see its comment.
    fixture_cols = "; ".join(f"{c}:enlist {sample_value(lit)}" for c, lit in cols)
    return f"""/ {src}.q - <one line: what this source is> (.qpipe.source.{src}).
/ .
/ SCAFFOLDED. `query` and `fixture` throw until they are written.

\\d .qpipe.source.{src}

source_name:`{src}

/ The columns this adapter RETURNS - for an identity adapter, also what it
/ reads. Not everything the source has: declaring one the worker never
/ touches means an upstream change to an unused column breaks the run.
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
{transports.get(transport).query_note}
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
    transform: str = "passthrough",
    shared: tuple[str, str] | None = None,
) -> str:
    """The worker file. `partition` scopes it to one slice of its dataset,
    which is what lets a second worker fill the same dataset; `check` adds a
    quality check stub; `transform` is `passthrough` or `derive`, and
    `shared` a twin's stream job and the transform it declares - see
    scaffold/transform.py."""
    check_stub = _CHECK_STUB.format(worker=worker) if check else ""
    derive_stub, transform_decl, transform_name = worker_transform(
        transform, worker, src, dataset, shared
    )
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
{check_stub}{derive_stub}
\\d .

{transform_decl}
/ `procname` is the process that runs this worker - the process registry is
/ read from this declaration, so there is no entry to add anywhere else.
.qetl.job.bounded.define[`{worker};
    `source`dataset`width`transform`facts{extra_keys}`procname`note!
        (`{src};`{dataset};{width};`{transform_name};.qpipe.job.{worker}.facts{extra_values};
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
/ Read the rows with .qetl.reaction.published[], never {dataset} by name:
/ under `uqs backfill` the worker writes HDB partitions and there is no table
/ to name. Write output with .qetl.reaction.write[table;row_key;rows], which
/ goes where the worker wrote - not a table here, in a process that exits -
/ and replaces this window's output, so a re-published window leaves one
/ answer. Rows need a `time` column inside the window. See
/ src/etl/reactions/rebuild_positions.q.
handler:{{[dataset;range_from;range_to]
    '"{name}: not implemented";
    }}

\\d .

{claim}{register}
"""
