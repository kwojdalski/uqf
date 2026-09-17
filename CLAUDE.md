# uqf

## Working on a GitHub issue: always in a new worktree

Several Claude Code sessions work in this repository at once. Whenever you pick
up a GitHub issue to work on, you MUST do that work in a new git worktree,
never in the main checkout.

1. `/claim <number>` first. If the issue is already claimed, stop.
2. Create a worktree on a new branch off an up-to-date `master`:
   ```bash
   git fetch origin
   git worktree add .claude/worktrees/issue-<number> -b kwojdalski/issue-<number>-<slug> origin/master
   ```
   (or use the `EnterWorktree` tool). `.claude/worktrees/` is gitignored.
3. Do all edits, tests and commits inside that worktree. Never run
   `git checkout`, `git switch`, `git stash` or `git reset` in the main
   checkout: another session is probably using it.
4. When done: push the branch, open a PR, `/release <number>`, then
   `git worktree remove .claude/worktrees/issue-<number>`.

Only triage without edits (reading code, commenting, labelling) may stay in
the main checkout.
