# Testing, gates, generated files and hooks

Read this before running a test, before committing, before pushing, and whenever
a hook or a CI check fails. Every fact cites the file that holds it; when this
page and the file disagree, the file wins.

## 1. Two interpreters

  |                  | KDB-X                                                                                                                                        | PeachQ                                                                                                                                                                  |
  | ---              | ---                                                                                                                                          | ---                                                                                                                                                                     |
  | What it is       | KX's licensed q (`.z.K` 5, build 2026.07.23 on the maintainer's Mac)                                                                         | an MIT-licensed q reimplementation (`scripts/peachq/resolve.py:4-9`)                                                                                                    |
  | Role             | what everything is verified against; the attestation runs on it                                                                              | CI's interpreter: master and nightly run the whole q suite on it                                                                                                        |
  | How it is chosen | `$QCMD`, else `q` on PATH; there is **no** `~/.kx/bin/q` fallback (`python/uqs/src/uqs/interpreter.py:46-53,158-168`; README "Requirements") | `--peachq PATH`, else `$QCMD` if it identifies as PeachQ, else the pinned build (`interpreter.py:285-300`, `conftest.py:331-346`)                                       |
  | Pin              | -                                                                                                                                            | `scripts/peachq/pin.json` (repo, commit, make target, per-platform flags); `UQF_PEACHQ_COMMIT=<40-hex>` overrides; built into `${XDG_CACHE_HOME:-~/.cache}/uqf/peachq/` |
  | Known gaps       | -                                                                                                                                            | `tests/q/peachq_known_gaps.txt` (q) and `python/peachq_known_gaps.txt` (pytest node ids)                                                                                |

- The binary is **identified by asking it**, not by its name: `IDENTIFY_SCRIPT`
  probes for `.pq.load_natives` (`interpreter.py:75-80`).
- **`UQF_PEACHQ` is no longer read; it is refused.** It sits in `REMOVED_ENV`
  (`interpreter.py:59-73`, mirrored in `scripts/peachq/resolve.py:72-80`). The
  root `conftest.py` raises a `pytest.UsageError` when it is set
  (`conftest.py:202-208`), and `kdbx_attestation.py` refuses it too. Unset it
  (`env -u UQF_PEACHQ ...`) if your shell exports it. `UQF_Q_IMPL` is refused
  the same way. Only `UQF_PEACHQ_COMMIT` and `UQF_PEACHQ_STACK_TEST` are live.
- **Nested `\d` contexts.** kdb+ 4.0 cannot load `\d .a.b`; KDB-X can, and so
  can PeachQ since v0.92 (`interpreter.py:83-90`). The tree still keeps every
  `\d` to one level everywhere it is checked (`src`, `scripts`, `sidecars`,
  `tests`, `docs`, `README.md`, `python/uqs/README.md` -
  `scripts/gates/policy/nested_contexts.py:31-39`), so define names fully
  qualified from the root. `scripts/portable/flatten_contexts.py` rewrites
  nested contexts for a kdb+ 4.0 deployment (`uqs deploy build --q-version 4.0`)
  and is no longer used by any test lane.
- Several gate docstrings still say "PeachQ cannot load a nested context"
  (`nested_contexts.py:3-4`, `.pre-commit-config.yaml:162`,
  `scripts/peachq/full_suite.py:4`). That was true before v0.92; the rule they
  enforce stands regardless.
- PeachQ v0.92 handles partitioned HDBs; the tree still carries older
  workarounds for it (`.qetl.io.on_peachq` and friends) - tracked in #2794.

## 2. The q suite

### Layout

  | Path                                       | Holds                                                                                        |
  | ---                                        | ---                                                                                          |
  | `tests/run_tests.q`                        | the runner: `q tests/run_tests.q [-files a.q,b.q]`, from the repo root                       |
  | `tests/lib/qunit.q`                        | vendored TimeStored qUnit (CC BY-NC-SA 2.0 UK - keep its header; `LICENSING.md`)             |
  | `tests/lib/support.q`                      | `.qt.support`: suite loading, `-files` selection, outcome files, `assertApprox`, `job_suite` |
  | `tests/lib/discovery.q`                    | the suite-layout rules and the replacement `runNsTests`                                      |
  | `tests/lib/etl_test_doubles.q`             | doubles for fetch/publish/checkpoint                                                         |
  | `tests/q/test_*.q`                         | every suite (there are no top-level `tests/test_*.q`)                                        |
  | `tests/q/run_examples.q`, `run_coverage.q` | every `@eg` in its own process; the coverage child                                           |
  | `tests/qrun/`                              | the pytest drivers that run all of the above                                                 |
  | `tests/remote/`                            | Docker scenarios (`-m docker`)                                                               |

### What `run_tests.q` does (`tests/run_tests.q`)

1. Seeds the RNG (`\S 20260916`, line 19; `test_seed.q` asserts it).
2. Loads qUnit, `support.q`, `src/init.q`, the integrations, `docs/man.q`, the
   TorQ process scripts, `src/etl/init.q`, the doubles, the reference worker and
   `scripts/dev/coverage.q` (lines 21-36).
3. `.qt.support.load_suites[]` loads every `tests/q/test_*.q`
   (`support.q:121-127`); `.qt.support.require_layout[]` refuses a bad layout.
4. Finds the suites (`.qt.support.suites[]`), narrows them to `-files`, orders
   them (`UQF_TEST_ORDER` = unset, `reverse` or `shuffle`), runs
   `.qunit.runTests`, writes `UQF_TEST_RESULTS`/`UQF_TEST_FAILURES` if set, and
   exits 1 on any failure or error.

### Suite layout - the `.qt` rules (`CLAUDE.md`; `tests/lib/discovery.q:1-50`)

- A suite is a namespace under `.qt` that **directly** holds at least one
  `test*` function: `.qt.<production-owner>[.<suite>]`, mirroring what it tests
  (`.qt.qetl.coverage`, `.qt.qpipe.job.fx_markout`, `.qt.qf.px.options`).
  Nothing lists suites; the runner finds them.
- Define every name with its whole path from the root
  (`.qt.qetl.io.batch:{[] ...}`) and call a suite's own helpers by their whole
  path. Never `\d .qt...` or `system "d ..."`.
- `.qt.support` (shared helpers) and `.qt.fixture.*` (doubles, fixture workers)
  are reserved and are never suites. A namespace under `.qt` that holds
  functions but no test is refused at load, and so is a `test*` member that is
  not a one-argument lambda (`discovery.q:107-131`).
- A job's suite is `.qt.qpipe.job.<job>` (`.qt.support.job_suite[job]`;
  `uqs job new` names it for you).
- The cases live in `tests/q/test_suite_layout.q`.

### Running a subset

```bash
q tests/run_tests.q -files test_coverage.q,test_dag.q        # every file LOADS; only these suites RUN
uv run pytest tests/qrun/test_q_suite.py::test_the_q_suite_passes --q-files test_coverage.q,test_dag.q
```

An empty or unknown `-files` name is refused (`support.q:129-189`). README says
pytest is "the one way to run every test" (#1808): every hook and CI lane
reaches `run_tests.q` through `tests/qrun/test_q_suite.py`; running it bare is
fine while iterating.

### qUnit: assert the message

`tests/lib/qunit.q`:

```q
assertError:{ [func; arg; msg] assertThrows[func; arg; "*"; msg] };   / line 124: ANY error passes
assertThrows:{ [func; arg; exceptionLike; msg] ... };                    / line 129: r[1] like exceptionLike
```

`assertError` passes on *any* error - including `'value` from a typo in the test
itself. Where the message is part of the contract, write
`.qunit.assertThrows[f;arg;"*expected text*";"why"]`. Keep the pattern to
`"prefix*"` or `"*middle*"`: on KDB-X `like` throws `'nyi` on three `*`s or a
`*` in the middle after literal text (`"load_cursor: *.bak*"`), which turns a
correct refusal test into an error.

Other assertions: `assertEquals`, `assertThat`, `assertTrue`, `assertFalse`,
`assertEmpty`, `assertNotEmpty`, `assertKnown`, `fail`, `mock`;
`.qt.support.assertApprox[actual;expected;tol;msg]` for floats.

Hooks are found by literal, case-sensitive prefix: `beforeNamespace*`,
`afterNamespace*`, `beforeParameters*`, `parameters*`, `afterParameters*`,
`setUp*`, `tearDown*`. snake_case only what follows the prefix
(`beforeNamespace_generate_trades`).

### Test hygiene (`uqf-q-rules.md` V2-V3 and `uqf-q-traps.md`)

- Restore every global, stub, environment variable and `.qetl.load.only` you
  set: suites share one process.
- Prove the negative (the ledger is still empty; the throwing stub was not
  called), not only what a call returns.
- Wire a streaming job's `publish` to a recorder before driving it.
- Assert a reference value or a provable identity, not "didn't throw".
- Don't pass a huge vector as an assertion's actual/expected; reduce it to a
  scalar first (`kdb-q-conventions`).

### PeachQ known gaps

- `tests/q/peachq_known_gaps.txt`: `.suite.test_name  # reason`, plus one
  `platform: Linux-x86_64` line. A reason starts `peachq-lacks: ` or
  `tree-bug: #N` - nothing else is accepted (`scripts/peachq/full_suite.py:62`,
  `:79-82`; the older `flattening:` reason is now refused). A reason that only
  repeats the test's message is refused. The list is held both ways: a listed
  test that passes fails the run. Off CI's platform the comparison is reported
  "not comparable".
- `python/peachq_known_gaps.txt`: `nodeid  # reason`, free text; listed tests
  become `xfail(strict=True)` on PeachQ (`conftest.py:298-323`).

## 3. Python tests

- The root `pyproject.toml` `[tool.pytest.ini_options]` sets only `testpaths`:
  `python/uqs/tests`, `python/uqf_frontend/tests`,
  `python/uqf_airflow_provider/tests`, `scripts/gates/tests`,
  `scripts/portable/tests`, `scripts/peachq/tests`, `scripts/dev/tests`,
  `tests/qrun`, `tests/remote`. Run pytest from the repository root.

- Markers are registered in `conftest.py` (`MARKERS`, `OPT_IN`):

  | Marker                                                    | Meaning                                                                       |
  | ---                                                       | ---                                                                           |
  | `kdbx`                                                    | runs on KDB-X; skips when `$QCMD` is not KDB-X, fails with `--require-q kdbx` |
  | `peachq`                                                  | runs on PeachQ; skips when none can be had, fails with `--require-q peachq`   |
  | `q_backend`                                               | runs a q script as its test; the `python-tests` hook leaves it out            |
  | `q_suite`                                                 | the whole qUnit suite (minutes)                                               |
  | `judges_reach_record`                                     | judges `.testreach.json`; runs last in the hook                               |
  | `release`, `live_stack`, `docker`, `external`, `coverage` | **opt-in**: skipped unless the word is in `-m`                                |

- Options from `conftest.py`: `--peachq PATH`, `--require-q {kdbx,peachq}`,
  `--q-files`, `--q-modules`, `--q-coverage-map`, `--smoke-*`, `--record-reach`,
  `--select-tests`, `--shard I/N`.

- xdist: `uv run pytest -n auto --dist loadgroup` is the canonical full form; a
  test on fixed ports carries `@pytest.mark.xdist_group("torq-stack")`. A test
  writes only under `tmp_path`; `scripts/dev/checkout_guard.py` fails a test
  under `python/` that writes into the checkout's `output/`.

- `conftest.py` scrubs git location variables and state locations
  (`UQF_STATUS_DIR`, `TORQDATA`, `UQS_DATA_ROOT`, `UQF_HOOKS_FULL`, ...) at
  import.

### Run only what you touched

This Mac has 11 cores and several sessions share it. **Never run the whole
Python suite, the whole q suite, `full_suite.py` or a stack test on your own
initiative**, and never more than `-n 2`:

```bash
uv run pytest -q -n 2 python/uqs/tests/test_<thing>.py
uv run pytest -q tests/qrun/test_q_suite.py::test_the_q_suite_passes --q-files test_<module>.q
uv run pytest -q tests/qrun/test_documented_examples.py     # after changing an @eg
```

Before pushing, let the hooks pick the reach of your diff:
`pre-commit run --from-ref origin/master --to-ref HEAD`, then
`pre-commit run --hook-stage pre-push --from-ref origin/master --to-ref HEAD`.
The push itself runs the same pre-push hooks.

## 4. qlinter and the q-traps gate

- Pin: `.qlinter-version` (`v0.17.1` today) is the one place the version is set.
  Install the matching tag:
  `cargo install --git https://github.com/kwojdalski/q-lint --tag v0.17.1 --locked`.
- Config: `.qlinter.toml` - `exclude = ["lib/**", ".claude/worktrees/"]`,
  `profile = "uqf"`, `ignore = ["QS001","QS008","QS010"]` (snake_case beside
  TorQ's camelCase; `_` in names; the `{[t;x]}` handler shape), plus per-file
  ignores, each with its reason.
- Use it first: `qlinter path/to/file.q`, `qlinter src/ tests/q/`,
  `qlinter --explain QF005`. Findings are clues to verify against code and a
  running q; a clean run proves nothing (`CLAUDE.md`). The `qlint-reviewer`
  agent rules on each finding.
- The commit gate is `scripts/gates/check.py q-traps`
  (`scripts/gates/policy/q_traps.py`): it runs the binary in `$QLINTER`, else
  `qlinter` on PATH, over every tracked `.q`, with
  `--profile uqf --extend-select QE,QA,QB,QD`. It fails on any
  `QE*`/`QA*`/`QB*`/`QD*` finding plus `QF001-QF004` (a builtin as a name) and
  `QP001`/`QP002` (bare `/` line; legacy datetime), with `QP002` exempt under
  `tests/`. It also checks q embedded in `.py` files for reserved parameter
  names.
- **It refuses a missing or a differently-versioned qlinter** (exit 1,
  `q_traps.py:443-456`). `CLAUDE.md` calls q-lint "suggested, not required" for
  building, testing and running; for committing it is required.
  `~/.local/bin/qlinter` may be a newer build than the pinned one in
  `~/.cargo/bin` - check `qlinter --version`.

## 5. The policy gates - `scripts/gates/check.py`

`python3 scripts/gates/check.py CHECK` (or `uv run python ...`). Each check is a
module in `scripts/gates/policy/`. Run each on its own and read its exit code;
never through a pipe (`cmd | tail` reports `tail`'s status).

  | Check                   | Holds                                                                                                                                      |
  | ---                     | ---                                                                                                                                        |
  | `etl-layering`          | `src/etl/core/` names no declaration; `src/` reads TorQ only through its one owner file per facility                                       |
  | `nested-contexts`       | no `\d` deeper than one level                                                                                                              |
  | `module-deps`           | the quant library's module graph matches `ALLOWED` in `module_deps.py`, is acyclic, and each name is defined once per namespace            |
  | `q-module-size`         | each `src/` q module within its line budget                                                                                                |
  | `registry-enlist`       | registry writes under `src/` store enlisted values                                                                                         |
  | `markout-mapped`        | a markout path selects `market_data` by date only and reads no depth (#2288)                                                               |
  | `variant-gather`        | the reference-variant gather reads the deduped rids once (#2289)                                                                           |
  | `declaration-reference` | `docs/reference/pipeline-declarations.md` matches the declaring q keys                                                                     |
  | `env-reference`         | `docs/reference/environment.md` and `.env.example` match the variables the code reads                                                      |
  | `doc-references`        | every `.q*` function named in `docs/`, `.claude/agents/` or `.claude/skills/` exists in the contract surface and takes the arguments shown |
  | `tool-pins`             | every copy of a tool version (uv, pre-commit, Python, ruff, Quarto, Node) matches its authority                                            |
  | `diagram-ports`         | `procname :port` labels in the d2 diagrams match the stack                                                                                 |
  | `bundle-blocks`         | nothing staged comes from an installed sidecar bundle                                                                                      |
  | `vendored`              | every `lib/` tree is byte-identical to the commit in `lib/VENDORED.toml`                                                                   |
  | `q-traps`               | see section 4                                                                                                                              |
  | `hook-scopes`           | no dead pre-commit scope; every tracked `.py` is gated; `--kdbx-only` lists the hooks that need q                                          |

`doc-references` matters for anything you write under `.claude/`: a `.qetl.*`,
`.qf.*` or `.qpipe.*` name in prose must exist (or be listed in its
`ALLOWED_MISSING` with a reason). TorQ's `.servers`, `.u`, `.lg` and kdb's `.Q`
are out of its scope; `.qtorq`, `.qunit` and `.qt` are reported as unverifiable,
not failed.

## 6. Generated files - `scripts/generate/derive.py`

`uv run python scripts/generate/derive.py TARGET [--check]`. Every output is
rendered before any is written; `--check` never writes; exit 0 current, 1
stale/refused, 2 usage (`derive.py:22-31`). There is no `all` target. Edit the
source, then regenerate in the same commit; never hand-edit an output.

  | Target                | Writes                                                                                                                                                                                                 | Stale when                                                                                                  | Needs                                                         | Commit hook                                                                                                      |
  | ---                   | ---                                                                                                                                                                                                    | ---                                                                                                         | ---                                                           | ---                                                                                                              |
  | `operational-docs`    | `docs/reference/processes.md`, `src/etl/generated/pipeline_dag.q`, `src/etl/generated/load_plan.q`, `scripts/processes/process_ports.csv`, the profile table in `docs/guides/runtimes-and-profiles.md` | a declaration (procname, edges, start_with_all, note), a reference a job makes, `python/uqs/src/uqs/model/` | no q                                                          | `operational-docs` **regenerates** (no `--check`): it rewrites, fails the commit, you `git add` and commit again |
  | `contract-surface`    | `docs/reference/surfaces/current/{functions,table_columns,processes,variables,transports,meta}.csv`                                                                                                    | any public q name, signature or table; the process registry; the env scan                                   | KDB-X                                                         | `contract-surface` (kdbx-only, `--check`, reports a contract diff)                                               |
  | `q-facts`             | `python/uqs/src/uqs/generated/q_facts.py`, `python/uqf_airflow_provider/src/uqf_airflow_provider/q_facts.py`                                                                                           | the q facts Python mirrors (`scripts/generate/export_q_facts.q`)                                            | KDB-X                                                         | `q-facts` (kdbx-only)                                                                                            |
  | `plant-schema`        | `src/etl/generated/plant_schema.json`                                                                                                                                                                  | `src/etl/plant_tables.q`, `plant_registry.q` (fingerprinted)                                                | KDB-X, or PeachQ when stale                                   | `plant-schema` (kdbx-only)                                                                                       |
  | `table-properties`    | `scripts/torqconfig/dataaccess/tableproperties.csv`                                                                                                                                                    | plant tables, workers' datasets (via committed `q_facts.py`)                                                | no q                                                          | `table-properties`                                                                                               |
  | `compression-config`  | `scripts/torqconfig/compressionconfig.csv`                                                                                                                                                             | `src/etl/compression_policy.q`, `core/compression.q`, plant tables                                          | KDB-X                                                         | `compression-config` (kdbx-only)                                                                                 |
  | `housekeeping-config` | `scripts/torqconfig/housekeeping.csv`, `housekeeping_schedule.csv`                                                                                                                                     | `python/uqs/src/uqs/stack/housekeeping.py`                                                                  | no q                                                          | `housekeeping-config`                                                                                            |
  | `environment-tables`  | the two tables in `docs/reference/environment.md`                                                                                                                                                      | `docs/reference/environment/read.csv`, `produced.csv`                                                       | no q                                                          | `environment-tables`                                                                                             |
  | `diagrams`            | `docs/diagrams/*.svg`                                                                                                                                                                                  | the `.d2` sources                                                                                           | the pinned d2 (`v0.9.0`); skips without it unless `--require` | `rendered-diagrams` (`--check`)                                                                                  |

- `uqs job new` reruns `operational-docs` itself and, where q is installed,
  `q-facts`, `table-properties` and the contract surface; without q it names the
  runs left to do (`new-job` skill). After a q change, run `contract-surface`,
  `q-facts` and `plant-schema` yourself if their hooks fail.
- Not a derive target: `docs/reference/q-api/` is rendered by
  `scripts/generate/q_api_docs.py` at docs build time and is gitignored.
  `docs/man.q` is not generated either: it reads the qDoc blocks when it loads.
- **Merges never stop on a generated file.** `.gitattributes` marks the
  generated outputs `merge=uqf-generated`; the driver keeps one side and the
  `regenerate-merged` post-merge/post-rewrite hook reruns the generators.
  Register it once per clone: `python3 scripts/dev/merge_generated.py install`
  (`./install.sh --dev` does it). `process_ports.csv`, the torqconfig CSVs and
  `environment.md` are not covered by the driver; resolve those by regenerating.
- `process_ports.csv` is an append-only lock: never renumber a row.

## 7. Hooks - `.pre-commit-config.yaml`

`default_install_hook_types: [pre-commit, pre-push, post-merge, post-rewrite]`.
Install all four with `pre-commit install` (`./install.sh --dev`). A hook that
needs q carries `# kdbx-only` on the line under its `- id:`; that tag is the one
list (`check.py hook-scopes --kdbx-only` prints it:
`q-tests,q-docs,contract-surface,q-facts,plant-schema,compression-config,q-coverage`).

**pre-commit (seconds):** file hygiene (`trailing-whitespace`,
`end-of-file-fixer`, YAML/TOML/JSON, large files, case and merge conflicts,
`mixed-line-ending`), `panache-format`/`panache-lint` (markdown reflowed to 80
columns - write a blank line before every list or it is folded into the
paragraph), `ruff`/`ruff-format`, `ty`, `check-hook-scopes`, the gates of
section 5, the generator checks of section 6, `q-docs` (the docs' q blocks on
KDB-X), `no-commit-to-branch` (refuses master/main) and
`branch-naming-with-git-username` (renames a branch to `<gh-user>/<branch>`).

**pre-push (the targeted suites):**

- `python-tests` - `scripts/dev/python_tests_hook.py`: testmon decides for `.py`
  changes, `.testreach.json` for everything else; always `-m "not q_backend"`.
  The whole suite runs only when the selector cannot decide (no record, a
  `conftest.py`, `pyproject.toml` or `uv.lock` change).
- `q-tests` (kdbx-only) - `scripts/dev/q_test_impact.py --hook` picks ALL, NONE
  or `-files ...` from the qlinter dependency graph, then runs the suite plus
  the documented and worked examples.
- `q-coverage` (kdbx-only) - the coverage gate over the same reach.
- `peachq-push` - changed `tests/q` files and each changed module's namesake
  suite on PeachQ, held to the gaps file. `SKIP=peachq-push git push` is the one
  documented skip.

**post-merge / post-rewrite:** `regenerate-merged`.

**Full mode:** `UQF_HOOKS_FULL=1 git commit` / `git push`, or
`python3 scripts/dev/hooks_full.py --all`; `UQF_Q_TESTS_FULL=1` runs the whole q
suite alone. Don't use it unless asked: it is the whole suite.

**Rules:** never `--no-verify`, never `git push --force`, never `SKIP=<id>` on
your own initiative: skipping a hook broken on master is the user's decision -
stop and report a hook you cannot pass.

## 8. KDB-X attestation

Hosted CI has no KDB-X licence (`scripts/dev/kdbx_attestation.py:101-107`), so
the seven kdbx-only hooks cannot run there. The developer runs them and records
the result in the branch:

```bash
uv run python scripts/dev/kdbx_attestation.py attest          # after the LAST rebase, on a clean tree
uv run python scripts/dev/kdbx_attestation.py attest --all    # every kdbx-only hook
uv run python scripts/dev/kdbx_attestation.py check BASE HEAD # what CI runs (no q)
```

- `attest` runs only the hooks whose `files:` scope the diff from
  `origin/master` touches, each over the whole tree; refuses a dirty tree
  (untracked files included) and a non-KDB-X q; runs `q-coverage` first and lets
  `q-tests` take its verdict from it. It records an empty commit with the
  trailer
  `KDB-X-gates: <tree> hooks=<ids> q-tests=<digest|all> q-coverage=<digest|all>`.
  A branch that needs no hook records nothing.
- A rebase changes the tree, so it needs a fresh attestation. A head behind
  master still passes while master's commits since the merge base touch no
  judged path (#1687).
- CI's "KDB-X attestation" check (`.github/workflows/kdbx-attestation.yml`)
  works out the same hook set and fails the PR unless the head tree carries a
  trailer covering it. The only way past is the `kdbx-attestation-override` PR
  label, which passes with a warning that names it.
- `scripts/dev/ship.sh <pr> issue-<n>` attests only when the check needs it,
  merges master in only on a conflict or a judged change, waits for "Repository
  checks" and merges. It is the merge queue's tool; an agent does not run it,
  merge, or attest unless the user asks.
- `CLAUDE.md` lists five KDB-X gates; the tagged set is seven (it also has
  `plant-schema` and `compression-config`). `scripts/dev/attest_kdbx.py`, named
  in older checkouts' `CLAUDE.md`, no longer exists.

## 9. CI - `.github/workflows/`

  | Workflow               | Does                                                                                                                                                                                                                                                                                                                                                     |
  | ---                    | ---                                                                                                                                                                                                                                                                                                                                                      |
  | `ci.yml`               | `author` (fails a PR not by `kwojdalski`); `scope` (`scripts/dev/ci_scope.py`); `checks` ("Hooks and static gates": `pre-commit run --all-files`, skipping the kdbx-only hooks with no q); `peachq-q` (whole q suite on PeachQ); `peachq-python` (4 shards); `doc-links` (Quarto render, q-api on PeachQ); `web`; `repository-checks` (the required job) |
  | `kdbx-attestation.yml` | the attestation check, on PR open/sync/label                                                                                                                                                                                                                                                                                                             |
  | `docs.yml`             | publishes the site rendered by a green master run                                                                                                                                                                                                                                                                                                        |
  | `release.yml`          | on `v*` tags: the plain and the `--q-version 4.0` artifacts                                                                                                                                                                                                                                                                                              |

A PR runs lean by default: author, hooks and static gates, the attestation, and
`web/` when touched; the whole Python suite, the whole q suite on PeachQ and the
docs render run on every push to master and nightly. `CI_PR_TESTS=full` (a
repository variable), the `ci:full` label or
`gh workflow run ci.yml --ref <branch> -f full=true` gives a PR the full run.
Only PRs authored by `kwojdalski` merge (`CONTRIBUTING.md`).

## 10. Other `scripts/dev` tools

  | Tool                                                                                                        | Use                                                                                                |
  | ---                                                                                                         | ---                                                                                                |
  | `bundle_suite.py <bundle>`                                                                                  | a sidecar bundle's own tests, installed into a throwaway copy - the only safe way to test a bundle |
  | `revendor.py TREE [--commit SHA] [--verify]`                                                                | the only way a `lib/` tree changes                                                                 |
  | `q_test_impact.py [paths] [--base REF]`                                                                     | which q suites a change reaches                                                                    |
  | `python_test_reach.py`, `python_tests_hook.py`                                                              | the push hook's Python selector                                                                    |
  | `coverage.q`, `master_coverage.py`                                                                          | the `.cov` instrumenter; whole-suite coverage on master                                            |
  | `pytest_shards.py`                                                                                          | CI shard assignment                                                                                |
  | `doc_examples.py`                                                                                           | finds docs q blocks marked `<!-- q-example: run|transcript -->`                                    |
  | `odbc_rosetta.sh`                                                                                           | q ODBC on Apple silicon, through x86_64 Rosetta                                                    |
  | `make_databento_duckdb.py`, `make_fx_deals_duckdb.py`                                                       | local DuckDB fixtures for the ODBC sources                                                         |
  | `merge_generated.py`, `hooks_full.py`, `checkout_guard.py`, `ci_scope.py`, `kdbx_attestation.py`, `ship.sh` | above                                                                                              |
