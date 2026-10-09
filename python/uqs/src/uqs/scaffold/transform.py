"""Scaffolding a job's transform: `--transform passthrough` or `--transform derive`.

A bounded worker always has a transform between its source and its dataset;
until #714 the scaffold could only write the passthrough, so every job that
derived anything - a notional, a markout - rewrote it by hand as a
`.qetl.transform.define`. `derive` writes that define instead: the input's
shape, the output's shape, and one example whose expected output is left
empty and marked SCAFFOLDED.

A streaming job (#742) gets the same two modes, and with them the handler the
transform needs: route the one subscribed table, drop the plant's `time`,
apply the transform, publish the result. Only for one table in and one out -
joins, buffering, timers and state are the job's business, and a scaffold that
guessed at them would be wrong more often than right. Without `--transform` a
streaming job keeps the custom-handler scaffold it always had.

WHY THE EXAMPLE IS EMPTY. `.qetl.transform.define` checks an example's SHAPE
when the file loads, and `.qetl.transform.verify` checks its VALUES when the
suite runs. An empty table of the output's shape passes the first and fails
the second for any real derivation of a non-empty fixture - so the tree loads,
and the suite says exactly which transform is unwritten.
"""

from __future__ import annotations

from uqs.paths import UqsError
from uqs.scaffold.columns import TIME_COLUMN, sample_value, type_char

#: The values `--transform` takes.
MODES = ("passthrough", "derive")


def check_mode(mode: str) -> str:
    """`mode`, or an error naming the ones there are."""
    if mode not in MODES:
        raise UqsError(f"--transform must be one of {', '.join(MODES)}, not {mode!r}")
    return mode


def without_time(cols: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """A plant table's columns as a job sees them: the plant stamps `time`."""
    return [(c, lit) for c, lit in cols if c != TIME_COLUMN]


def fixture_row(cols: list[tuple[str, str]]) -> str:
    """One deterministic row of `cols`, as a q table literal."""
    if not cols:
        raise UqsError("a transform's input needs at least one column besides time")
    return "([] " + "; ".join(f"{c}:enlist {sample_value(lit)}" for c, lit in cols) + ")"


# ------------------------------------------------------------------ BACKFILL


def worker_transform(
    mode: str, worker: str, src: str, dataset: str, shared: tuple[str, str] | None = None
) -> tuple[str, str, str]:
    """A worker's transform: (q inside its namespace, q after it, transform name).

    `shared` is (stream job, its declared transform) for a twin: the twin then
    applies that transform rather than one of its own, so a refill re-derives
    what the job publishes (#884), and nothing is written for `mode`."""
    if shared is not None:
        job, name = shared
        after = f"""/ {job}'s own transform, as it declares it: a refill re-derives what
/ {job} publishes, by construction (#884). The source's fetch must hand over
/ that transform's inputs, under its input names:
/ .qetl.transform.registry[`{name}]`inputs.
"""
        return "", after, name
    check_mode(mode)
    if mode == "passthrough":
        after = f"""/ Pass-through until a real transform is needed: the batch is published as
/ fetched. The example tables are what .qetl.transform checks the shape against.
.qetl.transform.passthrough[`{src}_passthrough;`batch;0#.qpipe.source.{src}.fixture[];.qpipe.source.{src}.fixture[]];
"""
        return "", after, f"{src}_passthrough"
    name = f"{worker}_transform"
    inside = f"""
/ SCAFFOLDED. One fetched batch, as the source delivers it, into rows of
/ {dataset}. Throws until written, so every window fails until it is.
/ @param batch rows of .qpipe.source.{src}'s contract
/ @return rows of {dataset}
derive:{{[batch]
    '"{worker}.derive: not implemented";
    }}
"""
    after = f"""/ The transform between source and dataset. Its example's expected output is
/ SCAFFOLDED - an empty {dataset}, which no real derivation of the fixture
/ returns - so .qetl.transform.verify fails it, and the suite with it, until
/ derive is written and the expected rows say what it should return.
.qetl.transform.define[`{name};`inputs`output`fn`examples!(
    (enlist `batch)!enlist 0#.qpipe.source.{src}.fixture[];
    .qetl.plant.shape `{dataset};
    .qpipe.job.{worker}.derive;
    enlist `inputs`expected!(
        (enlist `batch)!enlist .qpipe.source.{src}.fixture[];
        / SCAFFOLDED: the rows derive returns for the fixture above
        0#.qetl.plant.shape `{dataset}))];
"""
    return inside, after, name


# ----------------------------------------------------------------- STREAMING


def check_streaming(
    name: str,
    mode: str,
    subscribe_to: list[str],
    publishes: list[str],
    *,
    poll: bool,
    period: str | None,
) -> None:
    """Refuse a streaming transform the scaffold cannot write honestly."""
    check_mode(mode)
    if poll:
        raise UqsError(
            f"--transform {mode} wires a subscribed table to a published one, and --poll scaffolds "
            "a feed that fetches its own pages - drop one of them"
        )
    if not subscribe_to:
        raise UqsError(
            f"--transform {mode} needs --subscribe-to: a feed subscribes to nothing, so there is "
            "no batch to transform"
        )
    if len(subscribe_to) != 1 or len(publishes) != 1:
        raise UqsError(
            f"--transform {mode} scaffolds one table in and one out, and {name} reads "
            f"{len(subscribe_to)} and publishes {len(publishes)} - joining or splitting tables is "
            "the job's own logic: drop --transform for the custom-handler scaffold"
        )
    if period is not None:
        raise UqsError(
            f"--transform {mode} publishes as each batch arrives; --period adds a timer whose "
            "work the scaffold cannot know - drop --period, or drop --transform for the "
            "custom-handler scaffold"
        )


def check_passthrough_shapes(
    sub: str, pub: str, in_cols: list[tuple[str, str]], out_cols: list[tuple[str, str]]
) -> None:
    """Refuse a passthrough whose input and output differ, saying how."""
    have = [(c, type_char(lit)) for c, lit in in_cols]
    want = [(c, type_char(lit)) for c, lit in out_cols]
    if have == want:
        return
    raise UqsError(
        f"--transform passthrough publishes {sub}'s rows unchanged, but {pub} is not that shape: "
        f"{sub} has {', '.join(f'{c}:{t}' for c, t in have)}; "
        f"{pub} has {', '.join(f'{c}:{t}' for c, t in want)} (time aside). "
        f"Use --transform derive to reshape them"
        + (f", or --columns-from {sub} to define {pub} as a copy" if not want else "")
    )


def streaming_transform(
    name: str, mode: str, sub: str, pub: str, in_cols: list[tuple[str, str]]
) -> tuple[str, str, str]:
    """The q a transform-mode streaming job carries: (inside its namespace,
    after it, transform name). `in_cols` is `sub`'s columns without `time`."""
    xf = f"{name}_{mode}"
    derive = (
        f"""
/ SCAFFOLDED. A batch of {sub} rows, shaped as `input`, into rows of {pub},
/ shaped as `output`. Throws until written - and the example below fails
/ .qetl.transform.verify until both this and its expected rows are.
/ @param batch rows of input's shape
/ @return rows of output's shape
derive:{{[batch]
    '"{name}.derive: not implemented";
    }}
"""
        if mode == "derive"
        else ""
    )
    inside = f"""/ The shapes the handler moves rows between: what {sub} carries and what
/ {pub} takes, both without `time` - the plant stamps its own (invariant 1).
input:.qetl.plant.published `{sub}
output:.qetl.plant.published `{pub}

/ One deterministic {sub} row: the transform's example input, and what the
/ generated tests drive the handler with.
/ @return a one-row table of input's shape
fixture:{{[] {fixture_row(in_cols)}}}
{derive}
/ Route one batch. Only {sub} is accepted - another table is refused rather
/ than forwarded - and an empty batch publishes nothing. The plant's `time`
/ is dropped on the way in; .u.upd stamps the published rows' own.
/ @param t the table the batch arrived on
/ @param x the rows, as a table
/ @return nothing
/ @throws error naming the table, when it is not {sub}
on_batch:{{[t;x]
    if[not t=`{sub};
        '"{name}.on_batch: subscribes to `{sub}, not `",string t];
    if[0=count x; :()];
    rows:.qetl.transform.apply[`{xf};(enlist `batch)!enlist (cols .qpipe.job.{name}.input)#x];
    if[count rows; .qpipe.job.{name}.publish[`{pub};rows]];
    }}"""
    if mode == "passthrough":
        after = f"""
/ {sub}'s rows, published unchanged. The example is the fixture.
.qetl.transform.passthrough[`{xf};`batch;.qpipe.job.{name}.input;.qpipe.job.{name}.fixture[]];
"""
    else:
        after = f"""
/ The transform the handler applies. Its example's expected output is
/ SCAFFOLDED - an empty {pub}, which no real derivation of the fixture
/ returns - so .qetl.transform.verify fails it until the rows are written.
.qetl.transform.define[`{xf};`inputs`output`fn`examples!(
    (enlist `batch)!enlist .qpipe.job.{name}.input;
    .qpipe.job.{name}.output;
    .qpipe.job.{name}.derive;
    enlist `inputs`expected!(
        (enlist `batch)!enlist .qpipe.job.{name}.fixture[];
        / SCAFFOLDED: the rows derive returns for the fixture above
        .qpipe.job.{name}.output))];
"""
    return inside, after, xf


def streaming_test(name: str, namespace: str, mode: str, sub: str, pub: str) -> str:
    """The generated tests: routing, the empty batch, and what reaches `pub`.

    For a passthrough every test passes as written. For a derive the two that
    need the transform fail until it - and its example - are written.
    """
    ns = f".{namespace}"
    xf = f"{name}_{mode}"
    if mode == "passthrough":
        output_tests = f"""
test_fixture_rows_reach_{pub}:{{[t]
    wired[];
    .qpipe.job.{name}.on_batch[`{sub};batch[]];
    .qunit.assertEquals[recorded[;0];enlist `{pub};"one batch, published to {pub}"];
    .qunit.assertEquals[first recorded[;1];.qpipe.job.{name}.fixture[];
        "the rows unchanged, without time"]}};
"""
    else:
        output_tests = f"""
/ SCAFFOLDED until written: fails while derive throws or the example's
/ expected rows are not what it returns.
test_the_transform_returns_its_example:{{[t]
    r:select from .qetl.transform.verify `{xf} where not passed;
    .qunit.assertTrue[0=count r;
        "write .qpipe.job.{name}.derive and its example's expected rows - ",
        $[count r;first r`detail;""]]}};

test_derived_rows_reach_{pub}:{{[t]
    wired[];
    .qpipe.job.{name}.on_batch[`{sub};batch[]];
    want:(first .qetl.transform.def[`{xf}]`examples)`expected;
    .qunit.assertEquals[recorded[;0];enlist `{pub};"one batch, published to {pub}"];
    .qunit.assertEquals[first recorded[;1];want;"the example's expected rows"]}};
"""
    return f"""/ test_{name}.q - the {name} streaming job ({ns}).
/ .
/ Generated with --transform {mode}: the handler routes {sub} through the
/ {xf} transform to {pub}. These tests hold the routing, the empty batch
/ and what is published; replace or extend them as the job grows.

\\d {ns}

/ What the job published, as (table; rows) pairs.
recorded:()

/ Point the job's publish at `recorded`, emptied.
wired:{{[] `{ns}.recorded set ();
    .qetl.job.stream.wire[`{name};{{[t;x] `{ns}.recorded set {ns}.recorded,enlist (t;x)}}];}}

/ A {sub} batch as the plant delivers it: the fixture, stamped with `time`.
batch:{{[] update time:2026.01.01D00:00:00.000000000 from .qpipe.job.{name}.fixture[]}}

test_another_table_is_refused_not_forwarded:{{[t]
    wired[];
    .qunit.assertThrows[.qpipe.job.{name}.on_batch[`not_{sub}];batch[];"*subscribes to `{sub}*";
        "a table the job does not subscribe to is an error, never forwarded"];
    .qunit.assertEquals[count recorded;0;"and nothing is published"]}};

test_an_empty_batch_publishes_nothing:{{[t]
    wired[];
    .qpipe.job.{name}.on_batch[`{sub};0#batch[]];
    .qunit.assertEquals[count recorded;0;"an empty batch is not an empty publish"]}};
{output_tests}
/ What test_job_output_contracts.q drives {name} with: one {sub} batch, so
/ what it publishes is held to {pub}'s plant table by name, order and type.
contract_driver:{{[] .qpipe.job.{name}.on_batch[`{sub};{ns}.batch[]]}}

\\d .
"""
