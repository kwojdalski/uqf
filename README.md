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
QCMD=/path/to/q QHOME=/path/to/qhome uv run pytest tests/qrun/test_q_suite.py
```

**PeachQ is opt-in.** [PeachQ](https://peachq.org) is an MIT-licensed q
interpreter, used only when chosen out loud --- `QCMD` naming its binary, which
is asked which q it is ([details](docs/reference/environment.md#peachq)):

```
QCMD=/path/to/peachq/q uv run pytest tests/qrun/test_q_suite.py -m peachq --require-q peachq
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
  | **[`qlinter`](https://github.com/kwojdalski/q-lint)** | linting q source; `cargo install --git https://github.com/kwojdalski/q-lint --tag v0.14.20 --locked` | no - never needed to build, test or run            |

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
uv run uqs status            # up/down, pid, port and heartbeat per process
uv run uqs query "count fx_orderbook" --port 6052   # 6052 = base port + 2 = rdb1
```

**Run a backfill.** A bounded worker runs over a range and exits once the window
is covered. It registers with discovery, so the fleet above has to be up:

```
uv run uqs job backfill demo_deals_backfill --source-version v1 --from 2026-09-13 --to 2026-09-15 --fixture
```

The range is required - `--from`/`--to` (end excluded), `--date` or `--last` -
and UTC without an offset; `--fixture` is for the demo's built-in fixture data
([why](docs/guides/new-pipeline.md)).

**Add a pipeline.** `uqs job new` scaffolds one of [seven
shapes](docs/scaffolding/README.md), and `--dry-run` lists every file it would
create or append to without writing any of them:

```
# a streaming job: reads quote, publishes a table of its own
uv run uqs job new spread_stats --subscribe-to quote \
    --outputs spread_stats --schema "sym:symbol, spread_pips:float" --dry-run

# a feed: subscribes to nothing, publishes on a timer
uv run uqs job new rates_feed --outputs rates \
    --schema "sym:symbol, mid:float" --dry-run

# a bounded worker, with its source and transform, one day per window
uv run uqs job new eod_rates --kind backfill --outputs eod_rates \
    --schema "sym:symbol, mid:float" --width 1D --dry-run
```

The scaffold is meant to fail until you fill it in; [Adding a data
pipeline](docs/guides/new-pipeline.md) walks one through end to end.

**Check it all still works.** Selectors are listed under [Testing](#testing):

```
uv run pytest
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

pytest is the one way to run every test (#1808). qUnit stays the q suite's
framework and `.cov` its coverage instrumenter: pytest drives them, as it drives
the worked examples, the docs' q blocks and the checks that need real processes.

```
uv run pytest -n auto --dist loadgroup             # everything; a test whose interpreter is missing skips, saying so
uv run pytest -m "not q_backend"                   # without the q scripts run as tests (what the python-tests hook runs)
uv run pytest tests/qrun/test_q_suite.py::test_the_q_suite_passes   # the qUnit suite on KDB-X;
                                                   # --q-files test_a.q,test_b.q runs only those files' suites
uv run pytest tests/qrun/test_q_suite.py -m peachq      # the whole suite on PeachQ, held to its known gaps
uv run pytest tests/qrun/test_worked_examples.py   # every scripts/examples/*.q, on KDB-X
uv run pytest tests/qrun/test_doc_blocks.py -m kdbx     # the docs' marked q blocks on KDB-X
uv run pytest tests/qrun/test_doc_blocks.py -m peachq   # ... and on PeachQ; --peachq PATH picks one
uv run pytest tests/qrun/test_documented_examples.py   # every @eg in src/ and scripts/
uv run pytest tests/qrun/test_process_runners.py       # real processes, a second q, a temp HDB
uv run pytest -m coverage tests/qrun/test_q_coverage.py   # the q coverage gate (below)
uv run pytest -m release                           # release checks: the q suite reversed, each sidecar bundle installed and tested
uv run pytest -m live_stack                        # the attach workflow on isolated TorQ stacks
uv run pytest tests/remote -m docker               # a stack on another machine, in Docker
uv run pytest tests/qrun/test_external_smoke.py -m external --smoke-targets HOST:PORT --smoke-tables TABLE:COL,COL
```

### Where each check runs

Each check runs in one place, chosen by what it needs (#1879). A commit's hooks
are **fast**, a push's are **targeted test suites**, a pull request's CI runs
the **gates and attestation**, and master's CI - every push, and nightly - is
the **full, clean** run: a Linux checkout with nothing left over, PeachQ as its
q.

  | Check                                                             | Before #1965 | Now                      |
  | ---                                                               | ---          | ---                      |
  | format, lint, `ty`, static gates, generated-file checks, `q-docs` | commit       | commit, and every CI run |
  | `python-tests` (the Python tests the change reaches)              | commit       | push                     |
  | `q-tests`, `q-coverage` (the q suites the branch reaches, KDB-X)  | commit       | push                     |
  | `peachq-push` (changed q test files on PeachQ)                    | -            | push                     |
  | KDB-X attestation check, browser application (for `web/`)         | CI           | pull request CI          |
  | whole Python suite, whole q suite on PeachQ, docs render          | CI           | master CI and nightly    |
  | `regenerate-merged` (generated files a merge kept one side of)    | -            | after merge/rebase       |

- **Commit hooks.** The static hooks run on what is staged, in seconds.

- **Push hooks.** `python-tests` runs only the Python tests the pushed commits
  reach: testmon's record for a `.py` file, and for anything else a record of
  what each test reads, lists and starts (`scripts/dev/test_reach.py`) - a `.q`
  change runs every test that starts a q. `q-tests` and `q-coverage` run the q
  suites the branch reaches, on KDB-X (`scripts/dev/test_impact.py`).
  `peachq-push` runs the `tests/q/test_*.q` files the branch adds or changes,
  and the namesake suite of each changed module, on PeachQ, failing only on a
  test not in `tests/q/peachq_known_gaps.txt` - the early catch for CI's PeachQ
  job, which still runs the whole suite (`SKIP=peachq-push git push` skips it).
  The docs render (`doc-links`) and the docs' q blocks on PeachQ
  (`q-docs-peachq`) do not run: CI does. The whole Python suite runs only when
  the selector cannot decide - no record yet, a `conftest.py`, `pyproject.toml`
  or `uv.lock` change. The records are shared between the worktrees of a clone,
  so a new worktree starts from the last one written.

- **Generated files merge themselves.** `.gitattributes` gives the files
  `scripts/generate/derive.py` writes a merge driver that keeps one side, so a
  merge or rebase never stops on them; the `regenerate-merged` hook then reruns
  their `derive.py` target and leaves the result to commit, and the files'
  `--check` gates still decide. Once per clone (`./install.sh --dev` does both):

  ```
  pre-commit install                                  # commit, push, post-merge, post-rewrite hooks
  python3 scripts/dev/merge_generated.py install      # register the merge driver in git config
  ```

  CI and Kodiak never merge locally and need neither. A clone without the driver
  merges these files as text, as before.

- **CI.** Every hook again over every file, on every run. On a pull request that
  is all, beside the author check, the KDB-X attestation check and, for a `web/`
  change, the browser application's check and build (#2167): its push hooks
  already ran the tests it reaches. On every push to master and nightly (and on
  a pull request labelled `ci:full`, or `gh workflow run ci.yml -f full=true`)
  every group runs too: the whole Python suite in four PeachQ shards, the whole
  q suite on PeachQ, the docs render, and the browser application.
  `scripts/dev/ci_scope.py` decides, and says why - for each group run and each
  skipped, and on a pull request what master will run - in the "Scope" job's
  summary. "Repository checks" counts a skipped job as passed only when Scope
  said to skip it.

  The pull-request mode is a switch, the repository variable `CI_PR_TESTS`:
  unset or `lean` is the above; `full` has every pull request run each test
  group its change reaches, complete, as before #2167. Flip it in Settings ->
  Secrets and variables -> Actions -> Variables (`CI_PR_TESTS=full`), or
  `gh variable set CI_PR_TESTS --body full`. Nothing else changes with it: every
  job and condition stays in `ci.yml`. The tag `ci/full-pr-tests-baseline`
  points at master's `ci.yml` from before the change.

- **KDB-X attestation.** The KDB-X gates run locally
  (`uv run python scripts/dev/kdbx_attestation.py attest`), and CI checks the
  trailer.

**Full mode is one switch away on both sides.**

```
UQF_HOOKS_FULL=1 git commit ...               # this commit's hooks in full: the docs render,
                                              # the docs' q blocks on PeachQ
UQF_HOOKS_FULL=1 git push ...                 # this push's in full: the whole Python suite,
                                              # the whole q suite and coverage gate
python3 scripts/dev/hooks_full.py --all       # both over the whole tree, without either
UQF_Q_TESTS_FULL=1 git push ...               # the whole q suite only, as before
gh pr edit <n> --add-label ci:full            # CI: every job on this pull request
gh workflow run ci.yml --ref <branch> -f full=true   # CI: every job on a branch
```

Every push to master runs every CI job.

`coverage`, `release`, `live_stack`, `docker` and `external` are opt-in: a test
carrying one skips, saying how to select it, unless `-m` names it.

`-m kdbx` and `-m peachq` select by interpreter. `--require-q kdbx` (or
`peachq`) turns a skip for a missing interpreter into a failure: the hooks pass
it, so a gate that cannot run its tests fails instead of passing empty.

Coverage measures what the suites **execute**: line coverage for Python
(`uv run pytest --cov`, measuring each member's `src/`), statement and branch
coverage for q through the [`.cov` library](scripts/dev/coverage.q). The q half
is the gate (`-m coverage`): it fails when the set of functions nothing enters
differs from `tests/q/coverage_baseline.txt` in either direction. At push and in
an attestation it judges only the production files a change reaches, running
q-tests' suites plus, for each function judged, a suite that enters it according
to `tests/q/coverage_map.txt`; a subset that finds a function never entered
hands the verdict to a whole-suite run. The whole suite, judging every function,
runs on master's tip through `python3 scripts/dev/master_coverage.py` on a
machine with KDB-X: `scripts/dev/ship.sh` starts it after each merge, and it
also refreshes the map (`--write-map tests/q/coverage_map.txt`) (#1719).

## Licensing

This repository's own code is MIT licensed --- see [`LICENSE`](LICENSE).

Nine vendored dependencies under `lib/` and `tests/lib/` carry their own terms,
one of which (qUnit, the test framework) is **non-commercial**. See
[**`LICENSING.md`**](LICENSING.md) for the per-dependency breakdown: the license
each is under, where its full text lives, whether `src/init.q` loads it, and
whether it is verified working here.
