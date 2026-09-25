# Continuous integration

[The CI workflow](../../.github/workflows/ci.yml) runs on pull requests and
pushes to `master`. It checks the PR merge result using the same
`.pre-commit-config.yaml` as local commits, then checks the generated decision
register against GitHub. Failed hooks fail the job, including hooks that
reformat files; CI does not commit their changes.

The workflow installs Python 3.14, uv 0.12.14 and pre-commit 4.6.2. Workspace
dependencies come from `uv.lock`; `UV_LOCKED=true` also keeps the hooks' own
`uv run` commands from silently resolving a different environment. Action
revisions are pinned. The job token has only content and issue read access, and
is supplied to the decision check through `GH_TOKEN`.

## Lanes

One per layer. `all` is every lane except `coverage` and the two that reach
outside the process, so it is what a release runs and not what an edit runs -
run the lane matching the layer you changed.

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
scripts/test.py smoke --targets HOST:PORT --tables TABLE:COL,COL   # live external metadata check
scripts/test.py stack-smoke         # restart the fleet, watch what it publishes
scripts/test.py all                 # every lane except coverage, smoke and stack-smoke
```

They are separate because they prove different things, and five of them cannot
prove what they claim if folded into `q-unit`:

  | Lane                 | Proves what `q-unit` cannot                                                                                                                                                                                                           |
  | ---                  | ---                                                                                                                                                                                                                                   |
  | `q-order`            | no test depends on running after another — [below](#order-independence)                                                                                                                                                               |
  | `q-backfill-process` | single-instance locking and resumption across a restart, which need a real filesystem and a genuinely separate process                                                                                                                |
  | `q-two-instances`    | the only lane where a source runs **live**: `.qetl.job.bounded.connect`, a source's `query` and `.qetl.source.validate_live` execute nowhere else                                                                                     |
  | `stack-smoke`        | the wiring — a declared table with no rows, or a process writing to its error log while we watch. [Below](#what-ci-cannot-check), with the three bugs that motivated it                                                               |
  | `smoke`              | A source contract's live half, against the **same declaration** the fixture is checked against. Excluded from `all`: a local run that depends on a remote host trains everyone to read red as "the network again"                     |

## What CI cannot check

Every ETL bug this tree has had was in the seam between a job and the stack it
runs in, and every one of them passed CI on the day it shipped:

  | bug                                                         | CI said          | reality                                                      |
  | ---                                                         | ---              | ---                                                          |
  | `` `time _ batch `` in a job's `on_batch`                   | 1447 tests green | 634 trapped `'type` errors; consumed nothing for ten minutes |
  | a timer target that was a lambda's *result*, not the lambda | green            | "called" `()` every 5s - no error, no effect                 |
  | a process publishing onto a table with no schema (#287)     | green            | rows discarded by the plant, silently                        |

In each case the job's functions were correct, so no test that calls them could
tell. And none of it was visible from outside either: TorQ traps a handler error
into the process's own stderr log, so the process stays up, heartbeats, and
reports healthy while doing nothing.

```bash
python3 scripts/test.py stack-smoke
```

restarts the stack, watches it, and fails if either

1. a table a **running** pipeline declares it publishes has no rows, or
2. a running process wrote to its error log while we watched.

Between them they would have caught all three. Expectations are derived from the
registry rather than listed, so a new pipeline is covered the day it is declared -
which is the point, since the processes that break are the ones nobody thought
to look at.

It is **not** in `all` and not in the workflow: it needs a kdb+ licence, free
ports and a couple of minutes. Being unable to run it in CI is not a reason to
skip it, and the table above is why.

Two things to know before reading a failure:

- **Errors are counted as a difference, not a total.** A stack that has been up
  a while has old errors in its logs; what is asserted is that nothing went
  wrong *while we were watching*.
- **One stack per machine.** The ports are fixed, so running this from a
  worktree while another checkout's stack is up will restart the plant
  underneath that one's processes and leave them orphaned - up, and subscribed
  to a tickerplant that no longer exists.

`.qetl.cfg.audit` and the limit-breach table are exempt from check 1, with
reasons, in `stack_smoke.MAY_BE_EMPTY` - a table that is empty because nothing
happened carries no information. A test holds that list free of dead entries.

## Order independence

`scripts/test.py q-order` runs the whole q suite again with the suites in the
opposite order, and `UQF_TEST_ORDER=shuffle` runs them in a seeded random one.
Both are the same 1459 tests; only the order differs.

It exists because five tests once passed on `run_tests.q`'s hand-written
namespace list alone. Each read live global state that other suites mutate:

  | test                                                    | what leaked in                                                                                             |
  | ---                                                     | ---                                                                                                        |
  | the documentation ratchet                               | `.qcompletetest`, `.qmethodsonly` and `.qpipe.job.nt_k` - whole namespaces built inside assertions         |
  | every registered source has a `.qpipe.source` namespace | sources registered by `etl_test_doubles.q`, which have no declaration file                                 |
  | every registered worker has a `.qpipe.job` namespace    | `.qetl.job.bounded` fixture workers named `reference`, `partial`, `fixture_*`                              |
  | every dict-valued registry is covered                   | `.qetl.dag.jobs`, which has not collapsed into a table while it is empty                                   |
  | cross-arbitrage consumes a batch                        | `publish`, left wired by whichever suite ran first                                                         |

None was a flaky test - each was deterministic, and each measured the process
rather than the tree. The fix in every case was to ask the tree: a registry is
intersected with the declaration files, the namespace set is read from `src/`
rather than scanned live, and a suite that drives a job wires that job's
`publish` itself.

The lane is what keeps them fixed. **A discrepancy between the two lanes is the
signal**, in either direction - a test that passes listed and fails reversed
depends on running after something, and one that passes reversed and fails
listed depends on running before it.

## q coverage limitation

GitHub's hosted runner has no project-provided q interpreter. If none is found,
CI emits a warning and writes the limitation in the job summary: `q-tests` is
skipped, as are Python IPC tests whose existing fixtures require q. Static q
checks and all other Python tests still run. A green hosted run therefore does
not mean the q runtime tests passed.

Run `scripts/test.py q-unit` and `scripts/test.py q-examples` locally before
merging q changes. The hook, CI and the Python IPC fixtures all find q by the
same rule `scripts/test.py` applies, which is TorQ's: `$QCMD` if set, otherwise
`q` on `PATH`, and nothing else - no `~/.kx/bin/q` and no PeachQ fallback. If
the runner later supplies one, CI runs the hook automatically; a failing
interpreter or test is a failure, not a reason to skip it.

The local branch-name and protected-branch hooks are excluded in CI because PR
checkout can be detached and pushes to `master` are expected. Every other hook
retains its existing configuration.

## Browser application

The `Browser application` job installs Node 24 and runs `npm ci`,
`npm run check` (formatting, TypeScript and frontend tests), and `npm run build`
in `web/`. It uses the committed npm lockfile. These checks exercise UI
behaviour with API fixtures; a live TorQ gateway is not required and is not
claimed as covered by this job.
