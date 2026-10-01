# Reaction

A reaction recomputes something **when a bounded worker publishes a window**. It
is a handler that `.qetl.reaction` calls with the range just published, once per
window, after that window's coverage is recorded. Nothing polls, and nothing is
missed.

It is the only shape with **no process of its own**. It runs inside whichever
process publishes the dataset it watches, which is why the scaffold writes no
table, profile or port for it.

## Command

```bash
uqs job new rebuild_positions --triggered-by demo_deals --writes positions
```

`--triggered-by` is the dataset whose publication runs it, and it must be one a
**bounded worker** fills. Only a bounded worker's window announces itself
(`.qetl.job.bounded.do_window`); a streaming job's publication does not. So a
reaction on a table only streaming jobs write would load, register, and never
run, and the scaffold refuses it:

```
quote is published by streaming jobs only, and only a bounded worker's published
window fires a reaction (.qetl.job.bounded.do_window) - a reaction on it would
load and never run
```

The same rule holds beneath the scaffold: `.qetl.reaction.on`, `on_writing` and
`on_worker` refuse, at registration, a dataset that `.qetl.reaction.fillable[]`
does not list, so a reaction written or edited by hand cannot slip past it
either.

A handler reads what was published with `.qetl.reaction.published[]` and writes
its output with `.qetl.reaction.write[table;row_key;rows]`, which goes through
the publishing worker's own IO manager - into the HDB under `uqs backfill`, as
the worker's rows did. Never read the dataset by name, and never keep output in
a table of its own: a reaction runs inside the backfill process, which exits
when its range is done (#541). A write replaces what the table held for the
window being reacted to, matched by `row_key`, so re-publishing a window leaves
one answer per window rather than a second copy. The rows need a `time` column
inside that window, which is how the HDB writer partitions them and how the
window is cleared.

`--writes` is optional: the tables the handler writes. With it, the reaction
registers through `on_writing` and becomes a node in the job graph, where a
cycle is refused when the file loads. Writing the dataset it watches is refused
up front for that reason. Every option that shapes a process (`--procname`,
`--period`, `--profile`, `--columns`, ...) is refused rather than ignored.

```
scaffold rebuild_positions:
  create src/etl/reactions/rebuild_positions.q (24 lines)
  create tests/q/test_rebuild_positions.q (13 lines)
  append to tests/run_tests.q (1 line)
  note: implement .qpipe.job.rebuild_positions.handler, then replace the scaffolded test
  note: it runs inside deals_backfill1 - the process(es) that fill demo_deals
  note: --writes positions is asserted, not derived: the handler must write exactly that, and nothing checks it does
```

## The file

```q
\d .qpipe.job.rebuild_positions

/ Called with the dataset and the half-open range [range_from;range_to) just
/ published. Recompute exactly what that range changed, keyed by the window,
/ so a re-published window replaces its own rows instead of adding to them.
handler:{[dataset;range_from;range_to]
    '"rebuild_positions: not implemented";
    }

\d .

.qetl.reaction.on_writing[`demo_deals;`rebuild_positions;enlist `positions;.qpipe.job.rebuild_positions.handler];
```

`src/etl/init.q` loads `src/etl/reactions/` after every worker and streaming
job, and only when it holds a `.q` file, so a tree with no reactions loads as
before. A throwing handler **never fails the publication**: the failure is
recorded in `.qetl.reaction.history` and logged. So until the handler is
written, each window of `demo_deals` records a failed reaction, and the
scaffolded test fails.

**What `--writes` claims is not checked.** A handler can write anywhere, so the
graph edge is a promise (`derived` is `0b`, see `.qetl.reaction.on`). Where the
downstream work is a registered bounded worker, `.qetl.reaction.on_worker`
derives the edge instead; write that by hand in place of the scaffolded call.

A written one to copy from:
[`src/etl/reactions/rebuild_positions.q`](../../src/etl/reactions/rebuild_positions.q),
which keeps a net position per pair and window as `demo_deals` is published, and
its test drives it through the real worker.

[new-pipeline.md](../guides/new-pipeline.md#recomputing-on-an-upstream-publish)
has a worked handler and the dispatcher's guarantees.

## Listing and removing

`uqs list jobs` shows it with kind `reaction`, the dataset it reads, the process
it runs in, and `triggered`. `uqs job remove rebuild_positions` takes out the
file, the test and its `nsList` entry, and nothing else.
