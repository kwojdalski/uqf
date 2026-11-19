---
name: uqf-developer
description: >-
  The main developer guide for the uqf repository: where everything lives (the q
  quant library and ETL framework under src/, the TorQ adapter and overlays
  under scripts/, the uqs CLI and MCP under python/uqs, the frontend, sidecar
  bundles, the vendored lib/), how uqf sits on TorQ, the generated files and
  their generators, testing and gates (qUnit, pytest, qlinter, git hooks, KDB-X
  attestation, PeachQ), the repository workflow (a worktree per issue, claim and
  release, no shims), and debugging a running stack. TRIGGER when: doing
  development work anywhere in this repository - orienting in an unfamiliar
  area, changing code in any layer, running or adding tests, a hook, gate or CI
  failure, a stale generated file, a uqs command, process.csv or process
  overrides, runtimes, profiles, attachments, deployments or bundles, finding
  the right doc, or diagnosing a process that is down, idle or wrong. SKIP:
  plain TorQ questions with no uqf code involved (torq-developer); q style and
  precedence alone (kdb-q-conventions); scaffolding one job end to end (new-job,
  then come back here to test and ship it).
---

uqf is several components built **around** TorQ, not a replacement for it: a
pure-q eFX quant library (`src/`, seven `.qf` domains), a declaration-driven ETL
framework (`src/etl/`, `.qetl.*`, instances under `.qpipe.*`), one TorQ adapter
(`.qtorq`, `scripts/processes/torq_pipeline.q`), service-layer overlays on TorQ
(`scripts/torqconfig`, `scripts/torqcode`), and the `uqs` CLI (`python/uqs`)
that generates TorQ's configuration from the declarations and runs, inspects and
deploys the stack - plus a browser frontend, an Airflow provider and optional
sidecar bundles. TorQ and its Finance Starter Pack are vendored under `lib/` and
never edited.

This skill is the map and the procedures. Every fact cites the file that holds
it; when a file and this skill disagree, **the file wins** and the gap is worth
reporting. `CLAUDE.md` is binding and is summarised in rule group W.

--------------------------------------------------------------------------------

# HOW THE GUIDANCE IS SPLIT

  | Need                                                                                                                                                                                   | Go to                                                                                                                         |
  | ---                                                                                                                                                                                    | ---                                                                                                                           |
  | the map, the workflow, tests and gates, the CLI, TorQ integration, debugging                                                                                                           | **this skill** and its companion files                                                                                        |
  | the binding rules for q under `src/` and `scripts/` (layering L, declarations J, tables T, derived artefacts D, config C, logging G, traps Q, verification V) and a q review checklist | `uqf-framework` - load it as well whenever you write or review q there; this skill cites its rules rather than restating them |
  | q style: precedence, naming, sign conventions, qDoc                                                                                                                                    | `kdb-q-conventions`                                                                                                           |
  | adding one job (feed, etl, normalizer, horizon, backfill, reaction)                                                                                                                    | `new-job` skill; the `job-developer` agent does it end to end                                                                 |
  | TorQ itself: `.servers`, `.gw`, `.sub`, `.timer`, EOD, startup                                                                                                                         | `torq-developer`                                                                                                              |
  | claiming and releasing an issue                                                                                                                                                        | `claim`, `release` skills                                                                                                     |
  | a new doc page / changing an existing one                                                                                                                                              | `docs-writer` skill / `docs-maintainer` agent                                                                                 |

Specialist agents (hand work to them where they are the specialist):

  | Agent                           | Owns                                                                                                                                                                                                                                                                                                                         |
  | ---                             | ---                                                                                                                                                                                                                                                                                                                          |
  | `pipeline-developer`            | the ETL **framework**: bounded lifecycle, coverage ledger, run identity, IO managers, job graph, source contracts                                                                                                                                                                                                            |
  | `job-developer`                 | creating or changing jobs and sidecar pipelines                                                                                                                                                                                                                                                                              |
  | `uqf-quant-developer`           | the eFX quant modules under `src/foundation`, `pricing`, `portfolio`, `execution`, `market_data`                                                                                                                                                                                                                             |
  | `integration-automator`         | removing a second copy of a fact across TorQ/q/Python/docs                                                                                                                                                                                                                                                                   |
  | `round-trip-integrity-engineer` | scaffold, declaration-reader and generator round trips                                                                                                                                                                                                                                                                       |
  | `docs-maintainer`               | edits to existing docs (carries `.claude/docs-preferences.md`)                                                                                                                                                                                                                                                               |
  | `qlint-reviewer`                | ruling on qlinter findings                                                                                                                                                                                                                                                                                                   |
  | read-only auditors              | `ci-expert`, `security-auditor`, `q-authority-auditor`, `framework-coherence-auditor`, `dead-code-hunter`, `feature-duplication-auditor`, `causality-auditor`, `docstring-example-verifier`, `naming-cohesion-auditor`, `abbreviation-auditor`, `architecture-basher`, `data-platform-critic`, `production-readiness-critic` |

--------------------------------------------------------------------------------

# CORE PRINCIPLES

1. **A claim is true, or absent.** Publish, then record coverage, then
   checkpoint; an interruption must leave an under-claim. A wrong status or
   coverage row hides a gap nobody looks for again
   (`docs/architecture/pipeline-philosophy.md` §1).
2. **Derive; do not restate.** A fact has one home: TorQ's config, then the q
   declarations and `src/etl/plant_tables.q`, then Python, then docs. Anything
   written twice is generated and `--check`ed. Edit the source, regenerate,
   commit both - never hand-edit an output.
3. **Read what you call.** Before an IPC call, a qSQL query on a plant table, a
   `.qetl.*` call or a `uqs` flag, read its definition (or `--help`). Never
   infer from a name.
4. **Refuse at the boundary, by name.** Malformed input stops where it enters,
   with a message naming the fix. No silent healing, no fallbacks that hide a
   missing dependency, no backward-compatibility shims.
5. **Verify against a real q, and only what you touched.** KDB-X is the
   reference interpreter. Run the tests your change reaches, at most `-n 2`; the
   hooks and CI run the rest.

--------------------------------------------------------------------------------

# RULES

## Workflow (W) - from `CLAUDE.md` and the hooks

- **W1** Work on an issue in a **new worktree**: `/claim <n>` first (stop if
  claimed), then `git fetch origin` and
  `git worktree add .claude/worktrees/issue-<n> -b kwojdalski/issue-<n>-<slug> origin/master`.
  `.claude/worktrees/` is gitignored. Only read-only triage may stay in the main
  checkout.
- **W2** Never run `git checkout`, `switch`, `stash` or `reset` in the main
  checkout: other sessions use it. It may be far behind `origin/master`; read
  current code in a worktree on `origin/master`. The git stash stack is shared
  by every worktree - prefer a WIP commit.
- **W3** Commit and push only when asked. Never commit to `master`
  (`no-commit-to-branch` refuses it); push a branch with an explicit refspec
  (`git push origin HEAD:refs/heads/<branch>`); never force-push.
- **W4** Never `--no-verify`. `uqf-framework` V7 allows `SKIP=<id>` for a hook
  broken on master with the reason in the commit message - that is the user's
  decision, not yours: stop and report a hook you cannot pass.
  (`SKIP=peachq-push git push` is the one routine skip.)
- **W5** Run targeted tests only - never the whole Python suite, the whole q
  suite, `scripts/peachq/full_suite.py`, `UQF_HOOKS_FULL=1` or a stack test
  unless asked; `-n 2` at most (several sessions share 11 cores). Before
  pushing: `pre-commit run --from-ref origin/master --to-ref HEAD`, then the
  same with `--hook-stage pre-push`.
- **W6** KDB-X attestation: `CLAUDE.md` requires
  `uv run python scripts/dev/kdbx_attestation.py attest` after the **last**
  rebase of a PR that touches q, a generator or any `.py` (hosted CI has no
  KDB-X licence), then a push. A rebase invalidates it. When you were delegated
  a task, attest only if your brief says to; the merge queue
  (`scripts/dev/ship.sh`) attests otherwise. Never merge unless asked; only PRs
  authored by `kwojdalski` merge (`CONTRIBUTING.md`).
- **W7** When done with an issue: push, open the PR, `/release <n>`, then
  `git worktree remove .claude/worktrees/issue-<n>`.
- **W8** Never `uqs job install` or `uqs runtime prepare` in this checkout or a
  worktree (`--dry-run` is fine); `.claude/hooks/no_bundle_install.py` blocks
  them. Test a bundle with `uv run python scripts/dev/bundle_suite.py <bundle>`.
- **W9** Never edit `lib/torq`, `lib/torq-finance-starter-pack` or any `lib/`
  tree (`check.py vendored`; `scripts/dev/revendor.py` is the only way they
  change). **Vendor, don't reinvent**: wrap upstream q rather than rewriting it,
  and surface a missing licence to the user.
- **W10** No backward compatibility: a rename refuses the old spelling with a
  message naming the new one (`python/uqs/src/uqs/cli/renames.py`); no alias
  columns, no dual writes, no reader that branches on a version
  (`docs/architecture/markout-contract.md`, `bundle-overlay.md`).
- **W11** The repository is public: no bank-internal table names, hosts, schemas
  or logic, in code, tests, comments or commit messages - use generic analogues
  (`demo_deals`). Credentials never go in a file in the tree.
- **W12** Every filed issue gets a kind label and one rank label (`severity:*`
  for a defect, `priority:*` for an idea) (`CLAUDE.md`).
- **W13** `UQF_PEACHQ` is refused by `uqs`, pytest and the attestation; if your
  shell exports it, run with `env -u UQF_PEACHQ`. `QCMD` alone picks the q.

## Layers (L) - the full rules are `uqf-framework` L1-L6

- **L1** Nothing under `src/` knows TorQ; `.lg`, `.servers` and `.proc` are read
  only in `src/etl/core/log.q`, `worker_dependencies.q` and `run.q`
  (`check.py etl-layering`). `.qtorq` is the one TorQ-aware namespace.
- **L2** `src/etl/core/` never names a declaration; `src/` never reaches into
  `scripts/`.
- **L3** The quant library is pure: seven `.qf` domains, names fully qualified
  from the root, no `\d` deeper than one level anywhere
  (`check.py nested-contexts`), one definition per name, module edges reviewed
  in `scripts/gates/policy/module_deps.py`.
- **L4** Python derives from q; it never restates a schema, a table list or a
  process list (`python/uqs/src/uqs/model/` reads declarations as text).

## Jobs and the plant (J) - see `uqf-etl-framework.md`, `uqf-framework` J1-J11

- **J1** A job is one declaration file; there is no registration step. Scaffold
  with `uqs job new ... --dry-run`, then without, and follow `new-job`.
- **J2** A job calls its own `publish`, never `.u.upd`, never publishes `time`,
  and publishes only tables defined in `src/etl/plant_tables.q` (unkeyed, `time`
  first).
- **J3** Windows are half-open `[range_from;range_to)`; source queries are
  parameterised; fixtures are deterministic; `source_version` is required.
- **J4** A new standing process needs a profile or an `--unprofiled` reason and
  no `start_with_all` unless asked: the licence gives the plant 14 subscriber
  slots.
- **J5** Market data is never copied into a heap: `aj` on memory-mapped
  partitions, `date`-only selects, flat top-of-book columns.

## Generated files (D) - see `uqf-testing-and-gates.md` §6

- **D1** `scripts/generate/derive.py TARGET [--check]`: `operational-docs`
  (processes.md, `pipeline_dag.q`, `load_plan.q`, `process_ports.csv`, the
  profile table), `contract-surface`, `q-facts`, `plant-schema`,
  `compression-config` (these four need KDB-X), `table-properties`,
  `housekeeping-config`, `environment-tables`, `diagrams`.
- **D2** `process.csv`, `database.q` and the TorQ config CSVs under `$TORQDATA`
  are generated at bootstrap; overrides go through `uqs config set`, never an
  edit (`uqf-on-torq.md`).

## Tests (T) - see `uqf-testing-and-gates.md`

- **T1** A q suite is `.qt.<owner>[.<suite>]`, every name defined from the root
  with its whole path; never `\d .qt...`; helpers in `.qt.support`, doubles in
  `.qt.fixture.*`.
- **T2** Assert the message: `.qunit.assertThrows[f;arg;"*text*";"why"]`, not
  `assertError` (which passes on a typo). Keep `like` patterns to `"prefix*"` or
  `"*middle*"`.
- **T3** Restore what you replace; prove the negative; wire a job's `publish`
  before driving it; assert a reference value, not "didn't throw".
- **T4** Run `qlinter` on a q file before tracing it by hand; its findings are
  clues, a clean run is not proof, and the commit gate needs exactly the version
  in `.qlinter-version`.

## CLI (C) - see `uqf-cli.md`

- **C1** A group is a noun, a leaf a verb from one vocabulary; positionals
  `PROC`, `JOB`, `TARGET`, ...; `--json` on what reports, `--dry-run`/`-n` on
  what changes state, `--yes` on what asks, `--override NAME --reason` as the
  only bypass.
- **C2** A new command carries an effect mark (`ReadOnly`, `ChangesState`,
  `ConfirmsOnServer`), is imported in `cli/entry.py` (import order is help
  order), and gets its `test_json_stdout.py` and `test_dry_run_tree.py` cases.

--------------------------------------------------------------------------------

# THE DEVELOPMENT LOOP

1. **Claim and isolate** (W1). Read the issue and the code it names, in the
   worktree.

2. **Orient.** `uqf-layers.md` for where it lives; the module's header comment
   (this tree argues its design in headers); the matching `tests/q/test_*.q` or
   `python/*/tests/test_*.py` for the established style; `docs/architecture/`
   for why.

3. **Find what exists before writing.** Primitives in the file you are editing,
   a framework kind that already does it (`.qetl.job.stream.at_horizons`,
   `at_bars`, `at_rolling`, normalizers, reactions), an upstream tree in `lib/`.
   A loop over days in a job means you are rebuilding `.qetl.job.bounded`.

4. **Change the source of truth**, then regenerate what derives from it in the
   same commit (D1).

5. **Test what you touched** (W5): the q suites of the files you changed
   (`--q-files`), the Python tests of the modules you changed,
   `tests/qrun/test_documented_examples.py` after an `@eg` change, and load the
   tree after an ETL change:
   ```bash
   env -u UQF_PEACHQ q -q <<'EOF'
   \l src/init.q
   \l src/etl/init.q
   -1 "stream jobs: ",string count .qetl.job.stream.defined[];
   exit 0
   EOF
   ```

6. **Gates.** `qlinter` on the changed q files; the relevant `check.py` gates
   one at a time; `pre-commit run --from-ref origin/master --to-ref HEAD`.

7. **Commit** with the hooks on; if `operational-docs` rewrote a file, `git add`
   it and commit again. Commit messages follow the repository's attribution
   lines when the session supplies them.

8. **Push and PR** when asked (W3), attest if required (W6), `/release` (W7).

--------------------------------------------------------------------------------

# REVIEW CHECKLIST

1. **Layering** - no TorQ name under `src/`; no declaration name in
   `src/etl/core/`; no new owner file for a TorQ facility; no edit under `lib/`.
2. **One home per fact** - no hand-kept list of tables, processes, ports,
   sources or env names; generated outputs regenerated in the same commit; a doc
   table derived or gated.
3. **Declarations** - one file per job, scaffolded; `publish`, not `.u.upd`; no
   `time` column; published tables in `plant_tables.q`; half-open windows;
   parameterised queries; deterministic fixtures.
4. **q correctness** - no builtin as a name, no parameter named after a column
   it filters, `@[f;::;h]` for niladics, explicit parentheses for mixed
   arithmetic, no bare `/` line; qlinter clean at the pinned version.
5. **Tests** - assert messages; restore state; cover the negative; suite under
   `.qt`, defined from the root; on PeachQ too, or listed as a known gap with a
   `peachq-lacks:` or `tree-bug: #N` reason.
6. **CLI** - vocabulary, effect mark, `--json`/`--dry-run`/`--yes` where the
   conventions require them, no bespoke bypass flag, no hidden alias, the new
   command's test cases added.
7. **Live path** - every new check or capability is reached from a running path,
   not only from a test.
8. **Budget and safety** - no new `start_with_all`; nothing that installs a
   bundle into the checkout; no secret in a file; TLS verification on.
9. **Docs** - every `.qetl.*`/`.qf.*` name in prose exists
   (`check.py doc-references`); every `@eg` runs; generated pages untouched.
10. **Workflow** - in a worktree, on a branch, hooks run, no force-push, the
    issue claimed and released.

--------------------------------------------------------------------------------

# DEBUGGING - FIRST STEPS

Read state freely; start, stop or restart a stack only when asked.

```bash
uqs status                          # up? responding? wired? the footers name silent, failing, starved processes
uqs logs PROC --level ERROR         # the last line says where it stopped; -f to follow
uqs query PROC ".qetl.log.debug 1b" # DEBUG for one process, no restart
uqs query PROC                      # a session: .qetl.job.stream.def`job, .qpipe.job.<job>, .qtorq.received
uqs job runs list --unfinished      # a backfill that did not finish; re-running the same command resumes
uqs job gaps JOB --from T --to T    # where a streaming job was down, and refill commands
uqs config sources check SOURCE     # a source that won't connect or has the wrong shape
uqs data check                      # HDB partitions missing tables or columns (--apply to fix)
```

`up` but its table empty: its producer is not running - start through a profile.
Full ladder, failure table and the q traps: `uqf-debugging.md`.

--------------------------------------------------------------------------------

# COMPANION FILES

Read only when the task needs it; they are not auto-loaded.

- **`uqf-layers.md`** - the repository map: the four layers and the one arrow,
  every `src/` file and its `.qf` domain, `src/etl/`, every `scripts/`
  directory, the Python packages, `web/`, `sidecars/`, `lib/`, `tests/`, and the
  docs taxonomy. Read to orient or to find the right doc.
- **`uqf-etl-framework.md`** - load order and selective loading, every
  declaration verb and its required keys (`model/kinds.py`), the bounded
  lifecycle and `finish_window`, sources and transports, transforms, reactions,
  twins, variants, Kafka, verify; the plant registry; coverage, run identity,
  status, the job graph, IO managers, config, logging; `.qtorq` and the runners;
  the Python model. Read before changing the ETL tree.
- **`uqf-on-torq.md`** - the environment (`KDBSERVCONFIG`/`KDBSERVCODE` and the
  rest), the overlays, how `process.csv` and `database.q` are composed, process
  overrides, the gateway's policies, the processes, runtimes, profiles and the
  connection budget, attachments, deployments and targets, bundles and
  connectors, and TorQ/KDB-X behaviour this tree has paid for.
- **`uqf-cli.md`** - every `uqs` command group, the conventions and what
  enforces them, how a command registers and how to add one, the MCP server, the
  Python package map, the frontend and Airflow provider.
- **`uqf-testing-and-gates.md`** - KDB-X vs PeachQ, the q runner and suite
  layout, qUnit, pytest markers and options, qlinter and the q-traps gate, every
  `check.py` gate, every `derive.py` target, the hooks by stage, KDB-X
  attestation, CI, the `scripts/dev` tools.
- **`uqf-debugging.md`** - the outside-in ladder, missing-data protocol, a
  failed backfill, a dead streaming job, start refusals, a failure table, q
  traps measured on KDB-X, hook and gate failures, and machine quirks.
