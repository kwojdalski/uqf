# Pipeline declarations

Every key each pipeline building block accepts: what it means, its type, what
happens when it is absent, and what the declaring function refuses. For a
walk-through that builds one of each, read [Adding a data
pipeline](../guides/new-pipeline.md); for why the blocks are shaped this way,
[the pipeline philosophy](../architecture/pipeline-philosophy.md).

  | Block             | Declared with                         | Lives in                                                         | It says                                                |
  | ---               | ---                                   | ---                                                              | ---                                                    |
  | source            | `.qetl.source.define`                 | `src/etl/sources/<source>.q`, namespace `.qpipe.source.<source>` | what the external rows are and how to fetch a window   |
  | transform         | `.qetl.transform.define`              | beside the job that uses it                                      | what rows become, with worked examples                 |
  | bounded worker    | `.qetl.job.bounded.define`            | `src/etl/workers/<worker>.q`, namespace `.qpipe.job.<worker>`    | which source, which transform, which dataset, how wide |
  | streaming job     | `.qetl.job.stream.define`             | `src/etl/streaming/<job>.q`, namespace `.qpipe.job.<job>`        | which tables it reads and writes, and its handlers     |
  | normalizer        | `.qetl.job.stream.normalize`          | `src/etl/streaming/<name>.q`, namespace `.qpipe.job.<name>`      | many sources, one canonical table, a transform each    |

The namespaces separate framework machinery from concrete pipelines:

- `.qetl` owns contracts, registries and execution services.
- `.qpipe` owns concrete sources, jobs and shared transforms.
- `.qtorq` is the TorQ adapter in `scripts/processes/torq_pipeline.q`.

Bounded and streaming jobs share `.qpipe.job.<name>`, so their names must be
unique across execution modes. A normalizer is a streaming-job constructor, not
a third lifecycle. Its dispatcher and mapping registry live under
`.qetl.job.stream.normalizer`.

A private transform may stay beside its job. A shared transform belongs in
`src/etl/transforms/<name>.q`, under `.qpipe.transform.<name>`; the loader reads
sources, shared transforms, bounded workers and streaming jobs in that order.
For example, both Databento jobs use the `eq_orderbook` transform without
requiring either job to own the other's computation.

Framework services use the same root: `.qetl.io`, `.qetl.dag`, `.qetl.reaction`,
`.qetl.coverage`, `.qetl.run`, `.qetl.retention`, `.qetl.cfg`,
`.qetl.cfg.audit`, `.qetl.hb` and `.qetl.status`. Standard IO managers stay
under `.qetl.io`; a custom pipeline writer can live under `.qpipe.io.<name>`. A
custom reaction handler can live under `.qpipe.reaction.<name>`. The graph is
derived from declarations, so there is no separate pipeline graph to maintain.

The declaration verbs and `uqs job new --kind` values are one table,
`python/uqs/src/uqs/model/kinds.py`; the parser, `uqs job remove` and the
choices read it, and `python/uqs/tests/test_job_kinds.py` names any place that
lacks a verb q registers. A new kind starts there.

Every declaring function refuses a bad declaration **when the file loads**,
naming the key, so a mistake below surfaces the first time the tree is loaded
rather than part-way through a run.
`scripts/gates/check_declaration_reference.py` holds the key tables on this page
to the key lists in the q source, in both directions.

## Fixture, examples and run spec

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

## Source --- `.qetl.source.define`

`.qetl.source.define[source;decl]`, conventionally at the bottom of the source
file as `.qetl.source.define[source_name; ...]`.

  | key           | required | type                                                 | meaning                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | refused when                                                                                   |
  | ---           | ---      | ---                                                  | ---                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | ---                                                                                            |
  | `source`      | yes      | symbol                                               | the source's own name, the same word as its `.qpipe.source.<source>` namespace and `source_name`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | missing                                                                                        |
  | `table_name`  | yes      | symbol                                               | the table's name **on the external side**. `.qetl.source.validate_live` reads that table's metadata, and the job graph draws the edge as `<source>@<table>`                                                                                                                                                                                                                                                                                                                                                                                                                                  | missing                                                                                        |
  | `target`      | yes      | symbol                                               | the **local** table the rows land in                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | missing                                                                                        |
  | `columns`     | yes      | symbol vector                                        | the columns this adapter READS --- not every column the source has, because a declared column the worker never uses still breaks the run when it changes                                                                                                                                                                                                                                                                                                                                                                                                                                     | not symbols                                                                                    |
  | `types`       | yes      | string                                               | one q type character per field, e.g. `"psf"`. An upper-case character declares a column of vectors                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | not a string, or not one per field                                                             |
  | `time_column` | yes      | symbol                                               | the column windows are cut on                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | not in `columns`, or its type is not `"p"` (a `datetime` rounds sub-second values)             |
  | `row_key`     | yes      | symbol or symbol vector                              | the column(s) that identify a row uniquely. Only right if the source guarantees it. A worker's `on_conflict` matches incoming rows to rows already written by it                                                                                                                                                                                                                                                                                                                                                                                                                             | not symbols, or names a column not in `columns`                                                |
  | `query`       | yes      | lambda `{[h;range_from;range_to] ...}`               | fetches one window over the handle `h`. Parameterised, never built by string concatenation, and half-open: `>=` the lower bound, `<` the upper                                                                                                                                                                                                                                                                                                                                                                                                                                               | not a lambda                                                                                   |
  | `fixture`     | yes      | lambda `{[] ...}`                                    | a deterministic synthetic table of the same shape. Used when `UQF_SOURCE_CRED_<SOURCE>` is unset, which is the declared demo path and never a fallback for a failed connection; windowed on `time_column` exactly as the live query is                                                                                                                                                                                                                                                                                                                                                       | not a lambda                                                                                   |
  | `tz`          | yes      | symbol                                               | the zone `time_column` is expressed in: `` `UTC `` or a tz-database name such as `` `$"Europe/London" ``                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | not a single symbol                                                                            |
  | `transport`   | no       | `` `ipc ``, `` `odbc ``, `` `local `` or `` `mock `` | how the credential is opened. `ipc` (default): `host:port`, opened with `hopen`. `odbc`: a connection string, opened with `.qetl.io.odbc.open`. `local`: an HDB directory's path, checked by `.qetl.source.local_root` and read from its files through `.qetl.source.local`. `mock`: an integer seed, read by `.qetl.source.mock_seed`; the query generates the window's rows from it                                                                                                                                                                                                        | anything else                                                                                  |
  | `supporting`  | no       | dict: input name -> empty typed table                | further inputs the source hands its worker beside the primary one (#617), each with its contract. With it, `query` and `fixture` return a dict: `table_name` -> the window's rows, and each supporting name -> its rows. Only the primary is cut to the window and counted; a supporting input is context, validated against its contract but never windowed, and a live check reads its metadata by its name. The worker's transform must then read exactly these inputs, by name                                                                                                           | not a dict of tables, names `table_name`, or declared by a source whose `tz` is not `` `UTC `` |

## Where a source connects: `sources.csv`

What a live source connects to comes from `sources.csv`, one row per source
(#718). The file is TorQ configuration, so TorQ picks it the way it picks
`process.csv`, with `.proc.getconfigfile`. It takes the first of these that
exists, and the file it picks replaces the others whole:

  | layer       | file                                                       | owner                                                   |
  | ---         | ---                                                        | ---                                                     |
  | application | `$KDBAPPCONFIG/sources.csv`                                | the operator; gitignored, machine-specific              |
  | service     | `$KDBSERVCONFIG/sources.csv` (`scripts/torqconfig/`)       | this tree; a header and no rows                         |
  | base        | `$KDBCONFIG/sources.csv`                                   | TorQ; there is none                                     |

Its columns are exactly `source,transport,setting,secret_env`:

- `transport` must match the source's declaration.
- `setting` is the string the transport's `open` takes. That's a directory for
  `local`, `host:port` for `ipc`, or a connection string for `odbc`.
- A setting may use `${UQF_ROOT}`, `${TORQDATA}`, `${KDBHDB}` and `${KDBWDB}`
  (`.qtorq.source_settings_path_vars`).
- A secret is never written in the file. The setting says `{secret}`, and
  `secret_env` names the environment variable to fill it from when the source
  connects.

```csv
source,transport,setting,secret_env
hdb_transfer,local,${KDBHDB},
duckdb_deals,odbc,DRIVER=DuckDB;Database=/data/deals.duckdb;access_mode=READ_ONLY,
upstream_trades,ipc,db1:5010:svc:{secret},UPSTREAM_TRADES_PWD
```

`.qtorq.load_source_settings` reads the file when a TorQ process loads the tree.
A process with no file runs every source as before.

**These stop the process before any window is fetched:**

- a malformed file: a column missing or extra, a row with no source, transport
  or setting, an unknown transport, a source listed twice, or a password written
  inline;
- a row that cannot resolve when its source connects: the `SCAFFOLDED` stub, a
  transport the source doesn't declare, a `${VAR}` outside the list, or an unset
  `secret_env`. `uqs backfill --mode validate` checks these too.

**A row makes its source live.** A broken row fails the run; it never falls back
to the fixture.

**Overrides.** `UQF_SOURCE_CRED_` plus the source name upper-cased, when set,
wins over the row, for CI, containers and one-off runs. A plain q process that
loads no settings file reads only that variable.

**Commands.** `uqs config sources` shows the file the stack reads and, for each
row, where its credential comes from and what would stop it resolving. Secrets
show only as set or not set. `uqs config sources stub SOURCE` adds a
`SCAFFOLDED` row to the application layer's file, first copying the file the
stack reads now, so no row it had is hidden. It never changes a row that is
already there.

Fleet credentials are separate: kdb+ IPC between the stack's own processes still
uses TorQ's `passwords/` files and `.servers.USERPASS`.

## Transform --- `.qetl.transform.define`

`.qetl.transform.define[name;decl]`. A transform is the one deterministic step
of a job: the fetch before it and the publish after it are effects, it is not.

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
`.qetl.transform.passthrough[name;input_name;schema;rows]`: one input read under
`input_name`, output equal to input, `rows` as the example.

## Bounded worker --- `.qetl.job.bounded.define`

`.qetl.job.bounded.define[worker;decl]`, at the bottom of the worker file.

  | key              | required | type                                                                         | meaning                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | refused when                                                                                                                        |
  | ---              | ---      | ---                                                                          | ---                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | ---                                                                                                                                 |
  | `source`         | yes      | symbol                                                                       | a source registered with `.qetl.source.define`, which must have loaded first (`init.q` loads `sources/` before `workers/`)                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | not registered                                                                                                                      |
  | `dataset`        | yes      | symbol                                                                       | the name completeness is recorded under: coverage, materialisation metadata and `.qetl.reaction` reactions are all keyed by it. The rows themselves land in the **source's `target`**, which is a different key --- usually the same word, not necessarily                                                                                                                                                                                                                                                                                                                                 | another worker already declares the same `dataset` and `partition`                                                                  |
  | `width`          | yes      | timespan, e.g. `1D`                                                          | how wide each window is. A run over `[from;to)` is cut into windows this wide, oldest first, and each is fetched, checked and recorded separately                                                                                                                                                                                                                                                                                                                                                                                                                                          | not a timespan, or not positive                                                                                                     |
  | `transform`      | yes      | symbol                                                                       | a transform declared with `.qetl.transform.define`. It must read exactly one input, whose schema is the source's `columns` and `types`                                                                                                                                                                                                                                                                                                                                                                                                                                                     | not registered, more than one input, takes `as_of` (a window has no single instant), or its input is not the source's contract      |
  | `check`          | no       | lambda `{[batch] ...}`                                                       | the data-quality gate, run on the transformed batch before publish. Returns a table of failures (`check`, `status`, `detail`); empty means the batch passed. A failed batch is not published, its window is not recorded as covered, and the next run plans it again                                                                                                                                                                                                                                                                                                                       | at run time, when it is not a lambda or does not return a table                                                                     |
  | `io`             | no       | dict with `write`, optionally `write_keyed`, `flush`, `finish` and `recover` | where rows are written. `write` is a function `{[target;batch] ...}` returning the row count; `write_keyed` takes the run's `on_conflict` too, and without it only `append` is possible; `flush` finishes what lies before each window's end, `finish` runs once after a run's last window, and `recover` repairs what an interrupted run left. Absent, the process's default applies: `.qetl.io.memory` (an in-process table) in plain q, and `.qetl.io.hdb` - each row into the HDB partition of its own date - in a backfill process. `.qetl.io.discard` counts rows and stores nothing | not a dict carrying a callable `write`, or a `finish` that is not callable                                                          |
  | `facts`          | no       | function `batch -> dict`                                                     | labels recorded with each window's materialisation, beside the `rows`, `source_version` and `dry_run` the framework records itself --- the min and max time, a null fraction, a checksum. A failing `facts` is logged and does not fail the window. A dry run computes them and logs them, and records none                                                                                                                                                                                                                                                                                | --- (errors are logged, not thrown)                                                                                                 |
  | `partition`      | no       | symbol, default `` ` ``                                                      | lets several workers fill one dataset: each declares a different partition, and coverage is recorded per `(dataset, partition)`. `` ` `` means the dataset has no partition dimension                                                                                                                                                                                                                                                                                                                                                                                                      | not a symbol                                                                                                                        |
  | `procname`       | no       | symbol, default `` `<worker>1 ``                                             | the process that runs this worker. The `uqs` process registry is read from it                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | not a symbol                                                                                                                        |
  | `note`           | no       | string, default `""`                                                         | why it is deployed as it is, shown in [`processes.md`](processes.md)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | not a string                                                                                                                        |
  | `on_conflict`    | no       | symbol, default `` `upsert ``                                                | what a write does with a row whose `target_key` is already in the target: `upsert` replaces it and adds new keys; `replace` also deletes what the target held inside the window and the batch left out; `ignore` keeps the row already there; `append` writes it again (no check); `fail` fails the window. Matched per date partition in the HDB. `uqs backfill --on-conflict` overrides it for one run                                                                                                                                                                                   | not one of those five                                                                                                               |
  | `source_version` | no       | symbol, default none                                                         | the release a run records coverage under when `uqs backfill` is given no `--version`. Declare it for a source that is never restated (a recording, an append-only tape, historical market data); leave it out where the source can be corrected, so every run must name its release --- a default there files a restatement under the old version, and every window reads as already covered. A new `--version` always re-fetches covered windows                                                                                                                                          | not a symbol                                                                                                                        |
  | `target_key`     | no       | symbol or symbol vector, default the source `row_key`                        | the columns `on_conflict` matches rows by, named as the transform OUTPUTS them. Declare it when the transform renames a key column: `upstream_trades_backfill` declares `venue` where its source key says `ex`                                                                                                                                                                                                                                                                                                                                                                             | not symbols, or names a column the transform's declared `output` lacks (including a defaulted source key the transform renamed)     |
  | `window_column`  | no       | symbol, default the source `time_column`                                     | the OUTPUT column that carries the window's time. `replace` clears a window by it, so it must be the column the window was cut on. Declare it when the transform moves the time: `hdb_demo_markouts_backfill` outputs `time` as trade_time+horizon and declares `trade_time`. Undeclared it is the source's time column if the output keeps it, else `time`; a worker with neither refuses `replace` at plan time                                                                                                                                                                          | not an output column; at run time, `replace` on a worker with no such column                                                        |

`ns` is **not** a key: the namespace is always `.qpipe.job.<worker>`, and a
supplied `ns` is refused. `define` writes the lifecycle methods (`init`, `plan`,
`fetch`, `publish`, `checkpoint`, `spec`, `run`, `cleanup`) and the run state
into that namespace; a worker overrides a method by defining it there *before*
calling `define`.

A worker has no `start_with_all`: a backfill runs its range and exits, so it
never starts with the stack.

### Run spec

What one run of a worker is asked to do, passed to `.qpipe.job.<worker>.init` ---
or by `uqs backfill <worker> --version V --from F --to T`.

  | key              | type      | meaning                                                                                                                         | refused when                  |
  | ---              | ---       | ---                                                                                                                             | ---                           |
  | `source_version` | symbol    | which release of the upstream data this run's coverage is recorded under. Coverage under one release says nothing about another | null                          |
  | `range_from`     | timestamp | inclusive start                                                                                                                 | not before `range_to`         |
  | `range_to`       | timestamp | exclusive end                                                                                                                   | not after `range_from`        |

A run has a mode, set by `uqs backfill --mode M` (or `UQF_MODE`). Each opens and
writes strictly more than the one before:

  | mode       | reads config & code | reads the local ledgers | queries the source | writes anything durable |
  | ---        | ---                 | ---                     | ---                | ---                     |
  | `validate` | yes                 | no                      | no                 | no                      |
  | `plan`     | yes                 | read-only               | no                 | no                      |
  | `dry_run`  | yes                 | yes                     | yes                | no                      |
  | `run`      | yes                 | yes                     | yes                | yes                     |

`validate` checks the declaration, contract, fixture, range and conflict
strategy, and reports whether a credential is set without using it. `plan` lists
the windows a run would fetch now, from coverage and the checkpoint. `dry_run`
fetches, transforms and checks every window, and records nothing: no rows,
coverage, checkpoint, `etl_runs` row or facts, no reactions, and no HDB finish
or reload. Every one of those effects is named in
`.qetl.job.bounded.runtime.suppressed_in_dry_run`, and a new effect must be
added there to be gated at all; `tests/q/test_every_worker_runs.q` compares
everything durable before and after a dry run of every worker, so a write that
slips past the list fails it. `UQF_DRY_RUN=true` still means `dry_run`, and is
refused together with an explicit `run`. `uqs backfill --on-conflict S` (or
`UQF_ON_CONFLICT`) runs it under strategy `S` instead of the worker's
`on_conflict`. A window whose write fails --- a `fail` clash, or rows the store
refuses --- fails on its own: nothing covered, planned again next run, and the
run goes on.

In the HDB, a keyed write (any `on_conflict` but `append`) writes each date's
rewritten table whole into `<hdb>.staging/new/` first, and only once every date
is staged renames them into their partitions. A failure before that leaves the
HDB untouched, and a reload never maps a half-written table. A run killed
mid-write is put right by the next run's recovery, which sweeps that table's
staging: a table swapped out and never replaced is restored, and a staged one
never swapped in is discarded. Recovery also trims a partition an interrupted
`append` left with columns of different lengths back to its shortest column,
then re-finishes it. The window was never covered, so the next run writes its
rows again.

## Streaming job --- `.qetl.job.stream.define`

`.qetl.job.stream.define[job;decl]`, at the bottom of the job file. A streaming
job runs continuously in a TorQ process, reading tickerplant tables and
publishing others.

  | key                 | required                            | type                                        | meaning                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | refused when                                                                                 |
  | ---                 | ---                                 | ---                                         | ---                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               | ---                                                                                          |
  | `procname`          | yes                                 | symbol                                      | the TorQ process that runs this job. One generic process script serves every job, and the name it was started under decides which                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | not a symbol, or another job already claims it                                               |
  | `subscribe_to`      | yes                                 | symbol or symbol list                       | the tickerplant tables it reads. Empty (`` `symbol$() ``) for a feed, which reads nothing and produces rows on a timer                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | not symbols                                                                                  |
  | `publishes`         | yes                                 | symbol or symbol list                       | the tables it writes to the tickerplant. Empty for a job that keeps its output in its own process                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | not symbols                                                                                  |
  | `on_batch`          | when `subscribe_to` is non-empty    | function `{[t;x] ...}`                      | called with every batch: `t` is the **table name** the batch arrived on (a symbol, not the rows), `x` the rows, carrying the `time` column the tickerplant stamped                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | not callable, or absent while the job subscribes                                             |
  | `period`            | with `on_timer`                     | timespan, e.g. `0D00:00:01`                 | how often `on_timer` runs                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | not a positive timespan, or declared without `on_timer`                                      |
  | `on_timer`          | with `period`                       | function `{[] ...}`                         | called every `period`, with nothing: whatever it needs between ticks is state in its own namespace                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | not callable, or declared without `period`                                                   |
  | `start_with_all`    | no                                  | boolean, default `0b`                       | `1b` to start with the stack. Off by default because every started process spends one of the plant's licensed connections                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | not a boolean                                                                                |
  | `note`              | no                                  | string                                      | why it is deployed as it is, shown in [`processes.md`](processes.md)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | not a string                                                                                 |
  | `transform`         | no                                  | symbol                                      | the registered transform the job applies. Its backfill twin applies the same one, `tests/q/test_twins.q` reads it, and `uqs job new --twin-of` scaffolds it into the twin                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | not a symbol, or not a registered transform                                                  |
  | `replay`            | no                                  | boolean, default `0b`                       | `1b` to rebuild state from the day's tickerplant log at start, before live batches. Publish is muted during the replay (`.qetl.job.stream.replaying` is `1b`), so what was published before the restart is not published again. A time-based rule on a row reads `.qetl.job.stream.clock_of[now;rows]`, the process clock live and the row's receipt `time` on replay, so a restart decides as the running process did (#994; `tests/q/test_replay_equivalence.q` checks it). `posbook`, `fx_positions` and `kafka_flow` declare it                                                                                                                                                                                                                               | not a boolean, or declared without `on_batch`                                                |
  | `restore_from`      | with `replay 1b`                    | symbol list                                 | tables subscribed to, and replayed, only to restore state - not inputs, so the job graph draws no cycle when a job reads its own output back (`kafka_flow` restores its marks from `client_flow`)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | not symbols, or declared without `replay 1b` or `on_batch`                                   |
  | `on_replayed`       | with `replay 1b`                    | function `{[] ...}`                         | called once the replay ends, with publish live again, for what the replay left owed (`kafka_flow` publishes the rows that arrived while it was down)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | not callable, or declared without `replay 1b`                                                |
  | `on_endofday`       | no                                  | function `{[dt] ...}`                       | called with the date that ended, after the plant has rolled its log, so what it publishes opens the new day's log. A position book publishes its opening snapshot there, and `restore_from` replays it after a restart (#943). Trapped and logged: one job's failure doesn't stop the others'                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | not callable                                                                                 |
  | `on_start`          | no                                  | function `{[] ...}`                         | called by `start` before anything is opened, for what a running job needs and a declaration cannot hold - `fx_positions` loads its limits file here (#1112). A throw stops the job from starting                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | not callable                                                                                 |
  | `state`             | no                                  | symbol list                                 | the job's private variables in `.qpipe.job.<name>`. Their values as the file leaves them are what `.qetl.job.stream.reset[job]` restores, so a test never restates them (#967)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | names a variable the namespace lacks                                                         |
  | `carry`             | with `replay 1b`                    | dict: `state`, `table`, optionally `cap`    | state the job carries across days and restarts: `state` is a variable in its namespace, `table` a table it publishes. The shell publishes the state onto `table` at end of day, and on replay sets it from that snapshot and re-applies the batches replayed within `window` (default `0D00:01`) of the log's start, which a fill logged ahead of the snapshot needs (#963, #960). Live, the snapshot is ignored                                                                                                                                                                                                                                                                                                                                                  | not a dict with `state` and `table`; a `table` the job does not publish; without `replay 1b` |
  | `check`             | no                                  | function `{[rows] ...}`                     | a quality gate on what the job publishes, applied to every publish (handler, timer, poll). Returns the offending rows as a table in the bounded `check` shape --- `check`, `status`, `detail` --- plus an optional `row` column, the index of the offending row in the batch. Withheld rows are logged and counted in `.qetl.stream_health`, so `uqs summary` shows the job `failing`. A check that throws withholds the whole batch. `market_data` declares the `.qdqc` crossed-book and stale-quote checks (#944)                                                                                                                                                                                                                                               | not callable, or declared without `on_fail`                                                  |
  | `on_fail`           | with `check`                        | `` `drop `` or `` `hold ``                  | `` `drop `` publishes the rows `check` did not name (a failure with no `row`, or a throw, withholds the whole batch); `` `hold `` withholds the whole batch whenever anything fails                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               | anything else, or declared without `check`                                                   |

A job must declare `on_batch`, `on_timer` or both; one with neither would run
nothing and is refused.

The namespace is always `.qpipe.job.<job>`. A job publishes by calling its own
`publish`, never `.u.upd`: `publish` starts as a stub that throws, and
`.qetl.job.stream.wire` points it at the tickerplant (the runner) or at a
recorder (a test). Never publish a `time` column --- the tickerplant stamps its
own.

## Normalizer --- `.qetl.job.stream.normalize`

`.qetl.job.stream.normalize[name;decl]`. Several source tables mapped onto one
canonical table, one declared transform per source. `name` is also the canonical
table the normalizer publishes and its `.qpipe.job.<name>` namespace.

  | key              | required | type                                         | meaning                                                                                                                             | refused when                                                                                                                                     |
  | ---              | ---      | ---                                          | ---                                                                                                                                 | ---                                                                                                                                              |
  | `procname`       | yes      | symbol                                       | the TorQ process that runs it, as for a streaming job                                                                               | as for a streaming job                                                                                                                           |
  | `output`         | yes      | empty typed table                            | the canonical table's schema, without `time`                                                                                        | not an unkeyed table, no columns, or a `time` column                                                                                             |
  | `input`          | yes      | dict: source table -> transform name         | for each tickerplant table it reads, the `.qetl.transform` transform that maps a batch of it onto `output`                          | empty; a transform not registered, taking more than one input, or whose `output` is not exactly the canonical table --- columns, order and types |
  | `start_with_all` | no       | boolean, default `0b`                        | as for a streaming job                                                                                                              | not a boolean                                                                                                                                    |
  | `note`           | no       | string                                       | as for a streaming job                                                                                                              | not a string                                                                                                                                     |

`define` registers the streaming job itself: `subscribe_to` is the keys of
`input`, `publishes` is `name`, and `on_batch` is a dispatcher that trims each
batch to the columns its transform declares, applies it, and publishes. The
normalizer's file never handles a batch.

## Horizon job --- `.qetl.job.stream.at_horizons`

`.qetl.job.stream.at_horizons[name;decl]`. A job that evaluates each event once
its horizon has passed: a markout scores a fill against the market some time
after it, so the fill waits in a queue until the prices that judge it have
arrived (`src/etl/core/horizon.q`, #945). `demo_markout` and `crypto_markout`
are horizon jobs.

  | key              | required                    | type                                       | meaning                                                                                                                                                                                                                                             | refused when                                                        |
  | ---              | ---                         | ---                                        | ---                                                                                                                                                                                                                                                 | ---                                                                 |
  | `procname`       | yes                         | symbol                                     | the TorQ process that runs it, as for a streaming job                                                                                                                                                                                               | as for a streaming job                                              |
  | `events`         | yes                         | symbol                                     | the tickerplant table carrying the events to evaluate                                                                                                                                                                                               | not a symbol                                                        |
  | `reference`      | yes                         | symbol                                     | the tickerplant table they are evaluated against                                                                                                                                                                                                    | not a symbol                                                        |
  | `transform`      | yes                         | symbol                                     | a `.qetl.transform` with two inputs, **events first, reference second**; its inputs are the job's buffers                                                                                                                                           | not registered, not two inputs, or either input has no `time`       |
  | `publishes`      | yes                         | symbol                                     | the table the transform's rows are published onto                                                                                                                                                                                                   | not a symbol                                                        |
  | `horizon`        | yes                         | timespan                                   | how long an event waits after its `time` before it is evaluated                                                                                                                                                                                     | not a positive timespan                                             |
  | `period`         | yes                         | timespan                                   | the timer that evaluates what is ready                                                                                                                                                                                                              | as for a streaming job                                              |
  | `max_age`        | no                          | timespan                                   | the oldest a reference row may be at a horizon and still count; absent, the latest row counts however old                                                                                                                                           | not a timespan                                                      |
  | `max_lateness`   | no                          | timespan, default 0D for `time`, else 0D01 | how much older than its arrival an event's `event_time` can be; history eviction keeps this much extra                                                                                                                                              | not a non-negative timespan                                         |
  | `by`             | no                          | symbols, default `sym`                     | the reference's key, for keeping each key's as-of row when `max_age` is absent                                                                                                                                                                      | a column the reference input does not carry                         |
  | `keep`           | no                          | function of a batch                        | which rows of either table to buffer (e.g. `{[x] x[`sym] in .qsynth.pairs}`)                                                                                                                                                                        | not a function                                                      |
  | `event_time`     | no                          | symbol, default `time`                     | the events column an event's clock is read from (e.g. `source_time`, when it happened rather than when the plant received it)                                                                                                                       | not a timestamp column of the events input                          |
  | `reference_time` | no                          | symbol, default `time`                     | the reference column its clock is read from                                                                                                                                                                                                         | not a column of the reference input                                 |
  | `lookback`       | no                          | timespan, default `0D`                     | how far BEFORE an event its window reaches (a markout at -60s): history back to there, plus each key's older as-of anchor, is kept                                                                                                                  | not a timespan of zero or more                                      |
  | `ready_on`       | no                          | `` `wall`` (default) or `` `reference``    | `wall`: ready once `horizon` has elapsed since `event_time`. `reference`: ready once every reference key it needs has an anchor at or before `event_time - lookback` **and** has advanced to `event_time + horizon` - wall time alone is not enough | not one of the two; `reference` with a `by` of more than one column |
  | `legs`           | with `ready_on` `reference` | function of the pending events             | each event's reference keys - a cross pair's direct and cross legs; default its own `by` value                                                                                                                                                      | not a function, or without `ready_on` `reference`                   |
  | `identity`       | no                          | symbols                                    | the event columns naming one event: a redelivery replaces the pending one (the last wins), and one already scored is dropped while remembered                                                                                                       | a column the events input does not carry                            |
  | `remember`       | with `identity`             | timespan, default `0D01`                   | how long a scored identity is remembered, to drop redeliveries                                                                                                                                                                                      | not positive; without `identity`                                    |
  | `expire_after`   | no                          | timespan                                   | how long after its `event_time` an event still not ready is given up on: moved to `.qpipe.job.<name>.expired` with the reason (which leg had no reference, or had not advanced), and logged                                                         | not longer than `horizon`                                           |
  | `start_with_all` | no                          | boolean, default `0b`                      | as for a streaming job                                                                                                                                                                                                                              | as for a streaming job                                              |
  | `note`           | no                          | string                                     | as for a streaming job                                                                                                                                                                                                                              | as for a streaming job                                              |

`define` registers the streaming job itself: `subscribe_to` is `events` and
`reference`, `publishes` is `publishes`, and `transform` is declared so a
backfill twin applies the same one. It installs into `.qpipe.job.<name>`: -
`pending` and `history`: the buffers, typed as the transform's inputs; -
`on_batch`: projects each batch onto those columns, after `keep`; -
`score_ready[now]`: evaluates and publishes what has waited `horizon`, then
evicts it; - `on_timer`, `now` (`.z.p`) and the unwired `publish`.

Each of `event_time` through `expire_after` (#952) is off until declared, so a
declaration that names none of them behaves exactly as `demo_markout` and
`crypto_markout` do. `define` also installs `completed` (identities scored,
while remembered) and `expired` (events given up on, with `expired_at` and
`reason`).

Events are evicted only after a successful publish, so a publish that throws
leaves them queued for the next tick. On every tick the history is trimmed to
what a waiting event can still use: - **with `max_age`:** every row older than
the oldest waiting event minus `max_age` is dropped, except that under
`ready_on` `` `reference `` each key's latest row before that is kept as the
readiness anchor; - **without it:** each key's latest row before the oldest
waiting event is kept, because it is still that key's as-of answer. When
`event_time` is not receipt time the cutoff also moves back by `max_lateness`,
so a late event still finds its reference rows.

Either way, what is evaluated is what the full history would have given.

## Bars job --- `.qetl.job.stream.bars`

`.qetl.job.stream.at_bars[name;decl]`. A job that aggregates a stream into
fixed-width time windows: OHLC, VWAP and volume per interval
(`src/etl/core/bars.q`, #946). `exec_bars` is one: `executions` into `exec_bar`.

Declares `procname`, `period` (how often closed windows are looked for) and
`start_with_all`/`note` as for a streaming job, and:

- `events`: the table aggregated.
- `transform`: a `.qetl.transform` with ONE input, the events plus a `bar_start`
  column the kind adds; one row per group per window out, carrying the grouping
  columns and `bar_start`. A backfill twin applies the same one (cutting the
  window itself with `.qetl.job.stream.bars.assign`).
- `publishes`: the bar table.
- `width`: a positive timespan. Windows are half-open
  `[bar_start, bar_start+width)` on the event time; a row on a boundary opens
  the window that starts there. A window with no rows has no bar.
- `lateness`: a timespan, `0D` for none. A window closes, and its bar is
  published once, when its end plus `lateness` has passed.
- `by` (default `` `sym``), `event_time` (default `` `time``): the grouping
  columns, and the column the window is cut on.

A row arriving before its window closes amends it; one arriving after is
dropped, logged, and kept in `.qpipe.job.<name>.dropped` with its reason. At end
of day (`on_endofday`, #943) every window of the day that ended is closed,
whatever its lateness. The job replays its log and restores from its own bars
(`restore_from`), so a restart rebuilds the open windows and publishes none that
already went out. Bars are published before the windows are evicted, so a
publish that throws is retried on the next tick. `define` installs `pending`,
`closed`, `dropped`, `publish`, `now`, `on_batch`, `close_ready[now]`,
`on_timer` and `on_endofday` in `.qpipe.job.<name>`.

## Reactions --- `.qetl.reaction`

Running something when a dataset is published, rather than on a timer. A
reaction fires after a bounded worker publishes a window of `dataset` --- the
one path that notifies, and not on a dry run --- with that window's range.

  | call                                                         | handler                                                          | the job graph learns                                                   |
  | ---                                                          | ---                                                              | ---                                                                    |
  | `.qetl.reaction.on[dataset;name;handler]`                    | `{[dataset;range_from;range_to] ...}`                            | only that it reads `dataset`                                           |
  | `.qetl.reaction.on_writing[dataset;name;outputs;handler]`    | the same                                                         | that it reads `dataset` and writes `outputs`, as asserted              |
  | `.qetl.reaction.on_worker[dataset;worker;spec_fn]`           | `spec_fn` is `{[range_from;range_to] ...}` returning a run spec  | that it writes the worker's `dataset`, derived from its declaration    |

Prefer `on_worker` when the downstream work is itself a worker (plain q only;
TorQ refuses it, one bounded worker per process): its edge in the graph is read
from the worker's declaration rather than asserted. [Recomputing a table when
the one it reads is
published](../guides/new-pipeline.md#recomputing-on-an-upstream-publish) covers
when to use which.

## Sinks --- `alert_sink`

Jobs write inward by default. `alert_sink` (`src/etl/streaming/alert_sink.q`,
process `alert_sink1`) is the first outbound sink: a streaming job that
subscribes to `fx_limit_breach` and POSTs each breach to a webhook as JSON
(`text` for chat webhooks, `breach` for anything that parses it).

It is a streaming job rather than a `.qetl.io` manager because a manager is
where a bounded worker's finished window goes, and a failure there fails the
window; a breach has no window or coverage, and its failure policy is retry then
record. File sinks from bounded workers (Parquet/CSV), ODBC write-back and Kafka
are not done: #947 stays open for them.

  | behaviour    | what it does                                                                                                                                                                                                                                                                                               |
  | ---          | ---                                                                                                                                                                                                                                                                                                        |
  | guarantee    | **at least once**, and in memory only: a breach can be sent twice (a timeout after the target acted, a crash after the POST), and what is queued is lost on restart. The receiver must tolerate a repeat                                                                                                   |
  | throttle     | `.qlimit.throttle` on the breach's scope and metric, `alert_period` (5 minutes): a standing breach is delivered once per period                                                                                                                                                                            |
  | retries      | one attempt on arrival, then one per 10 s tick, `max_attempts` (3) in all. No sleeping                                                                                                                                                                                                                     |
  | failure      | after the last attempt the breach moves to `.qpipe.job.alert_sink.dead` with its error, is logged at error, and leaves the throttle so its next report is delivered                                                                                                                                        |
  | URL          | `UQF_SOURCE_CRED_ALERT_SINK` only (a webhook URL carries its token), never logged. There is no `sources.csv` row for it: that file's rows are for sources with a transport and a table to read                                                                                                             |
  | none set     | the job **refuses**: `on_batch` throws naming the variable, which `.qetl.stream_health` counts as failing. It does not idle, since an idle sink drops breaches silently                                                                                                                                    |
  | testing      | `.qpipe.job.alert_sink.post[target;body]` is the one seam that touches the network (it calls `.qetl.webhook.post`, which treats any non-2xx status as a failed delivery); `tests/q/test_alert_sink.q` replaces it with a fake that records calls and fails on demand                                       |

## Job graph: derived, not declared

`.qetl.dag.register[job;decl]` exists, but no job file calls it. The graph is
assembled from the declarations above, so a job's edges cannot disagree with
what it does:

  | call                          | registers                                                                                                                                                                                        |
  | ---                           | ---                                                                                                                                                                                              |
  | `.qetl.dag.adopt_workers[]`   | every bounded worker, reading `<source>@<table>` and writing the source's `target`                                                                                                               |
  | `.qetl.dag.adopt_streams[]`   | every streaming job and normalizer, by job name, read from `.qetl.job.stream`: inputs are `subscribe_to`, outputs `publishes`                                                                    |
  | `.qetl.dag.adopt_pipelines[]` | processes that run no declared job (`tap1`), from `src/etl/generated/pipeline_dag.q`, generated from `NON_JOB_PIPELINES`                                                                         |
  | `.qetl.dag.adopt_feeders[]`   | every continuous feeder                                                                                                                                                                          |
  | `.qetl.dag.adopt_reactions[]` | every `.qetl.reaction` reaction, with the edges its call declares                                                                                                                                |

`.qetl.dag.adopt_all[]` runs all five, reactions last.
