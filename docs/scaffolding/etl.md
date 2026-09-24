# Scaffolding an etl

An etl subscribes to one or more plant tables, computes something from each
batch, and republishes. Most of this stack is etls: `posbook1` folds fills
into positions, `markout1` scores execution quality, `arbitrage1` finds
crossed levels in a merged book.

If your rows come from a timer rather than a subscription you want
[feed.md](feed.md); if you are spelling several tables into one shape,
[normalizer.md](normalizer.md) does most of the work for you.

## The command

```bash
uqs new-job spreadmon --subscribes quotes \
    --publishes spread_bps --columns "sym:symbol, spread:float"
```

`--subscribes` is what makes it an etl rather than a feed. It takes a
comma-separated list — subscribing to two tables is legal and common
(`markout1` reads `trades` and `quote`).

```
scaffold spreadmon:
  create src/etl/streaming/spreadmon.q (32 lines)
  append to scripts/processes/uqs_tables.q (3 lines)
  append to tests/q/test_stack_tables.q (1 line)
  append to scripts/processes/uqs_catalog.q (2 lines)
  create tests/q/test_spreadmon.q (20 lines)
  append to tests/run_tests.q (1 line)
  note: implement .qsub.spreadmon.on_batch, then replace the scaffolded test
  note: start it with its producers: quotes
```

That last note is the one to read. `quotes` has a producer — `quotesfeed1` —
and an etl started without it subscribes **successfully**, heartbeats, reports
`up`, and receives nothing. There is no error and no symptom except an output
table that stays empty.

## What you get

```q
\d .qsub.spreadmon

publish:.qstream.unwired `spreadmon;

on_batch:{[t;data]
    '"spreadmon.on_batch: not implemented";
    }

\d .

.qstream.register[`spreadmon;`procname`subscribes`publishes`on_batch`note!(
    `spreadmon1;
    `quotes;
    enlist `spread_bps;
    .qsub.spreadmon.on_batch;
    "SCAFFOLDED: say why this exists, and why it does or does not start with the stack")];
```

**`on_batch` takes two arguments**, and the first is the one that catches
people out. `t` is the table name the batch arrived on — a symbol, not the
data — so a job subscribing to two tables branches on it:

```q
on_batch:{[t;data]
    $[t=`trades; .qsub.spreadmon.from_trades data;
      t=`quote;  .qsub.spreadmon.from_quote data;
      ()]}
```

`data` is the rows. **It carries a `time` column the plant stamped**, which is
why `` `time _ batch `` appears in this tree's history as a bug worth naming:
it is `_` the drop operator applied to a symbol and a table, which throws
`'type` on every batch. The process stays `up` and consumes nothing.

## The rules

**Never call `.u.upd`.** Call `publish`. The runner wires it to the plant, a
test wires it to a recorder, and `check_etl_layering.py` fails the build if
anything under `src/` reaches for `.qpipe`.

**Never publish `time`.** The plant stamps its own (invariant 1).

**An empty batch is legal.** Publishing nothing for a tick is normal, and
every aggregate has to survive it — `0=count data` on the way in is cheaper
than a null propagating into a P&L number.

## Testing it without a stack

The reason `publish` is a stub rather than a direct `.u.upd` call: a test
wires it to a recorder and reads the job's output as data. Use
`.qstream.wire`, not an assignment to the namespace's `publish`:

```q
.qstream.wire[`spreadmon; {[tbl;rows] `.mytest.published set (tbl;rows); count rows}];
.qsub.spreadmon.on_batch[`quotes; fixture];
.qunit.assertEquals[count last .mytest.published; 3; "one row per quoted pair"];
```

**Wire it before driving the job, unconditionally.**
`tests/q/test_cross_arbitrage.q` is the worked example, and its comment
records why: `publish` starts as `.qstream.unwired`, which *throws*, and
`on_batch` only reaches it when a batch actually produces output. So a test
that drove the job and happened to produce nothing **passed while leaving
publish unwired** — then threw as soon as another suite's leftover state made
the batch produce something. It had been another suite's `beforeNamespace`
doing the wiring, by running first; under a shuffled order it no longer did.

## Then

Implement `on_batch`, replace `tests/q/test_spreadmon.q`, and add `spreadmon1`
to a profile — an etl is usually the **leaf** a profile names, because it is
the output you came for. Adding it as a leaf pulls its producers in
automatically; you never list them.
