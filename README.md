# uqf

A q/kdb+ tree covering the span of an electronic FX (eFX) data platform:
the **data engineering** that lands and backfills market data, the
**quantitative library** that prices and measures it, the **data
processing** that reshapes raw venue feeds into the shapes analytics
expects, and the **operational tooling** — process orchestration, an HTTP
gateway and a browser application — that runs the whole thing as a fleet.

**The names.** `uqf` is the repository and the family - the prefix on every
Python package in it (`uqf_frontend`, `uqf_airflow_provider`).
`uqs`, the *ultimate q stack*, is one of those components rather than the
whole: the process orchestrator and its `uqs` command. The distinction is
what the names are for, so a package named plainly `uqf` would be wrong -
it would claim to be the project while being one part of it.

The q side uses neither: every namespace there is `.q<area>`, from `.qbook`
to `.qmatz`.

These are separate components with separate contracts, not one library with
extras bolted on. See [Components](#components) for what each is and where
it lives; the quantitative library is described under
[Quant modules](#quant-modules).

Every function has a corresponding unit test written against the vendored
[qUnit](https://www.timestored.com/kdb-guides/kdb-regression-unit-tests)
framework - see [Testing](#testing).

## Contents

- [Requirements](#requirements) — KDB-X preferred, what else might work, and why nothing falls back automatically
- [Quick start](#quick-start) — price something, run the fleet, run a backfill, add a pipeline, run the tests
- [Components](#components) — what this tree contains, and which part owns what
- [Quant modules](#quant-modules) — each `src/` pricing file, its namespace and tests
  - [Conventions](#conventions) — quoting, sign, pip factors, naming
- [Further reading](#further-reading) — the `docs/` map and component READMEs
- [Browser application](#browser-application) — the React desk and operations app
- [Testing](#testing) — the four lanes, and what each one proves
- [Documentation](#documentation) — generating browsable API docs from qDoc
- [Licensing](#licensing) — MIT, plus five vendored dependencies with their own terms

## Requirements

**KDB-X is preferred** — KX's own interpreter, and the one everything here is
verified against. The personal edition is free for non-commercial use; it
requires registering for a license at
[kx.com](https://kx.com/kdb-personal-edition-download/).

```
q tests/run_tests.q
```

**Other free q implementations may work for part of the tree.** Nothing in
`src/` reaches for a KDB-X-only feature, and the quant library loads cleanly
on a third-party q; KX's own kdb+ personal edition, being the predecessor
KDB-X is compatible with, should be the closest fit of all. None of them is
verified here, though, and a third-party q tried against this tree got
partway through `tests/run_tests.q` before failing — so treat another
interpreter as worth trying for the pure-q modules in `src/`, not as a
substitute for a green suite. The fleet is a separate question again: the
stack under `scripts/` runs on vendored TorQ, which has its own
compatibility surface.

The tooling will not pick one for you. There is deliberately no *automatic*
fallback anywhere in it: a suite that passed against something the code is
not verified on is worse than one that does not run, so every entry point
skips rather than substituting. Choosing another interpreter is therefore
explicit — `scripts/test.py` reads `$Q` and `$QHOME`:

```
Q=/path/to/q QHOME=/path/to/qhome scripts/test.py q-unit
```

Run everything from the repository root - the load scripts use
paths relative to it (e.g. `src/foundation/stats.q`).

### Also needed, by component

| Tool | For | Required? |
|---|---|---|
| **KDB-X** | everything in `src/`, `scripts/` and `tests/` | preferred — see [above](#requirements) for what else may work |
| **[uv](https://docs.astral.sh/uv/)** | the Python packages and every `uqs` command | yes, for the fleet |
| **`qcon`** | attaching a console to a running process: `uqs query --port <p> --console` | no - only that command |
| **`rlwrap`** | line editing and history inside `qcon` | no - `qcon` runs without it |
| **`multitail`** | `uqs multitail`: following process logs one pane per file | no - `uqs logs -f` follows the same files merged into one stream |
| **Node** | building and running the [browser application](#browser-application) — `^22.13 \|\| ^24 \|\| >=26`, the intersection of what the toolchain declares | no - only for `web/` |
| **[`qlinter`](https://github.com/kwojdalski/q-lint)** | linting q source without running it, and diagnostics in an editor | no - suggested when writing or debugging q |

`qcon` is kdb's console client. It ships with some kdb+ distributions and
**not** with the KDB-X personal edition, where `~/.kx/bin/` holds only `q`
and `pg` - so `uqs raw -- qcon ...` is the one documented command that
may not work out of the box. Everything else reaches a running process
through IPC instead: `uqs query`, `uqs summary` and
`uqs logs` need nothing beyond what is already installed.

`torq.sh` resolves both through `$QCON` and `$RLWRAP`, which
`uqs`'s `build_env()` sets, so a differently-named or
differently-located binary is a variable to set rather than a patch.

### `qlinter`, suggested

[q-lint](https://github.com/kwojdalski/q-lint) is a separate project - a q
linter in Rust that **never executes the source it reads**, which is what
makes it safe to point at a file mid-debug and to run on every keystroke in an
editor. It is not needed to build, test or run anything here; it is suggested
because reading a diagnostic is faster than tracing a q bug by hand, and
because several of this tree's recurring traps are among the things it checks
for - a builtin used as a parameter name, a bare `/` opening a comment block,
a legacy `datetime`.

```
cargo install --git https://github.com/kwojdalski/q-lint --locked
qlinter src/ tests/q/
qlinter --explain QF001
```

It reads this repository's `[tool.q-lint]` section in `pyproject.toml` for
exclusions, so the vendored TorQ tree and nested agent worktrees are skipped
without anyone passing `--exclude`.

For diagnostics in an editor rather than a terminal, `qlinter --lsp` is a
language server; its repository has the VS Code extension and the Neovim and
Helix configuration.

## Quick start

Five entry points, one per thing you might have come for. None depends on
the others - the library prices without a running process, and the fleet
runs without anyone loading the library by hand.

**Price something.** The quant library is pure functions and no processes,
so this needs nothing but the interpreter:

```
q src/init.q
q).qopt.gk_call[1.10;1.12;0.045;0.02;0.10;0.75]   / Garman-Kohlhagen call premium
q).qfwd.fwd_simple[1.10;0.05;0.02;1]              / CIRP outright forward
q).qexec.markout[1;1.1000;1.1010;10000]           / post-trade markout, in pips
```

**Run the fleet.** The TorQ stack - tickerplant, RDB, HDB, gateway, the
feeds and the ETL processes - with generated configuration:

```
uv sync
uv run uqs start all
uv run uqs summary            # up/down, pid, port and heartbeat per process
uv run uqs query "count quotes" --port 6052   # 6052 = base port + 2 = rdb1
```

**Run a backfill.** A bounded worker takes its range from the environment
and exits once the window is covered, which is why it is a job an operator
or Airflow triggers rather than a process that starts with the stack. It
registers with discovery, so the fleet above has to be up:

```
UQF_BACKFILL_WORKER=demo_deals_backfill \
UQF_BACKFILL_VERSION=v1 \
UQF_BACKFILL_FROM=2026.09.13D00:00 \
UQF_BACKFILL_TO=2026.09.15D00:00 \
  uv run uqs start deals_backfill1
```

All four variables are required together: the process refuses to start and
names every missing one at once, because a backfill that silently defaulted
its range would publish the wrong window and record coverage for it.

**Add a pipeline.** `uqs new-job` scaffolds one of three shapes, and
`--dry-run` lists every file it would create or append to without writing
any of them:

```
# a streaming job: reads quote, publishes a table of its own
uv run uqs new-job spread_stats --subscribes quote \
    --publishes spread_stats --columns "sym:symbol, spread_pips:float" --dry-run

# a feed: subscribes to nothing, publishes on a timer
uv run uqs new-job rates_feed --publishes rates \
    --columns "sym:symbol, mid:float" --dry-run

# a bounded worker, with its source and transform, one day per window
uv run uqs new-job eod_rates --kind backfill --dataset eod_rates \
    --columns "sym:symbol, mid:float" --width 1D --dry-run
```

The handler it writes throws and the test it writes fails, on purpose: a
scaffold that left something green would look implemented from outside. The
output ends with what is still yours to write.
[Adding a data pipeline](docs/guides/new-pipeline.md) walks one through end
to end.

**Check it all still works.** One lane per layer, listed under
[Testing](#testing):

```
./scripts/test.py all
```

`all` is every lane except `coverage` and the two that reach outside the
process: `smoke`, which checks a live external source's metadata against its
declared contract (ETL-20) and so needs credentials and a reachable source,
and `stack-smoke`, which restarts the fleet and watches it.

## Components

Seven of them, each with its own contract and its own place in `docs/`. A
change usually belongs to exactly one.

| Component | Where | What it does |
|---|---|---|
| **Data engineering** | [`src/etl/`](src/etl) | The pipeline framework: bounded and continuous workers, normalizers that spell many sources one way, a bitemporal coverage ledger, run identity, IO managers, source contracts, and a job graph derived from declared inputs and outputs. Asset-oriented, in the sense [the philosophy note](docs/architecture/pipeline-philosophy.md) sets out |
| **Quant library** | [`src/foundation/`](src/foundation), [`pricing/`](src/pricing), [`portfolio/`](src/portfolio), [`execution/`](src/execution) | Pure functions, no I/O: CIRP forwards and swap points, Garman-Kohlhagen options and Greeks, position risk and VaR, execution analytics. [Detailed below](#quant-modules) |
| **Data processing** | [`src/market_data/`](src/market_data) | Reshaping and signal extraction — wide venue books folded into vector columns, LOB microstructure features, data-quality checks that report rather than throw |
| **Fleet and orchestration** | [`scripts/`](scripts), [`python/uqs/`](python/uqs) | The uqf stack stack: feeds, ETL processes, tap and backfill workers, plus the CLI/MCP orchestrator that generates their configuration and starts, stops and reports on them |
| **Scheduling and access** | [`python/uqf_airflow_provider/`](python/uqf_airflow_provider), [`python/uqf_frontend/`](python/uqf_frontend), [`web/`](web) | An Airflow sensor reading q-side status, an HTTP gateway over the fleet, and the React desk and operations app |
| **Database metadata** | [`src/metadata/`](src/metadata) | Partition-level profiling of an HDB: row counts, temporal span, null density and configurable eFX breakdowns, refreshed under an explicit bound and exposed to TorQ's DQE through a thin adapter. [The guide](docs/guides/metatables.md) |

Authority is split deliberately between them: q and TorQ own process
startup, source reads and coverage; Airflow owns ordering, retries and
alerting. Neither infers the other's facts from log text. That rule, and
the others the tree is built on, are written down in
[the pipeline philosophy](docs/architecture/pipeline-philosophy.md).

## Quant modules

Seventeen modules under `src/`, each in its own flat namespace (`.qstats`,
`.qccy`, `.qfwd`, `.qopt`, `.qrisk`, `.qexec`, `.qmicro` and the rest), each
with a matching test file and a qDoc block per function.

The inventory — what each is for, the namespace convention and the exceptions
to it, and the conventions all of them follow (BASE/QUOTE quoting, `side`,
`pip_factor`, `lower_snake_case`) — is
[**`docs/reference/quant-modules.md`**](docs/reference/quant-modules.md).

The data-engineering component is documented separately: see
[pipeline-framework-gaps.md](docs/architecture/pipeline-framework-gaps.md)
for how `src/etl/` maps onto a Dagster-shaped framework, and
[etl-framework-requirements.md](docs/reference/etl-framework-requirements.md)
for the contract CI holds it to.

## Further reading

[**`docs/`**](docs/README.md) is the map, and lists every page. Five
directories, one question each:

| Directory | Answers |
|---|---|
| [`docs/guides/`](docs/guides/) | *How do I do this?* — running the stack, adding a pipeline, the CI gates |
| [`docs/services/`](docs/services/README.md) | *What does this running service do, and how do I run it?* — one page per service |
| [`docs/architecture/`](docs/architecture/) | *Why is it shaped this way?* |
| [`docs/reference/`](docs/reference/) | *What is the contract?* — the quant modules, environment variables and requirement ids |
| [`docs/integrations/`](docs/integrations/torq/README.md) | *How does this meet something external?* |

Still open, and why: the `decision`-labelled issues.

Component READMEs: [`python/uqs/`](python/uqs/README.md) (the stack CLI),
[`python/uqf_frontend/`](python/uqf_frontend/README.md) (the API),
[`web/`](web/README.md) (the React desk app),
[`python/uqf_airflow_provider/`](python/uqf_airflow_provider/README.md).

## Browser application

The local React [desk and operations app](web/README.md) provides catalog-driven
queries, coverage gaps, backfill status, fleet health, queue, connections and
usage views. It can run against the API through a development proxy or be
served by the API under `/ui/`.

## Testing

```
scripts/test.py q-unit              # deterministic qUnit suite
scripts/test.py q-order             # the same suite, reversed and shuffled
scripts/test.py q-metatables-hdb    # metatable queries against a temporary HDB
scripts/test.py q-examples          # every documented @eg runs, in its own process
scripts/test.py q-scripts           # every worked example under scripts/examples/
scripts/test.py q-backfill-process  # bounded lifecycle, real filesystem, child processes
scripts/test.py q-two-instances     # a second kdb+ process, data moved across the wire
scripts/test.py python              # orchestrator and frontend
scripts/test.py q-coverage          # what the q suite executes
scripts/test.py coverage            # the same, q and Python together
scripts/test.py smoke               # live external metadata check
scripts/test.py stack-smoke         # restart the fleet, watch what it publishes
scripts/test.py all                 # every lane except coverage, smoke and stack-smoke
```

### Coverage

`scripts/test.py coverage` measures what the suites actually **execute** —
line coverage for Python, and **statement and branch coverage for q** through
the [`.cov` library](scripts/dev/coverage.q).

q has no coverage tool, so `.cov` is one, with
[KX's own coverage API](https://code.kx.com/developer/libraries/code-coverage/):

```q
\l scripts/dev/coverage.q
.cov.format.display .cov.run[.qfwd.fwd_cont; 1.10 0.02 0.01 0.5; (enlist `namespaces)!enlist `.qfwd]
```
```
Coverage: 0.3% of 10913 tracked character(s) in 39 function(s)
38 function(s) with incomplete coverage

.qfwd.fwd_simple  0%
X  {[spot;rd;rf;t] <<<spot*.qrates.growth_simple[rd;t]%.qrates.growth_simple[rf;t]>>>}

.qfwd.fwd_cont  100%
   {[spot;rd;rf;t] spot*.qrates.growth_cont[rd-rf;t]}
```

Same three entry points (`.cov.run`, `.cov.format.go`,
`.cov.format.display`), same settings keys (`context`, `functions`,
`ignoreFunctions`, `namespaces`, `ignoreNamespaces`), same results columns
and the same `<<<>>>` / `X` marks.

It counts **lines and branches separately**, so an untaken `$` arm shows up
even though the statement containing it ran, and a loop reports *iterations*
rather than merely whether it ran. Coverage is the share of *characters* in
tracked ranges — KX's definition, which weights a long branch more than a
short one, so a report cannot look green because the untaken paths happen to
be the terse ones.

Three things it is careful about, each of which would otherwise make the
number worse than none:

- **It instruments statement positions only.** `if[c;a;b]` is a control
  statement and its arms take probes; `$[c;a;b]` is a conditional
  *expression* whose arms must not, or the value changes. A `$` arm is
  *wrapped* — `.cov.b[i;arm]` returns the arm — so laziness is preserved and
  an untaken arm is still not evaluated.
- **It follows captured copies.** `.qio.memory` holds `write_memory`, so
  every bounded worker writes through a copy taken before instrumentation.
  Without `.cov.reseed` that function reports as never called while being
  exercised on every window — and the obvious response to such a number is
  to write a test that already exists.
- **It nests.** A suite being measured can contain tests that call
  `.cov.run`; this one does. Probe ids are allocated monotonically and never
  reset, so an inner run cannot land on an outer run's counters.

`tests/q/run_coverage.q` drives it over the whole suite, which is what the
lane runs.

The Python side is gated three ways on every commit, all scoped by *intent*
(every `.py` file except vendored) rather than by directory:

| gate | hook | what it proves |
|---|---|---|
| lint | `ruff`, `ruff-format` | style and a curated rule set (`E F I UP B`) |
| type | `ty` | types resolve across module boundaries |
| test | `python-tests` | the whole workspace suite passes |

`scripts/gates/check_hook_scopes.py` asserts all three cover **every** tracked
Python file, and fails the commit otherwise. That check exists because the
lint gate silently drifted once: it was scoped to a directory that stayed
valid while the code moved out from under it, leaving **43 of 47 files
ungated** with nothing to complain about. A type gate can drift the same way,
and the symptom is identical — everything passes because almost nothing is
checked.

`ty` runs with `pass_filenames: false` on purpose: it type-checks a *project*,
not a file list. Passing only the staged files would check each in isolation
and miss exactly the cross-module breakage a type checker is for.

Run the lane matching the layer you changed (requirement ETL-21). `q-unit` is
also runnable directly as `q tests/run_tests.q`: it loads every module and
every `test_*.q` file, prints a pass/fail summary, and exits non-zero if
anything failed - safe to wire into CI as-is. As of this writing:
**1459 tests, all passing**.

The lanes are separate because they prove different things, and two of them
cannot prove what they claim if folded into the first:

- **`q-backfill-process`** checks single-instance locking and resumption
  across a restart. Both need a real filesystem and a genuinely separate q
  process: an in-process test can assert `acquire_lock` throws, but that is
  q refusing itself, not the mutual exclusion the lock exists to provide -
  and an in-process "resume" never discards its own memory, so it cannot
  show the state on disk was sufficient.
- **`q-two-instances`** is the only lane in `all` where a source runs
  **live**. It starts a plain q process on the starter pack's HDB
  (`tests/q/upstream_instance.q`), sets that process as the
  `upstream_trades` credential, and moves trades into this process through
  the framework: connect, validate the remote `meta`, evaluate the query
  remotely, transform, record coverage, idle on a second run, re-fetch one
  window after a restatement, and refuse to start once the upstream is
  gone. Every other lane runs workers on their fixtures, so this is the
  only place `.qbw.connect`, a source's `query` and `.qsrc.validate_live`
  execute at all - which is how a bare table name in a query lambda stayed
  latent in three sources until this lane ran.
- **`smoke`** also carries ETL-12's live half: every registered source is
  validated against **the same declaration** its fixture is validated
  against in the deterministic suite. That is what makes a fixture
  meaningful rather than merely present — two separate declarations would
  let a suite pass while the real source had changed. A source whose
  credential is unset is skipped, not failed.
- **`smoke`** is the only lane that touches a live external source
  (requirement ETL-20), and is excluded from `all` on purpose. Folding it in
  would make every local run depend on a remote host being up, which trains
  everyone to read a red suite as "the network again" - which is how a real
  schema change gets ignored. Unconfigured, it **skips and exits 0**: an
  unconfigured checkout is not a failure.

Every function is tested against at least one of: a published textbook
reference value (e.g. Hull's Black-Scholes worked example for
`gk_call`/`gk_put`), a provable identity (put-call parity, delta-call minus
delta-put equals the foreign discount factor, day-count-neutral round
trips), or an explicit round trip through an inverse function (e.g.
building a forward with `fwd_simple` and recovering the input rate with
`implied_foreign_rate`). See `.claude/skills/kdb-q-conventions/SKILL.md` for
why this project leans on identities/round-trips rather than hand-computed
expected values wherever possible.

`tests/q/test_execution_scale.q` additionally generates a 1,000,000-row
synthetic trade table (many currency pairs, times of day, bid/ask levels
and liquidity sizes) and computes `markout` over it as a single vectorized
call, as a scale/integration check beyond the per-function unit tests.

## Documentation

Every function in `src/*.q` has a [qDoc](https://www.timestored.com/qstudio/help/qdoc)
comment block (JavaDoc-style: `@param`, `@return`, `@throws`, `@eg`).
Generate browsable HTML API docs with:

```
brew install openjdk                        # or any JDK 8+
curl -LO https://www.timestored.com/qstudio/files/qstudio.jar   # ~120MB, place at repo root
./scripts/dev/gen-docs.sh                        # writes build/docs/index.html (gitignored)
```

`qstudio.jar` also bundles a small q linter that `gen-docs.sh` runs as a
side effect (`build/docs/lint.csv`/`build/docs/lint.html`); this repo's cross-module
calls between `src/*.q`'s separate namespaces (e.g. `forwards.q` calling
`.qccy.ccy_pair_legs`) trigger a number of expected "undeclared variable"
false positives there (the linter checks each file in isolation and can't
see the other loaded namespaces), so don't be alarmed by those specifically.

**Note:** TimeStored's own qDoc docs state the CLI usage as
`QDocMain <sourceFolder> <targetFolder>` - that argument order is
backwards. The verified, working order (baked into `gen-docs.sh`) is
`QDocMain <targetFolder> <sourceFolder>`.

## Licensing

This repository's own code is MIT licensed — see [`LICENSE`](LICENSE).

Five vendored dependencies under `lib/` and `tests/lib/` carry their own
terms, one of which (qUnit, the test framework) is **non-commercial**. See
[**`LICENSING.md`**](LICENSING.md) for the per-dependency breakdown: the
license each is under, where its full text lives, whether `src/init.q` loads
it, and whether it is verified working here.
