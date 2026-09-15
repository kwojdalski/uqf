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

## q coverage limitation

GitHub's hosted runner has no project-provided q interpreter. If none is
found, CI emits a warning and writes the limitation in the job summary:
`q-tests` is skipped, as are Python IPC tests whose existing fixtures require
q. Static q checks and all other Python tests still run. A green hosted run
therefore does not mean the q runtime tests passed.

Run `QHOME="$HOME/.kx" "$HOME/.kx/bin/q" tests/run_tests.q` locally before
merging q changes. If the runner later supplies an interpreter through PATH,
`~/.kx/bin/q`, `./q` or `./peachq/q`, CI automatically runs the existing q hook;
a failing interpreter/test is a failure, not a reason to skip it.

The local branch-name and protected-branch hooks are excluded in CI because
PR checkout can be detached and pushes to `master` are expected. Every other
hook retains its existing configuration.

## Decision-register failures

`python3 scripts/build_decision_log.py --check` needs GitHub access, so it
runs in CI instead of during every local commit. After an answer or issue
body changes, run `python3 scripts/build_decision_log.py` from an authenticated
checkout, inspect `docs/decisions.md`, and commit the regenerated snapshot.
The check compares against live issue data; an API failure also fails it.
No manual changes to generated text are needed.

CI runs checks; this workflow does not configure branch-protection rules.
