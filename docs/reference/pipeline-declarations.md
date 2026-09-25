# Pipeline declarations

Every key each pipeline building block accepts: what it means, its type, what
happens when it is absent, and what the declaring function refuses. For a
walk-through that builds one of each, read [Adding a data
pipeline](../guides/new-pipeline.md); for why the blocks are shaped this way,
[the pipeline philosophy](../architecture/pipeline-philosophy.md).

  | Block             | Declared with            | Lives in                                                  | It says                                                |
  | ---               | ---                      | ---                                                       | ---                                                    |
  | source            | `.qsrc.define`           | `src/etl/sources/<source>.q`, namespace `.qfeed.<source>` | what the external rows are and how to fetch a window   |
  | transform         | `.qxf.define`            | beside the job that uses it                               | what rows become, with worked examples                 |
  | bounded worker    | `.qbw.define`            | `src/etl/workers/<worker>.q`, namespace `.qwrk.<worker>`  | which source, which transform, which dataset, how wide |
  | streaming job     | `.qstream.define`        | `src/etl/streaming/<job>.q`, namespace `.qsub.<job>`      | which tables it reads and writes, and its handlers     |
  | normalizer        | `.qnorm.define`          | `src/etl/streaming/<name>.q`, namespace `.qsub.<name>`    | many sources, one canonical table, a transform each    |

Every declaring function refuses a bad declaration **when the file loads**,
naming the key, so a mistake below surfaces the first time the tree is loaded
rather than part-way through a run.
`scripts/gates/check_declaration_reference.py` holds the key tables on this page
to the key lists in the q source, in both directions.

## Fixture, examples and the run spec are three different things

They are easy to confuse because each is "some rows written by hand".

  | Name          | Belongs to      | Is                                                                                                  | Used for                                                                                                |
  | ---           | ---             | ---                                                                                                 | ---                                                                                                     |
  | `fixture`     | a source        | a function of no arguments returning a synthetic table in the source's shape                        | standing in for the live source when no credential is configured, and checked against the same contract |
  | `examples`    | a transform     | hand-written pairs of input tables and the output table expected from them                          | proving the transform computes what it claims, on every run of the suite                                |
  | the run spec  | one worker run  | `source_version`, `range_from`, `range_to`                                                          | saying which range to fill and which release of the source the coverage is recorded under               |

A worker's transform often uses its source's fixture as its example input
(`demo_deals_backfill.q` does), which is convenient, and is also why the two get
mixed up: the fixture is the *source's* stand-in, the example is the
*transform's* test.

## Source --- `.qsrc.define`

`.qsrc.define[source;decl]`, conventionally at the bottom of the source file as
`.qsrc.define[source_name; ...]`.

  | key           | required | type                                           | meaning                                                                                                                                                                                                                                | refused when                                                                           |
  | ---           | ---      | ---                                            | ---                                                                                                                                                                                                                                    | ---                                                                                    |
  | `source`      | yes      | symbol                                         | the source's own name, the same word as its `.qfeed.<source>` namespace and `source_name`                                                                                                                                              | missing                                                                                |
  | `table_name`  | yes      | symbol                                         | the table's name **on the external side**. `.qsrc.validate_live` reads that table's metadata, and the job graph draws the edge as `<source>@<table>`                                                                                   | missing                                                                                |
  | `target`      | yes      | symbol                                         | the **local** table the rows land in                                                                                                                                                                                                   | missing                                                                                |
  | `columns`     | yes      | symbol vector                                  | the columns this adapter READS --- not every column the source has, because a declared column the worker never uses still breaks the run when it changes                                                                               | not symbols                                                                            |
  | `types`       | yes      | string                                         | one q type character per field, e.g. `"psf"`. An upper-case character declares a column of vectors                                                                                                                                     | not a string, or not one per field                                                     |
  | `time_column` | yes      | symbol                                         | the column windows are cut on                                                                                                                                                                                                          | not in `columns`, or its type is not `"p"` (a `datetime` rounds sub-second values)     |
  | `row_key`     | yes      | symbol or symbol vector                        | the column(s) that identify a row uniquely. Only right if the source guarantees it. Validated and stored, not yet used by any read                                                                                                     | not symbols, or names a column not in `columns`                                        |
  | `query`       | yes      | lambda `{[h;range_from;range_to] ...}`         | fetches one window over the handle `h`. Parameterised, never built by string concatenation, and half-open: `>=` the lower bound, `<` the upper                                                                                         | not a lambda                                                                           |
  | `fixture`     | yes      | lambda `{[] ...}`                              | a deterministic synthetic table of the same shape. Used when `UQF_SOURCE_CRED_<SOURCE>` is unset, which is the declared demo path and never a fallback for a failed connection; windowed on `time_column` exactly as the live query is | not a lambda                                                                           |
  | `tz`          | yes      | symbol                                         | the zone `time_column` is expressed in: `` `UTC `` or a tz-database name such as `` `$"Europe/London" ``                                                                                                                               | not a single symbol                                                                    |
  | `transport`   | no       | `` `ipc `` or `` `odbc ``                      | how the credential is opened. `ipc` (default): `host:port`, opened with `hopen`. `odbc`: a connection string, opened with `.qodbc.open`                                                                                                | anything else                                                                          |

The credential is read from the environment variable `UQF_SOURCE_CRED_` plus the
source name upper-cased, and from nowhere else.

## Transform --- `.qxf.define`

`.qxf.define[name;decl]`. A transform is the one deterministic step of a job:
the fetch before it and the publish after it are effects, it is not.

  | key        | required | type                                                   | meaning                                                                                                                                                                                                   | refused when                                                                                          |
  | ---        | ---      | ---                                                    | ---                                                                                                                                                                                                       | ---                                                                                                   |
  | `inputs`   | yes      | dict: name -> empty typed table                        | every table the transform reads, and the exact schema of each. A caller handing it a mistyped or widened table is refused before `fn` runs                                                                | not a symbol-keyed dict of unkeyed tables, or empty                                                   |
  | `output`   | yes      | empty typed table                                      | the exact columns that come out, **in order** --- rows are published positionally, so order is part of the schema                                                                                         | not an unkeyed table                                                                                  |
  | `fn`       | yes      | function                                               | takes the inputs as arguments, in the order `inputs` declares them, plus the instant as a last argument when `as_of` is set                                                                               | not a function, or a lambda whose argument count is not `count inputs` (+1 with `as_of`)              |
  | `examples` | yes      | list of dicts `` `inputs`expected `` (+ `` `as_of ``)  | hand-written: `inputs` keyed by exactly the declared input names, `expected` the table the transform must return for them                                                                                 | empty, an example whose tables do not match the declared schemas, or no example carrying any rows     |
  | `as_of`    | no       | boolean, default `0b`                                  | `1b` when `fn` needs the current instant. The caller reads the clock once and passes it in, so the transform stays a function of its arguments; each example then states the timestamp it was written for | not a boolean, or an example without an `as_of` timestamp                                             |

`tests/q/test_transform.q` verifies every registered transform on every run of
the suite. For each example it checks that the output has the declared schema,
matches `expected` (floats within `1e-9`, row order significant) and is
identical on a second call --- which catches a transform that reads `.z.p` or
draws a random number. It also feeds every transform all-empty inputs and
expects an empty table of the declared schema.

A job that copies rows unchanged still declares its transform, with
`.qxf.passthrough[name;input_name;schema;rows]`: one input read under
`input_name`, output equal to input, `rows` as the example.

## Bounded worker --- `.qbw.define`

`.qbw.define[worker;cfg]`, at the bottom of the worker file.

  | key         | required | type                              | meaning                                                                                                                                                                                                                                                               | refused when                                                                                                                        |
  | ---         | ---      | ---                               | ---                                                                                                                                                                                                                                                                   | ---                                                                                                                                 |
  | `source`    | yes      | symbol                            | a source registered with `.qsrc.define`, which must have loaded first (`init.q` loads `sources/` before `workers/`)                                                                                                                                                   | not registered                                                                                                                      |
  | `dataset`   | yes      | symbol                            | the name completeness is recorded under: coverage, materialisation metadata and `.qreact` reactions are all keyed by it. The rows themselves land in the **source's `target`**, which is a different key --- usually the same word, not necessarily                   | another worker already declares the same `dataset` and `partition`                                                                  |
  | `width`     | yes      | timespan, e.g. `1D`               | how wide each window is. A run over `[from;to)` is cut into windows this wide, oldest first, and each is fetched, checked and recorded separately                                                                                                                     | not a timespan, or not positive                                                                                                     |
  | `transform` | yes      | symbol                            | a transform declared with `.qxf.define`. It must read exactly one input, whose schema is the source's `columns` and `types`                                                                                                                                           | not registered, more than one input, takes `as_of` (a window has no single instant), or its input is not the source's contract      |
  | `check`     | no       | lambda `{[batch] ...}`            | the data-quality gate, run on the transformed batch before publish. Returns a table of failures (`check`, `status`, `detail`); empty means the batch passed. A failed batch is not published, its window is not recorded as covered, and the next run plans it again  | at run time, when it is not a lambda or does not return a table                                                                     |
  | `io`        | no       | dict with `write`                 | where rows are written. `write` is a function `{[target;batch] ...}` returning the row count. Default `.qio.memory`, an in-process table; `.qio.discard` counts rows and stores nothing                                                                               | not a dict carrying a `write` lambda                                                                                                |
  | `facts`     | no       | function `batch -> dict`          | labels recorded with each window's materialisation, beside the `rows`, `source_version` and `dry_run` the framework records itself --- the min and max time, a null fraction, a checksum. A failing `facts` is logged and does not fail the window                    | --- (errors are logged, not thrown)                                                                                                 |
  | `partition` | no       | symbol, default `` ` ``           | lets several workers fill one dataset: each declares a different partition, and coverage is recorded per `(dataset, partition)`. `` ` `` means the dataset has no partition dimension                                                                                 | not a symbol                                                                                                                        |
  | `procname`  | no       | symbol, default `` `<worker>1 ``  | the process that runs this worker. The `uqs` process registry is read from it                                                                                                                                                                                         | not a symbol                                                                                                                        |
  | `note`      | no       | string, default `""`              | why it is deployed as it is, shown in [`processes.md`](processes.md)                                                                                                                                                                                                  | not a string                                                                                                                        |

`ns` is **not** a key: the namespace is always `.qwrk.<worker>`, and a supplied
`ns` is refused. `define` writes the lifecycle methods (`init`, `plan`, `fetch`,
`publish`, `checkpoint`, `spec`, `run`, `cleanup`) and the run state into that
namespace; a worker overrides a method by defining it there *before* calling
`define`.

A worker has no `start_with_all`: a backfill runs its range and exits, so it
never starts with the stack.

### The run spec

What one run of a worker is asked to do, passed to `.qwrk.<worker>.init` --- or
by `uqs backfill <worker> --version V --from F --to T`.

  | key              | type      | meaning                                                                                                                         | refused when                  |
  | ---              | ---       | ---                                                                                                                             | ---                           |
  | `source_version` | symbol    | which release of the upstream data this run's coverage is recorded under. Coverage under one release says nothing about another | null                          |
  | `range_from`     | timestamp | inclusive start                                                                                                                 | not before `range_to`         |
  | `range_to`       | timestamp | exclusive end                                                                                                                   | not after `range_from`        |

Setting `UQF_DRY_RUN` makes a run publish no rows, record no coverage and write
no checkpoint.

## Streaming job --- `.qstream.define`

`.qstream.define[job;decl]`, at the bottom of the job file. A streaming job runs
continuously in a TorQ process, reading tickerplant tables and publishing
others.

  | key                 | required                            | type                              | meaning                                                                                                                                                                             | refused when                                                    |
  | ---                 | ---                                 | ---                               | ---                                                                                                                                                                                 | ---                                                             |
  | `procname`          | yes                                 | symbol                            | the TorQ process that runs this job. One generic process script serves every job, and the name it was started under decides which                                                   | not a symbol, or another job already claims it                  |
  | `subscribe_to`      | yes                                 | symbol or symbol list             | the tickerplant tables it reads. Empty (`` `symbol$() ``) for a feed, which reads nothing and produces rows on a timer                                                              | not symbols                                                     |
  | `publishes`         | yes                                 | symbol or symbol list             | the tables it writes to the tickerplant. Empty for a job that keeps its output in its own process                                                                                   | not symbols                                                     |
  | `on_batch`          | when `subscribe_to` is non-empty    | function `{[t;x] ...}`            | called with every batch: `t` is the **table name** the batch arrived on (a symbol, not the rows), `x` the rows, carrying the `time` column the tickerplant stamped                  | not callable, or absent while the job subscribes                |
  | `period`            | with `on_timer`                     | timespan, e.g. `0D00:00:01`       | how often `on_timer` runs                                                                                                                                                           | not a positive timespan, or declared without `on_timer`         |
  | `on_timer`          | with `period`                       | function `{[] ...}`               | called every `period`, with nothing: whatever it needs between ticks is state in its own namespace                                                                                  | not callable, or declared without `period`                      |
  | `start_with_all`    | no                                  | boolean, default `0b`             | `1b` to start with the stack. Off by default because every started process spends one of the plant's licensed connections                                                           | not a boolean                                                   |
  | `note`              | no                                  | string                            | why it is deployed as it is, shown in [`processes.md`](processes.md)                                                                                                                | not a string                                                    |

A job must declare `on_batch`, `on_timer` or both; one with neither would run
nothing and is refused.

The namespace is always `.qsub.<job>`. A job publishes by calling its own
`publish`, never `.u.upd`: `publish` starts as a stub that throws, and
`.qstream.wire` points it at the tickerplant (the runner) or at a recorder (a
test). Never publish a `time` column --- the tickerplant stamps its own.

## Normalizer --- `.qnorm.define`

`.qnorm.define[name;decl]`. Several source tables mapped onto one canonical
table, one declared transform per source. `name` is also the canonical table the
normalizer publishes and its `.qsub.<name>` namespace.

  | key              | required | type                                         | meaning                                                                                                                  | refused when                                                                                                                                     |
  | ---              | ---      | ---                                          | ---                                                                                                                      | ---                                                                                                                                              |
  | `procname`       | yes      | symbol                                       | the TorQ process that runs it, as for a streaming job                                                                    | as for a streaming job                                                                                                                           |
  | `output`         | yes      | empty typed table                            | the canonical table's schema, without `time`                                                                             | not an unkeyed table, no columns, or a `time` column                                                                                             |
  | `input`          | yes      | dict: source table -> transform name         | for each tickerplant table it reads, the `.qxf` transform that maps a batch of it onto `output`                          | empty; a transform not registered, taking more than one input, or whose `output` is not exactly the canonical table --- columns, order and types |
  | `start_with_all` | no       | boolean, default `0b`                        | as for a streaming job                                                                                                   | not a boolean                                                                                                                                    |
  | `note`           | no       | string                                       | as for a streaming job                                                                                                   | not a string                                                                                                                                     |

`define` registers the streaming job itself: `subscribe_to` is the keys of
`input`, `publishes` is `name`, and `on_batch` is a dispatcher that trims each
batch to the columns its transform declares, applies it, and publishes. The
normalizer's file never handles a batch.

## Reactions --- `.qreact`

Running something when a dataset is published, rather than on a timer. A
reaction fires after a bounded worker publishes a window of `dataset` --- the
one path that notifies, and not on a dry run --- with that window's range.

  | call                                                  | handler                                                          | the job graph learns                                                   |
  | ---                                                   | ---                                                              | ---                                                                    |
  | `.qreact.on[dataset;name;handler]`                    | `{[dataset;range_from;range_to] ...}`                            | only that it reads `dataset`                                           |
  | `.qreact.on_writing[dataset;name;outputs;handler]`    | the same                                                         | that it reads `dataset` and writes `outputs`, as asserted              |
  | `.qreact.on_worker[dataset;worker;spec_fn]`           | `spec_fn` is `{[range_from;range_to] ...}` returning a run spec  | that it writes the worker's `dataset`, derived from its declaration    |

Prefer `on_worker` when the downstream work is itself a worker: its edge in the
graph is read from the worker's declaration rather than asserted. [Recomputing a
table when the one it reads is
published](../guides/new-pipeline.md#recomputing-a-table-when-the-one-it-reads-is-published)
covers when to use which.

## The job graph is derived, not declared

`.qdag.register[job;spec]` exists, but no job file calls it. The graph is
assembled from the declarations above, so a job's edges cannot disagree with
what it does:

  | call                      | registers                                                                                                                                                                                 |
  | ---                       | ---                                                                                                                                                                                       |
  | `.qdag.adopt_workers[]`   | every bounded worker, reading `<source>@<table>` and writing the source's `target`                                                                                                        |
  | `.qdag.adopt_pipelines[]` | every streaming job and normalizer, from `src/etl/generated/pipeline_dag.q` --- generated from the `uqs` process registry, which is itself read from their `subscribe_to` and `publishes` |
  | `.qdag.adopt_feeders[]`   | every continuous feeder                                                                                                                                                                   |
  | `.qdag.adopt_reactions[]` | every `.qreact` reaction, with the edges its call declares                                                                                                                                |

`.qdag.adopt_all[]` runs all four, reactions last.
