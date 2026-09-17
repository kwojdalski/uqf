# uqf

A q/kdb+ tree covering the span of an electronic FX (eFX) data platform:
the **data engineering** that lands and backfills market data, the
**quantitative library** that prices and measures it, the **data
processing** that reshapes raw venue feeds into the shapes analytics
expects, and the **operational tooling** — process orchestration, an HTTP
gateway and a browser application — that runs the whole thing as a fleet.

These are separate components with separate contracts, not one library with
extras bolted on. See [Components](#components) for what each is and where
it lives; the quantitative library is described under
[Quant modules](#quant-modules).

Every function has a corresponding unit test written against the vendored
[qUnit](https://www.timestored.com/kdb-guides/kdb-regression-unit-tests)
framework - see [Testing](#testing).

## Contents

- [Requirements](#requirements) — KDB-X, and why there is no fallback interpreter
- [Quick start](#quick-start) — load it and price something
- [Components](#components) — what this tree contains, and which part owns what
- [Quant modules](#quant-modules) — each `src/` pricing file, its namespace and tests
  - [Conventions](#conventions) — quoting, sign, pip factors, naming
- [Further reading](#further-reading) — the `docs/` map and component READMEs
- [Browser application](#browser-application) — the React desk and operations app
- [Testing](#testing) — the four lanes, and what each one proves
- [Documentation](#documentation) — generating browsable API docs from qDoc
- [Licensing](#licensing) — MIT, plus seven vendored dependencies with their own terms

## Requirements

You need **KDB-X**, KX's own interpreter. The personal edition is free for
non-commercial use; it requires registering for a license at
[kx.com](https://kx.com/kdb-personal-edition-download/).

```
q tests/run_tests.q
```

This tree targets KDB-X alone. There is deliberately no fallback interpreter
anywhere in the tooling: a suite that passed against something the code is not
verified on is worse than one that does not run, so every entry point skips
rather than substituting.

Run everything from the repository root - the load scripts use
paths relative to it (e.g. `src/foundation/stats.q`).

### Also needed, by component

| Tool | For | Required? |
|---|---|---|
| **KDB-X** | everything in `src/`, `scripts/` and `tests/` | yes |
| **[uv](https://docs.astral.sh/uv/)** | the Python packages and every `uqf-stack` command | yes, for the fleet |
| **`qcon`** | attaching a console to a running process: `uqf-stack raw -- qcon gateway1 admin:admin` | no - only that command |
| **`rlwrap`** | line editing and history inside `qcon` | no - `qcon` runs without it |
| **Node** | building and running the [browser application](#browser-application) | no - only for `web/` |

`qcon` is kdb's console client. It ships with some kdb+ distributions and
**not** with the KDB-X personal edition, where `~/.kx/bin/` holds only `q`
and `pg` - so `uqf-stack raw -- qcon ...` is the one documented command that
may not work out of the box. Everything else reaches a running process
through IPC instead: `uqf-stack query`, `uqf-stack summary` and
`uqf-stack logs` need nothing beyond what is already installed.

`torq.sh` resolves both through `$QCON` and `$RLWRAP`, which
`torq_orchestrator`'s `build_env()` sets, so a differently-named or
differently-located binary is a variable to set rather than a patch.

## Quick start

Four entry points, one per thing you might have come for. None depends on
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
uv run uqf-stack start all
uv run uqf-stack summary            # up/down, pid, port and heartbeat per process
uv run uqf-stack query "count quotes" --port 6052   # 6052 = base port + 2 = rdb1
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
  uv run uqf-stack start deals_backfill1
```

All four variables are required together: the process refuses to start and
names every missing one at once, because a backfill that silently defaulted
its range would publish the wrong window and record coverage for it.

**Check it all still works.** Four lanes, described under
[Testing](#testing):

```
./scripts/test.sh all
```

`all` runs three of them - the q suite, the bounded-worker lifecycle against
a real filesystem, and the Python suite. The fourth, `smoke`, is deliberately
separate: it checks a live external source's metadata against its declared
contract (ETL-20) and so needs credentials and a reachable source.

## Components

Seven of them, each with its own contract and its own place in `docs/`. A
change usually belongs to exactly one.

| Component | Where | What it does |
|---|---|---|
| **Data engineering** | [`src/etl/`](src/etl) | The pipeline framework: bounded and continuous workers, a bitemporal coverage ledger, run identity, IO managers, source contracts, and a job graph derived from declared inputs and outputs. Asset-oriented, in the sense [the philosophy note](docs/architecture/pipeline-philosophy.md) sets out |
| **Quant library** | [`src/foundation/`](src/foundation), [`pricing/`](src/pricing), [`portfolio/`](src/portfolio), [`execution/`](src/execution) | Pure functions, no I/O: CIRP forwards and swap points, Garman-Kohlhagen options and Greeks, position risk and VaR, execution analytics. [Detailed below](#quant-modules) |
| **Data processing** | [`src/market_data/`](src/market_data) | Reshaping and signal extraction — wide venue books folded into vector columns, LOB microstructure features, data-quality checks that report rather than throw |
| **Fleet and orchestration** | [`scripts/`](scripts), [`python/torq_orchestrator/`](python/torq_orchestrator) | The uqf stack stack: feeds, ETL processes, tap and backfill workers, plus the CLI/MCP orchestrator that generates their configuration and starts, stops and reports on them |
| **Scheduling and access** | [`python/uqf_airflow_provider/`](python/uqf_airflow_provider), [`python/uqf_frontend/`](python/uqf_frontend), [`python/uqf_client/`](python/uqf_client), [`web/`](web) | An Airflow sensor reading q-side status, an HTTP gateway over the fleet, a q client, and the React desk and operations app |
| **Database metadata** | [`src/metadata/`](src/metadata) | Partition-level profiling of an HDB: row counts, temporal span, null density and configurable eFX breakdowns, refreshed under an explicit bound and exposed to TorQ's DQE through a thin adapter. [The guide](docs/guides/metatables.md) |
| **Reference data model** | [`env/`](env/README.md) | Typed table shapes for a broader eFX system — market data, positions, predictions, orders, routing, an economic calendar — as scaffolding this library's functions could sit inside |

Authority is split deliberately between them: q and TorQ own process
startup, source reads and coverage; Airflow owns ordering, retries and
alerting. Neither infers the other's facts from log text. That rule, and
the others the tree is built on, are written down in
[the pipeline philosophy](docs/architecture/pipeline-philosophy.md).

## Quant modules

Each module loads into its own flat namespace after `src/init.q` -
`.qstats`, `.qccy`, `.qdcf`, `.qrates`, `.qfwd`, `.qopt`, `.qrisk`, `.qpos`,
`.qexec`, `.qbook`, `.qmicro`, `.qdqc`, `.qexdef`. Kept single-level
throughout rather than nested under a shared parent (e.g. not
`.q.options`). This began as a portability constraint and is now a
convention the tree keeps: the filename-to-namespace tie is what the naming
auditor checks and what `docs/man.q`'s registry is generated against.

Every module has a matching
test file, and every function carries a [qDoc](#documentation) block with
`@param`/`@return`/`@eg` — those are the per-function reference, so the
table below only says what each module is *for*.

| Module | Namespace | For | Tests |
|---|---|---|---|
| [`foundation/stats.q`](src/foundation/stats.q) | `.qstats` | normal distribution helpers, shared polynomial evaluator | [tests](tests/q/test_stats.q) |
| [`foundation/ccy.q`](src/foundation/ccy.q) | `.qccy` | CURCUR pair convention: validate, normalize, split, build | [tests](tests/q/test_ccy.q) |
| [`foundation/daycount.q`](src/foundation/daycount.q) | `.qdcf` | day count fractions — dates to the year fraction `t` pricing takes | [tests](tests/q/test_daycount.q) |
| [`foundation/rates.q`](src/foundation/rates.q) | `.qrates` | discount/growth factors, simple↔continuous conversion | [tests](tests/q/test_rates.q) |
| [`pricing/forwards.q`](src/pricing/forwards.q) | `.qfwd` | CIRP forwards and swap points, cross rates, synthetic cross books | [tests](tests/q/test_forwards.q) |
| [`pricing/options.q`](src/pricing/options.q) | `.qopt` | Garman-Kohlhagen pricing, Greeks, implied vol | [tests](tests/q/test_options.q) |
| [`portfolio/risk.q`](src/portfolio/risk.q) | `.qrisk` | pip value, P&L, carry, parametric and historical VaR | [tests](tests/q/test_risk.q) |
| [`portfolio/positions.q`](src/portfolio/positions.q) | `.qpos` | weighted-average-cost position tracking, currency exposure, reconciliation | [tests](tests/q/test_positions.q) |
| [`execution/execution.q`](src/execution/execution.q) | `.qexec` | markouts, effective spread, slippage, fill/reject ratios, VWAP, sweep pricing | [tests](tests/q/test_execution.q) |
| [`market_data/book.q`](src/market_data/book.q) | `.qbook` | reshapes wide/mis-typed order books into the shape the other modules expect | [tests](tests/q/test_book.q) |
| [`market_data/microstructure.q`](src/market_data/microstructure.q) | `.qmicro` | LOB signals: book pressure, microprice, order flow imbalance, VAMP | [tests](tests/q/test_microstructure.q) |
| [`market_data/dqchecks.q`](src/market_data/dqchecks.q) | `.qdqc` | limit and data-quality checks, reported rather than thrown | [tests](tests/q/test_dqchecks.q) |
| [`integrations/data.q`](src/integrations/data.q) | `.qdata` | external data access | [tests](tests/q/test_data.q) |
| [`examples/example_defaults.q`](src/examples/example_defaults.q) | `.qexdef` | shared example inputs used by docstrings and demos | — |

The data-engineering component is documented separately: see
[pipeline-framework-gaps.md](docs/architecture/pipeline-framework-gaps.md)
for what `src/etl/` has and lacks, and
[etl-framework-requirements.md](docs/reference/etl-framework-requirements.md)
for the contract CI holds it to.

### Conventions

Currency pairs follow BASE/QUOTE quoting throughout (`rate` = 1 BASE in
QUOTE units); `side` is `1` for long/buy, `-1` for short/sell;
`pip_factor` is `10000` for most pairs and `100` for JPY crosses. All
function names, parameters and locals use `lower_snake_case`. See the
[`kdb-q-conventions` skill](.claude/skills/kdb-q-conventions/SKILL.md) for
the full set of conventions and the q arithmetic gotcha that shaped how
this code is written.

## Further reading

[**`docs/`**](docs/README.md) is the map — five directories, one question
each, and the rule for which a new page belongs in.

- **How do I do this?** → [`docs/guides/`](docs/guides/):
  [uqf-stack.md](docs/guides/uqf-stack.md) (running the stack),
  [ci.md](docs/guides/ci.md) (the gates, and running them locally).
- **Why is it shaped this way?** → [`docs/architecture/`](docs/architecture/):
  [restatement-design.md](docs/architecture/restatement-design.md),
  [event-tape.md](docs/architecture/event-tape.md),
  [pipeline-framework-gaps.md](docs/architecture/pipeline-framework-gaps.md),
  [cryptorust-discovery.md](docs/architecture/cryptorust-discovery.md).
- **What is the contract?** → [`docs/reference/`](docs/reference/):
  [environment.md](docs/reference/environment.md) (every variable,
  machine-checked), [ETL](docs/reference/etl-framework-requirements.md) and
  [frontend](docs/reference/frontend-requirements.md) requirements.
- **What was decided, and when?** →
  [the decision register](docs/decisions/README.md), generated from the
  GitHub issue comments that are its authority.
- **How does this meet something external?** →
  [`docs/integrations/torq/`](docs/integrations/torq/README.md), including the
  generated [process table](docs/integrations/torq/processes.md).
- **What is planned?** → [`docs/ROADMAP.md`](docs/ROADMAP.md).

Component READMEs: [`web/`](web/README.md) (the React desk app),
[`env/`](env/README.md),
[`python/torq_orchestrator/`](python/torq_orchestrator/README.md).
## Browser application

The local React [desk and operations app](web/README.md) provides catalog-driven
queries, coverage gaps, backfill status, fleet health, queue, connections and
usage views. It can run against the API through a development proxy or be
served by the API under `/ui/`.

## Testing

```
scripts/test.sh q-unit              # deterministic qUnit suite
scripts/test.sh q-backfill-process  # bounded lifecycle, real filesystem, second process
scripts/test.sh python              # orchestrator and frontend
scripts/test.sh smoke               # live external metadata check
scripts/test.sh all                 # everything except smoke
```

The Python side is gated three ways on every commit, all scoped by *intent*
(every `.py` file except vendored) rather than by directory:

| gate | hook | what it proves |
|---|---|---|
| lint | `ruff`, `ruff-format` | style and a curated rule set (`E F I UP B`) |
| type | `ty` | types resolve across module boundaries |
| test | `python-tests` | the whole workspace suite passes |

`scripts/check_hook_scopes.py` asserts all three cover **every** tracked
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
**491 tests, all passing**.

The lanes are separate because they prove different things, and two of them
cannot prove what they claim if folded into the first:

- **`q-backfill-process`** checks single-instance locking and resumption
  across a restart. Both need a real filesystem and a genuinely separate q
  process: an in-process test can assert `acquire_lock` throws, but that is
  q refusing itself, not the mutual exclusion the lock exists to provide -
  and an in-process "resume" never discards its own memory, so it cannot
  show the state on disk was sufficient.
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
./scripts/gen-docs.sh                        # writes build/docs/index.html (gitignored)
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

### Alternative: q-doc (live, no external download)

[`lib/q-doc/`](lib/q-doc) is a vendored copy of
[jasraj/q-doc](https://github.com/jasraj/q-doc) (see Licensing) - unlike
`gen-docs.sh`, it needs no jar download, but it runs as a live kdb+
process serving docs over HTTP rather than writing static files:

```
./scripts/run_qdoc.sh                        # starts on port 8090 by default
q) .qdoc.parser.init `:src                    # at the q) prompt once it's up
```

Then browse `http://localhost:8090/index-kdb.html`. Requires real
kdb+/KDB-X (see Licensing).

**Known gap:** q-doc's `@param` tag expects `@param name (Type)
description` - one token for the type, in parentheses. This repo's
existing `@param` comments (written for `gen-docs.sh`'s qDoc) instead
follow `@param name description` with no type token, so q-doc misparses
the first description word as an (unrecognized, logged-as-a-warning) type
and drops it from the rendered description. Harmless - parsing still
succeeds and the rest of each description renders correctly - but don't
expect q-doc's rendered `@param` text to exactly match the source
comment.

## Licensing

This repository's own code is MIT licensed — see [`LICENSE`](LICENSE).

Seven vendored dependencies under `lib/` and `tests/lib/` carry their own
terms, one of which (qUnit, the test framework) is **non-commercial**. See
[**`LICENSING.md`**](LICENSING.md) for the per-dependency breakdown: the
license each is under, where its full text lives, whether `src/init.q` loads
it, and whether it is verified working here.
