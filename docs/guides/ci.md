# Continuous integration

[The CI workflow](../.github/workflows/ci.yml) runs on pull requests and pushes
to `master`. It checks the PR merge result using the same
`.pre-commit-config.yaml` as local commits, then checks the generated decision
register against GitHub. Failed hooks fail the job, including hooks that
reformat files; CI does not commit their changes.

The workflow installs Python 3.14, uv 0.12.14 and pre-commit 4.6.2. Workspace
dependencies come from `uv.lock`; `UV_LOCKED=true` also keeps the hooks' own
`uv run` commands from silently resolving a different environment. Action
revisions are pinned. The job token has only content and issue read access,
and is supplied to the decision check through `GH_TOKEN`.

## What CI cannot check: the wiring

Every ETL bug this tree has had was in the seam between a job and the stack
it runs in, and every one of them passed CI on the day it shipped:

| bug | CI said | reality |
|---|---|---|
| `` `time _ batch `` in a job's `on_batch` | 1447 tests green | 634 trapped `'type` errors; consumed nothing for ten minutes |
| a timer target that was a lambda's *result*, not the lambda | green | "called" `()` every 5s - no error, no effect |
| a process publishing onto a table with no schema (#287) | green | rows discarded by the plant, silently |

In each case the job's functions were correct, so no test that calls them
could tell. And none of it was visible from outside either: TorQ traps a
handler error into the process's own stderr log, so the process stays up,
heartbeats, and reports healthy while doing nothing.

```bash
python3 scripts/test.py stack-smoke
```

restarts the stack, watches it, and fails if either

1. a table a **running** pipeline declares it publishes has no rows, or
2. a running process wrote to its error log while we watched.

Between them they would have caught all three. Expectations are derived
from the registry rather than listed, so a new pipeline is covered the day
it is declared - which is the point, since the processes that break are the
ones nobody thought to look at.

It is **not** in `all` and not in the workflow: it needs a kdb+ licence,
free ports and a couple of minutes. Being unable to run it in CI is not a
reason to skip it, and the table above is why.

Two things to know before reading a failure:

- **Errors are counted as a difference, not a total.** A stack that has
  been up a while has old errors in its logs; what is asserted is that
  nothing went wrong *while we were watching*.
- **One stack per machine.** The ports are fixed, so running this from a
  worktree while another checkout's stack is up will restart the plant
  underneath that one's processes and leave them orphaned - up, and
  subscribed to a tickerplant that no longer exists.

`.qcfgaudit` and the limit-breach table are exempt from check 1, with
reasons, in `stack_smoke.MAY_BE_EMPTY` - a table that is empty because
nothing happened carries no information. A test holds that list free of
dead entries.

## q coverage limitation

GitHub's hosted runner has no project-provided q interpreter. If none is
found, CI emits a warning and writes the limitation in the job summary:
`q-tests` is skipped, as are Python IPC tests whose existing fixtures require
q. Static q checks and all other Python tests still run. A green hosted run
therefore does not mean the q runtime tests passed.

Run `scripts/test.py q-unit` and `scripts/test.py q-examples` locally before
merging q changes. The hook, CI and the Python IPC fixtures all find q by the
same rule `scripts/test.py` applies: `$Q` if set, otherwise `~/.kx/bin/q`,
and nothing else - no PATH lookup and no PeachQ fallback, because an
interpreter chosen for you is one the result was not verified on. If the
runner later supplies one at that path, CI runs the hook automatically; a
failing interpreter or test is a failure, not a reason to skip it.

The local branch-name and protected-branch hooks are excluded in CI because
PR checkout can be detached and pushes to `master` are expected. Every other
hook retains its existing configuration.

## Browser application

The `Browser application` job installs Node 24 and runs `npm ci`,
`npm run check` (formatting, TypeScript and frontend tests), and
`npm run build` in `web/`. It uses the committed npm lockfile.
These checks exercise UI behaviour with API fixtures; a live TorQ gateway
is not required and is not claimed as covered by this job.
