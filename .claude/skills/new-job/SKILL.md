---
name: new-job
description: Add a new ETL job to this tree end to end - scaffold it with `uqf-stack new-job`, implement the handler, write the test that replaces the failing stub, and verify it against a real q. Covers both shapes: a streaming job (a feed, an etl or a normalizer under `src/etl/streaming/`) and a bounded worker (a source + worker + transform under `src/etl/sources/` and `src/etl/workers/`). Use when the user asks to add a job, a feed, a backfill, a worker, a source, or a pipeline stage, or says "scaffold" or "new-job". Distinct from the `pipeline-developer` agent, which changes the FRAMEWORK those jobs run on - the lifecycle, the coverage ledger, the job graph. This is for adding one job to a framework that already works.
---

# Adding an ETL job

You are adding one job to a framework that already handles windowing,
retries, coverage, checkpoints and the job graph. **You do not write any of
that.** If you find yourself writing a loop over days, you are rebuilding
`.qbw` and should stop.

Read `docs/guides/new-pipeline.md` before writing q. It is the long form of
everything below, and it argues the shape rather than just stating it.

## Step 1 — decide the shape, then scaffold

Two shells, and picking wrong is the only structural mistake here that is
expensive to undo.

| | Bounded | Continuous |
|---|---|---|
| You know the range before you start | yes | no |
| It finishes and exits | yes | never |
| Examples | a backfill, a nightly window, a restatement | a tickerplant feed, a poller |
| Shell | `.qbw` | `.qstream` / `.qcont` |

```bash
# continuous: subscribes to two tables, publishes one
uqf-stack new-job markout2 --subscribes trades,quote \
    --publishes my_metric --columns "sym:symbol, value:float" --dry-run

# continuous FEED: subscribes to nothing, publishes on a timer
uqf-stack new-job tickfeed --publishes ticks --columns "sym:symbol, px:float"

# bounded worker: source + worker + transform together
uqf-stack new-job fx_rates --kind backfill --dataset fx_rates \
    --columns "sym:symbol, mid:float" --width 1D
```

**Always `--dry-run` first** and show the user what it would write. It
appends to `registry.py` and `uqf_stack_tables.q`, which are files they may
have opinions about.

`kind` is derived for a streaming job — no `--subscribes` means a feed — so
do not ask for it. `--columns` is only for the table the job OWNS; a job
publishing onto a table someone else defined takes no `--columns` and no
`--publishes`, and declares the edge in q alone.

## Step 2 — what the scaffold deliberately leaves broken

The generated handler **throws** and the generated test **fails**. That is
the point: a scaffold that left something green would make "generated" and
"implemented" look identical from outside, which is the state that produces
a process reporting `up` while publishing nothing.

So after scaffolding, the tree is in a known state:

- `src/etl/init.q` still LOADS — the one thing the scaffold never breaks
- `q tests/run_tests.q` fails **three** times, and not yet on your stub

That last one is not the intent, it is the current state (#350, #352). The
scaffolded test is missing from the failures because its namespace is not in
`nsList`, so it loaded and never ran; the three you get instead are the
hand-kept lists in Step 5. Close those first, then the suite fails once — on
your stub — and that is where the work starts.

If the tree does not LOAD after scaffolding, that is a bug in the scaffold,
not in your job. Say so rather than working around it.

## Step 3 — implement, smallest piece first

Write in this order, and run the suite between each:

1. **The transform or handler body.** For a streaming job that is
   `on_batch[t;data]` or `on_timer[]`; for a bounded worker it is the source's
   `query` and `fixture`.
2. **The test**, replacing the scaffolded stub entirely. Delete
   `test_<name>_is_implemented` — leaving it beside a real test means a red
   suite forever.
3. **The docstrings.** Every function gets a qDoc block with `@param`,
   `@return`, `@throws` if it can throw, and `@eg`. `docs/man.q` is generated
   from these — run `scripts/generate/generate_man_registry.py` and commit
   the result.

### The rules that are not optional

- **`publish`, never `.u.upd`.** A job calls `publish` in its own namespace;
  the runner wires it to the tickerplant and a test wires it to a recorder.
  Nothing under `src/` may touch TorQ — `scripts/gates/check_etl_layering.py`
  fails the build if it does.
- **Never publish `time`.** `.u.upd` stamps its own (invariant 1).
- **Parameterised queries, never concatenation** (ETL-08). The window bounds
  are arguments to a functional select evaluated remotely. Where a driver
  cannot parameterise, there is exactly one escape function,
  `.qodbc.literal`.
- **Half-open windows `[from;to)`** — `>=` on the lower bound, `<` on the
  upper. One wrong operator double-publishes every boundary row, and the
  duplicate surfaces far from here.
- **A deterministic fixture.** One that changes between runs makes a failing
  assertion impossible to attribute. The scaffold writes one row; replace it
  with rows that exercise what the source actually does.

## Step 4 — verify against a real q, not just the unit suite

```bash
q tests/run_tests.q                       # the suite; the scaffolded test must be gone
uv run python scripts/test.py q-examples  # every @eg you wrote actually runs
uv run pytest python/ -q                  # the registry entry resolves
uv run python scripts/gates/check_q_traps.py
```

Then load the tree on its own, because the unit suite is not the same thing:

```bash
q -q <<'EOF'
\l src/init.q
\l src/etl/init.q
-1 "loaded, registered: ",string `<name> in key .qstream.jobs;
exit 0
EOF
```

A job can pass every unit test and still fail to load — the framework reads
some declarations at LOAD time, which no unit test exercises.

## Step 5 — what you do NOT have to do

Say this back to the user, because it is the part that surprises people:

- **No `\l` line.** `src/etl/init.q` globs its three declaration
  directories. Only add a name to its `lead` list if your file reads another
  job's table at load time — and you will know, because the tree stops
  loading with a bare `` `.qsub.<name> ``.
- **No test FILE registration.** `tests/run_tests.q` globs
  `tests/q/test_*.q`. Its namespace list is still kept by hand, though, so add
  `.<name>test` to `nsList` — the scaffold does not (#350), and until you do,
  your test file loads and none of its tests run.
  `test_the_runner_runs_every_suite_it_loads` fails and names the missing one.
- **Two more hand-kept lists** fail on a new job and are not about your job:
  `test_every_job_is_registered` in `test_stream_job.q`, and - if you publish a
  new table - `expected` in `test_stack_tables.q`. The second is a deliberate
  gate; the first is redundant with a generic check (#352).
- **No `schema=` in the registry.** It derives from `table`.
- **No `subscribes=`/`publishes=` in the registry.** They defer to the q
  declaration with `FROM_DECLARATION`, which the scaffold writes for you.

## When to stop and ask

- The job needs a framework change — a new lifecycle hook, a coverage-schema
  column, a new IO manager. That is `pipeline-developer`'s work, not this.
- The job needs a new pricing or execution function under `src/foundation/`,
  `pricing/`, `portfolio/`, `execution/` or `market_data/`. That is
  `uqf-developer`'s.
- The port budget is full. The licence caps a q process at 16 concurrent
  connections and every streaming job opens one to the plant; past that the
  cap, not the configuration, decides what runs. `uqf-stack start` warns, but
  check before adding the seventeenth.
