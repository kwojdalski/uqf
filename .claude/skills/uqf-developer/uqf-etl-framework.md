# The ETL framework - `src/etl/`

Read this before changing a declaration, a framework shell, the plant tables,
coverage, run identity or the job graph, or before answering "how does a job get
from a file to a running process". Every key a declaration takes is
`docs/reference/pipeline-declarations.md` (gated by
`check.py declaration-reference`); why the framework is shaped this way is
`docs/architecture/pipeline-philosophy.md`. Adding one job end to end is the
`new-job` skill; changing the framework itself is the `pipeline-developer`
agent's specialty.

## 1. The shape in one paragraph

A **declaration** is one q file that calls one define verb and registers itself
as it loads. There is no registration step and no list to edit: `src/etl/init.q`
globs the declaration directories, Python reads the same files **as text** to
derive the process registry, ports, `process.csv`, the load plan and the job
graph, and the runners in `scripts/processes/` put each job on TorQ. Nothing
under `src/` knows TorQ exists; the one namespace that does is `.qtorq`
(`scripts/processes/torq_pipeline.q`).

## 2. Load order - `src/etl/init.q`

Precondition: `src/init.q` is loaded and the process runs from the repository
root.

1. The core, as explicit `\l` lines (`init.q:47-156`): status, state, logging,
   coercion and `declared.q` first; then coverage (`intervals.q`,
   `materialisation.q`), mapped books and reference variants, run identity, the
   IO managers, ODBC, heartbeat, the job graph (`dag.q`, then the generated
   `pipeline_dag.q`), `declaration_load.q` and the generated `load_plan.q`; the
   `core/variants/` directory; worker config, runtime and dependencies; the
   source modules; the fetch path; the bounded-worker modules and `twin.q`, then
   the `--once` contract (`once.q`, `bounded_once.q`; `stream_once.q` follows
   `stream_poll.q`); stream health, uptime, retention; the streaming modules
   (`stream_job.q`, `stream_poll.q`, ...), the normalizer, Kafka, horizon, bars,
   rolling, `tick.q`; the `verify_*.q` modules and `compare.q`. Order that is
   load-bearing is explained in the header (`init.q:16-24`).
2. `plant_registry.q`, then `plant_tables.q`, `compression_policy.q`, and
   `synthetic_market.q` (a job filters on `.qf.demo.pairs`) (`init.q:159-167`).
3. The declarations, globbed in this directory order: `sources`, `transforms`,
   `workers`, `streaming`, `reactions` (`init.q:203`). Sources come before
   workers because `.qetl.job.bounded.define` resolves its source at define
   time. Within a directory files load alphabetically and **no file may depend
   on a peer at load time**: a table's schema comes from `.qetl.plant`, never
   another job's namespace (`test_no_job_reads_a_peer_job_at_load`).
4. `.qetl.dag.check_acyclic[]`, and on a full load
   `.qetl.twin.require_resolved[]`.

### Selective loading (`src/etl/core/declaration_load.q`)

A process that knows what it runs sets one of these **before** loading
`src/etl/init.q`; neither is defined in q (setting it is the selection):

- `.qetl.load.only` - process names or job names. Note procnames are not always
  `<job>1`: `fx_markout` runs as `fxmarkout1`. An unknown name throws and tells
  you to regenerate with `derive.py operational-docs`.
- `.qetl.load.only_sources` - source names; loads each source's file and what it
  reaches, no job (what `uqs config sources check` uses).

The closure is not computed in q: `python/uqs/src/uqs/model/load_plan.py` reads
the declarations as text and writes `src/etl/generated/load_plan.q`
(`.qetl.load.order`, `.declares`, `.jobs`, `.procs`, `.sources`, `.topics`).
`.qtorq.load_uqf[only]` sets `.qetl.load.only`, changes to `UQF_ROOT`, loads
`src/init.q` and `src/etl/init.q`, and restores the directory.

## 3. Declaration verbs and kinds

All job instances live at `.qpipe.job.<name>`; sources at
`.qpipe.source.<name>`; transforms at `.qpipe.transform.<name>`. Streaming and
bounded jobs share `.qpipe.job`, so a name is unique across both. Every define
refuses an unknown key by name and treats anything callable except `(::)` as a
function (`src/etl/core/declared.q`).

  | Kind (`uqs job list`) | Verb                                           | File                         | Required keys                                                                                  | Scaffold                                                                 |
  | ---                   | ---                                            | ---                          | ---                                                                                            | ---                                                                      |
  | streaming             | `.qetl.job.stream.define[job;decl]`            | `core/stream_job.q:212`      | `subscribe_to`, `outputs` (+ `on_batch` if it subscribes; `on_batch` or `on_timer`)            | `--kind streaming` (default); a **feed** is one with no `--subscribe-to` |
  | normalizer            | `.qetl.job.stream.normalize[name;decl]`        | `core/normalizer.q:165`      | `output`, `input` (source table -> transform name)                                             | `--kind normalizer`                                                      |
  | horizon               | `.qetl.job.stream.at_horizons[name;decl]`      | `core/horizon.q:401`         | `events`, `reference`, `transform`, `outputs`, `horizon`, `lateness`, `period`                 | `--kind horizon`                                                         |
  | streaming (bars)      | `.qetl.job.stream.at_bars[name;decl]`          | `core/bars.q:269`            | `events`, `transform`, `outputs`, `width`, `lateness`, `period`                                | none - write it by hand after a scaffold                                 |
  | streaming (rolling)   | `.qetl.job.stream.at_rolling[name;decl]`       | `core/rolling.q:419`         | `events`, `transform`, `outputs`, `windows`, `lateness`, `period`                              | none                                                                     |
  | backfill              | `.qetl.job.bounded.define[worker;decl]`        | `core/bounded_worker.q:216`  | `source`, `outputs` (one dataset), `width`, `transform`                                        | `--kind backfill`                                                        |
  | reaction              | `.qetl.reaction.on`, `on_writing`, `on_worker` | `core/react.q:119,148,186`   | dataset, name, handler (and outputs for `on_writing`)                                          | `--triggered-by DATASET`                                                 |
  | -                     | `.qetl.source.define[source;decl]`             | `core/source_registry.q:231` | `source`, `table_name`, `time_column`, `row_key`, `columns`, `types`, `query`, `fixture`, `tz` | written with `--kind backfill`                                           |
  | -                     | `.qetl.transform.define[name;decl]`            | `core/transform.q:163`       | `inputs`, `output`, `fn`, `examples` (optional `as_of`)                                        | `--transform derive`                                                     |

`--kind connector` scaffolds a Python publisher outside q plus the q job that
reshapes what it publishes. The authority for the verbs and the kinds is
`python/uqs/src/uqs/model/kinds.py`: `VERBS` (the six declaration verbs, with
the sub-namespace horizon, bars and rolling install into, and each verb's
`once_runner`), `LISTED_KIND` (what `uqs job list` prints), `REACTION`,
`SCAFFOLD_KINDS` (`streaming`, `backfill`, `normalizer`, `connector`, `horizon`)
and `SCAFFOLD_VERB` (the verb each kind writes; `connector` writes none). Add a
new kind's verb there first; the tests then name every place that still lacks
it. There is no continuous define verb: `.qetl.job.continuous`
(`core/continuous_state.q`) is a cursor helper with no registry, deliberately
with no path to `stage_completion`.

### One unit with `--once` (`core/once.q`, #2798)

`uqs job run JOB --once` (streaming) and `uqs job backfill JOB ... --once`
(bounded) run ONE unit of the job's real processing in a fresh process, then
exit with an outcome. A unit is one poll, one timer cycle, one input batch and
its processing, or one planned window (`.qetl.once.kinds`).

- The framework's runner for the kind ends the process, never the job:
  `.qetl.job.stream.once.run` (`core/stream_once.q`) and
  `.qetl.job.bounded.once.limit` (`core/bounded_once.q`, which keeps the first
  planned window). A job declares nothing for it, so a scaffolded job has
  `--once` the moment it exists; never write a one-shot loop in a job.
- Outcomes and exit codes (`.qetl.once.codes`, read by Python through
  `q_facts`): `done` 0, `failed` 1, `stopped` 4, `no_work` 6. A unit may publish
  nothing, and pending event-time state is never flushed to make it finish.
- A subscriber's wait for its batch is bounded: `--once-timeout DURATION`, 60 s
  by default, 3600 s at most; reaching it is `no_work`.
- The process writes `<status_dir>/once_<procname>.json`. A process of the job
  that is already running is refused, never stopped or reused.
- A new declaration verb names its `once_runner` in `kinds.py`, or
  `python/uqs/tests/test_once_contract.py` refuses it. The q cases are
  `tests/q/test_once.q`.

### Streaming job keys (`core/stream_job.q`)

Optional keys: `procname` (default `<job>1`), `on_batch`, `on_timer` with
`period` (a pair; positive timespan), `poll`, `transform` (a registered
`.qetl.transform`), `on_endofday`, `replay`, `restore_from`, `carry`,
`on_replayed`, `state`, `ephemeral`, and the passthrough keys `start_with_all`,
`note`, `on_start`, `check` with `on_fail` (`` `drop `` or `` `hold ``) and `q`
(which interpreter versions the job needs).

- `carry:`state`table!(...)` (optional `cap`) declares state carried across days
  and restarts; the table must be in `outputs` and `replay 1b` is required
  (`stream_job.q:705-718`). `state` needs `replay 1b` or `ephemeral "<why>"`.
- `poll` (a polling feed) needs `fetch`, `normalize`, `next_cursor`; it needs
  `period` and refuses `on_timer` and `subscribe_to` (`stream_poll.q:86-127`).
  `uqs job preview JOB` runs one page without publishing.
- `publish` starts as `.qetl.job.stream.unwired`, which throws. A runner wires
  it to `.qtorq.publish`; a test wires it with
  `.qetl.job.stream.wire[job;recorder]`.

Instances today (q, full load): 29 streaming declarations; normalizers
`executions`, `market_data`, `order_outcomes`; horizons `crypto_markout`,
`eq_markout`, `fx_markout`, `fx_markout_wide`; bars `book_liquidity_1m`,
`crypto_book_1s`, `exec_bars`; rolling `order_quality`; 14 bounded workers; 13
sources; one reaction (`src/etl/reactions/rebuild_positions.q`, on
`demo_deals`).

### Bounded worker lifecycle (`core/bounded_worker.q`, `core/worker_runtime.q`)

- Accepted keys are the `worker_cfg` columns: `partition`, `procname`, `note`,
  `on_conflict` (default `` `upsert ``), `source_version`, `target_key`,
  `check`, `io`, `facts`, `window_column`, `partition_column`, `revision`,
  `align`, `batching`, `pin`, `twin_of`, `twin_calculation`, `q`.
- `define` stamps `init`, `plan`, `fetch`, `publish`, `checkpoint`, `spec`,
  `run`, `cleanup` into the worker's namespace unless it defined them first.
- Phases: ready -> running -> idle \| completed \| partial \| failed \| stopped.
  A run pins config, recovers unfinished work, replays owed reactions, loads the
  checkpoint, plans windows, runs each window (fetch, ready, transform, check,
  publish), finishes the store.
- **`finish_window` (`worker_runtime.q:485`) is the order that is the
  requirement:** publish the rows, then record coverage, then write the
  checkpoint. Every interruption leaves an under-claim. Each step goes through
  `runtime.commit[dry;...]`, so a dry run suppresses all three.
- Windows are half-open `[range_from;range_to)`; `source_version` is required,
  never an optional filter. A window covered at this version is skipped; at a
  different version it is refetched. Re-running the same command is the resume.
- Declared dependencies resolve through `.servers` in
  `core/worker_dependencies.q` - the one file `etl-layering` lets read it.
- `twin_of` (on a worker) names the streaming job it refills; with a shared
  `transform` a refill re-derives exactly what the job publishes (`core/twin.q`,
  checked from both ends and at the end of a full load).

### Sources (`core/source_*.q`)

- Transports registered: `ipc` (default), `odbc`, `local`, `mock`, `csv`
  (`source_transports.q:108-149`; `.qetl.source.transports[]`). Some in-code
  comments still list only four.
- Optional keys with defaults: `transport`, `credential_example`, `supporting`,
  `raw` (physical table -> the columns the adapter reads, when it returns other
  columns than it reads), `complete_through`, `ready`, `reference_read`,
  `batch_read`. `target` is refused with a migration message.
- `query` is `{[h;range_from;range_to] ...}`, parameterised - never string
  concatenation. The one escape for a driver that cannot bind is
  `.qetl.io.odbc.literal`. `fixture` is niladic and deterministic, and must pass
  the same contract (`.qetl.source.validate_fixture`).
- Credentials: `UQF_SOURCE_CRED_<SOURCE>`, else the source's row in
  `sources.csv` (which names only the variable a secret lives in). Neither: the
  worker runs on its fixture and warns. A live check never reads a fixture. TLS
  verification stays on. Check one with `uqs config sources check SOURCE`
  (stages credential -> tls -> connect -> schema -> read -> output,
  `core/live_check.q`).

### Transforms (`core/transform.q`)

`fn`'s arity equals the number of inputs (plus one with `as_of`). Each example
is `inputs`/`expected`; `.qetl.transform.verify` runs every example plus an
all-empty case, checks schema, equality (float tolerance 1e-9) and determinism.
Redefining a transform replaces it.

### Reactions (`core/react.q`)

A reaction recomputes when a bounded worker publishes a window of a dataset; it
runs in the worker's process. The watched dataset must be one a worker fills
(`require_fillable`); one on a streaming-only table would never fire, and the
scaffold refuses it. Handlers use `.qetl.reaction.published[]` and
`.qetl.reaction.write`. `register` is private.

### Also in the core

  | Area                     | Namespace / file                                                                                                      | One line                                                                                                       |
  | ---                      | ---                                                                                                                   | ---                                                                                                            |
  | verification             | `.qetl.verify`, `verify_*.q`                                                                                          | `uqs job check`: producers of a dataset, captured read-only, compared, completeness, sandboxed fault injection |
  | comparison               | `.qetl.compare`, `compare.q`                                                                                          | `uqs data diff`                                                                                                |
  | reference variants       | `.qetl.reference` (`reference_variant.q`, `reference_library.q`), `.qetl.variant` (`variant_run.q`), `core/variants/` | SELECT x TRANSFORM x COMBINE over mapped books (#2288/#2289); `.qetl.reference.variants` is the registry       |
  | Kafka                    | `.qetl.kafka.topic`, `.qetl.kafka`, `.qetl.kafka.diag`, `.qetl.kafka.decoder`                                         | topics as declared transforms; poll -> publish -> commit; six-stage diagnostics                                |
  | retention                | `.qetl.retention`                                                                                                     | declared pruning plans; nothing is pruned by default                                                           |
  | enrichment, mapped books | `enrich.q`, `mapped_books.q`, `mapped_depth.q`                                                                        | joins against memory-mapped HDB partitions                                                                     |
  | tick                     | `.qetl.tick`                                                                                                          | a TorQ-free tickerplant for `run_stream.q` and tests                                                           |

**Market data is never copied into a heap.** It can be tens of terabytes:
markouts and references `aj` against memory-mapped partitions, selecting by
`date` alone and reading flat top-of-book columns, never the nested depth
(`check.py markout-mapped`, `variant-gather`;
`docs/architecture/markout-memory.md`).

## 4. The plant: `plant_registry.q` and `plant_tables.q`

- `src/etl/plant_registry.q` is the `.qetl.plant` API: `names`, `schema`,
  `shape`, `published`, `columns`, `clocks`, `time_column`, `nested`, `sorting`,
  `equivalence`, `problems`, `vendored`, `own`, `materialise` and more. It must
  load first. **There is no `define` or `table` function.**
- `src/etl/plant_tables.q` holds declarations only: each table is a plain
  assignment,
  `.qetl.plant.<name>:([]time:`timestamp$();sym:`g#`symbol$();...)`, followed by `.qetl.plant.nested`/`sorting`/`equivalence` calls where needed. 42 owned tables; with the vendored `quote`, `trade` and `packets` (parsed from the starter pack's `database.q`) `.qetl.plant.names\[\]` is 45. It has no closing `\d
  .` so `uqs job new` can append; the scaffold never edits `plant_registry.q\`.
- A plant table is unkeyed, carries `time` first (the tickerplant stamps it),
  and groups `sym` by default. Never name a table or column after a q builtin
  (`fills` is why the fill tape is `executions`).
- A job may publish only to a table defined here: `.u.upd` onto an undefined
  table drops the rows silently, so `.qtorq.assert_publishable` refuses to start
  such a process. `tests/q/test_job_output_contracts.q` holds each published
  table to its plant table by name, order and type (`.qtorq.publish` sends
  columns positionally).
- Derived from it: `src/etl/generated/plant_schema.json`
  (`derive.py plant-schema`), the generated `database.q` the segmented
  tickerplant loads as its `-schemafile`
  (`python/uqs/src/uqs/model/plant_schema.py`: the vendored file read fresh,
  plus every owned table), `tableproperties.csv` and the contract surface.
- `/ BEGIN bundle <name>` ... `/ END bundle <name>` blocks appear only when a
  bundle is installed into a working tree; `check.py bundle-blocks` refuses a
  commit that stages them. None are in the committed file.

## 5. Coverage, runs, status, the job graph

  | Concern         | Namespace, file                                         | Facts                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
  | ---             | ---                                                     | ---                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
  | coverage ledger | `.qetl.coverage`, `materialisation.q`, `intervals.q`    | root table `etl_coverage`, columns `.qetl.coverage.schema` (`dataset partition source_version range_from range_to rows_published recorded_at superseded_at run_id touched`); append-only and bitemporal (`superseded_at` = `0Wp` while current; `supersede` is the only updater); write `stage_completion` -> `stage_written`; read `is_covered[ds;part;version;as_of;from;to]`, `missing`, `require_covered`, `history`; intervals `require_interval`, `compose`, `gaps` |
  | run identity    | `.qetl.run`, `run.q`                                    | `etl_runs` and `etl_run_meta`; `begin` persists before returning, `finish` updates; `current[]` is the null GUID outside a run; `proc_name[]` is the one `.proc` read; `uqs job runs list/show/audit`                                                                                                                                                                                                                                                                     |
  | status files    | `.qetl.status`, `status.q`                              | `airflow_status_<instance>.txt` under `UQF_STATUS_DIR` (else `TORQDATA`); states `starting running idle completed failed stopped`; a cross-repo contract read by `python/uqf_airflow_provider` and the frontend                                                                                                                                                                                                                                                           |
  | liveness        | `.qetl.hb`, `.qetl.uptime`, `.qetl.stream_health`       | `worker_heartbeat`; `etl_stream_uptime` sessions (what `uqs job gaps` reads - a weak claim, kept apart from coverage); per-job batch health files                                                                                                                                                                                                                                                                                                                         |
  | job graph       | `.qetl.dag`, `dag.q`                                    | nodes adopted from the registries: `adopt_workers`, `adopt_feeders`, `adopt_streams` (from `subscribe_to`/`outputs`), `adopt_pipelines` (the generated `pipeline_dag.q`, which carries only processes with no job - `tap1`), `adopt_reactions`; edges match outputs to inputs; `check_acyclic`, `topological`, `layers`, `d2`                                                                                                                                             |
  | IO managers     | `.qetl.io`, `io_manager.q`, `io_hdb*.q`, `io_remote*.q` | required `write`, optional `write_keyed`, `finish`, `flush`, `recover`, `on_ready`; strategies `upsert replace ignore append fail`; `memory` (default), `discard`, `hdb`, `hdb_scoped`, `remote.manager` (attachments); `torq_backfill.q` swaps in the HDB writer                                                                                                                                                                                                         |
  | worker config   | `.qetl.cfg`, `worker_config.q`                          | precedence env (`UQF_<KEY>`) > overrides > yaml > default; only env and overrides are wired; `.qetl.cfg.explain` says which layer answered                                                                                                                                                                                                                                                                                                                                |
  | logging         | `.qetl.log`, `log.q`                                    | `.qetl.log.ch.info|warn|err|dbg|trc[channel;text;fields]`; channels `.qetl.log.channels` (`lifecycle ipc source transform storage coverage eod config`); `.qetl.log.debug 1b` per process; the only `.lg` reader                                                                                                                                                                                                                                                          |

## 6. The TorQ adapter and the runners

`scripts/processes/torq_pipeline.q` (`.qtorq`) is the only q that talks to TorQ.
Its header lists **nine invariants**, each enforced so a job going through
`publish` never meets them (the header's opening line still says "seven");
`uqf-q-traps.md` reproduces them. The functions: `load_uqf`, `subscribe_etl`
(borrows the access-listed `metrics` proctype), `assert_publishable`, `publish`
(drops `time`, unkeys, enlists atoms), `safe_timer` (`@[f;::;h]` around
`.timer.repeat`), `install_period_handlers` (root `endofperiod`/`endofday`),
`hdb_handle`, `reload_hdb`, `load_source_settings`.

  | Runner                                                  | Runs                                                                                                                       |
  | ---                                                     | ---                                                                                                                        |
  | `scripts/processes/torq_stream.q` (`.qproc.stream`)     | one streaming job as a TorQ process (`-job`, else the procname)                                                            |
  | `scripts/processes/torq_backfill.q` (`.qproc.backfill`) | one bounded worker as a TorQ process (proctype `backfill`, not started with all); exits with `.qetl.job.bounded.exit_code` |
  | `scripts/jobs/run_stream.q` (`.qproc.standalone`)       | any streaming job on stock kdb+, no TorQ: `-plant PORT`, `-job J -tp PORT`, or `-job J -feed F` in one process             |
  | `scripts/jobs/run_backfill.q`                           | one worker in-process, no TorQ (`-worker -version -from -to [-fixture]`)                                                   |
  | `scripts/jobs/attached_*.q`                             | a backfill into an attached external stack's HDB (`docs/guides/attach-existing-torq.md`)                                   |

## 7. `uqs job new` in brief

The `new-job` skill is the procedure. The facts it rests on:

- Kinds: `streaming` (default), `backfill`, `normalizer`, `connector`,
  `horizon`; a reaction is `--triggered-by DATASET`. Flags include
  `--subscribe-to`, `--outputs`, `--schema` / `--schema-from`, `--raw-table` /
  `--raw-columns`, `--source`, `--width`, `--proc`, `--start-with-all`,
  `--transport`, `--period`, `--horizon`, `--poll`, `--cursor-fields`,
  `--profile` / `--unprofiled REASON`, `--slice`, `--check`, `--twin-of`,
  `--transform passthrough|derive`, `--bundle DIR`, `--dry-run`/`-n`
  (`uv run --project python/uqs uqs job new --help` is authoritative).
- The scaffold leaves the handler throwing and the test failing on purpose;
  `python/uqs/tests/test_no_scaffold_left.py` lists every `SCAFFOLDED`
  placeholder. A new streaming job needs `--profile` or `--unprofiled`
  (`python/uqs/src/uqs/model/profiles.py`, `unprofiled.py`).
- `uqs job remove JOB --dry-run` undoes a scaffold; it refuses one whose markers
  are gone without `--override even-if-edited --reason`.
- Code: `python/uqs/src/uqs/cli/job/create*.py`, `cli/job/remove.py`,
  `python/uqs/src/uqs/scaffold/`.

## 8. The Python model that reads the declarations

`python/uqs/src/uqs/model/` parses the q declarations as text and never runs q
for the registry:

  | Module                                                                        | Role                                                                                                  |
  | ---                                                                           | ---                                                                                                   |
  | `declarations.py`                                                             | procname, edges, `start_with_all`, `note`, `q` from each declaration (uses `DECLARATION_VERBS`)       |
  | `registry.py`                                                                 | `PIPELINES`: one `Pipeline` per process, plus `NON_JOB_PIPELINES` (tap1)                              |
  | `pipelines.py`, `pipeline.py`                                                 | derived ports, `process.csv` rows                                                                     |
  | `load_plan.py`                                                                | each process's file closure -> `load_plan.q`                                                          |
  | `profiles.py`, `unprofiled.py`                                                | named start sets (closures over the graph); standing processes no profile reaches, each with a reason |
  | `dependencies.py`, `process_graph.py`, `producers.py`, `pipeline_edges.py`    | the process graph, `uqs graph`, producers per table, edge checks against the scripts                  |
  | `plant_schema.py`, `plant_metadata.py`, `plant_metadata_gen.py`, `schemas.py` | the generated `database.q`, `plant_schema.json`                                                       |
  | `runtime_members.py`, `infra.py`, `connectors.py`, `transports.py`, `jobs.py` | runtime membership, vendored processes beneath profiles, connectors, transports, `uqs job list` rows  |
  | `kinds.py`, `qnames.py`, `q_source.py`, `q_support.py`                        | verbs and kinds; reserved names; q-as-text helpers; the `q` key                                       |

q owns the domain (schemas, keys, transforms, which producer writes what);
Python derives from it and never restates it. The `q-authority-auditor` agent
audits that boundary; `integration-automator` removes a second copy of a fact.
