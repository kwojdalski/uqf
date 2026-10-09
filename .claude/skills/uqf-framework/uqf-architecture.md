# uqf architecture - where everything lives

Read this before changing a framework shell, adding a module, or answering
"where does X come from". Every row names the file that holds the fact. The
authoritative long forms are: - `docs/architecture/stack.md` (the topology); -
`docs/architecture/pipeline-philosophy.md` (the reasons); -
`docs/reference/pipeline-declarations.md` (every declaration key); -
`docs/reference/processes.md`, which is generated (every process and port).

## Four layers, with different rules

  | Layer                 | Where                                                                                                     | Shape                                                                                              |
  | ---                   | ---                                                                                                       | ---                                                                                                |
  | Quant library         | `src/foundation`, `pricing`, `portfolio`, `execution`, `market_data`, `metadata`                          | pure functions, one flat `.q<abbrev>` namespace per file, loaded by `src/init.q`; no state, no I/O |
  | Pipeline framework    | `src/etl/core/`                                                                                           | shells and contracts (`.qetl.*`); knows no instance and no TorQ                                    |
  | Declarations          | `src/etl/sources`, `transforms`, `workers`, `streaming`, `reactions`                                      | one file per instance; registers itself as it loads; `src/etl/init.q` loads them last              |
  | Adapters and surfaces | `scripts/processes/` (`.qtorq`, runners), `python/` (`uqs`, `uqf_frontend`, the Airflow provider), `web/` | the only code that knows TorQ, HTTP, Airflow or a browser exists                                   |

Load order: `src/init.q`, the library, then `src/etl/init.q`, which loads the
core and then the declarations. A process that names what it runs sets
`.qetl.load.only`, with job or process names, or `.qetl.load.only_sources`, with
source names, **before** `src/etl/init.q`. It then loads only that closure, from
`src/etl/generated/load_plan.q` (`src/etl/core/declaration_load.q`). Unset, the
whole tree loads.

## The quant library: file to namespace

  | File                    | Namespace  |     | File                           | Namespace |
  | ---                     | ---        | --- | ---                            | ---       |
  | `foundation/stats.q`    | `.qstats`  |     | `portfolio/positions.q`        | `.qpos`   |
  | `foundation/ccy.q`      | `.qccy`    |     | `portfolio/allocation.q`       | `.qalloc` |
  | `foundation/daycount.q` | `.qdcf`    |     | `portfolio/desk_positions.q`   | `.qdesk`  |
  | `foundation/rates.q`    | `.qrates`  |     | `portfolio/limits.q`           | `.qlimit` |
  | `foundation/calendar.q` | `.qcal`    |     | `portfolio/risk.q`             | `.qrisk`  |
  | `foundation/schema.q`   | `.qschema` |     | `execution/execution.q`        | `.qexec`  |
  | `foundation/render.q`   | `.qrender` |     | `market_data/book.q`           | `.qbook`  |
  | `pricing/cross.q`       | `.qcross`  |     | `market_data/microstructure.q` | `.qmicro` |
  | `pricing/forwards.q`    | `.qfwd`    |     | `market_data/dqchecks.q`       | `.qdqc`   |
  | `pricing/options.q`     | `.qopt`    |     | `metadata/metatables.q`        | `.qmeta`  |

The directories are organisational: a namespace doesn't follow its path. The
mapping is the list in `src/init.q`. `src/namespaces.q` (`.qns`) is the one
enumeration of namespaces that every tool goes through.

The three position models answer different questions, and a fourth would have to
justify itself the same way: - `.qpos` is a running book with a weighted
average; - `.qalloc` matches lots across any dimensions; - `.qdesk` is a running
netted book across any dimensions, with no cost basis.

## The pipeline framework: file to namespace

  | File                                         | Namespace                                         | What it owns                                                                                                                                                          |
  | ---                                          | ---                                               | ---                                                                                                                                                                   |
  | `stream_job.q`, `stream_poll.q`              | `.qetl.job.stream`                                | streaming declarations, `wire`, `start`, polling feeds                                                                                                                |
  | `normalizer.q`                               | `.qetl.job.stream.normalizer`                     | several shapes in, one canonical table out                                                                                                                            |
  | `bounded_worker.q`                           | `.qetl.job.bounded`                               | bounded workers: plan, fetch, transform, publish, cover                                                                                                               |
  | `worker_runtime.q`                           | `.qetl.job.bounded.runtime`                       | sequences a run; the only reader of `.servers`                                                                                                                        |
  | `backfill_state.q`                           | `.qetl.job.bounded.state`                         | checkpoints, locks, contracts                                                                                                                                         |
  | `continuous_state.q`                         | `.qetl.job.continuous`                            | cursor state for continuous jobs                                                                                                                                      |
  | `source_contract.q`                          | `.qetl.source`                                    | the source registry, validation (`raw` and output contracts), transports (`ipc`, `odbc`, `local`, `mock`), credentials, `sources.csv`, time zones, coercion, fetching |
  | `transform.q`                                | `.qetl.transform`                                 | declared transforms with worked examples (`verify`)                                                                                                                   |
  | `materialisation.q`, `intervals.q`           | `.qetl.coverage`                                  | the append-only coverage ledger                                                                                                                                       |
  | `io_manager.q`, `io_hdb.q`                   | `.qetl.io`                                        | writes (`write` only - nothing reads back through it)                                                                                                                 |
  | `singlestore_odbc.q`                         | `.qetl.io.odbc`                                   | ODBC open, close, `run_sql`, `literal`                                                                                                                                |
  | `run.q`                                      | `.qetl.run`                                       | run identity and ledger; the only reader of `.proc`                                                                                                                   |
  | `react.q`                                    | `.qetl.reaction`                                  | recomputing when a worker publishes a window                                                                                                                          |
  | `dag.q`                                      | `.qetl.dag`                                       | the job graph, adopted from the registries                                                                                                                            |
  | `declaration_load.q`                         | `.qetl.load`                                      | selective loading from the load plan                                                                                                                                  |
  | `live_check.q`                               | `.qetl.livecheck`                                 | `uqs config sources check`'s q half                                                                                                                                   |
  | `status.q`                                   | `.qetl.status`                                    | the status file that Airflow and the frontend read, a cross-repo contract                                                                                             |
  | `uptime.q`, `heartbeat.q`, `stream_health.q` | `.qetl.uptime`, `.qetl.hb`, `.qetl.stream_health` | uptime sessions, heartbeats, batch health                                                                                                                             |
  | `log.q`                                      | `.qetl.log`                                       | logging; the only reader of `.lg`                                                                                                                                     |
  | `worker_config.q`, `config_audit.q`          | `.qetl.cfg`, `.qetl.cfg.audit`                    | typed configuration, and its precedence                                                                                                                               |
  | `tick.q`                                     | `.qetl.tick`                                      | a TorQ-free tickerplant, for `run_stream.q` and tests                                                                                                                 |
  | `coercion.q`                                 | `.qetl.coerce`                                    | column coercion                                                                                                                                                       |

## Two frameworks, one adapter

  |            | bounded (batch)                                          | streaming                                                                          |
  | ---        | ---                                                      | ---                                                                                |
  | framework  | `.qetl.job.bounded`                                      | `.qetl.job.stream`                                                                 |
  | instance   | `.qpipe.job.<worker>`, under `workers/`                  | `.qpipe.job.<job>`, under `streaming/`                                             |
  | runner     | `scripts/processes/torq_backfill.q`                      | `scripts/processes/torq_stream.q`; `run_stream.q` on stock kdb+                    |
  | lifecycle  | init → plan → fetch → transform → publish → cover → done | wire `publish` → subscribe → `on_batch` per batch, `on_timer` per period → forever |
  | guarantees | coverage, and retry-safe publication                     | freshness, and uptime sessions (`uqs gaps`)                                        |

A bounded worker's `define` stamps the inherited lifecycle methods into its
namespace. A streaming declaration carries its callbacks, and the runner wires
only `publish`. A streaming job and its backfill **twin** share a declared
`transform`, so a refill re-derives what the job publishes (`--twin-of`).

## Who owns which authority

  | Concern                                                                                   | Owner                                                     |
  | ---                                                                                       | ---                                                       |
  | process start, source reads, query failures, checkpoints, run and window counts, coverage | q and TorQ (the framework)                                |
  | task ordering, scheduling, retries, timeouts, concurrency, alert routing                  | Airflow (`python/uqf_airflow_provider`)                   |
  | how the two exchange facts                                                                | structured status (`.qetl.status`), never parsed log text |

There's no scheduler in this tree, deliberately. Adding one would create a
second authority for ordering and retries (`pipeline-philosophy.md` §8).

## Where a fact lives

  | Fact                   | Authority                                                                                            | Derived into                                                      |
  | ---                    | ---                                                                                                  | ---                                                               |
  | a plant table's schema | `src/etl/plant_tables.q` (`.qetl.plant`)                                                             | the generated `database.q` the tickerplant loads                  |
  | which processes exist  | the q declarations (`procname`), plus `NON_JOB_PIPELINES` in Python for processes with no job        | `process.csv`, `docs/reference/processes.md`                      |
  | a process's port       | `scripts/processes/process_ports.csv`, an append-only offset from the base port                      | `process.csv`                                                     |
  | the job graph          | each declaration's `subscribe_to`/`publishes`/`dataset`                                              | `.qetl.dag`, `src/etl/generated/pipeline_dag.q`, profile closures |
  | what a process loads   | each declaration's references, read as text by `python/uqs/src/uqs/model/load_plan.py`               | `src/etl/generated/load_plan.q`                                   |
  | query policies         | `scripts/torqconfig/dataaccess/querypolicy.csv`, capped by `.checkinputs.policyceiling`              | enforced on `gateway1` (`docs/architecture/query-policies.md`)    |
  | the frontend catalog   | `scripts/processes/uqs_catalog.q` (`.qcat`)                                                          | the browser's table list                                          |
  | an installed bundle    | its own folder (`bundle.json`), plus the ledger `src/etl/installed_bundles.json`, written on install | blocks and job files in a working tree, never committed           |

## The data flow, in one paragraph

Feeds publish raw tables, such as `fx_orderbook`, `trades` and `crypto_book`.
They come from uqf's own synthetic feeds, external Python publishers (Databento,
Kafka, cryptorust) or `cryptomock1`. Normalizers fold the shapes that carry one
fact into canonical tables: `executions` and `market_data`. Consumers read the
canonical tables: - `posbook1` marks positions at mids; - `fxpositions1` nets
fills, with limit breaches; - the markout jobs score fills at horizons; -
`superbook1` → `arbitrage1`/`crossarb1` find cross-source and cross-route
opportunities.

Bounded workers backfill datasets from external sources into the HDB, recording
coverage, and never touch the tickerplant. Reactions recompute derived datasets
when a worker publishes a window. The gateway serves reads through
`.dataaccess.getdata` under per-table policies. The browser reads as the
`browser` role, through `.uqf.browse`.
