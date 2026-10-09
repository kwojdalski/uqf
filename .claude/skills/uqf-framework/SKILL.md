---
name: uqf-framework
description: >-
  The uqf framework on TorQ/kdb+: its layers, rules, verification and debugging,
  in the form an agent can act on. TRIGGER when: working anywhere in this
  repository (uqf) - writing or reviewing q under src/ or scripts/, a streaming
  job, bounded worker, source, transform, normalizer or reaction, the uqs CLI
  (python/uqs), a runtime, profile, deployment or sidecar bundle; or debugging a
  process that is down, idle, failing or publishing nothing. SKIP: plain TorQ
  questions with no uqf code involved (use torq-developer), and pure q-language
  questions (kdb-q-conventions).
---

uqf is a framework that sits **around** TorQ, not a replacement for it. TorQ's
tickerplant, RDB/WDB/HDB, discovery and gateway run unchanged from the vendored
`lib/torq` and `lib/torq-finance-starter-pack`. uqf adds: - a pure-q quant
library (`src/`, flat `.q<abbrev>` namespaces); - a declaration-driven ETL
framework (`src/etl/`, `.qetl.*`); - one TorQ adapter (`.qtorq`,
`scripts/processes/torq_pipeline.q`); - the `uqs` CLI that generates TorQ's
configuration from the declarations and runs the stack.

Why the framework is shaped this way is
`docs/architecture/pipeline-philosophy.md`. Every key a declaration takes is
`docs/reference/pipeline-declarations.md`. This skill tells you what to do, and
where the authority for each fact lives. When the two disagree, the source file
wins, and the gap is worth reporting.

Apply the rules below. Each cites the file that holds or enforces it.

--------------------------------------------------------------------------------

# CORE PRINCIPLES

1. **A claim is true, or absent.** A wrong coverage row, heartbeat or status
   hides a gap that nobody looks for again. A missing one only causes work to be
   redone. Prefer the under-claim every time:
   - publish, then record coverage, then checkpoint
     (`.qetl.job.bounded.runtime.finish_window`);
   - a failed check takes the same path as a failed fetch.

   (`pipeline-philosophy.md` §1)

2. **Derive; do not restate.** Anything written twice is generated, or checked
   against its source. Before writing a list, a schema or a doc table by hand,
   find what it can be derived from. Before editing a generated file, edit its
   source and regenerate (§4, rule D1).

3. **Nothing exists that does nothing.** Ask first which live path reaches a new
   check, capability or abstraction. A check off the publish path is decoration
   (§3).

4. **Refuse at the boundary, by name.** Malformed input stops where it enters,
   with a message naming what is wrong and how to fix it. Silent healing is a
   bug, in tests too (§5).

5. **Read what you call.** Before writing an IPC call, a qSQL query against a
   plant table, or a call into a `.qetl.*` function, read the function or the
   table's definition (`src/etl/plant_tables.q`). Never infer one from its name.

--------------------------------------------------------------------------------

# RULES

## Layers (L)

- **L1** Nothing under `src/` knows TorQ exists. No `.u.upd`, `.servers`, `.lg`,
  `.proc` or `.sub` outside the one owner file per facility:
  - `.lg` only in `src/etl/core/log.q`;
  - `.servers` only in `src/etl/core/worker_runtime.q`;
  - `.proc` only in `.qetl.run.proc_name` (`src/etl/core/run.q`).

  Enforced by `scripts/gates/check_etl_layering.py`.

- **L2** The one namespace that may know TorQ is `.qtorq`
  (`scripts/processes/torq_pipeline.q`). The arrow points one way: `src/` never
  reaches into `scripts/`. A dependency you must guard against being absent is
  pointing the wrong way (`pipeline-philosophy.md` §10).

- **L3** `src/etl/core/` never references a declaring namespace (`.qpipe.job.*`,
  `.qpipe.source.*`). A shell that branches on which instance it runs has
  stopped being generic (`check_etl_layering.py`).

- **L4** Never edit `lib/torq` or `lib/torq-finance-starter-pack`. Extend them
  through the overlays and generated configuration that `uqs` applies after
  every vendored layer (`pipeline-philosophy.md` §8; `docs/faq.md`).

- **L5** Never edit `process.csv`, `database.q` or
  `scripts/processes/process_ports.csv` by hand. All three are generated on
  every `uqs` command, from the declarations and `src/etl/plant_tables.q`
  (`docs/faq.md`, "Can I edit...").

- **L6** The quant library is flat: one `.q<abbrev>` namespace per file, loaded
  by `src/init.q`. Pure functions only, with no state, no I/O and no logging.
  ETL instances nest under `.qpipe.job.<name>` and `.qpipe.source.<name>`; those
  are the only nested namespaces (`src/init.q` header, `src/namespaces.q`).

## Jobs and declarations (J)

- **J1** The unit of work is a **declaration**, not a process. It is one file,
  under:
  - `src/etl/streaming/` for `.qetl.job.stream.define`;
  - `src/etl/workers/` for `.qetl.job.bounded.define`;
  - `src/etl/sources/` for `.qetl.source.define`;
  - `src/etl/transforms/` for `.qetl.transform.define`;
  - `src/etl/reactions/` for `.qetl.reaction`.

  There is no registration step: `src/etl/init.q` globs the directories, and the
  process registry, port and job graph are derived from the declarations
  (`docs/faq.md`, "How do I add a job?").

- **J2** Create jobs with `uqs job new ... --dry-run` first, then without
  `--dry-run`, and follow the `new-job` skill. Never hand-write a declaration
  the scaffold can write. Its output is the current template; a copy in a doc
  goes stale.

- **J3** A job calls `publish` in its own namespace, never `.u.upd`. The runner
  wires `publish` to `.qtorq.publish`, and a test wires it to a recorder with
  `.qetl.job.stream.wire` (`docs/scaffolding/etl.md`).

- **J4** Never publish a `time` column. The plant stamps its own (TorQ invariant
  1, `torq_pipeline.q` header), and `.qtorq.publish` drops one if present.

- **J5** A table a job publishes must be defined in `src/etl/plant_tables.q`. On
  the plant, `.u.upd` onto an undefined table drops the rows silently (invariant
  8). `.qtorq.assert_publishable` refuses to start such a process.

- **J6** Bounded windows are half-open, `[range_from;range_to)`: `>=` on the
  lower bound, `<` on the upper. Source queries are parameterised. The one
  escape for a driver that can't bind parameters is `.qetl.io.odbc.literal`
  (`src/etl/core/singlestore_odbc.q`).

- **J7** Fixtures are deterministic, and they must satisfy the same contract as
  the live source (`.qetl.source.validate_fixture`). A fixture that changes
  between runs makes a failing assertion impossible to attribute.

- **J8** Several tables carrying one fact in different shapes go through a
  **normalizer** (`.qetl.job.stream.normalize`, `src/etl/core/normalizer.q`),
  with one declared `.qetl.transform` per source. Never branch on source in a
  consumer. Examples: `executions` from `trades` and `crypto_trades`, and
  `market_data` from `quote`, `fx_orderbook` and `crypto_book`
  (`docs/architecture/stack.md`).

- **J8b** State a job carries across days is handled in `on_endofday[dt]`, which
  runs after the plant has rolled its log (`.qetl.job.stream.end_of_day`, #943).
  A position book carries: it publishes an opening snapshot that `restore_from`
  replays after a restart. Never assume a job's state empties at midnight on its
  own.

- **J8a** An event that is evaluated some time after it arrives, such as a fill
  marked out at horizons, is a **horizon job** (`.qetl.job.stream.at_horizons`,
  `src/etl/core/horizon.q`). Don't hand-build a pending queue and timer: the
  kind owns the queue, readiness, eviction and the history bound. Event time,
  reference readiness with cross legs, a lookback window, identity and expiry
  are declared keys (#952), and `uqs job new --kind horizon` scaffolds one.

- **J8b** Time bars (OHLC, VWAP, volume per interval) are a **bars job**
  (`.qetl.job.stream.at_bars`, `src/etl/core/bars.q`). Declare the width, the
  allowed lateness and the aggregation as a `.qetl.transform`; the kind owns
  window closing, late rows, end of day and replay. `exec_bars` and its twin
  `hdb_exec_bars_backfill` are the example.

- **J9** A source whose adapter returns other columns than it reads declares
  `raw`: physical table → the columns it reads. `columns`/`types` then describe
  only what it returns. The live check holds each side separately
  (`src/etl/core/source_registry.q`, "RAW INPUTS").

- **J10** `start_with_all 1b` only when the user asks for it. Every started
  process spends one of the licence's 16 connections, with two held back, so 14
  slots in all. `uqs list profiles` shows each profile's cost (`docs/faq.md`).

- **J11** Job names are unique across streaming and bounded jobs: they share
  `.qpipe.job` (`.qetl.job.stream.define`).

## Tables and names (T)

- **T1** Never name a table, column, parameter or local after a q builtin.
  `fills` is why the fill tape is `executions` (`stack.md`). A builtin as a
  parameter fails at call time (`'nyi`, or `'match` for `fills`), not at
  definition. `qlinter` reports these as QF001--QF004.
- **T2** Plant tables are unkeyed: `.qtorq.publish` unkeys a keyed table
  (invariant 2). Keep keyed state private, in the job's namespace, and publish a
  flat snapshot.
- **T3** A plant table carries `time` first, and its `sym` is grouped by default
  (`parse_columns` in `python/uqs/src/uqs/scaffold/columns.py`).

## Derived artefacts (D)

- **D1** Edit the source, then regenerate. Never edit the output:

  | Derived                                                                                                                            | From                           | Regenerate / check                                                              |
  | ---                                                                                                                                | ---                            | ---                                                                             |
  | `src/etl/generated/load_plan.q`, `pipeline_dag.q`, `docs/reference/processes.md`, `process_ports.csv`, `docs/guides/uqs.md` tables | the declarations               | `uv run python scripts/generate/generate_operational_docs.py` (`--check` in CI) |
  | `docs/reference/surfaces/current/`                                                                                                 | the loaded q tree              | `uv run python scripts/generate/contract_surface.py export`                     |
  | rendered `docs/diagrams/*.svg`                                                                                                     | `docs/diagrams/*.d2`           | `scripts/generate/render_diagrams.py`                                           |
  | `docs/reference/environment.md`                                                                                                    | the code reading each variable | checked by `scripts/gates/check_env_reference.py`                               |

- **D2** A name or call that docs mention is checked against the q source
  (`scripts/gates/check_doc_references.py`). Renaming a function means updating
  the prose that names it.

## Configuration and credentials (C)

- **C1** Worker configuration is `.qetl.cfg`, with precedence env (`UQF_<KEY>`)
  > overrides (`set_override`) > yaml > code default. Only env and overrides are
  wired today. `.qetl.cfg.explain` says which layer answered
  (`src/etl/core/worker_config.q` header).
- **C2** A source's credential comes from `UQF_SOURCE_CRED_<SOURCE>`, else its
  row in `sources.csv` (`uqs config sources`). With neither, a worker runs on
  its fixture and warns. The live check never reads a fixture. Credentials and
  secrets never go into a file in the tree.
- **C3** TLS verification stays on. A credential that turns it off is refused by
  `uqs config sources check` (`src/etl/core/live_check.q`).

## Logging (G)

- **G1** Log through `.qetl.log.info|warn|err|dbg|trc[id;text;fields]`, where
  `fields` is a dict (`src/etl/core/log.q`). Not `-1`, `0N!` or `.lg` (L1).
- **G2** DEBUG is per process, switched on at runtime with
  `uqs query ".qetl.log.debug 1b" --port <port>`. A backfill takes `--debug`, or
  `--trace` for every source query. Never switch it on fleet-wide
  (`docs/faq.md`).

## q traps that cost this tree real time (Q)

Each was measured on KDB-X. The full list, with reproductions and the TorQ
invariants, is `uqf-q-traps.md`.

- **Q1** Trap a niladic call with `@[f;::;handler]`, never `.[f;();handler]`.
  The `.` form fires the handler even when `f` succeeds (TorQ invariant 4).
- **Q2** `x -1` applies `x` to `-1`, and `.z.p -0D00:01` applies `.z.p`. Write
  `x - 1` or `x-1` (qlinter QB010).
- **Q3** A line holding only `/` opens a block comment that runs to a line
  holding `\`. Use `/ .` for a blank comment line (QP001).
- **Q4** `like` with an interior `*` beside another `*` throws `'nyi`. Use
  `"*x*"` or `"prefix*"`, or `ss`.
- **Q5** `where col=col`, with a parameter sharing a column's name, matches
  every row. Name parameters apart from columns (`from_ts`/`to_ts`).
- **Q6** `sum` over booleans is an int; `sum` over an empty `each` is `()`.
  Write `sum "j"$x`.

## Verification (V)

- **V1** Run `qlinter` on a q file **before** tracing it by hand (`CLAUDE.md`).
  Its findings are clues to verify against a running q. A clean run is not
  proof.
- **V2** The q suite is `q tests/run_tests.q`, on KDB-X. Python is
  `uv run pytest -q`, from the repository root; its `testpaths` cover every
  package and the gates. Named lanes run with
  `uv run python scripts/test.py <lane>`: `q-unit`, `q-unit-peachq`,
  `q-scripts`, `q-examples`, `q-docs`, `bundles`, `python`, `smoke`.
- **V3** Assert the message, not just the throw. Use
  `.qunit.assertThrows[f;arg;"*expected text*";"why"]`. `assertError` passes on
  a typo in the test itself (`CLAUDE.md`).
- **V4** A test that replaces a global saves and restores it. A test that sets
  `.qetl.load.only` deletes it again. Suites share one process, and leftover
  state makes another suite pass or fail by order.
- **V5** Code must work on KDB-X and on PeachQ. PeachQ can't load nested `\d`
  contexts and runs a flattened copy of the tree
  (`scripts/portable/flatten_contexts.py`). A test it can't run goes into
  `tests/q/peachq_known_gaps.txt` or `python/peachq_known_gaps.txt`, with the
  reason. Both lists are held both ways: a listed test that passes fails the
  build too. A q reason starts `peachq-lacks:`, `tree-bug: #N` or `flattening:`,
  and says why - not the assertion's message (#986). Both lists record CI's
  Linux PeachQ, and only CI's lane is authoritative: off that platform the q
  lane reports "not comparable" (#968), so a local pass is no promise.
- **V6** Every `@eg` line in a qDoc block is executed, by
  `tests/q/run_examples.q` and `.egtest`. One that can't run without a live
  process is listed in `.egtest.needs_live`, with the reason.
- **V7** Run the commit hooks every time. Never use `--no-verify`. To skip a
  hook that's broken on `master`, use `SKIP=<id>`, and give the reason in the
  commit message.

## Working in the repository (W)

- **W1** Work on a GitHub issue in a new worktree, never in the main checkout.
  Other sessions share it. Run `/claim <n>`, then
  `git worktree add .claude/worktrees/issue-<n> -b kwojdalski/issue-<n>-<slug> origin/master`.
  Never run `checkout`, `switch`, `stash` or `reset` in the main checkout
  (`CLAUDE.md`).
- **W2** Never hand-write a bundle job into the tree. A sidecar bundle lives in
  its own folder, and `uqs job install`/`uqs runtime prepare` writes it into a
  working tree. The commit gate refuses its blocks, its job files and its ledger
  (`scripts/gates/check_bundle_blocks.py`, `docs/guides/sidecar-bundles.md`).

--------------------------------------------------------------------------------

# REVIEW CHECKLIST

1. **Layering:** no TorQ name under `src/`; no instance name inside
   `src/etl/core/`; no new owner file for a TorQ facility (L1--L3).
2. **Declaration:** a job is one file plus one declaration, with no hand-edited
   registry, port or `process.csv` (J1, L5).
3. **Publishing:** `publish`, not `.u.upd`; no `time` column; the published
   table is in `plant_tables.q`, unkeyed (J3--J5, T2).
4. **Windows:** `>=` lower, `<` upper; the query parameterised; the fixture
   deterministic and valid against the contract (J6, J7).
5. **Names:** no builtin used as a name; no parameter named after a column it
   filters (T1, Q5).
6. **Error handling:** errors are trapped with `@[f;::;h]` for niladics; a trap
   doesn't swallow a structural bug as if it were "no data yet"; refusals name
   the fix (Q1, principle 4).
7. **Derived artefacts:** sources edited, outputs regenerated in the same
   commit, and the contract surface re-exported when public q names changed
   (D1).
8. **Tests:** they assert messages, restore what they change, cover the negative
   (that the thing did *not* happen), run on both interpreters, or are listed as
   a known PeachQ gap with a reason (V3--V6).
9. **Live path:** every new check or capability is reached from a running path,
   not only from a test (principle 3).
10. **Budget:** a new standing process has a profile or an `--unprofiled`
    reason, and doesn't take `start_with_all` without being asked (J10).

--------------------------------------------------------------------------------

# ADDING A JOB - two stages, with a gate between them

Use the `new-job` skill for the commands. The staging is what matters. Feature
logic layered on plumbing that doesn't work produces bugs that look like logic
errors.

## Stage 1 - the scaffold loads

1. `uqs job new NAME ... --dry-run`, then the same command without `--dry-run`.

2. The scaffold leaves the handler **throwing** and the test **failing**, on
   purpose. The tree must still load:
   ```bash
   q -q <<'EOF'
   \l src/init.q
   \l src/etl/init.q
   -1 "loaded: ",string `NAME in key .qetl.job.stream.jobs;
   exit 0
   EOF
   ```

3. `uv run pytest -q python/uqs/tests/test_no_scaffold_left.py` lists every
   `SCAFFOLDED` placeholder as `path:line`. That list is the to-do list.

**Gate:** the tree loads, the generated files are regenerated, and the only
failures are the scaffold's own stubs. If the tree doesn't load, that's a
scaffold bug: report it, don't work around it.

## Stage 2 - the logic, smallest piece first

1. Write the handler (`on_batch`/`on_timer`) or the source's `query`/`fixture`.
2. Replace the stub test entirely. Wire `publish` to a recorder **before**
   driving the job (`docs/scaffolding/etl.md`, "Testing it without a stack").
3. Write the qDoc blocks, with `@param`, `@return`, `@throws` and an `@eg` that
   runs (V6).
4. Run `q tests/run_tests.q`, `uv run pytest -q`, `qlinter` on the new files,
   and `uv run python scripts/gates/check_q_traps.py`.
5. Run it for real:
   - `uqs up NAME`, which starts it and its log;
   - `uqs summary`, which should show it `up` and wired;
   - or on stock kdb+ with `q scripts/processes/run_stream.q -job NAME`, which
     starts its in-tree producers itself.

--------------------------------------------------------------------------------

# DEBUGGING

Work from the outside in. Most problems stop at step 1 or 2. The full runbook is
`docs/guides/when-it-breaks.md`, and its answers are summarised in
`uqf-operations.md`.

1. **Is it running, and wired?** `uqs summary` shows each process's status
   beside what it subscribes to and publishes. A process that's `up` but idle is
   usually one whose producer isn't running: nothing errors. Start it through a
   profile (`uqs start --profile NAME`), which pulls in its producers.
2. **What does its log say?** `uqs logs <proc> --level ERROR`, or `-f` to
   follow. The last of these lines tells you where rows stop:

   | Last line you see             | Means                                                  |
   | ---                           | ---                                                    |
   | `waiting for the tickerplant` | the plant isn't up                                     |
   | no `first batch received`     | subscribed, but its producer is missing                |
   | no `first rows published`     | batches arrive and the handler publishes nothing       |
   | `on_batch failed`             | the handler threw: the table and error are on the line |
3. **DEBUG for that one process:**
   `uqs query ".qetl.log.debug 1b" --port <port>` (G2).
4. **Look inside it:** `uqs query --proc <proc>` opens a session. There:
   - `` .qetl.job.stream.def `job `` is its declaration;
   - `.qpipe.job.<job>` is its state;
   - `.qtorq.received` and `.qtorq.published` count rows per table.
5. **Reproduce without the stack:** in plain q, load `src/init.q` then
   `src/etl/init.q`, `.qetl.job.stream.wire` the job to a recorder, and call
   `.qpipe.job.<job>.on_batch` with a batch you build.
6. **A backfill has exited by the time you look.**
   - `uqs run status` lists runs that never finished.
   - `uqs run show <run_id>` gives the facts, the log paths and the command that
     resumes.
   - Re-running the same command **is** the resume: covered windows are skipped.
7. **Gaps in a streaming job's output:** `uqs gaps <job> --from .. --to ..`.
   With a backfill twin, it prints the refill commands.
8. **A source won't connect, or is the wrong shape:**
   `uqs config sources check <source>`. Its stages are credential → tls →
   connect → schema → read → output, each with its own diagnostic.
9. **HDB errors** such as `./<date>/<table>. OS reports: No such file`:
   `uqs data hdb-check`, then `--fix`.

--------------------------------------------------------------------------------

# COMPANION FILES

Read one only when the task needs it. They aren't auto-loaded.

- **`uqf-architecture.md`:** the layer map; each namespace and the file that
  holds it; the job-graph flow; who owns which authority; where a fact lives.
  Read before changing a framework shell, adding a module, or answering "where
  does X come from".
- **`uqf-operations.md`:** the `uqs` command map; runtimes, profiles and ports;
  the connection budget; deployment, including `--q-target 4.0`; ODBC; PeachQ.
  Read when starting, stopping, deploying or diagnosing a stack.
- **`uqf-q-traps.md`:** each q trap with its measured reproduction, plus the
  nine TorQ invariants from `torq_pipeline.q`. Read when a q change "works" but
  a value downstream is wrong.
- **Neighbouring skills:**
  - `new-job` for scaffolding commands;
  - `kdb-q-conventions` for q style and precedence;
  - `torq-developer` for TorQ itself;
  - `software-architect` and `bugfinder` for reviews.
