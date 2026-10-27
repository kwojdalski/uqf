# uqf

A q/kdb+ tree covering the span of an electronic trading data platform: the
**data engineering** that lands and backfills market data, the **quantitative
library** that prices and measures it, the **data processing** that reshapes raw
venue feeds into the shapes analytics expects, and the **operational tooling** ---
process orchestration, an HTTP gateway and a browser application --- that runs
the whole thing as a fleet.

**The names.** `uqf` is the repository and the package family (`uqf_frontend`,
`uqf_airflow_provider`); `uqs`, the *ultimate q stack*, is the stack CLI. On the
q side every namespace is `.q<area>`, with `.qetl` (the ETL framework) and
`.qpipe` (the pipelines built on it) grown into trees.

## Requirements

**KDB-X is preferred** --- KX's own interpreter, and the one everything here is
verified against. Its Community Edition is free for personal and commercial use;
it requires registering for a license, and the [install
guide](https://code.kx.com/get-started/install.html) walks through it. Other q
implementations may run the pure-q modules in `src/`, but none is verified.

Every entry point, the running stack included, finds q the way TorQ does:
`$QCMD` if set, otherwise `q` on `PATH`. There is no second fallback (no
`~/.kx/bin/q`). To choose an interpreter, set `$QCMD` and `$QHOME`:

```
QCMD=/path/to/q QHOME=/path/to/qhome scripts/test.py q-unit
```

**PeachQ is opt-in.** [PeachQ](https://peachq.org) is an MIT-licensed q
interpreter, used only when chosen out loud --- `UQF_Q_IMPL=peachq` plus `QCMD`
([details](docs/reference/environment.md#peachq)):

```
UQF_Q_IMPL=peachq QCMD=/path/to/peachq/q scripts/test.py q-unit
```

Run everything from the repository root; the load scripts use relative paths.

### Also needed, by component

  | Tool                                                  | For                                                                                                  | Required?                                          |
  | ---                                                   | ---                                                                                                  | ---                                                |
  | **KDB-X**                                             | everything in `src/`, `scripts/` and `tests/`                                                        | preferred                                          |
  | **[uv](https://docs.astral.sh/uv/)**                  | the Python packages and every `uqs` command                                                          | yes, for the fleet                                 |
  | **`qcon`**                                            | `uqs query --port <p>` with no expression; not in KDB-X personal edition                             | no - only that command                             |
  | **`rlwrap`**                                          | `torq.sh` debug starts and `qcon`; `uqs start` refuses without it                                    | yes, for the fleet                                 |
  | **`multitail`**                                       | `uqs logs --multitail`                                                                               | no - `uqs logs -f` follows the same files          |
  | **Node**                                              | the [browser application](web/README.md), `^22.13 \|\| ^24 \|\| >=26`                                | no - only for `web/`                               |
  | **[`qlinter`](https://github.com/kwojdalski/q-lint)** | linting q source; `cargo install --git https://github.com/kwojdalski/q-lint --tag v0.14.16 --locked` | no - never needed to build, test or run            |

**Setup is `./install.sh`.** It checks these, says how to install anything
missing (it never runs sudo), then puts `uqs` on your PATH. Flags: `--dev`
(Python workspace and pre-commit hooks), `--web`, `--odbc`
([ODBC](docs/guides/odbc.md)) and `--check`.

## Quick start

Five entry points, one per thing you might have come for. None depends on the
others.

**Price something.** The quant library is pure functions and no processes, so
this needs nothing but the interpreter:

```
q src/init.q
q).qf.px.gk_call[1.10;1.12;0.045;0.02;0.10;0.75]   / Garman-Kohlhagen call premium
q).qf.px.fwd_simple[1.10;0.05;0.02;1]              / CIRP outright forward
q).qf.exec.markout[1;1.1000;1.1010;10000]           / post-trade markout, in pips
```

**Run the fleet.** The TorQ stack - tickerplant, RDB, HDB, gateway, the feeds
and the ETL processes - with generated configuration:

```
./install.sh                  # once: checks prerequisites, puts uqs on PATH
uv run uqs start all
uv run uqs summary            # up/down, pid, port and heartbeat per process
uv run uqs query "count fx_orderbook" --port 6052   # 6052 = base port + 2 = rdb1
```

**Run a backfill.** A bounded worker runs over a range and exits once the window
is covered. It registers with discovery, so the fleet above has to be up:

```
uv run uqs job backfill demo_deals_backfill --version v1 --from 2026-09-13 --to 2026-09-15 --fixture
```

`--from` and `--to` are required and UTC; `--fixture` is for the demo's built-in
fixture data ([why](docs/guides/new-pipeline.md)).

**Add a pipeline.** `uqs job new` scaffolds one of three shapes, and `--dry-run`
lists every file it would create or append to without writing any of them:

```
# a streaming job: reads quote, publishes a table of its own
uv run uqs job new spread_stats --subscribe-to quote \
    --publishes spread_stats --columns "sym:symbol, spread_pips:float" --dry-run

# a feed: subscribes to nothing, publishes on a timer
uv run uqs job new rates_feed --publishes rates \
    --columns "sym:symbol, mid:float" --dry-run

# a bounded worker, with its source and transform, one day per window
uv run uqs job new eod_rates --kind backfill --dataset eod_rates \
    --columns "sym:symbol, mid:float" --width 1D --dry-run
```

The scaffold is meant to fail until you fill it in; [Adding a data
pipeline](docs/guides/new-pipeline.md) walks one through end to end.

**Check it all still works.** One lane per layer, listed under
[Testing](#testing):

```
./scripts/test.py all
```

## Components

Six of them, each with its own contract and its own place in `docs/`. A change
usually belongs to exactly one.

  | Component                   | Where                                                                                                                        | What it does                                                                                                                                                                                                                        |
  | ---                         | ---                                                                                                                          | ---                                                                                                                                                                                                                                 |
  | **Data engineering**        | [`src/etl/`](src/etl)                                                                                                        | The pipeline framework: workers, normalizers, a bitemporal coverage ledger, source contracts and a job graph derived from declared inputs and outputs                                                                               |
  | **Quant library**           | [`src/foundation/`](src/foundation), [`pricing/`](src/pricing), [`portfolio/`](src/portfolio), [`execution/`](src/execution) | Pure functions, no I/O: forwards, options and Greeks, position risk and VaR, execution analytics, in seven `.qf` domains --- [`qf-domains.md`](docs/reference/qf-domains.md), [`quant-modules.md`](docs/reference/quant-modules.md) |
  | **Data processing**         | [`src/market_data/`](src/market_data)                                                                                        | Reshaping and signal extraction: wide venue books, LOB microstructure features, data-quality checks                                                                                                                                 |
  | **Fleet and orchestration** | [`scripts/`](scripts), [`python/uqs/`](python/uqs)                                                                           | The uqf stack, plus the CLI/MCP orchestrator that generates its configuration and starts, stops and reports on it                                                                                                                   |
  | **Scheduling and access**   | [`python/uqf_airflow_provider/`](python/uqf_airflow_provider), [`python/uqf_frontend/`](python/uqf_frontend), [`web/`](web)  | An Airflow sensor, an HTTP gateway over the fleet, and the React desk and operations app                                                                                                                                            |
  | **Database metadata**       | [`src/metadata/`](src/metadata)                                                                                              | Partition-level profiling of an HDB, built nightly by DQE: [guide](docs/guides/metatables.md)                                                                                                                                       |

q and TorQ own process startup, source reads and coverage; Airflow owns
ordering, retries and alerting --- see [the pipeline
philosophy](docs/architecture/pipeline-philosophy.md).

## Further reading

[**`docs/`**](docs/README.md) is the map, and lists every page. Five
directories, one question each:

  | Directory                                         | Answers                                                                                                             |
  | ---                                               | ---                                                                                                                 |
  | [`docs/guides/`](docs/guides/)                    | *How do I do this?* — running the stack, adding a pipeline, deploying                                               |
  | [`docs/scaffolding/`](docs/scaffolding/README.md) | *How do I create one of these?* — one page per shape `uqs job new` writes                                           |
  | [`docs/services/`](docs/services/README.md)       | *What does the stack run?* — a showcase of every process, one short card each, with full pages linked               |
  | [`docs/architecture/`](docs/architecture/)        | *Why is it shaped this way?* — including the running stack                                                          |
  | [`docs/reference/`](docs/reference/)              | *What is the contract?* — the quant modules, environment variables, pipeline declaration keys and the process table |

Component READMEs: [`python/uqs/`](python/uqs/README.md) (the stack CLI),
[`python/uqf_frontend/`](python/uqf_frontend/README.md) (the API),
[`web/`](web/README.md) (the React desk app),
[`python/uqf_airflow_provider/`](python/uqf_airflow_provider/README.md).

## Testing

```
scripts/test.py q-unit    # the deterministic qUnit suite
scripts/test.py python    # orchestrator and frontend
scripts/test.py all       # every lane except the two coverage lanes, smoke and stack-smoke
```

One lane per layer; run the one matching what you changed.
`scripts/test.py --help` lists every lane.

`scripts/test.py coverage` measures what the suites **execute**: line coverage
for Python, statement and branch coverage for q through the [`.cov`
library](scripts/dev/coverage.q). `q-coverage` is the gate: it fails when the
set of functions nothing enters differs from `tests/q/coverage_baseline.txt` in
either direction. At commit and in an attestation it judges only the production
files a change reaches, running q-tests' suites plus, for each function judged,
a suite that enters it according to `tests/q/coverage_map.txt`; a subset that
finds a function never entered hands the verdict to a whole-suite run. The whole
suite, judging every function, runs on master's tip through
`python3 scripts/dev/master_coverage.py` on a machine with KDB-X:
`scripts/dev/ship.sh` starts it after each merge, and it also refreshes the map
(`--write-map tests/q/coverage_map.txt`) (#1719).

## Licensing

This repository's own code is MIT licensed --- see [`LICENSE`](LICENSE).

Seven vendored dependencies under `lib/` and `tests/lib/` carry their own terms,
one of which (qUnit, the test framework) is **non-commercial**. See
[**`LICENSING.md`**](LICENSING.md) for the per-dependency breakdown: the license
each is under, where its full text lives, whether `src/init.q` loads it, and
whether it is verified working here.
