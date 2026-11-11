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

Two rules hold for every `define`, and live once in `src/etl/core/declared.q`
(`.qetl.declared`):

- **An unknown key is refused**, by name, together with the keys the declaration
  does take. It is the first check on the dictionary, so a misspelled key
  (`start_with_al`, `on_endofdya`, `transfrom`) names itself instead of
  registering and never being read.
- **A function is anything callable except `(::)`**: a lambda, a projection, a
  composition or an iterator. A projection counts because binding a handle or a
  configuration into a function is how authors hand one over. Arity is checked
  only for a lambda, the one shape whose parameter list q can read. Every
  function-valued key, bounded `check`, `facts` and `pin` included, is checked
  when the declaration is made, not at its first use.

`tests/q/test_declared.q` finds every `define` in the tree and holds each to
both rules.

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
requiring either job to own the other's computation. (They are the
`sidecars/databento` bundle's, and a bundle has no transforms directory, so the
transform sits in the source's file, which both jobs reach.)

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
`scripts/gates/check.py declaration-reference` holds the key tables on this page
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

  | key                  | required | type                                                             | meaning                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | refused when                                                                                                                                                                 |
  | ---                  | ---      | ---                                                              | ---                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | ---                                                                                                                                                                          |
  | `source`             | yes      | symbol                                                           | the source's own name, the same word as its `.qpipe.source.<source>` namespace and `source_name`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | missing                                                                                                                                                                      |
  | `table_name`         | yes      | symbol                                                           | the table's name **on the external side**. `.qetl.source.validate_live` reads that table's metadata, and the job graph draws the edge as `<source>@<table>`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               | missing                                                                                                                                                                      |
  | `columns`            | yes      | symbol vector                                                    | the columns this adapter READS --- not every column the source has, because a declared column the worker never uses still breaks the run when it changes                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | not symbols                                                                                                                                                                  |
  | `types`              | yes      | string                                                           | one q type character per field, e.g. `"psf"`. An upper-case character declares a column of vectors                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | not a string, or not one per field                                                                                                                                           |
  | `time_column`        | yes      | symbol                                                           | the column windows are cut on                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | not in `columns`, or its type is not `"p"` (a `datetime` rounds sub-second values)                                                                                           |
  | `row_key`            | yes      | symbol or symbol vector                                          | the column(s) that identify a row uniquely. Only right if the source guarantees it. A worker's `on_conflict` matches incoming rows to rows already written by it                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | not symbols, or names a column not in `columns`                                                                                                                              |
  | `query`              | yes      | lambda `{[h;range_from;range_to] ...}`                           | fetches one window over the handle `h`. Parameterised, never built by string concatenation, and half-open: `>=` the lower bound, `<` the upper                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | not a function, or a lambda not taking 3 arguments                                                                                                                           |
  | `fixture`            | yes      | lambda `{[] ...}`                                                | a deterministic synthetic table of the same shape. Used when `UQF_SOURCE_CRED_<SOURCE>` is unset, which is the declared demo path and never a fallback for a failed connection; windowed on `time_column` exactly as the live query is                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | not a function, or a lambda taking an argument                                                                                                                               |
  | `tz`                 | yes      | symbol                                                           | the zone `time_column` is expressed in: `` `UTC `` or a tz-database name such as `` `$"Europe/London" ``                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | not a single symbol                                                                                                                                                          |
  | `transport`          | no       | `` `ipc ``, `` `odbc ``, `` `local ``, `` `mock `` or `` `csv `` | how the credential is opened. `ipc` (default): `host:port`, opened with `hopen`. `odbc`: a connection string, opened with `.qetl.io.odbc.open`. `local`: an HDB directory's path, checked by `.qetl.source.local_root` and read from its files through `.qetl.source.local`. `mock`: an integer seed, read by `.qetl.source.mock_seed`; the query generates the window's rows from it. `csv`: a file's path, checked by `.qetl.source.csv_path`; `.qetl.source.csv_rows` reads every field as text                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | anything else                                                                                                                                                                |
  | `credential_example` | no       | string                                                           | what this source's credential LOOKS like, for the warning a worker logs when `UQF_SOURCE_CRED_<SOURCE>` is unset (`.qetl.source.credential_example`). Without it the warning shows the transport's generic example, which for a DuckDB file over ODBC asks for a server and password it has no use for                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |                                                                                                                                                                              |
  | `supporting`         | no       | dict: input name -> empty typed table                            | further inputs the source hands its worker beside the primary one (#617), each with its contract. With it, `query` and `fixture` return a dict: `table_name` -> the window's rows, and each supporting name -> its rows. Only the primary is cut to the window and counted; a supporting input is context, validated against its contract but never windowed, and a live check reads its metadata by its name. The worker's transform must then read exactly these inputs, by name                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | not a dict of tables, names `table_name`, or declared by a source whose `tz` is not `` `UTC ``                                                                               |
  | `complete_through`   | no       | lambda `{[h] ...}`                                               | how far the source vouches for every row, as a timestamp: every row stamped before it is present, exclusive like a window end. Planning (`uqs job backfill --mode plan` too) clips the range to it and the rest stays uncovered for the next run. Default `{[h] 0Wp}` (closed data); null means nothing complete yet. A source over a still-growing file declares it; the fixture is never clipped                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |                                                                                                                                                                              |
  | `ready`              | no       | lambda `{[batch;range_from;range_to] ...}`                       | whether a fetched window can be scored: returns its unmet requirements as a list of strings, `()` when ready (#1927). A window with any is refused after fetch and before publish - no rows, no coverage, the run ends `partial` naming what is missing - so the next run plans it again. Meant for a source whose `supporting` reference data may not have landed; a missing leg inside data that did land is not unmet.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |                                                                                                                                                                              |
  | `reference_read`     | no       | dict `date_skew`, `lookback_cap`, `symbols`                      | how much of its supporting inputs a source reads as reference, so the numbers are declared rather than literals in its query: `date_skew` whole days the partition constraint is widened by each side of the window, `lookback_cap` the look-back when the reference has no age limit (`max_age` 0Wn; otherwise `.qetl.source.reference_lookback` gives `max_age` plus the most negative horizon), and `symbols` a lambda `{[spec;rows] ...}` returning the symbols to read, where a conversion pair is added. Every key is required; needs supporting inputs                                                                                                                                                                                                                                                                                                                                                                                                                                             | not a dict of exactly those keys; a negative `date_skew`; a `lookback_cap` that is not a positive finite timespan; `symbols` not a two-argument lambda; no supporting inputs |
  | `batch_read`         | no       | dict `window`, `needs`, `read`, `keys`, `sort`                   | how the source reads its supporting inputs a batch at a time, for a worker whose `batching` says `source_read:`batch` (#1921). `window` `{...}` returns the primary rows and a *presence summary* of the supporting inputs (a row per key held) - what `ready` judges over the whole window; `needs` `{...}` the reference keys some primary rows need (for the markout source, `reference_read`'s `symbols`, conversion pairs included); `read` `{...}` the supporting inputs for exactly those keys; `keys` and `sort` name, per supporting input, its key column and the column its rows are sorted on. Every key is required; UTC sources with supporting inputs only | not a dict of exactly those keys; a lambda of the wrong arity; `keys`/`sort` not naming every supporting input, or a column it lacks; a zoned source; no supporting inputs                                                                                                                                                    |                                                                                                                                                                              |
  | `raw`                | no       | dict: physical table -> empty typed table                        | the columns the adapter reads from each physical table when its output differs from its input (see [ODBC](../guides/odbc.md#verifying))                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |                                                                                                                                                                              |

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
  `secret_env`. `uqs job backfill --mode validate` checks these too.

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

## A fills source --- `.qetl.source.fills.declare`

A fills drop (a csv file or an ODBC table from a broker or a venue) is declared
by a column mapping instead of a hand-written adapter, and is read as the
canonical shape `executions` carries (#1981). `src/etl/sources/eq_fills.q` is
the bundled instance, and `src/etl/workers/eq_fills_backfill.q` its bounded
worker.

  | key          | meaning                                                                                                                                                                                           | refused when                                                                                                                           |
  | ---          | ---                                                                                                                                                                                               | ---                                                                                                                                    |
  | `transport`  | `` `csv `` (the credential is the file's path) or `` `odbc `` (a DuckDB-dialect connection string)                                                                                                | anything else                                                                                                                          |
  | `table_name` | the table read; for a csv file only the name the live check uses                                                                                                                                  | not a plain identifier                                                                                                                 |
  | `columns`    | the mapping, canonical field to the source's column. Required: `venue`, `fill_id`, `sym`, `side`, `size`, `price`, `source_time`; optional: `fee`, `fee_ccy` (unmapped reads as null, never zero) | a required field missing (all named at once), an unknown field, a column name that is not a plain identifier, two fields on one column |
  | `sides`      | every spelling the source uses for a side, to `1` (buy) or `-1` (sell), matched without case; no default                                                                                          | not a dictionary, a value other than 1 or -1, one direction only, one spelling twice                                                   |
  | `clock`      | whose clock `source_time` is: `` `venue `` or `` `receipt `` (#1925); no default                                                                                                                  | anything else                                                                                                                          |
  | `tz`         | `` `UTC ``; no other zone is accepted, and a csv time carrying a non-zero offset refuses the file                                                                                                 | any other zone                                                                                                                         |

Everything is refused at declaration, with the fault named, before a window is
read. At read time a row that cannot be read (no time, a size or price that is
not a number, a side the encoding does not name, an empty venue, fill id or
symbol) refuses its window rather than being dropped: a dropped fill under a
covered window is a coverage over-claim.

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

## Outputs --- one key, two meanings

Both job types name what they produce under `outputs`, a symbol list. The key is
shared; the job type still decides what it means, and the page keeps the two
apart on purpose:

  | job type      | how many     | what the job type does with them                                                                                                                                                                                        |
  | ---           | ---          | ---                                                                                                                                                                                                                     |
  | bounded       | exactly one  | the dataset coverage is recorded under, and the one `replace` and reactions are keyed on. Rows are written through the worker's IO manager, window by window, and a window is claimed only after its rows are published |
  | streaming     | zero or more | the tables the job publishes onto the tickerplant. Nothing records them as complete: a streaming job has no coverage ledger, and its publication guarantees are the tickerplant's                                       |

A bounded worker with zero outputs, or several, is refused at declaration
(`define: <worker> declares 2 outputs - a bounded worker fills exactly one dataset ...`):
coverage is per dataset, so a worker filling two would have one ledger row
describing two tables. The retired spellings --- a worker's `dataset`, a
streaming job's `publishes` --- are refused with the rename, not accepted as
aliases.

## Bounded worker --- `.qetl.job.bounded.define`

`.qetl.job.bounded.define[worker;decl]`, at the bottom of the worker file.

  | key                | required | type                                                                         | meaning                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | refused when                                                                                                                                                                                                                   |
  | ---                | ---      | ---                                                                          | ---                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | ---                                                                                                                                                                                                                            |
  | `source`           | yes      | symbol                                                                       | a source registered with `.qetl.source.define`, which must have loaded first (`init.q` loads `sources/` before `workers/`)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | not registered                                                                                                                                                                                                                 |
  | `outputs`          | yes      | symbol list of exactly one dataset, e.g. `` enlist `demo_deals ``            | the dataset the worker fills, and the name completeness is recorded under: coverage, replacement, materialisation metadata and `.qetl.reaction` reactions are all keyed by it. The rows themselves land in the **source's `target`**, which is a different key --- usually the same word, not necessarily                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               | zero outputs, several, or an atom; `dataset` (retired); another worker already declares the same dataset and `partition`                                                                                                       |
  | `width`            | yes      | timespan, e.g. `1D`                                                          | how wide each window is. A run over `[from;to)` is cut into windows this wide, oldest first, and each is fetched, checked and recorded separately                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | not a timespan, or not positive                                                                                                                                                                                                |
  | `transform`        | yes      | symbol                                                                       | a transform declared with `.qetl.transform.define`. It must read exactly one input, whose schema is the source's `columns` and `types`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | not registered, more than one input, takes `as_of` (a window has no single instant), or its input is not the source's contract                                                                                                 |
  | `check`            | no       | lambda `{[batch] ...}`                                                       | the data-quality gate, run on the transformed batch before publish. Returns a table of failures (`check`, `status`, `detail`); empty means the batch passed. A failed batch is not published, its window is not recorded as covered, and the next run plans it again                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | at run time, when it is not a lambda or does not return a table                                                                                                                                                                |
  | `io`               | no       | dict with `write`, optionally `write_keyed`, `flush`, `finish` and `recover` | where rows are written. `write` is a function `{[target;batch] ...}` returning the row count; `write_keyed` takes the run's `on_conflict` too, and without it only `append` is possible; `flush` finishes what lies before each window's end, `finish` runs once after a run's last window, and `recover` repairs what an interrupted run left. Absent, the process's default applies: `.qetl.io.memory` (an in-process table) in plain q, and `.qetl.io.hdb` - each row into the HDB partition of its own date - in a backfill process. `.qetl.io.discard` counts rows and stores nothing                                                                                                                                                                                                                                                                                                                                                                                              | not a dict carrying a callable `write`, or a `finish` that is not callable                                                                                                                                                     |
  | `facts`            | no       | function `batch -> dict`                                                     | labels recorded with each window's materialisation, beside the `rows`, `source_version` and `dry_run` the framework records itself --- the min and max time, a null fraction, a checksum. A failing `facts` is logged and does not fail the window. A dry run computes them and logs them, and records none                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | --- (errors are logged, not thrown)                                                                                                                                                                                            |
  | `partition`        | no       | symbol, default `` ` ``                                                      | lets several workers fill one dataset: each declares a different partition, and coverage is recorded per `(dataset, partition)`. `` ` `` means the dataset has no partition dimension                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | not a symbol                                                                                                                                                                                                                   |
  | `procname`         | no       | symbol, default `` `<worker>1 ``                                             | the process that runs this worker. The `uqs` process registry is read from it                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | not a symbol                                                                                                                                                                                                                   |
  | `note`             | no       | string, default `""`                                                         | why it is deployed as it is, shown in [`processes.md`](processes.md)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | not a string                                                                                                                                                                                                                   |
  | `on_conflict`      | no       | symbol, default `` `upsert ``                                                | what a write does with a row whose `target_key` is already in the target: `upsert` replaces it and adds new keys; `replace` also deletes what the target held inside the window and the batch left out; `ignore` keeps the row already there; `append` writes it again (no check); `fail` fails the window. Matched per date partition in the HDB. `uqs job backfill --on-conflict` overrides it for one run                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | not one of those five                                                                                                                                                                                                          |
  | `source_version`   | no       | symbol, default none                                                         | the release a run records coverage under when `uqs job backfill` is given no `--source-version`. Declare it for a source that is never restated (a recording, an append-only tape, historical market data); leave it out where the source can be corrected, so every run must name its release --- a default there files a restatement under the old version, and every window reads as already covered. A new `--source-version` always re-fetches covered windows                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | not a symbol                                                                                                                                                                                                                   |
  | `target_key`       | no       | symbol or symbol vector, default the source `row_key`                        | the columns `on_conflict` matches rows by, named as the transform OUTPUTS them. Declare it when the transform renames a key column: `upstream_trades_backfill` declares `venue` where its source key says `ex`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | not symbols, or names a column the transform's declared `output` lacks (including a defaulted source key the transform renamed)                                                                                                |
  | `window_column`    | no       | symbol, default the source `time_column`                                     | the OUTPUT column that carries the window's time. `replace` clears a window by it, so it must be the column the window was cut on. Declare it when the transform moves the time: `hdb_fx_markouts_backfill` outputs `time` as source_time plus the furthest horizon and declares `source_time`. Undeclared it is the source's time column if the output keeps it, else `time`; a worker with neither refuses `replace` at plan time                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | not an output column; at run time, `replace` on a worker with no such column                                                                                                                                                   |
  | `partition_column` | no       | symbol, default none                                                         | the OUTPUT column that holds this worker's `partition`, so a `replace` clears only the rows of its own partition. Without it, a `replace` --- declared, or the one a live run switches to over a window a fixture run claimed --- is refused (declared) or withheld (automatic) for any worker with a `partition`, because the window may hold other partitions' rows whether or not their workers are loaded in this process (#1476, #1499). An unpartitioned worker's `replace` is likewise refused while the dataset's declarations or coverage ledger name any partition (#1518). Needs a `partition`, and a `partition` needs it: define refuses one without, and publish refuses any row whose value here is another partition's (#1539)                                                                                                                                                                                                                                          | not a symbol, there is no `partition`, a `partition` has no column, or it is not among the transform's output columns                                                                                                          |
  | `align`            | no       | timespan that divides the width, default none                                | the bar width a bar-building worker cuts its output to (`hdb_exec_bars_backfill` 1m, `hdb_crypto_book_backfill` 1s). A run whose `--from` or `--to` is not a multiple of it is refused at init: a window edge inside a bar would build that bar twice from part of its fills and, under `upsert`, the partial second build would replace the first (#1303)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | not a timespan, not positive, or the width is not a multiple of it; at run time, an unaligned range                                                                                                                            |
  | `revision`         | no       | positive long, default none                                                  | the revision of the worker's output logic (#1097). Coverage is recorded under `<release>@r<revision>`, so bumping it when a transform or scoring rule changes plans every window again rather than mixing the old logic's windows with the new; old claims keep their own identity. Bump it with the change, in the same commit                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | not a positive long                                                                                                                                                                                                            |
  | `batching`         | no       | dict, default none (off)                                                     | compute batching inside each window, off unless declared (#1921): `group_by`, `max_rows`, optionally `max_groups`, `context`, `source_read` and `reuse_rows`. See [Compute batching](#compute-batching)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | malformed; `group_by` not a source column; `max_rows` not a positive, finite long; a bad `context`, `source_read` or `reuse_rows`, `source_read` or `reuse_rows`                                                               |
  | `pin`              | no       | function, default none                                                       | a niladic function a run calls once, before it plans or fetches anything, that records (pins) the configuration snapshot the worker's rows name and returns the snapshot ids, a symbol list (#1999, `src/etl/core/bounded_pin.q`). Off unless declared; a dry run does not call it. The worker's `check` is where a batch naming a snapshot that is not durably recorded is refused                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | not a function; a pin that throws fails the run before its first window, and one that returns anything but symbols fails it too                                                                                                |
  | `twin_of`          | no       | symbol, default none                                                         | the streaming job this worker repairs: a **declared twin** (see below). `outputs` is the live output it repairs, so a live job with several needs no second key. Validated whenever both declarations are loaded, from either end                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | not a symbol, null, itself or a bounded worker; later, any of the refusals below                                                                                                                                               |
  | `twin_calculation` | no       | `` `shared ``, default none                                                  | the promise that the twin and its live job apply one registered `transform`. Needs `twin_of`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | not `` `shared ``, or declared without `twin_of`; later, a transform that differs from the live job's                                                                                                                          |

`ns` is **not** a key: the namespace is always `.qpipe.job.<worker>`, and a
supplied `ns` is refused. `define` writes the lifecycle methods (`init`, `plan`,
`fetch`, `publish`, `checkpoint`, `spec`, `run`, `cleanup`) and the run state
into that namespace; a worker overrides a method by defining it there *before*
calling `define`.

A worker has no `start_with_all`: a backfill runs its range and exits, so it
never starts with the stack.

### Twins --- a backfill that repairs a live job

A backfill that refills a streaming job's table is only useful while it still
writes what the job publishes. `twin_of` declares that, on the backfill, and q
holds the pair to it whenever both declarations are loaded (#2103) --- with no
CLI involved, and from whichever end loads last: `.qetl.job.bounded.define`
checks a twin whose live job is already loaded, `.qetl.job.stream.define` the
twins waiting for it, and a process that loads every declaration refuses a
`twin_of` that names no loaded job (`.qetl.twin.require_resolved`).

```q
.qetl.job.bounded.define[`hdb_exec_bars_backfill;
    `source`outputs`width`transform`twin_of!
    (`hdb_executions;enlist `exec_bar;0D01:00:00;`hdb_exec_bars;`exec_bars)];
```

  | held to                  | refusal (`twin: <worker> is declared a twin of <job> but ...`)                                                                                                                                              |
  | ---                      | ---                                                                                                                                                                                                         |
  | its destination          | `it writes marks, which fx_markout does not list among its outputs (marks_v2) - a twin repairs one of the live job's outputs, even when another table has the same columns`                                 |
  | the dataset contract     | the twin's transform output against the plant's table (`.qetl.plant.problems`): `marks.px is typed "j" - the plant takes "f"`, `marks has columns px - the plant takes px qty`, or `x is not a plant table` |
  | a promised calculation   | `it promises a shared calculation (twin_calculation `shared) but applies A where B applies C`, or `B declares no transform`                                                                                 |

The destination check is the point of the declaration: the live job moving to
`marks_v2`, another valid table with the same columns, leaves each producer
satisfying its own schema and the backfill no longer repairing the live output.
It is refused when the live job loads, whatever the schemas say. A live job with
several outputs needs nothing more: the twin's single output is the one it
repairs, and it must be among them.

**The calculation is never assumed.** A twin reads another source through
another transform, so matching columns say nothing about matching arithmetic.
`.qetl.twin.report[]` lists every declared twin with one of:

  | `calculation`      | meaning                                                                                                                                                                                                                                                            |
  | ---                | ---                                                                                                                                                                                                                                                                |
  | `shared`           | both apply one registered transform. Declare `twin_calculation: `shared` to make q refuse a pair that stops doing so; an unpromised pair that happens to share one is reported too                                                                                 |
  | `not_established`  | no shared transform --- allowed, since a twin's transform is usually an adapter over the same core, but recorded, logged and reported as "calculation equivalence not established". `tests/q/test_twins.q` holds the pairs it lists to the live job's own examples |
  | `unchecked`        | the live job is not loaded in this process                                                                                                                                                                                                                         |

Only a declared twin is validated. Two producers of one dataset are not a twin
(`eq_fills_backfill` and the `executions` normalizer both write `executions`),
and nothing looks at datasets to find a pair. Transport, timestamps, windowing,
width, source and the whole execution lifecycle stay free to differ.
(`.qetl.uptime.twins`, which `uqs job gaps` uses to pick the backfill that
refills an outage, still goes by dataset.)
`uqs job new --kind backfill --twin-of JOB` writes the declaration, and
`twin_calculation` when JOB declares a transform the twin then applies.

### Compute batching

A window is the unit of *durable* ownership: one fetch, one publish, one
coverage record. A transform over a large window can still be too big to run at
once, without the window being wrong. `batching` splits the **compute** and
leaves the ownership alone: the window is fetched once, its primary rows are
planned into batches of whole groups, each batch is transformed on its own, and
the outputs are joined and go through the check, the publish and the coverage
record as an unbatched window's do.

```q
batching:`group_by`max_rows`max_groups!(`sym;50000;4)
```

  | key           | meaning                                                                                                                                                                                                                                                                                                                                                  |
  | ---           | ---                                                                                                                                                                                                                                                                                                                                                      |
  | `group_by`    | the primary columns that identify one calculation group: one symbol or several (a composite key). Rows that agree on all of them are a group, and **a group is never split**                                                                                                                                                                             |
  | `max_rows`    | the most primary rows a batch holds. A group larger than this is refused, naming the group and the ways out (raise it, shorten `width`, or declare a finer `group_by`)                                                                                                                                                                                   |
  | `max_groups`  | the most groups a batch holds, default no limit. `1` is a batch per group; larger values batch several, for a workload where groups share context                                                                                                                                                                                                        |
  | `context`     | optional `{[supporting;rows] ...}` returning the supporting inputs a batch of those primary rows needs, out of the window's. Without it every batch gets all of them. Only for a source with supporting inputs                                                                                                                                           |
  | `source_read` | optional, `` `window `` (default) or `` `batch ``. `` `batch `` has each batch read the references its own rows need from the source, instead of the window's being read whole; it needs a source that declares `batch_read`, and replaces `context` (the source narrows). See [Reading each batch from the source](#reading-each-batch-from-the-source) |
  | `reuse_rows`  | optional, default `0`: the most reference rows kept between batches so a book one batch read can serve the next, least recently used out first. Needs `source_read` `` `batch ``                                                                                                                                                                         |

What that guarantees, because publish and coverage are still one write per
window:

- coverage is never recorded after only some batches succeeded: a failing batch
  fails the window, naming the batch, before anything is written;
- a window is claimed only if it was **ready** (#1927, #1942). The source's
  `ready` stage runs between `fetch` and `transform` over the whole fetched
  window, so a window that is not ready ends partial before any batch starts:
  nothing is transformed, published or claimed, and the next run plans it again;
- a retry has nothing half-written to duplicate, omit or delete, and no batch
  writes beside a sibling's output;
- `replace` clears the window's ownership scope in one write, whatever the batch
  count: a batch that scores nothing, an all-empty result and output stamped
  after midnight are the cases `tests/q/test_bounded_batching.q` pins;
- no durable partition is created: batching is not a partitioning scheme.

What it does not do: with the default `source_read` it reads the source once per
window, so it splits the transform's *work* and not the read, and it does not
lower the process's peak memory (see below for why that holds for `source_read`
`` `batch `` too). Batches are planned in ascending key order and a batch keeps
the window's row order, so a plan is the same on every run.

#### Reading each batch from the source

By default the window's references are read once, whole, and a `context` only
narrows what each batch is *given*. A source that declares `batch_read`
(`src/etl/core/source_batch_read.q`) lets a worker say `source_read:`batch\`
instead, and then:

- the window is fetched as its **primary rows and a presence summary** of the
  references: a row for each key (for the markout source, each pair and source)
  that holds a book anywhere in the span the window reads. That is what the
  source's `ready` hook judges, **over the whole window, before any batch is
  read**, so a window with no books, or a conversion pair with none of its
  declared source, is still refused whole, and no batch reads anything for it;
- each batch asks the source (`needs`, then `read`) for **the keys its own rows
  need and no others**. For the markout source `needs` is `reference_read`'s
  `symbols`, so it includes the conversion pairs
  (`markout_core.conversion_pairs`) as well as the legs of every route, and
  `read` is the same book query the unbatched `query` runs, restricted to those
  symbols (the last book before the window and every one inside it, widened by
  the declared `date_skew`);
- a read is retried as the window's own fetch is, a source answer that holds a
  key nobody asked for is refused, and a batch whose read fails fails the window
  naming the batch, before anything is written or covered.

`reuse_rows` lets a book one batch read serve a later one. The cache is keyed by
**worker, `source_version`, and the window's bounds**: a hit needs all of them
to match. Each transform attempt clears this worker's previous reads, including
those of the same window and version, so a retry sees newly arrived references.
Once a batch has read, the cache is cut to `reuse_rows` rows, least recently
used first, and an entry bigger than the bound alone is never kept: the worker's
peak is one batch plus the bound. It is off (`0`) because it only pays when
batches share keys.

The batched rows are the unbatched rows: `tests/q/test_bounded_batch_read.q`
runs the markout worker against a stand-in HDB unbatched, batched per symbol
through the source, with and without reuse, and with all symbols in one batch,
and compares what each publishes by identity (36 rows, USDPLN's two legs
included). The fixture has no source to read from, so a run on it uses the
window it fetched.

**What it measured.** `scripts/bench/batching.q` runs `hdb_fx_markouts_backfill`
end to end into a real HDB root, against **a second q process** that serves the
window the way an HDB would and counts what it is asked. One machine (a busy
one), KDB-X, 20,000 executions, fastest of two, stage times summed over the
run's twelve windows:

  | 200 quotes a book                | fetch ms | transform ms | publish ms | queries | book rows read | worker peak MB |
  | ---                              | ---      | ---          | ---        | ---     | ---            | ---            |
  | unbatched                        | 5        | 701          | 1184       | 14      | 2197           | 343            |
  | per symbol, window read once     | 5        | 141          | 1154       | 14      | 2197           | 343            |
  | per symbol, read by batch        | 5        | 149          | 1144       | 26      | 2608           | 343            |

  | 20,000 quotes a book             | fetch ms | fetch peak MB | transform ms | publish ms | queries | book rows read | MB returned |
  | ---                              | ---      | ---           | ---          | ---        | ---     | ---            | ---         |
  | unbatched                        | 79       | 80.5          | 63094        | 1211       | 14      | 219444         | 18.4        |
  | per symbol, window read once     | 75       | 80.5          | 3955         | 1192       | 14      | 219444         | 18.4        |
  | per symbol, read by batch        | 11       | 4.2           | 4057         | 1206       | 26      | 259355         | 21.6        |

What that says:

- **Narrowing is where the compute time goes**, whichever way the narrowing is
  done: with large books the transform took 63 s unbatched and 4 s batched.
  Reading by batch costs the same transform time as narrowing a window read
  whole.
- **Reading by batch lowers what the fetch holds, not the process's peak.** The
  fetch stage's peak fell from 80.5 MB to 4.2 MB, because the window's books are
  no longer fetched whole. The worker's peak did **not** fall: 343 MB in every
  configuration, set by the **publish** stage, which writes the window's joined
  output (326,404 rows) in one go. That is the design (one write, one coverage
  record a window), so a lower peak needs either a smaller window (`width`) or a
  writer that does not copy the joined output, and not more batching. The
  transform stage's own peak is not lower either: every batch's output is held
  until the join.
- **The price is more, smaller reads.** Reading by batch sent 26 queries against
  14 and read 18% more book rows, because a leg two batches share is read by
  each. The reuse cache gets them back where legs are shared: on a window of
  EURPLN, EURUSD and USDPLN executions (5,000 quotes a book) reading by batch
  read 19,950 book rows, and 9,976 with `reuse_rows:10000`, which is what
  reading the window whole reads (9,974), for 2.3 MB more in the transform. On
  the default pool only one pair has two legs, nothing is shared and the cache
  only costs memory.
- Rows *scanned* by the HDB were not measured: the stand-in holds its tables in
  memory. A production HDB is a measurement still to be made.

#### Why batches do not run concurrently

The issue asked for bounded concurrency "only if the benchmark justifies it". It
does not, and it is not built. In the default regime publish is 88% of a
window's time (1,144 ms of 1,303 ms with 200 quotes a book), a single write that
cannot be split across batches without giving up the
one-write-one-coverage-record rule, and the reads that concurrency would overlap
are 1% of the transform (43 ms of the HDB's time in 4,057 ms with 20,000 quotes
a book). Where compute does dominate (large books), batching already took it
from 63 s to 4 s, and running those batches on secondary threads (`peach`) would
need `-s`, a transform that is safe on secondary threads, and the reads and the
cache (which mutate globals and use a handle) kept on the main thread. That is a
different design and it would only be worth building if a real HDB's transform
stays compute-bound after narrowing; the benchmark above is the place to find
out.

**What the worker claims.** That its transform, given a group's rows and the
context declared for them, produces that group's output whatever other groups
are in the window. The framework cannot check that; compare a batched with an
unbatched run, as `tests/q/test_bounded_batching.q` does for
`hdb_fx_markouts_backfill`. That worker is **not** batched as shipped: enabling
it is a decision for `scripts/bench/batching.q` on a real HDB.

### Run spec

What one run of a worker is asked to do, passed to `.qpipe.job.<worker>.init` ---
or by `uqs job backfill <worker> --source-version V --from F --to T`.

  | key              | type      | meaning                                                                                                                         | refused when                  |
  | ---              | ---       | ---                                                                                                                             | ---                           |
  | `source_version` | symbol    | which release of the upstream data this run's coverage is recorded under. Coverage under one release says nothing about another | null                          |
  | `range_from`     | timestamp | inclusive start                                                                                                                 | not before `range_to`         |
  | `range_to`       | timestamp | exclusive end                                                                                                                   | not after `range_from`        |

A run has a mode, set by `uqs job backfill --mode M` (or `UQF_MODE`). Each opens
and writes strictly more than the one before:

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
refused together with an explicit `run`. `uqs job backfill --on-conflict S` (or
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

  | key                 | required                            | type                                                                                                                                                                   | meaning                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | refused when                                                                                               |
  | ---                 | ---                                 | ---                                                                                                                                                                    | ---                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | ---                                                                                                        |
  | `procname`          | no                                  | symbol, default `` `<job>1 ``                                                                                                                                          | the TorQ process that runs this job. One generic process script serves every job, and the name it was started under decides which                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | not a symbol, or another job already claims it                                                             |
  | `subscribe_to`      | yes                                 | symbol or symbol list                                                                                                                                                  | the tickerplant tables it reads. Empty (`` `symbol$() ``) for a feed, which reads nothing and produces rows on a timer                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | not symbols                                                                                                |
  | `outputs`           | yes                                 | symbol or symbol list                                                                                                                                                  | the tables it publishes onto the tickerplant. Empty for a job that keeps its output in its own process                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | not symbols; `publishes` (retired)                                                                         |
  | `on_batch`          | when `subscribe_to` is non-empty    | function `{[t;x] ...}`                                                                                                                                                 | called with every batch: `t` is the **table name** the batch arrived on (a symbol, not the rows), `x` the rows, carrying the `time` column the tickerplant stamped                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | not callable, or absent while the job subscribes                                                           |
  | `period`            | with `on_timer`                     | timespan, e.g. `0D00:00:01`                                                                                                                                            | how often `on_timer` runs                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | not a positive timespan, or declared without `on_timer`                                                    |
  | `on_timer`          | with `period`                       | function `{[] ...}`                                                                                                                                                    | called every `period`, with nothing: whatever it needs between ticks is state in its own namespace                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | not callable, or declared without `period`                                                                 |
  | `poll`              | no                                  | dict of `fetch`, `normalize`, `next_cursor`, optionally `close`, `start_cursor`, `load`/`save`/`advances` together, `source`, `cursor`, `page_limit`, `row_key`, `ack` | a polling feed's steps, from which `define` builds the `on_timer` (see `src/etl/core/stream_poll.q`). Declared with `period`, with no `on_timer` and no `subscribe_to`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | a missing step, a step that is not callable, `poll` with `on_timer` or `subscribe_to`, or without `period` |
  | `start_with_all`    | no                                  | boolean, default `0b`                                                                                                                                                  | `1b` to start with the stack. Off by default because every started process spends one of the plant's licensed connections                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | not a boolean                                                                                              |
  | `note`              | no                                  | string                                                                                                                                                                 | why it is deployed as it is, shown in [`processes.md`](processes.md)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | not a string                                                                                               |
  | `transform`         | no                                  | symbol                                                                                                                                                                 | the registered transform the job applies. Its backfill twin applies the same one, `tests/q/test_twins.q` reads it, and `uqs job new --twin-of` scaffolds it into the twin                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | not a symbol, or not a registered transform                                                                |
  | `replay`            | no                                  | boolean, default `0b`                                                                                                                                                  | `1b` to rebuild state from the day's tickerplant log at start, before live batches. Publish is muted during the replay (`.qetl.job.stream.replaying` is `1b`), so what was published before the restart is not published again. A time-based rule on a row reads `.qetl.job.stream.clock_of[now;rows]`, the row's receipt `time` live and on replay (#2459), so a restart decides as the running process did (#994; `tests/q/test_replay_equivalence.q` checks it). `posbook`, `fx_positions` and `kafka_flow` declare it                                                                                                                                                                                                                                           | not a boolean, or declared without `on_batch`                                                              |
  | `restore_from`      | with `replay 1b`                    | symbol list                                                                                                                                                            | tables subscribed to, and replayed, only to restore state - not inputs, so the job graph draws no cycle when a job reads its own output back (`kafka_flow` restores its marks from `client_flow`)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | not symbols, or declared without `replay 1b` or `on_batch`                                                 |
  | `on_replayed`       | with `replay 1b`                    | function `{[] ...}`                                                                                                                                                    | called once the replay ends, with publish live again, for what the replay left owed (`kafka_flow` publishes the rows that arrived while it was down)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | not callable, or declared without `replay 1b`                                                              |
  | `on_endofday`       | no                                  | function `{[dt] ...}`                                                                                                                                                  | called with the date that ended, after the plant has rolled its log, so what it publishes opens the new day's log. A position book publishes its opening snapshot there, and `restore_from` replays it after a restart (#943). Trapped and logged: one job's failure doesn't stop the others'                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | not callable                                                                                               |
  | `on_start`          | no                                  | function `{[] ...}`                                                                                                                                                    | called by `start` before anything is opened, for what a running job needs and a declaration cannot hold - `fx_positions` loads its limits file here (#1112). A throw stops the job from starting                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | not callable                                                                                               |
  | `state`             | no                                  | symbol list                                                                                                                                                            | the job's private variables in `.qpipe.job.<name>`. Their values as the file leaves them are what `.qetl.job.stream.reset[job]` restores, so a test never restates them (#967)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | names a variable the namespace lacks                                                                       |
  | `ephemeral`         | with `state`, unless `replay 1b`    | string                                                                                                                                                                 | why losing the job's `state` on a restart is acceptable - the recovery policy a stateful job that does not replay must declare (#1073)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | not a non-empty string; declared with `replay 1b`                                                          |
  | `carry`             | with `replay 1b`                    | dict: `state`, `table`, optionally `cap`                                                                                                                               | state the job carries across days and restarts: `state` is a variable in its namespace, `table` a table it publishes. The shell publishes the state onto `table` at end of day, and on replay sets it from that snapshot and re-applies the batches replayed within `window` (default `0D00:01`) of the log's start, which a fill logged ahead of the snapshot needs (#963, #960). Live, the snapshot is ignored                                                                                                                                                                                                                                                                                                                                                    | not a dict with `state` and `table`; a `table` the job does not publish; without `replay 1b`               |
  | `check`             | no                                  | function `{[rows] ...}`                                                                                                                                                | a quality gate on what the job publishes, applied to every publish (handler, timer, poll). Returns the offending rows as a table in the bounded `check` shape --- `check`, `status`, `detail` --- plus an optional `row` column, the index of the offending row in the batch. Withheld rows are logged and counted in `.qetl.stream_health`, so `uqs status` shows the job `failing`. A check that throws withholds the whole batch. `market_data` declares the `.qf.mkt` crossed-book check (#944); a book's age is left to consumers (#2456)                                                                                                                                                                                                                      | not callable, or declared without `on_fail`                                                                |
  | `on_fail`           | with `check`                        | `` `drop `` or `` `hold ``                                                                                                                                             | `` `drop `` publishes the rows `check` did not name (a failure with no `row`, or a throw, withholds the whole batch); `` `hold `` withholds the whole batch whenever anything fails                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | anything else, or declared without `check`                                                                 |

A job must declare `on_batch`, `on_timer` or both; one with neither would run
nothing and is refused.

**Passthrough options.** Every job kind below (normalizer, horizon, bars,
rolling) accepts `start_with_all`, `note`, `on_start`, `check` and `on_fail` in
its own declaration and forwards them unchanged to `define`, which validates
them once, at declaration time. The list is `.qetl.job.stream.passthrough_keys`,
the one authority: a new `define` option is added there and every kind has it,
and `tests/q/test_stream_job.q` iterates kinds against it. The kind tables below
list only a kind's own keys.

The namespace is always `.qpipe.job.<job>`. A job publishes by calling its own
`publish`, never `.u.upd`: `publish` starts as a stub that throws, and
`.qetl.job.stream.wire` points it at the tickerplant (the runner) or at a
recorder (a test). Never publish a `time` column --- the tickerplant stamps its
own.

Whatever it is wired to, a batch for a table in `src/etl/plant_tables.q` is held
to that table's contract first, `.qetl.plant.problems`: its columns' names,
order and types, and every nested value's element type. A tickerplant stores
column vectors by position, so a batch that does not fit would be stored wrong
rather than refused. One that does not fit is withheld whole, logged, and
counted against the job in `uqs status`, as a failed `check` is. A table the
plant does not carry passes unchecked.

## Normalizer --- `.qetl.job.stream.normalize`

`.qetl.job.stream.normalize[name;decl]`. Several source tables mapped onto one
canonical table, one declared transform per source. `name` is also the canonical
table the normalizer publishes and its `.qpipe.job.<name>` namespace.

  | key              | required | type                                         | meaning                                                                                                                             | refused when                                                                                                                                     |
  | ---              | ---      | ---                                          | ---                                                                                                                                 | ---                                                                                                                                              |
  | `procname`       | no       | symbol                                       | the TorQ process that runs it, as for a streaming job                                                                               | as for a streaming job                                                                                                                           |
  | `output`         | yes      | empty typed table                            | the canonical table's schema, without `time`                                                                                        | not an unkeyed table, no columns, or a `time` column                                                                                             |
  | `input`          | yes      | dict: source table -> transform name         | for each tickerplant table it reads, the `.qetl.transform` transform that maps a batch of it onto `output`                          | empty; a transform not registered, taking more than one input, or whose `output` is not exactly the canonical table --- columns, order and types |

Besides these, it accepts the passthrough options (see the streaming job).

`define` registers the streaming job itself: `subscribe_to` is the keys of
`input`, `outputs` is `name`, and `on_batch` is a dispatcher that trims each
batch to the columns its transform declares, applies it, and publishes. The
normalizer's file never handles a batch.

## Order-outcome contract --- `order_outcomes`

The normalizer that makes order-quality analytics independent of the order
source (#1396). `order_outcomes1` maps each order source's table, columns and
status codes onto the plant table `order_outcomes`; `order_quality1` (a rolling
job) reads only that. It is NOT `executions`: that tape is fills, and a reject
rate needs the rejects, the cancels and the requests still open.

  | column                     | meaning                                                                                                   |
  | ---                        | ---                                                                                                       |
  | `time`                     | the plant's receipt of the normalized row; never an event time                                            |
  | `source_time`              | when the source says the event happened; the rolling job's event time                                     |
  | `source`                   | the feed: the scope of `request_id`                                                                       |
  | `request_id`               | the source's id of one request attempt, unique within `source`; a retry is a new request with a new id    |
  | `source_status`            | the source's own code, verbatim                                                                           |
  | `outcome`                  | `filled`, `rejected`, `cancelled` (the request ended), `unresolved` (a non-terminal update), `unmapped`   |
  | `book`, `product`, `venue` | grouping dimensions, null where the source gives none; a venue is never inferred                          |

Identity is the pair (`source`; `request_id`), two columns rather than a joined
string, so ids of two sources cannot collide.

**Counting policy.** One counted observation is one REQUEST, once, at its latest
terminal outcome by `source_time` (ties by arrival). Per window the statistic
(`.qpipe.transform.order_quality.stats_by`) takes, for each identity, its last
terminal row and counts it if that row is inside the window.

  | case                                              | counted                                                                        |
  | ---                                               | ---                                                                            |
  | transport redelivery of a row                     | once: same identity                                                            |
  | new, partial fill, partial fill, filled           | once, `filled`, at the last update; the first three are `unresolved`           |
  | partial fill then cancel of the remainder         | once, `cancelled`                                                              |
  | amendment / replace                               | `unresolved` while open; same request, or the adapter maps the chain to one id |
  | cancel                                            | once, `cancelled`, in the denominator                                          |
  | reject then retry (new `request_id`)              | twice                                                                          |
  | corrected outcome (rejected, then filled)         | once, the later one; an earlier publication is not republished                 |
  | still open, or a status the adapter does not know | in no count; the row is in the table, so the exclusion is visible              |

`n_orders` is the counted requests (filled + rejected + cancelled); `fill_ratio`
and `reject_ratio` divide by it, and are null for a window with none. A group
with an outcome in the longest window but none counted in a short one publishes
0 and null, as before.

There is no consolidation stage between the adapter and the statistic: the
rolling job's buffer is the declared stateful stage (retention: the longest
window plus the lateness; restart: the kind's replay). Its limit is that
horizon: a redelivery keeps its source time, so one older than the horizon is in
no window, and a correction of a request whose first outcome was evicted counts
as a request of its own.

**Adding a source** is a `status_map`, one `.qetl.transform` into
`.qpipe.job.order_outcomes.order_outcomes`, and one entry in the normalizer's
`input`; the quality transform, the plant table and `order_quality1` do not
change. A row without an id, or a status the map lacks, is `unmapped`.

**Intentional differences from the pre-contract statistic** (for the `orders`
feed, whose rows are one per order at a terminal status and whose ids are
unique, the series is unchanged): a status other than `filled`, `rejected` or
`cancelled` used to be counted in `n_orders`; it is now `unmapped` and counted
nowhere. Two `orders` rows with one `order_id` in the same hour used to be two
orders; they are now one request. Lateness is now judged on the outcome's
`source_time`, so an outcome the normalizer delivers more than 5 s after the
order's stamp is dropped and logged rather than counted.

## Horizon job --- `.qetl.job.stream.at_horizons`

`.qetl.job.stream.at_horizons[name;decl]`. A job that evaluates each event once
its horizon has passed: a markout scores a fill against the market some time
after it, so the fill waits in a queue until the prices that judge it have
arrived (`src/etl/core/horizon.q`, #945). `crypto_markout` and `fx_markout` are
horizon jobs.

  | key                | required                                | type                                                | meaning                                                                                                                                                                                                                                                                                                                                                                                        | refused when                                                                    |
  | ---                | ---                                     | ---                                                 | ---                                                                                                                                                                                                                                                                                                                                                                                            | ---                                                                             |
  | `procname`         | no                                      | symbol                                              | the TorQ process that runs it, as for a streaming job                                                                                                                                                                                                                                                                                                                                          | as for a streaming job                                                          |
  | `events`           | yes                                     | symbol                                              | the tickerplant table carrying the events to evaluate                                                                                                                                                                                                                                                                                                                                          | not a symbol                                                                    |
  | `reference`        | yes                                     | symbol                                              | the tickerplant table they are evaluated against                                                                                                                                                                                                                                                                                                                                               | not a symbol                                                                    |
  | `transform`        | yes                                     | symbol                                              | a `.qetl.transform` with two inputs, **events first, reference second**; its inputs are the job's buffers                                                                                                                                                                                                                                                                                      | not registered, not two inputs, or either input has no `time`                   |
  | `outputs`          | yes                                     | symbol list of one table                            | the table the transform's rows are published onto                                                                                                                                                                                                                                                                                                                                              | not a one-table symbol list; `publishes` (retired)                              |
  | `horizon`          | yes                                     | timespan                                            | how long an event waits after its `time` before it is evaluated                                                                                                                                                                                                                                                                                                                                | not a positive timespan                                                         |
  | `period`           | yes                                     | timespan                                            | the timer that evaluates what is ready                                                                                                                                                                                                                                                                                                                                                         | as for a streaming job                                                          |
  | `max_age`          | no                                      | timespan                                            | the oldest a reference row may be at a horizon and still count; absent, the latest row counts however old                                                                                                                                                                                                                                                                                      | not a timespan                                                                  |
  | `lateness`         | unless `event_time` is `time` (then 0D) | timespan, `0D` for none                             | how much older than its arrival an event's `event_time` can be; history eviction keeps this much extra                                                                                                                                                                                                                                                                                         | not a non-negative timespan                                                     |
  | `by`               | no                                      | symbols, default `sym`                              | the reference's key, for keeping each key's as-of row when `max_age` is absent                                                                                                                                                                                                                                                                                                                 | a column the reference input does not carry                                     |
  | `keep`             | no                                      | function of a batch                                 | which rows of either table to buffer (e.g. `{[x] x[`sym] in .qf.demo.pairs}`)                                                                                                                                                                                                                                                                                                                  | not a function                                                                  |
  | `event_time`       | no                                      | symbol, default `time`                              | the events column an event's clock is read from (e.g. `source_time`, when it happened rather than when the plant received it)                                                                                                                                                                                                                                                                  | not a timestamp column of the events input                                      |
  | `reference_time`   | no                                      | symbol, default `time`                              | the reference column its clock is read from                                                                                                                                                                                                                                                                                                                                                    | not a column of the reference input                                             |
  | `lookback`         | no                                      | timespan, default `0D`                              | how far BEFORE an event its window reaches (a markout at -60s): history back to there, plus each key's older as-of anchor, is kept                                                                                                                                                                                                                                                             | not a timespan of zero or more                                                  |
  | `ready_on`         | no                                      | `` `wall`` (default), `` `reference`` or `` `both`` | `wall`: ready once `horizon` has elapsed since `event_time`. `reference`: ready once every reference key it needs has an anchor at or before `event_time - lookback` **and** has advanced to `event_time + horizon` - wall time alone is not enough. `both` (#1395): the horizon has elapsed on the plant clock **and** the reference covers it                                                | not one of the three; `reference` or `both` with a `by` of more than one column |
  | `retain_by`        | no                                      | symbols, default `by`                               | the reference columns that identify one as-of series - `by` plus, for a reference quoted by several sources, the source: retention keeps each series' latest pre-cutoff row, not just each key's (#1395)                                                                                                                                                                                       | a column the reference does not carry, or not including `by`                    |
  | `shared_reference` | no                                      | boolean, default `0b`                               | `1b`: the jobs of ONE process that read the same reference table keep one buffer of it instead of one each (#2288). The first to see a batch appends the rows any member keeps and hands the buffer on - q shares it by reference - and a trim keeps the union of what every member's pending events can still need. Needs the same reference input shape in every member (`horizon_shared.q`) | not a boolean, or a member that reads the table as another shape                |
  | `legs`             | with `ready_on` `reference` or `both`   | function of the pending events                      | each event's reference keys - a cross pair's direct and cross legs; default its own `by` value                                                                                                                                                                                                                                                                                                 | not a function, or without `ready_on` `reference` or `both`                     |
  | `identity`         | no                                      | symbols                                             | the event columns naming one event: a redelivery replaces the pending one (the last wins), and one already scored is dropped while remembered                                                                                                                                                                                                                                                  | a column the events input does not carry                                        |
  | `remember`         | with `identity`                         | timespan, default `0D01`                            | how long a scored identity is remembered, to drop redeliveries                                                                                                                                                                                                                                                                                                                                 | not positive; without `identity`                                                |
  | `expire_after`     | no                                      | timespan                                            | how long after its `event_time` an event still not ready is given up on: moved to `.qpipe.job.<name>.expired` with the reason (which leg had no reference, or had not advanced), and logged                                                                                                                                                                                                    | not longer than `horizon`                                                       |
  | `ephemeral`        | yes, in effect                          | string                                              | as for a streaming job: the kind holds its queues as `state` and does not replay, so a horizon job says why losing its pending events on a restart is acceptable (#1073)                                                                                                                                                                                                                       | as for a streaming job                                                          |

Besides these, it accepts the passthrough options (see the streaming job).

`define` registers the streaming job itself: `subscribe_to` is `events` and
`reference`, `outputs` is passed through as declared, and `transform` is
declared so a backfill twin applies the same one. It installs into
`.qpipe.job.<name>`: - `pending` and `history`: the buffers, typed as the
transform's inputs; - `on_batch`: projects each batch onto those columns, after
`keep`; - `score_ready[now]`: evaluates and publishes what has waited `horizon`,
then evicts it; - `on_timer`, `now` (`.z.p`) and the unwired `publish`.

Each of `event_time` through `expire_after` (#952) is off until declared, so a
declaration that names none of them behaves exactly as `crypto_markout` does.
`define` also installs `completed` (identities scored, while remembered) and
`expired` (events given up on, with `expired_at` and `reason`). An event that
arrives with a null `event_time` has no horizon: `on_batch` never buffers it,
but moves it to `expired` with reason `no_event_time` and logs it (#2424).

Events are evicted only after a successful publish, so a publish that throws
leaves them queued for the next tick. On every tick the history is trimmed to
what a waiting event can still use: - **with `max_age`:** every row older than
the oldest waiting event minus `max_age` is dropped, except that under
`ready_on` `` `reference `` or `` `both `` each series' latest row before that
is kept as the readiness anchor; - **without it:** each series' latest row
before the oldest waiting event is kept, because it is still that series' as-of
answer. A series is a `by` key, or with `retain_by` the key and its source. When
`event_time` is not receipt time the cutoff also moves back by `lateness`, so a
late event still finds its reference rows.

Either way, what is evaluated is what the full history would have given.

## Bars job --- `.qetl.job.stream.bars`

`.qetl.job.stream.at_bars[name;decl]`. A job that aggregates a stream into
fixed-width time windows: OHLC, VWAP and volume per interval
(`src/etl/core/bars.q`, #946). `exec_bars` is one: `executions` into `exec_bar`;
`crypto_book_1s` is another, a snapshot rather than an aggregate (the last
`crypto_book` state per `venue, sym` per second, `by` `venue`sym).

  | key          | required                                | type                     | meaning                                                                                                                                                                                                                                                                       | refused when                                                                                                                                            |
  | ---          | ---                                     | ---                      | ---                                                                                                                                                                                                                                                                           | ---                                                                                                                                                     |
  | `procname`   | no                                      | symbol                   | the TorQ process that runs it, as for a streaming job                                                                                                                                                                                                                         | as for a streaming job                                                                                                                                  |
  | `events`     | yes                                     | symbol                   | the tickerplant table aggregated                                                                                                                                                                                                                                              | not a symbol                                                                                                                                            |
  | `transform`  | yes                                     | symbol                   | a `.qetl.transform` with ONE input, the events plus a `bar_start` column the kind adds; one row per group per window out, carrying the grouping columns and `bar_start`. A backfill twin applies the same one (cutting the window itself with `.qetl.job.stream.bars.assign`) | not registered or not one input; an input without the `by` columns, `event_time` and `bar_start`, or an output without the `by` columns and `bar_start` |
  | `outputs`    | yes                                     | symbol list of one table | the bar table                                                                                                                                                                                                                                                                 | not a one-table symbol list; `publishes` (retired)                                                                                                      |
  | `width`      | yes                                     | timespan                 | the window length. Windows are half-open `[bar_start, bar_start+width)` on the event time; a row on a boundary opens the window that starts there. A window with no rows has no bar                                                                                           | not a positive timespan, or one that does not divide a day (a window would straddle midnight, #2422)                                                    |
  | `lateness`   | unless `event_time` is `time` (then 0D) | timespan, `0D` for none  | a window closes, and its bar is published once, when its end plus `lateness` has passed                                                                                                                                                                                       | not a timespan of zero or more                                                                                                                          |
  | `period`     | yes                                     | timespan                 | how often closed windows are looked for                                                                                                                                                                                                                                       | as for a streaming job                                                                                                                                  |
  | `by`         | no                                      | symbols, default `sym`   | the grouping columns                                                                                                                                                                                                                                                          | a column the transform's input or output does not carry                                                                                                 |
  | `event_time` | no                                      | symbol, default `time`   | the column the window is cut on                                                                                                                                                                                                                                               | a column the transform's input does not carry                                                                                                           |

Besides these, it accepts the passthrough options (see the streaming job).

A row arriving before its window closes amends it; one arriving after is
dropped, logged, and kept in `.qpipe.job.<name>.dropped` with its reason. On the
plant's own `time` (the default `event_time`) a row is judged by whether its
window has closed, which the job remembers for a day, not by the job's clock
against the window's end (#2423): the plant stamps a row before the job sees it.
At end of day (`on_endofday`, #943) every window of the day that ended is
closed, whatever its lateness. The job replays its log and restores from its own
bars (`restore_from`), so a restart rebuilds the open windows and publishes none
that already went out. Bars are published before the windows are evicted, so a
publish that throws is retried on the next tick. `define` installs `pending`,
`closed`, `dropped`, `publish`, `now`, `on_batch`, `close_ready[now]`,
`on_timer` and `on_endofday` in `.qpipe.job.<name>`.

## Rolling-window job --- `.qetl.job.stream.rolling`

`.qetl.job.stream.at_rolling[name;decl]`. A job that keeps a statistic over the
last N minutes, updated continuously: a venue's reject rate, a client's volume,
at several window lengths at once (`src/etl/core/rolling.q`, #1224). Bars give
fixed windows published once; this gives a window that slides, re-evaluated
every period. `order_quality` is one: `order_outcomes` into `order_quality`,
fill and reject ratios per book over 1m, 5m and 1h, on the outcome's source
time.

  | key          | required                                | type                     | meaning                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | refused when                                                                                                                                                                                 |
  | ---          | ---                                     | ---                      | ---                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | ---                                                                                                                                                                                          |
  | `procname`   | no                                      | symbol                   | the TorQ process that runs it, as for a streaming job                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | as for a streaming job                                                                                                                                                                       |
  | `events`     | yes                                     | symbol                   | the tickerplant table read                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | not a symbol                                                                                                                                                                                 |
  | `transform`  | yes                                     | symbol                   | a `.qetl.transform` with ONE input, the events plus three columns the kind adds: `window` (the window length, a timespan), `as_of` (the instant the windows end at) and `inside` (whether the row is in that window). It returns one row per key per window length carrying the grouping columns, `window` and `as_of`, and reads only the rows `inside`. The input holds every row of the longest window once per window length, so a key quiet in a short window is still a row there: it publishes the statistic's identity (0 for a count or sum, null for a mean or ratio), which each transform documents | not registered or not one input; an input without `window`, `as_of` and `inside` typed as above, the `by` columns and `event_time`; an output without the `by` columns, `window` and `as_of` |
  | `outputs`    | yes                                     | symbol list of one table | the table written                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               | not a one-table symbol list; `publishes` (retired)                                                                                                                                           |
  | `windows`    | yes                                     | timespans                | the window lengths. A window is `(as_of-N, as_of]` on the event time: a row exactly N old is out, a row exactly at `as_of` is in                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | empty, or not all positive timespans                                                                                                                                                         |
  | `lateness`   | unless `event_time` is `time` (then 0D) | timespan, `0D` for none  | a row received more than `lateness` after its event time is dropped, logged, and kept in `.qpipe.job.<name>.dropped` with its reason; one within it is buffered and is in the next publication whose window holds it. When `event_time` is the plant's own `time` there is no distinct receipt, so no row is late                                                                                                                                                                                                                                                                                               | not a timespan of zero or more                                                                                                                                                               |
  | `period`     | yes                                     | timespan                 | how often the job publishes                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | not a positive timespan                                                                                                                                                                      |
  | `by`         | no                                      | symbols, default `sym`   | the grouping columns                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | a column the transform's input or output does not carry, or one the shell adds                                                                                                               |
  | `event_time` | no                                      | symbol, default `time`   | the column the window is read from                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | a column the transform's input does not carry                                                                                                                                                |

Besides these, it accepts the passthrough options (see the streaming job).

Every `period` the job publishes at `as_of` = the clock floored to the period, a
grid a restart and a backfill share; a grid point already published is not
published again. A key is published only if it had a row within the longest
window, and rows older than the longest window plus `lateness` are evicted, so
state is bounded however long the process runs. Cost is one pass per window
length over the longest window's rows per period, O(W x R); a transform that is
a stdev or a drawdown cannot be held as running sums, which is why the statistic
is a transform and not an accumulator.

A rolling window is not day-bucketed: at end of day nothing is published and the
buffer is kept, so the series carries on across midnight. The plant's log is per
day, though, so after a restart the replay holds only today's rows; the job
records `covered_from`, the instant the plant says its log began (the
transport's optional `plant_roll`: TorQ's STP roll, GMT by default; run_stream's
local midnight; the first message where the plant cannot say; never the host's
zone, #2191), and withholds a window length whose span reaches before it (logged
when the set changes) rather than publishing a rate over part of its window. The
job replays its input and restores from its own output (`restore_from`), so the
first publication after a restart is the one an uninterrupted job would make.
`.qetl.job.stream.rolling.compute` and `series` are the pure form, for a
backfill twin reading the HDB at a fixed cadence. `define` installs `pending`,
`dropped`, `last_as_of`, `covered_from`, `withheld`, `publish`, `now`,
`on_batch`, `evaluate[now]`, `on_timer` and `on_endofday` in
`.qpipe.job.<name>`.

## Reactions --- `.qetl.reaction`

Running something when a dataset is published, rather than on a timer. A
reaction fires after a bounded worker publishes a window of `dataset` --- the
one path that notifies, and not on a dry run --- with that window's range.

  | call                                                         | handler                                                          | the job graph learns                                                   |
  | ---                                                          | ---                                                              | ---                                                                    |
  | `.qetl.reaction.on[dataset;name;handler]`                    | `{[dataset;range_from;range_to] ...}`                            | only that it reads `dataset`                                           |
  | `.qetl.reaction.on_writing[dataset;name;outputs;handler]`    | the same                                                         | that it reads `dataset` and writes `outputs`, as asserted              |
  | `.qetl.reaction.on_worker[dataset;worker;spec_fn]`           | `spec_fn` is `{[range_from;range_to] ...}` returning a run spec  | that it writes the worker's `dataset`, derived from its declaration    |

A successful outcome is recorded durably, and a window with one is not re-fired.
When you change a handler, call `.qetl.reaction.revise[dataset;name;`v2\]\`
straight after registering it: an outcome counts only under the revision it was
recorded with, so a new revision owes every covered window again (#1100). The
default is a null revision, which matches only outcomes recorded without one.

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

  | behaviour    | what it does                                                                                                                                                                                                                                                                                                |
  | ---          | ---                                                                                                                                                                                                                                                                                                         |
  | guarantee    | **at least once**, and in memory only: a breach can be sent twice (a timeout after the target acted, a crash after the POST), and what is queued is lost on restart. The receiver must tolerate a repeat                                                                                                    |
  | throttle     | `.qf.port.throttle` on the breach's scope and metric, `alert_period` (5 minutes): a standing breach is delivered once per period                                                                                                                                                                            |
  | retries      | one attempt on arrival, then one per 10 s tick, `max_attempts` (3) in all. No sleeping                                                                                                                                                                                                                      |
  | failure      | after the last attempt the breach moves to `.qpipe.job.alert_sink.dead` with its error, is logged at error, and leaves the throttle so its next report is delivered                                                                                                                                         |
  | URL          | `UQF_SOURCE_CRED_ALERT_SINK` only (a webhook URL carries its token), never logged. There is no `sources.csv` row for it: that file's rows are for sources with a transport and a table to read                                                                                                              |
  | none set     | the job **refuses**: `on_batch` throws naming the variable, which `.qetl.stream_health` counts as failing. It does not idle, since an idle sink drops breaches silently                                                                                                                                     |
  | testing      | `.qpipe.job.alert_sink.post[target;body]` is the one seam that touches the network (it calls `.qetl.webhook.post`, which treats any non-2xx status as a failed delivery); `tests/q/test_alert_sink.q` replaces it with a fake that records calls and fails on demand                                        |

## Job graph: derived, not declared

`.qetl.dag.register[job;decl]` exists, but no job file calls it. The graph is
assembled from the declarations above, so a job's edges cannot disagree with
what it does:

  | call                          | registers                                                                                                                                                                                                   |
  | ---                           | ---                                                                                                                                                                                                         |
  | `.qetl.dag.adopt_workers[]`   | every bounded worker, reading `<source>@<table>` and writing the worker's one declared output                                                                                                               |
  | `.qetl.dag.adopt_streams[]`   | every streaming job and normalizer, by job name, read from `.qetl.job.stream`: inputs are `subscribe_to`, outputs are `outputs`                                                                             |
  | `.qetl.dag.adopt_pipelines[]` | processes that run no declared job (`tap1`), from `src/etl/generated/pipeline_dag.q`, generated from `NON_JOB_PIPELINES`                                                                                    |
  | `.qetl.dag.adopt_feeders[]`   | every continuous feeder                                                                                                                                                                                     |
  | `.qetl.dag.adopt_reactions[]` | every `.qetl.reaction` reaction, with the edges its call declares                                                                                                                                           |

`.qetl.dag.adopt_all[]` runs all five, reactions last.
