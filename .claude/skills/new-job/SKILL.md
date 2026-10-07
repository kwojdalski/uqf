---
name: new-job
description: >-
  Add a new ETL job to this tree end to end - scaffold it with `uqs job new`,
  implement the handler, write the test that replaces the failing stub, and
  verify it against a real q. Covers every shape `uqs job new` scaffolds: a
  streaming job (a feed, an etl, or a normalizer under `src/etl/streaming/`), a
  bounded worker (a source + worker + transform under `src/etl/sources/` and
  `src/etl/workers/`) and a reaction to a worker's published window (under
  `src/etl/reactions/`). Use when the user asks to add a job, a feed, a
  backfill, a worker, a source, a reaction, or a pipeline stage, or says
  "scaffold", "new-job" or "uqs job new". Distinct from the `pipeline-developer`
  agent, which changes the FRAMEWORK those jobs run on - the lifecycle, the
  coverage ledger, the job graph. This is for adding one job to a framework that
  already works.
---

# Adding an ETL job

You are adding one job to a framework that already handles windowing, retries,
coverage, checkpoints and the job graph. **You do not write any of that.** If
you find yourself writing a loop over days, you are rebuilding
`.qetl.job.bounded` and should stop.

Read `docs/guides/new-pipeline.md` before writing q. It is the long form of
everything below, and it argues the shape rather than just stating it.

## Step 1 --- decide the shape, then scaffold

Two shells, and picking wrong is the only structural mistake here that is
expensive to undo.

  |                                     | Bounded                                                  | Continuous                                         |
  | ---                                 | ---                                                      | ---                                                |
  | You know the range before you start | yes                                                      | no                                                 |
  | It finishes and exits               | yes                                                      | never                                              |
  | Examples                            | a backfill, a nightly window, a restatement              | a tickerplant feed, a poller                       |
  | Shell                               | `.qetl.job.bounded`                                      | `.qetl.job.stream` / `.qetl.job.continuous`        |

```bash
# continuous: subscribes to two tables, publishes one
uqs job new markout2 --subscribe-to trades,quote \
    --publishes my_metric --columns "sym:symbol, value:float" --dry-run

# continuous FEED: subscribes to nothing, publishes on a timer
uqs job new tickfeed --publishes ticks --columns "sym:symbol, px:float"

# bounded worker: source + worker + transform together
uqs job new fx_rates --kind backfill --dataset fx_rates \
    --columns "sym:symbol, mid:float" --width 1D

# the same, reading a database over ODBC rather than a q process
uqs job new ledger --kind backfill --dataset ledger \
    --columns "sym:symbol, amt:float" --transport odbc

# a second worker over that source, into its own dataset: the source is reused
uqs job new fx_rates_1h --kind backfill --dataset fx_rates_1h \
    --source fx_rates --columns "sym:symbol, mid:float" --width 0D01

# normalizer: several tables carrying one fact, one canonical table out -
# NAME is that table, and each source gets a .qetl.transform mapping and an example
uqs job new ticks --kind normalizer --subscribe-to quote,trades \
    --columns "source_time:timestamp, sym:symbol, px:float" --dry-run

# reaction: recompute each time a bounded worker publishes a window of a dataset.
# No process of its own - it runs in the worker's. --writes puts it in the job graph
uqs job new rebuild_positions --triggered-by demo_deals --writes positions --dry-run
```

A reaction's DATASET must be one a bounded worker fills: only a worker's window
fires reactions, so one on a streaming-only table would never run, and the
scaffold refuses it. See `docs/scaffolding/reaction.md`.

A normalizer's mappings start from each source's whole plant schema and a typed
example row, so the file loads; each mapping throws until written. Narrow each
input to the columns its mapping reads, and replace each example with a real row
and the canonical row it becomes.

`--period` sets a feed's tick (default one second), or adds an `on_timer` to an
etl, as markout and fx_positions have. For a backfill, `--partition SYM` scopes
the worker to one slice of its dataset - the only way two workers can fill one
dataset - and `--check` scaffolds a quality check that throws until written.

`--transform passthrough|derive` scaffolds the transform. For a backfill the
default is the pass-through; `derive` declares a `.qetl.transform.define` whose
example fails until `derive` and its expected rows are written. For a streaming
job reading ONE table and publishing ONE, it also writes the whole `on_batch`
(routing, `time` dropped, empty batch, publish) and its tests; `passthrough` is
refused unless both tables have the same shape. Feeds, `--poll`, `--period` and
several tables in or out are refused - use the custom-handler scaffold there.

To undo a scaffold, `uqs job remove NAME --dry-run` shows everything it would
take back out, then run it without `--dry-run`. It refuses a job whose
SCAFFOLDED markers are gone, and keeps any table or source something else still
uses.

`--procname` names the process (default `NAME1`, or `NAME_backfill1`), and
`--start-with-all` puts a streaming job or normalizer in `uqs start all`. Leave
that off unless the user asks: a new process in the default start counts against
the licence's connection budget, and the profile tests pin the default set.

After writing, `uqs job new` regenerates the derived files and, where q is
installed, re-exports the contract surface. Without q it says so, and the
`contract-surface` hook fails until `contract_surface.py export` is run.

**Always `--dry-run` first** and show the user what it would write. It appends
to `plant_tables.q` and two test lists, which are files they may have opinions
about.

`kind` is derived for a streaming job --- no `--subscribe-to` means a feed ---
so do not ask for it. `--publishes` takes a comma list. A table the plant
already defines is published onto as it is - no `--columns`, and none is
accepted - so `--columns` (or `--columns-from TABLE`, which copies an existing
plant table's shape exactly; `venue:g#symbol` groups a column other than `sym`,
`x:list` is a vector column) shapes the one NEW table, and two new tables in one
scaffold are refused. `--subscribe-to` must name tables the plant defines
(`plant_tables.q` or the vendored `quote`/`trade`): a typo is refused rather
than scaffolded into a job that never receives a row, so scaffold a producer
before its consumer.

## Step 2 --- what the scaffold deliberately leaves broken

The generated handler **throws** and the generated test **fails**. That is the
point: a scaffold that left something green would make "generated" and
"implemented" look identical from outside, which is the state that produces a
process reporting `up` while publishing nothing.

So after scaffolding, the tree is in a known state:

- `src/etl/init.q` still LOADS --- the one thing the scaffold never breaks
- `q tests/run_tests.q` fails on your stub, which is where the work starts -
  and, for a job that subscribes and publishes, on its `contract_driver` too
- `uv run pytest python/` fails on
  `test_the_prose_architecture_doc_is_consistent_with_the_registry`: name the
  new process in `docs/architecture/stack.md`. That file is authored prose, so
  the scaffold cannot write it.
- `uv run pytest python/uqs` fails on `test_no_scaffold_left.py`, which lists
  every placeholder still carrying `SCAFFOLDED`, by `path:line`. That list is
  the to-do list: the handler, the test, the job's `note`, and the two below.
- A new streaming job or normalizer is in **no profile** unless you said which:
  `--profile NAME` adds it to one in `python/uqs/src/uqs/model/profiles.py`, and
  `--unprofiled "REASON"` exempts it in `UNPROFILED`. Ask the user which - it is
  a judgement about what runs together. Without either, `test_profiles.py` fails
  until one is written. Bounded workers are exempt - a backfill is triggered,
  not started with the stack.
- For a job that defines a NEW table, the scaffold appends a SCAFFOLDED
  `.qcat.describe` line to `scripts/processes/uqs_catalog.q`. The description is
  for someone choosing a table, so it is yours to write. If the desk should not
  see the table, delete that line and add the table to `.qcat.hidden` with the
  reason - `tests/q/test_catalog.q` refuses a published table that is in neither
  list. Only the prose is scaffolded: columns and types come from `meta` on a
  running process, so a column type the front end cannot coerce no longer blocks
  the entry.
- A streaming job that subscribes AND publishes gets a `contract_driver` in its
  scaffolded test file, which throws until written: the batch
  `tests/q/test_job_output_contracts.q` pushes through the job to hold every
  table it publishes to its plant table by name, order and type -
  `.qtorq.publish` sends columns positionally, so a reordered `select` is
  otherwise silent. A feed needs no driver; it runs on its own timer.

The scaffold also appends the job's table (if it owns one) to `expected` in
`test_stack_tables.q` and regenerates `processes.md` and
`src/etl/generated/pipeline_dag.q`, so neither CI's `--check` nor a hand-kept
list goes red on a job that is merely unfinished.

If the tree does not LOAD after scaffolding, that is a bug in the scaffold, not
in your job. Say so rather than working around it.

## Step 3 --- implement, smallest piece first

Write in this order, and run the suite between each:

1. **The transform or handler body.** For a streaming job that is
   `on_batch[t;x]` or `on_timer[]`; for a bounded worker it is the source's
   `query` and `fixture`.
2. **The test**, replacing the scaffolded stub entirely. Delete
   `test_<name>_is_implemented` --- leaving it beside a real test means a red
   suite forever.
3. **The docstrings.** Every function gets a qDoc block with `@param`,
   `@return`, `@throws` if it can throw, and `@eg`. `docs/man.q` reads these
   when it loads, so they are in the registry with nothing to regenerate.

### The rules that are not optional

- **`publish`, never `.u.upd`.** A job calls `publish` in its own namespace; the
  runner wires it to the tickerplant and a test wires it to a recorder. Nothing
  under `src/` may touch TorQ --- `scripts/gates/check_etl_layering.py` fails
  the build if it does.
- **Never publish `time`.** `.u.upd` stamps its own (invariant 1).
- **Parameterised queries, never concatenation.** The window bounds are
  arguments to a functional select evaluated remotely. Where a driver cannot
  parameterise, there is exactly one escape function, `.qetl.io.odbc.literal`.
- **Half-open windows `[from;to)`** --- `>=` on the lower bound, `<` on the
  upper. One wrong operator double-publishes every boundary row, and the
  duplicate surfaces far from here.
- **A deterministic fixture.** One that changes between runs makes a failing
  assertion impossible to attribute. The scaffold writes one row; replace it
  with rows that exercise what the source actually does.

## Step 4 --- verify against a real q, not just the unit suite

```bash
q tests/run_tests.q                       # the suite; the scaffolded test must be gone
uv run python scripts/test.py q-examples  # every @eg you wrote actually runs
uv run pytest python/ -q                  # the declaration reads back as a process
uv run python scripts/gates/check_q_traps.py
```

Then load the tree on its own, because the unit suite is not the same thing:

```bash
q -q <<'EOF'
\l src/init.q
\l src/etl/init.q
-1 "loaded, registered: ",string `<name> in key .qetl.job.stream.jobs;
exit 0
EOF
```

A job can pass every unit test and still fail to load --- the framework reads
some declarations at LOAD time, which no unit test exercises.

## Step 5 --- what you do NOT have to do

Say this back to the user, because it is the part that surprises people:

- **No `\l` line.** `src/etl/init.q` globs its declaration directories, in plain
  alphabetical order. A table's schema at load time comes from `.qetl.plant`,
  never from another job's namespace - `test_no_job_reads_a_peer_job_at_load`
  refuses a file that does (#731).
- **No test registration at all.** `tests/run_tests.q` globs `tests/q/test_*.q`
  for the file, and the scaffold appends the test's NAMESPACE to that file's
  `nsList` (#350). Both halves matter: the list is kept by hand, and a namespace
  missing from it means the file loads and none of its tests run.
- **No hand-kept job or table list.** `test_every_job_is_registered` derives its
  jobs from `src/etl/streaming/` (#352); `expected` in `test_stack_tables.q` is
  still a deliberate gate, and the scaffold appends the new table to it. The
  Python tests that used to pin every process and table (`test_core.py`,
  `test_schemas.py`) derive them from the registry and from that q list.
- **No regeneration step.** `uqs job new` reruns
  `scripts/generate/generate_operational_docs.py` itself, and `docs/man.q` reads
  the new qDoc blocks when it loads.
- **No hand-copied source for a second worker.** An existing source is reused
  rather than rewritten, and an existing table is not defined again. A dataset
  another worker already fills with no partition is refused:
  `.qetl.job.bounded.define` would refuse the pair at load.
- **No registry entry at all.** The process registry is read from the q
  declarations (`model/declarations.py`): `procname`, the edges, and the
  optional `start_with_all` (default on demand) and `note` all live on the job's
  own `.qetl.job.stream.define` / `.qetl.job.bounded.define`. The port is
  appended to `scripts/processes/process_ports.csv` by the regeneration above,
  so no existing process moves.

## When to stop and ask

- The job needs a framework change --- a new lifecycle hook, a coverage-schema
  column, a new IO manager. That is `pipeline-developer`'s work, not this.
- The job needs a new pricing or execution function under `src/foundation/`,
  `pricing/`, `portfolio/`, `execution/` or `market_data/`. That is
  `uqf-developer`'s.
- The port budget is full. The licence caps a q process at 16 concurrent
  connections and every streaming job opens one to the plant; past that the cap,
  not the configuration, decides what runs. `uqs start` warns, but check before
  adding the seventeenth.
