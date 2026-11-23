# The binding rules for q under `src/` and `scripts/`

Read this before writing or reviewing q in this tree: a framework shell, a
streaming job, a bounded worker, a source, transform, normalizer or reaction, or
a `.qtorq` change. Each rule cites the file that holds or enforces it; when the
file and this page disagree, the file wins and the gap is worth reporting. Why
the framework is shaped this way is `docs/architecture/pipeline-philosophy.md`;
every key a declaration takes is `docs/reference/pipeline-declarations.md`.

The rule groups here are L, J, N, K, G, Q and V. `SKILL.md` keeps W (workflow),
D (generated files), T (tests) and C (the CLI), and summarises L and J.

## Layers (L)

- **L1** Nothing under `src/` knows TorQ exists: no `.u.upd`, `.servers`, `.lg`,
  `.proc` or `.sub` outside the one owner file per facility - `.lg` only in
  `src/etl/core/log.q`, `.servers` only in `src/etl/core/worker_dependencies.q`,
  `.proc` only in `src/etl/core/run.q` (`TORQ_OWNERS` in
  `scripts/gates/policy/etl_layering.py`, enforced by
  `scripts/gates/check.py etl-layering`).
- **L2** The one namespace that may know TorQ is `.qtorq`
  (`scripts/processes/torq_pipeline.q`). The arrow points one way: `src/` never
  reaches into `scripts/`. A dependency you must guard against being absent is
  pointing the wrong way (`pipeline-philosophy.md` §10).
- **L3** `src/etl/core/` never references a declaring namespace (`.qpipe.job.*`,
  `.qpipe.source.*`). A shell that branches on which instance it runs has
  stopped being generic (`check.py etl-layering`).
- **L4** Never edit `lib/torq`, `lib/torq-finance-starter-pack` or any `lib/`
  tree. Extend TorQ through the overlays and the generated configuration `uqs`
  applies after every vendored layer (`uqf-on-torq.md` §3).
- **L5** Never edit `process.csv`, `database.q` or
  `scripts/processes/process_ports.csv` by hand. The first two are rewritten
  under `$TORQDATA` whenever a command bootstraps (`start`, `restart`, `exec`,
  `job backfill`, `data replay`, `data seed` - not `stop` or `status`);
  `process_ports.csv` is an append-only lock written by
  `derive.py operational-docs` (`uqf-on-torq.md` §4).
- **L6** The quant library is seven namespaces - `.qf.px`, `.qf.mkt`,
  `.qf.exec`, `.qf.port`, `.qf.math`, `.qf.util`, `.qf.demo` - loaded by
  `src/init.q`; several files share a domain and none adds a namespace of its
  own. Pure functions only: no state, no I/O, no logging. ETL instances nest
  under `.qpipe.job.<name>` and `.qpipe.source.<name>` (`src/init.q` header,
  `src/namespaces.q`).

## Jobs and declarations (J)

- **J1** The unit of work is a **declaration**, not a process: one file under
  `src/etl/streaming/` (`.qetl.job.stream.define`), `workers/`
  (`.qetl.job.bounded.define`), `sources/` (`.qetl.source.define`),
  `transforms/` (`.qetl.transform.define`) or `reactions/` (`.qetl.reaction`).
  There is no registration step: `src/etl/init.q` globs the directories, and the
  process registry, port and job graph derive from the declarations.
- **J2** Create a job with `uqs job new ... --dry-run`, then without, and follow
  the `new-job` skill. Never hand-write a declaration the scaffold can write:
  its output is the current template, and a copy in a doc goes stale.
- **J3** A job calls `publish` in its own namespace, never `.u.upd`. The runner
  wires `publish` to `.qtorq.publish`; a test wires it to a recorder with
  `.qetl.job.stream.wire` (`docs/scaffolding/etl.md`).
- **J4** Never publish a `time` column. The plant stamps its own (TorQ invariant
  1, `torq_pipeline.q` header), and `.qtorq.publish` drops one if present.
- **J5** A table a job publishes is defined in `src/etl/plant_tables.q`. On the
  plant, `.u.upd` onto an undefined table drops the rows silently (invariant 8);
  `.qtorq.assert_publishable` refuses to start such a process.
- **J6** Bounded windows are half-open, `[range_from;range_to)`: `>=` on the
  lower bound, `<` on the upper. Source queries are parameterised; the one
  escape for a driver that cannot bind parameters is `.qetl.io.odbc.literal`
  (`src/etl/core/singlestore_odbc.q`).
- **J7** Fixtures are deterministic and satisfy the same contract as the live
  source (`.qetl.source.validate_fixture`). A fixture that changes between runs
  makes a failing assertion impossible to attribute.
- **J8** Several tables carrying one fact in different shapes go through a
  **normalizer** (`.qetl.job.stream.normalize`, `src/etl/core/normalizer.q`),
  with one declared `.qetl.transform` per source. Never branch on source in a
  consumer (`executions` from `trades` and `crypto_trades`; `market_data` from
  `quote`, `fx_orderbook` and `crypto_book`).
- **J9** State a job carries across days and restarts is **declared**:
  `carry:`state`table!(...)` on `.qetl.job.stream.define`. The shell publishes
  the snapshot at end of day, restores it on replay (re-applying what was logged
  just ahead of it) and ignores its live echo. `on_endofday[dt]` is for anything
  else a job does at end of day.
- **J10** An event evaluated some time after it arrives (a fill marked out at
  horizons) is a **horizon job** (`.qetl.job.stream.at_horizons`,
  `src/etl/core/horizon.q`). Don't hand-build a pending queue and a timer: the
  kind owns the queue, readiness, eviction and the history bound. Event time,
  reference readiness with cross legs, a lookback, identity and expiry are
  declared keys; `uqs job new --kind horizon` scaffolds one.
- **J11** Time bars (OHLC, VWAP, volume per interval) are a **bars job**
  (`.qetl.job.stream.at_bars`, `src/etl/core/bars.q`): declare the width, the
  allowed lateness and the aggregation as a `.qetl.transform`; the kind owns
  window closing, late rows, end of day and replay. `exec_bars` and its twin
  `hdb_exec_bars_backfill` are the example.
- **J12** A statistic over the last N minutes, updated continuously (a rolling
  reject rate, volume, short-horizon vol), is a **rolling job**
  (`.qetl.job.stream.at_rolling`, `src/etl/core/rolling.q`): declare the window
  lengths, lateness, period and statistic as a `.qetl.transform` over rows
  tagged `window`/`as_of`/`inside`; the kind owns the `(as_of-N, as_of]`
  windows, eviction, late rows and restart. `order_quality` is the example.
- **J13** A source whose adapter returns other columns than it reads declares
  `raw`: physical table → the columns it reads. `columns`/`types` then describe
  only what it returns, and the live check holds each side separately
  (`src/etl/core/source_registry.q`, "RAW INPUTS").
- **J14** `start_with_all 1b` only when the user asks. The licence leaves 14
  connection slots on the tickerplant, and each **tickerplant subscriber** -
  every started non-backfill pipeline, plus rdb1, wdb1, sctp1 and metrics1 -
  spends one, a feed included; only a backfill spends none
  (`model/pipeline_edges.py`; `uqf-on-torq.md` §7). `uqs profile list` shows
  each profile's slots.
- **J15** Job names are unique across streaming and bounded jobs: they share
  `.qpipe.job` (`.qetl.job.stream.define`).

## Names and tables (N)

- **N1** Never name a table, column, parameter or local after a q builtin.
  `fills` is why the fill tape is `executions`. A builtin as a parameter fails
  at call time (`'nyi`, or `'match` for `fills`), not at definition; `qlinter`
  reports these as QF001-QF004.
- **N2** Plant tables are unkeyed: `.qtorq.publish` unkeys a keyed table
  (invariant 2). Keep keyed state private, in the job's namespace, and publish a
  flat snapshot.
- **N3** A plant table carries `time` first, and its `sym` is grouped by default
  (`parse_columns` in `python/uqs/src/uqs/scaffold/columns.py`).

## Configuration and credentials (K)

- **K1** Worker configuration is `.qetl.cfg`, with precedence env (`UQF_<KEY>`)
  > overrides (`set_override`) > yaml > code default; only env and overrides are
  wired today. `.qetl.cfg.explain` says which layer answered
  (`src/etl/core/worker_config.q` header).
- **K2** A source's credential comes from `UQF_SOURCE_CRED_<SOURCE>`, else its
  row in `sources.csv` (`uqs config sources`). With neither, a worker runs on
  its fixture and warns; the live check never reads a fixture. Credentials never
  go in a file in the tree.
- **K3** TLS verification stays on. A credential that turns it off is refused by
  `uqs config sources check` (`src/etl/core/live_check.q`).

## Logging (G)

- **G1** Log through `.qetl.log.ch.info|warn|err|dbg|trc[channel;text;fields]`,
  where `channel` is one of `.qetl.log.channels` and `fields` is a dict carrying
  the worker, source or job name (`src/etl/core/log.q`). Not `-1`, `0N!` or
  `.lg` (L1).
- **G2** DEBUG is per process, switched on at runtime with
  `uqs query PROC ".qetl.log.debug 1b"`. A backfill takes the global `--debug`
  (`uqs --debug job backfill ...`), or `--trace` for every source query. Never
  switch it on across the whole stack.

## q traps that cost this tree real time (Q)

Each was measured on KDB-X; the reproductions and the nine TorQ invariants are
`uqf-q-traps.md`.

- **Q1** Trap a niladic call with `@[f;::;handler]`, never `.[f;();handler]`:
  the `.` form fires the handler even when `f` succeeds (invariant 4).
- **Q2** `x -1` applies `x` to `-1`, and `.z.p -0D00:01` applies `.z.p`. Write
  `x - 1` or `x-1` (qlinter QB010).
- **Q3** A line holding only `/` opens a block comment that runs to a line
  holding `\`. Use `/ .` for a blank comment line (QP001).
- **Q4** `like` with an interior `*` beside another `*` throws `'nyi`. Use
  `"*x*"`, `"prefix*"` or `ss`.
- **Q5** `where col=col`, with a parameter sharing a column's name, matches
  every row. Name parameters apart from columns (`from_ts`/`to_ts`) (QB001).
- **Q6** `sum` over booleans is an int; `sum` over an empty `each` is `()`.
  Write `sum "j"$x`.

## Verification (V)

- **V1** Run `qlinter` on a q file **before** tracing it by hand (`CLAUDE.md`).
  Its findings are clues to verify against a running q; a clean run is not
  proof.
- **V2** Assert the message, not just the throw:
  `.qunit.assertThrows[f;arg;"*expected text*";"why"]`. `assertError` passes on
  a typo in the test itself.
- **V3** A test that replaces a global saves and restores it; one that sets
  `.qetl.load.only` deletes it again. Suites share one process, and leftover
  state makes another suite pass or fail by order.
- **V4** Code works on KDB-X and on PeachQ. Define every name fully qualified
  from the root, with at most one level of `\d` (`check.py nested-contexts`). A
  test PeachQ cannot run goes into `tests/q/peachq_known_gaps.txt` (reason
  `peachq-lacks:` or `tree-bug: #N`, nothing else -
  `scripts/peachq/full_suite.py`) or `python/peachq_known_gaps.txt`. Both lists
  are held both ways, and only CI's Linux lane is authoritative
  (`uqf-testing-and-gates.md` §2).
- **V5** Every `@eg` line in a qDoc block is executed, by
  `tests/q/run_examples.q` and `.qt.man.examples`. One that cannot run without a
  live process is listed in `.qt.man.examples.needs_live`, with the reason.

Running tests, the hooks and the attestation are `uqf-testing-and-gates.md`; the
workflow rules are `SKILL.md` W.

## Review checklist for a q diff

1. **Layering** - no TorQ name under `src/`; no instance name inside
   `src/etl/core/`; no new owner file for a TorQ facility (L1-L3).
2. **Declaration** - one file plus one declaration; no hand-edited registry,
   port or `process.csv` (J1, L5).
3. **Publishing** - `publish`, not `.u.upd`; no `time` column; the table is in
   `plant_tables.q`, unkeyed (J3-J5, N2).
4. **Windows** - `>=` lower, `<` upper; the query parameterised; the fixture
   deterministic and valid against the contract (J6, J7).
5. **The right kind** - a normalizer, carried state, horizon, bars or rolling
   job rather than a hand-built queue, timer or branch on source (J8-J12).
6. **Names** - no builtin as a name; no parameter named after a column it
   filters (N1, Q5).
7. **Error handling** - `@[f;::;h]` for niladics; a trap does not swallow a
   structural bug as "no data yet"; refusals name the fix (Q1).
8. **Derived artefacts** - sources edited, outputs regenerated in the same
   commit, the contract surface re-exported when public q names changed
   (`SKILL.md` D1).
9. **Tests** - they assert messages, restore what they change, cover the
   negative, and run on both interpreters or are listed as a known PeachQ gap
   with a reason (V2-V5).
10. **Live path** - every new check or capability is reached from a running
    path, not only from a test.
11. **Budget** - a new standing process has a profile or an `--unprofiled`
    reason and no `start_with_all` unless asked (J14).

## Adding a job - two stages, with a gate between them

The `new-job` skill has the commands; the staging is what matters. Feature logic
layered on plumbing that does not work produces bugs that look like logic
errors.

**Stage 1 - the scaffold loads.**

1. `uqs job new NAME ... --dry-run`, then the same without `--dry-run`.

2. The scaffold leaves the handler **throwing** and the test **failing**, on
   purpose. The tree must still load:
   ```bash
   env -u UQF_PEACHQ q -q <<'EOF'
   \l src/init.q
   \l src/etl/init.q
   / a streaming job, normalizer or horizon; a backfill is `NAME_backfill in
   / key .qetl.job.bounded.worker_cfg, a reaction is `NAME in
   / exec name from .qetl.reaction.for_dataset `DATASET
   -1 "loaded: ",string `NAME in key .qetl.job.stream.jobs;
   exit 0
   EOF
   ```

3. `uv run pytest -q python/uqs/tests/test_no_scaffold_left.py` lists every
   `SCAFFOLDED` placeholder as `path:line`: that is the to-do list.

**Gate:** the tree loads, the generated files are regenerated, and the only
failures are the scaffold's own stubs. If the tree does not load, that is a
scaffold bug: report it, don't work around it.

**Stage 2 - the logic, smallest piece first.**

1. Write the handler (`on_batch`/`on_timer`) or the source's `query`/`fixture`.
2. Replace the stub test entirely. Wire `publish` to a recorder **before**
   driving the job (`docs/scaffolding/etl.md`, "Testing it without a stack").
3. Write the qDoc blocks, with `@param`, `@return`, `@throws` and an `@eg` that
   runs (V5).
4. Run the new job's q suite and Python tests (`-n 2` at most), `qlinter` on the
   new files and `python3 scripts/gates/check.py q-traps`.
5. Run it for real when asked: `uqs start NAME1 -f` (the job's process,
   `uqs process list` names it), then `uqs status` shows it `up` and wired; or,
   on stock kdb+, `q scripts/jobs/run_stream.q -job NAME`.
