# Normalizer

A normalizer answers one question from several tables that each answer it
differently. `executions1` turns FX `trades` and crypto `crypto_trades` into one
`executions` table; `marks1` turns `quote` and `crypto_book` into one mid per
instrument. Downstream reads one shape and never learns there were two.

It is an [etl](etl.md) with a specific job, and the scaffold knows enough about
that job to write considerably more of it.

## Command

```bash
uqs new-job all_fills --kind normalizer \
    --subscribe-to trades,crypto_trades \
    --columns "sym:symbol, venue:symbol, size:float"
```

**Do not pass `--publishes`.** A normalizer publishes the table it is named for,
and the scaffold refuses the redundancy rather than reconciling it:

```
$ uqs new-job all_fills --kind normalizer --subscribe-to trades,crypto_trades --publishes all_fills ...
a normalizer publishes its own NAME - drop --publishes
```

So `--columns` describes the **canonical output**, and each subscribed table is
an input whose shape is read from the plant schema.

```
scaffold all_fills:
  create src/etl/streaming/all_fills.q (65 lines)
  ...
  note: implement .qpipe.job.all_fills.from_trades and its example
  note: implement .qpipe.job.all_fills.from_crypto_trades and its example
  note: start it with its producers: crypto_trades trades
```

**One note per source.** That is the shape of the work: a normalizer is not one
function, it is one function per input, each landing on the same output.

## What you get

The extra bulk is the part you would otherwise write by hand. For each
subscribed table, the scaffold copies **that table's real plant schema** into
the job:

```q
/ The canonical output. No `time`: the plant stamps it.
all_fills:([] sym:`symbol$(); venue:`symbol$(); size:`float$())

/ SCAFFOLDED. What the trades mapping reads - trades's whole plant schema.
/ Narrow it to the columns from_trades uses: a column it never touches still
/ breaks it when upstream changes that column.
trades:([] time:`timestamp$(); sym:`symbol$(); side:`long$();
           trade_price:`float$(); size:`float$(); pip_factor:`long$())

from_trades:{[batch]
    '"all_fills.from_trades: not implemented";
    }

.qetl.transform.define[`all_fills_from_trades;`inputs`output`fn`examples!(
    (enlist `trades)!enlist .qpipe.job.all_fills.trades;
    .qpipe.job.all_fills.all_fills;
    .qpipe.job.all_fills.from_trades;
    enlist `inputs`expected!( ... ))];
```

**Narrow the input schema.** The comment is the instruction: the scaffold copies
the *whole* upstream table because it cannot know which columns you need, and
leaving it whole means an upstream change to a column you never read breaks your
job. Deleting the columns `from_trades` does not touch is part of implementing
it, not tidying afterwards.

## What `.qetl.transform.define` adds

Each mapping is registered as a **transform with examples**, not just a
function. `inputs` and `output` are the shapes it promises; `examples` are
input/expected pairs checked when the tree loads. So a mapping that stops
producing the canonical shape fails at load, in the suite, rather than by
publishing a wrong-shaped row the plant discards silently.

That is why the note says "implement `from_trades` **and its example**". The
example is not a test you may skip --- `.qetl.transform.define` refuses a
transform whose examples are all empty.

## Then

Implement one mapping at a time and run `scripts/test.py q-unit` between each:
with several sources it is the only way to know which one broke. Replace
`tests/q/test_all_fills.q` entirely, and add `all_fills1` to a profile --- a
normalizer is nearly always a dependency rather than a leaf, so it usually
arrives in one by being what a leaf reads.
