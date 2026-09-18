# MIGRATION PLAN: align this Mac tree with the canonical Bitbucket upstream

Date: 2026-09-15
Planner: migration-planner (plan only — no source file changed, no step executed, no issue filed)
Prior plans in `docs/migrations/`: none (this directory did not exist; this is the first plan)

---

## Headline

**The canonical upstream is not reachable from this machine.** `git remote -v` lists exactly one
remote — `origin https://github.com/kwojdalski/uqf.git`. There is no Bitbucket remote, and none of
the canonical commits named in the drift report (`bb92cde`, `b44b2c8`, `0b0ce56`) exist in this
repository's object store (`git cat-file -t` → *not a valid object name* for all three). Every path
below that begins "fetch canonical" has an unmet prerequisite that only the user can satisfy, from
the work machine `/home/s6999648/repos/uqf`, probably over VPN. Nothing in this plan can start with
a fetch.

    Now:   A feature branch on the public GitHub mirror, itself 15 commits behind that mirror's
           master, which is in turn the exact 140-commit-behind drift baseline; plus one day of
           uncommitted work whose target paths upstream has already moved.
    Goal:  This tree's layout, history and in-flight work reconciled with the canonical Bitbucket
           master, with nothing half-renamed and the test gate green at every stop.

    Blocked on (outside this repo):
      1. Someone on the work machine pushing canonical master to the GitHub mirror (or adding a
         reachable Bitbucket remote / supplying a bundle). Gates every migration path.
      2. A decision only the user can make: whether today's `.qpipe` work is superseded by
         upstream's `src/etl/core/markout.q` + `src/etl/workers/markout.q`. Unreadable from here.

---

## What is actually true here (verified, not assumed)

The drift report is a snapshot taken on another machine. Five of its assumptions are wrong or
incomplete for *this* tree, and two of the corrections change the plan.

| # | Checked | Result |
|---|---------|--------|
| 1 | `git remote -v` | Only `origin` → github.com/kwojdalski/uqf. No Bitbucket remote. |
| 2 | `git rev-parse origin/master` | `b62464e39935649903f7caca46a94a196f4a78e1` |
| 3 | `git ls-tree origin/master src/ tests/ python/` | Flat `src/*.q`, flat `tests/test_*.q`, `tests/run_tests.q` present, `python/uqf-client` hyphenated — fully **pre**-restructuring. |
| 4 | `git rev-list --left-right --count origin/master...HEAD` | `15  2` |
| 5 | `git rev-list --left-right --count @{u}...HEAD` | `0  0` |
| 6 | `q tests/run_tests.q` | **342 tests: 342 passed, 0 failed, 0 errored** |
| 7 | `git log --all -- scripts/torq_markout_etl.q` | empty — never existed in history |
| 8 | `git branch -vv` (master) | `01a3f73 [origin/master: behind 35]` |

### Correction 1 (changes the plan): the mirror's master has **not** diverged from canonical

The drift context states that today's two pushed commits mean the GitHub mirror "has now DIVERGED
from canonical, not merely lagged — a mirror refresh is no longer a fast-forward."

That is not what happened. `a27e785` and `a4a7dd4` were pushed to the branch
`kwojdalski/snake-case-function-names`, not to master. `origin/master` is still exactly `b62464e`,
which contains no `.claude/agents/causality-auditor.md`, no `extension-brainstormer.md`, no
`docstring-example-verifier.md` and no `scripts/processes/torq_pipeline.q`
(`git ls-tree -r origin/master .claude/agents/` returns only `uqf-developer.md`;
`git ls-tree origin/master scripts/` has no `torq_pipeline.q`).

**Consequence:** refreshing the mirror's *master* from canonical is still a clean, non-diverged
update. The divergence is quarantined inside one feature branch, which is precisely where a
divergence is cheap. This removes the need for any force-push to master, and with it the single
most dangerous step the drift report implied.

### Correction 2 (changes the plan): `origin/master` **is** the drift baseline

`origin/master` = `b62464e3993…`, and the report's `uqf-copy` snapshot is `b62464e3`. They are the
same commit. So the mirror is not "~132+ commits behind" — it is the full **140 commits behind**,
and the report's measured delta (223 files, +19,863/−6,102) applies verbatim to `origin/master`.
`uqf-copy` is not a separate third tree to reconcile; it is a second checkout of the mirror's tip.
Three trees in play collapse to two distinct commits.

### Correction 3: this working tree is behind even the mirror

Local `HEAD` is 15 commits behind `origin/master` (fifteen merge commits, PRs #26–#41). Local
`master` is 35 behind. So the tree is roughly **155 commits behind canonical**, not 140, and any
plan must close the mirror gap before or as part of closing the canonical gap.

### Correction 4: the markout "refactor" is net-new, with no git rollback point

`scripts/torq_markout_etl.q` is untracked (`??`) and has **never** appeared in this repository's
history on any branch. There is no committed 183-line predecessor here, so "183→128 lines" is not a
reviewable diff and `git checkout --` cannot restore anything. The same is true of
`scripts/torq_posbook_etl.q` (120 lines) and `scripts/torq_fx_trades_feed.q` (52 lines). These three
files exist **only** in the working tree. Losing them loses them.

### Correction 5: `docs/integrations/torq/` already exists locally

It is listed as an absent upstream-only subsystem, but `docs/integrations/torq/README.md` is present and modified.
`docs/{guides,architecture,reference,decisions,integrations}` are genuinely absent.

---

## Gate hazards found in the existing verification setup

A gate that silently skips reads as a pass, so these were checked before being relied on.

1. **The q gate dies on adoption.** The `q-tests` pre-commit hook runs `"$QBIN" tests/run_tests.q`
   with `files: '\.q$'`. Upstream *deletes* `tests/run_tests.q` in favour of `tests/q/run_unit.q`
   and `scripts/test.sh q-unit`. The moment the restructuring lands, every `.q` commit hard-fails
   on a missing file. This is the loud failure and therefore the safe one — but it means
   `.pre-commit-config.yaml` must arrive in the *same* step as the test-tree move, never after.
2. **The Python gates silently skip on adoption — the dangerous one.** `ruff`, `ruff-format` and
   `uqf-client-tests` are all scoped `files: '^python/uqf-client/'`. Upstream renames that directory
   to `python/uqf_client/`. The regex then matches nothing, the hooks report success without
   examining a single file, and the Python subproject becomes unlinted and untested while the hook
   output still says green. Any step that performs this rename must re-point the regex in the same
   commit, and must be gated by `pre-commit run --all-files --verbose` with the hook's file count
   inspected — not merely by its exit code.
3. **`scripts/test.sh` does not exist here.** Upstream's stated entry point
   (`scripts/test.sh q-unit`) is absent locally. It cannot be used as a gate until it arrives.
4. **No step may commit on master.** `no-commit-to-branch` blocks `master` and `main`.
5. **Branch names are rewritten under you.** `branch-naming-with-git-username` has
   `always_run: true` and will silently `git branch -m` any branch not matching
   `kwojdalski/<name>`. Create branches already namespaced or the plan's refs drift.
6. **`core.hooksPath` is unset**; `.git/hooks/pre-commit` is the pre-commit shim. Upstream's
   `.githooks/pre-commit` requires an explicit `git config core.hooksPath .githooks` — a manual step
   that no file copy performs.

**The only gate confirmed working right now** is `q tests/run_tests.q` (342/342 green, executed
during this planning run, interpreter `/Users/krzysztofwojdalski/.kx/bin/q`). It is the baseline
every early step is measured against, and it stops existing partway through the migration — the plan
below names its successor explicitly rather than letting the gate evaporate.

---

## PATHS CONSIDERED

| # | Path | Breakage risk | Reversible | Effort | Work discarded |
|---|------|---------------|------------|--------|----------------|
| A | Refresh mirror from canonical, then replay local work onto the new layout (**migrate then port**) | low | yes, to `a4a7dd4` / tag | M | none |
| B | Reconstruct the restructuring locally by hand from the drift report | **HIGH** | **no** (parallel history) | L | none up front, then all of it |
| C | Land today's work on the lagging mirror first, then take the refresh (**port then migrate**) | medium | yes | L | none, but every port is redone |
| D | Abandon the mirror line; canonical work moves to the work machine, salvage today's work as patches | low | partly (branch abandoned) | S–M | the two pushed commits' place in history |

Effort drivers: **A** = one conflict resolution against 223 moved/changed files, done once, plus a
decision. **B** = hand-reproducing 140 commits of renames *and* ~19.8k lines of new upstream content
that no report contains. **C** = the same port done twice, the second time across renamed paths.
**D** = re-establishing an environment, not resolving conflicts.

### Why A

It is the only path where the irreversible act (rewriting the feature branch) happens to a branch
nobody else builds on, after the upstream layout is visible on disk and the supersession question
has been answered — so the port is made against known paths instead of guessed ones. Correction 1
is what makes it cheap: master is not diverged, so the refresh is an ordinary update and no force-push
to a shared branch appears anywhere in the plan.

### Disqualifying the others

- **B is the trap, and it is the most tempting option because it is the only one that can start
  right now, with no VPN and no work machine.** It is wrong on two independent grounds. First, a
  hand-made `git mv` of `src/ccy.q → src/foundation/ccy.q` produces a *different commit* than
  upstream's real move; when canonical finally arrives, git sees two unrelated histories that both
  created `src/foundation/ccy.q`, and the conflict is permanent — it must be resolved on every
  future merge, forever. Second, it cannot work even in principle: the drift report enumerates
  renames, but upstream also adds `src/etl/core/` (14 files), `src/etl/workers/` (7 files),
  `python/uqf_airflow_provider/`, `tests/integration/`, six `docs/` trees and `.github/agents/*` —
  roughly 19,863 added lines whose *contents* appear in no report. Reconstruction produces the
  directory names and none of the code. Do not start this because the other paths are blocked.
- **C** doubles the work and concentrates the risk at the worst moment. Every file it touches
  (`scripts/torq_*_etl.q`, `python/torq_orchestrator/`) is a file upstream has moved or rewritten,
  so each commit it creates becomes a rename-plus-content conflict later. Worse, it commits to the
  `.qpipe` design *before* anyone has read `src/etl/core/markout.q` — the exact decision the user
  must make first. It also drags the crypto-fills work, which is unrelated to the migration, into
  the migration's blast radius.
- **D** is a legitimate answer and should be reconsidered if the mirror push proves impossible for
  policy reasons, but as a default it discards commits that are already pushed and public, and it
  strands the user's own uncommitted crypto-fills work in a tree that is about to be re-cloned. Keep
  it as the fallback if Step 1 fails, not as the opening move.

**RECOMMENDED: A** — because the upstream layout must be readable before today's work can be ported
onto it, and Correction 1 means getting it costs an ordinary fetch rather than a force-push.

---

## STEPS (Path A)

Steps 0–2 are the only ones startable today. Steps 3+ are gated on the mirror refresh.

### Step 0 — Make the uncommitted work recoverable

- **Precondition:** `git status --porcelain` shows the 6 modified + 3 untracked `.q` files.
  `git stash list` is empty (verified).
- **Action:** Capture the working tree to a location outside the repo — a patch for tracked changes
  and plain copies of the three untracked files, which no git command can restore (Correction 4).
  Do not commit, do not stash: a stash lives in the repo whose branch is about to be rewritten.
  Keep the user's crypto-fills work and the migration-related `.qpipe` work as **separate** patches;
  they have different fates and must not be ported as one blob.
- **Gate:** the patch applies clean to a pristine checkout —
  `git stash list` still empty, and the patch file's `git apply --check` succeeds against
  `a4a7dd4`. Byte-compare the three copied `.q` files against the originals.
- **Rollback:** nothing to roll back; the step only creates files outside the repo.
- **If interrupted here:** the tree is exactly as it is now, with a recoverable copy. Fully safe.
- **Reversible:** yes, trivially.

### Step 1 — Get canonical onto a reachable remote (EXTERNAL — blocks everything after)

- **Precondition:** access to the work machine `/home/s6999648/repos/uqf` and its Bitbucket remote.
  Not satisfiable from this Mac (see Headline).
- **Action:** From the work machine, push canonical master to the GitHub mirror. Because
  `origin/master` is an ancestor of canonical (Correction 2 — the mirror tip *is* the drift
  baseline), this is a fast-forward and needs no `--force`. If policy forbids pushing the work
  repo to a public mirror, the alternative is `git bundle create uqf-canonical.bundle master` on
  the work machine, transferred by hand — the plan continues unchanged from Step 2, fetching the
  bundle instead of the remote.
- **Gate:** on this Mac, `git fetch origin && git rev-parse origin/master` returns a commit that is
  **not** `b62464e`, and `git ls-tree origin/master src/` shows `src/foundation/`. If the second
  check fails the push went to the wrong ref.
- **Rollback:** none needed — a fast-forward of the mirror destroys no history. If the wrong ref was
  pushed, the mirror's previous tip `b62464e` is preserved in this Mac's reflog.
- **If interrupted here:** unchanged local tree, refreshed mirror. Safe indefinitely.
- **Reversible:** yes.
- **Blocked on:** a human with VPN/work-machine access. This is the whole plan's critical path.

### Step 2 — Answer the supersession question (DECISION — blocks Steps 5–6)

- **Precondition:** Step 1's gate passed, so `src/etl/core/markout.q` and
  `src/etl/workers/markout.q` are readable via `git show origin/master:src/etl/core/markout.q`.
- **Action:** Read upstream's ETL core against the three untracked local files and decide, per file,
  one of: **port** (`.qpipe` adds something upstream lacks), **merge** (fold the useful blocks into
  `src/etl/`), or **drop** (superseded). The same question applies to the `Pipeline` dataclass
  registry in `core.py` versus upstream's `worker_config.q` / `worker_runtime.q` and the new
  `extra_processes.csv` / `process_overrides.csv` — upstream may already have replaced the literal
  `process.csv` row dicts by a different mechanism, in which case the +447/−152 refactor is wasted
  motion rather than a conflict.
- **Gate:** a written per-file verdict for all four artefacts (`torq_pipeline.q`,
  `torq_markout_etl.q`, `torq_posbook_etl.q`, `core.py` registry). No command can prove this —
  **the step is unverifiable and is the plan's highest-uncertainty item.**
- **Rollback:** n/a (a decision, not a change).
- **If interrupted here:** unchanged tree; the decision is simply not yet made.
- **Reversible:** yes.

### Step 3 — Adopt upstream onto a fresh local branch, without touching the old one

- **Precondition:** Step 1 gate green. `git status --porcelain` is **empty** (Step 0 captured it).
- **Action:** Tag the current tip as a rollback anchor, then create
  `kwojdalski/adopt-canonical` directly at `origin/master`. Do not rebase, do not merge, do not
  reset the existing branch — the existing branch stays exactly where it is as the fallback.
  Note the branch-naming hook (hazard 5): the `kwojdalski/` prefix is mandatory or it renames it.
- **Gate:** `git rev-parse HEAD` equals `origin/master`; `git status --porcelain` empty;
  `ls src/foundation src/etl/core tests/q` all exist; and the **new** gate runs green —
  `scripts/test.sh q-unit` (which only exists on this branch; hazard 3). If upstream's test entry
  point fails here, stop: the problem is upstream's tree, not the migration, and Step 4 must not
  proceed on a red baseline.
- **Rollback:** `git checkout kwojdalski/snake-case-function-names` — the old branch and
  `a4a7dd4` are untouched. Fully reversible.
- **If interrupted here:** two independent local branches, both green, neither modified. The safest
  stopping point in the plan and a legitimate end-of-day state.
- **Reversible:** yes, completely.

### Step 4 — Re-point the verification config, in one commit, before any code is ported

- **Precondition:** on `kwojdalski/adopt-canonical`, Step 3 gate green.
- **Action:** Reconcile `.pre-commit-config.yaml` with the new layout *if* upstream's own version has
  not already done so — specifically the `^python/uqf-client/` → `^python/uqf_client/` regexes
  (hazard 2) and the `tests/run_tests.q` entry point (hazard 1) — and set
  `git config core.hooksPath .githooks` if upstream ships `.githooks/pre-commit` (hazard 6).
  Nothing else changes in this commit.
- **Gate:** `pre-commit run --all-files --verbose` and **read the per-hook file counts**: the ruff
  and pytest hooks must report a non-zero number of files. Exit code 0 alone is not the gate — a
  skipping hook also exits 0. That is the entire point of doing this before Step 5.
- **Rollback:** `git checkout -- .pre-commit-config.yaml && git config --unset core.hooksPath`.
- **If interrupted here:** a green upstream tree with working gates and no ported work. Safe.
- **Reversible:** yes.

### Step 5 — Port the three agent definitions (the trivially portable half of `a27e785`)

- **Precondition:** Step 4 gate green.
- **Action:** Add `causality-auditor.md`, `docstring-example-verifier.md` and
  `extension-brainstormer.md`. Decide their home first: upstream introduced `.github/agents/*` with
  ten agents while these three live in `.claude/agents/`. Placing them in `.claude/agents/` beside
  upstream's `.github/agents/` leaves two competing agent directories — resolve that rather than
  inherit it. Update every path these three files reference: they cite `src/*.q` and `tests/*.q`
  paths that the restructuring moved, so a copy without a reference sweep is a half-applied step.
- **Gate:** `grep -rnE 'src/(ccy|daycount|rates|stats|forwards|options|positions|risk|execution|book|dqchecks|microstructure|data|example_defaults)\.q|tests/test_' <the three files>` returns
  nothing, and `pre-commit run --all-files` stays green.
- **Rollback:** `git revert` the single commit, or delete the three files.
- **If interrupted here:** upstream layout plus three agents; no pipeline work. Green and coherent.
- **Reversible:** yes.

### Step 6 — Port, merge or drop the `.qpipe` work per Step 2's verdict

- **Precondition:** **Step 2's written verdict exists.** Do not start this step on a guess — that is
  what makes it the step most likely to be wasted.
- **Action:** For each artefact marked *port* or *merge*, land it at its upstream-correct path
  (`src/etl/workers/`, not `scripts/`) with its loader wiring updated in the same commit — a worker
  that is moved but not registered with `worker_config.q` / `worker_runtime.q` is exactly the
  half-applied failure this plan exists to prevent. For each marked *drop*, delete the captured copy
  and record why, so the next person does not resurrect it.
- **Gate:** `scripts/test.sh q-unit` green, plus the ETL suite — `tests/integration/` exists upstream
  and is the only thing that can prove a worker is actually registered. Confirm its runner's name on
  the refreshed tree before relying on it; if no command exercises the new worker, say so and treat
  the step as unverified.
- **Rollback:** `git revert` per commit; the captured copies from Step 0 remain outside the repo.
- **If interrupted here:** some workers ported, some not. **This is the one step whose interruption
  is genuinely ambiguous** — mitigate by committing one worker per commit, each independently green,
  so "where did it stop" is answerable from `git log`.
- **Reversible:** yes.

### Step 7 — Rewrite issues #42–#46 onto post-restructuring paths

- **Precondition:** Step 3 gate green (the real new paths are readable). `gh auth status` green
  (verified: logged in as `kwojdalski`).
- **Action:** Update the five issue bodies. All five citations currently resolve against *this* tree
  and all five break after adoption — verified by reading the cited lines:
  `src/execution.q:164` = `vwap:{[prices;sizes]` → `src/execution/execution.q`;
  `src/dqchecks.q:74` = `check_limit:{[metrics;limits;key_col]` → `src/market_data/dqchecks.q`;
  `src/options.q:41` = `d1:{[s;k;rd;rf;sigma;t]…` → `src/pricing/options.q`;
  `tests/test_execution.q:126` = `test_hit_ratio_by_no_grouping_gives_one_overall_row:{[t]` →
  `tests/q/test_execution.q`; `docs/ROADMAP.md` → confirm whether it survived the `docs/` reshuffle.
  Cite the new path **and** re-locate the line number — the renames also changed file contents, so
  carrying the old line number across is a silently wrong reference.
- **Gate:** for each issue, the quoted path exists on `kwojdalski/adopt-canonical` and the cited line
  contains the quoted identifier: `git show HEAD:<newpath> | sed -n '<newline>p'`.
- **Rollback:** issue edits are recoverable from GitHub's edit history, but clumsily. Low stakes.
- **If interrupted here:** a mix of stale and updated issues. Annoying, not dangerous. Work
  highest-numbered first so "everything below N is stale" stays a true statement.
- **Reversible:** partly (edit history only).

### Step 8 — Retire the old branch (IRREVERSIBLE — isolated deliberately)

- **Precondition:** Steps 5–7 complete and green; Step 2 verdicts all discharged; the Step 0
  captures still verified present.
- **Action:** Point `kwojdalski/snake-case-function-names` at the new work, or delete it. This is the
  **only** irreversible step and it is last on purpose.
- **Gate:** before acting, prove nothing is lost — `git log --oneline <old>..<new>` accounts for the
  content of `a27e785` and `a4a7dd4`, and every Step 2 *drop* verdict is written down.
- **Rollback:** the tag from Step 3 and the reflog only. After the reflog expires (90 days default),
  a deleted branch whose commits are unreachable is gone.
- **If interrupted here:** two branches coexist. Harmless — this step is housekeeping, and skipping
  it forever costs nothing but tidiness.
- **Reversible:** **no.** Needs explicit sign-off, not just a plan.

### Step 9 — Re-apply the user's crypto-fills work

- **Precondition:** Step 4 green; the crypto-fills patch from Step 0 kept separate.
- **Action:** Apply the `cli.py` / `core.py` / `torq_demo_mcp.py` crypto-fills changes and the
  `docs/guides/torq-demo.md` + `docs/integrations/torq/README.md` edits to the new layout. Note that upstream supersedes
  `docs/guides/torq-demo.md` with `docs/guides/torq-demo.md`, so the doc edits must be re-targeted or they
  will be applied to a file that is no longer the live one. This work is **independent of the
  migration** and could equally be landed first on the old branch and cherry-picked.
- **Gate:** `cd python/torq_orchestrator && uv run pytest` (note: `pytest` is not on `PATH` — only
  reachable through `uv run`, verified), plus `pre-commit run --all-files` with a non-zero Python
  file count per hazard 2.
- **Rollback:** `git revert`; the patch survives outside the repo.
- **If interrupted here:** partially re-applied Python work. Keep it to one commit to make this
  binary.
- **Reversible:** yes.

---

## ISSUE LIST (not filed)

| # | Title | Depends on | Gate | Blocked on a decision? |
|---|-------|-----------|------|------------------------|
| M0 | Capture uncommitted work outside the repo before any branch surgery | — | patch `git apply --check`s against `a4a7dd4`; 3 untracked `.q` files byte-compared | no |
| M1 | Push canonical Bitbucket master to the GitHub mirror (work machine / VPN) | — | `git fetch && git rev-parse origin/master` ≠ `b62464e`; `git ls-tree origin/master src/` shows `src/foundation/` | **yes — external access; whether a public mirror may carry it** |
| M2 | Decide: is the `.qpipe` block library superseded by upstream `src/etl/core/markout.q`? | M1 | written port/merge/drop verdict for all 4 artefacts | **yes — the plan's key unknown** |
| M3 | Branch `kwojdalski/adopt-canonical` at refreshed `origin/master`; tag the old tip | M0, M1 | `scripts/test.sh q-unit` green on the new branch | no |
| M4 | Re-point `.pre-commit-config.yaml` and `core.hooksPath` at the new layout | M3 | `pre-commit run --all-files --verbose` with **non-zero file counts** on the ruff/pytest hooks | no |
| M5 | Port the three agent definitions, sweeping their pre-rename path references | M4 | pre-rename-path grep returns nothing; pre-commit green | partly — `.claude/agents/` vs upstream `.github/agents/` |
| M6 | Port/merge/drop the `.qpipe` workers into `src/etl/workers/` with loader wiring | M2, M4 | `scripts/test.sh q-unit` + `tests/integration/` green, one commit per worker | no (M2 carries the decision) |
| M7 | Rewrite issues #42–#46 onto post-restructuring paths and re-located line numbers | M3 | `git show HEAD:<newpath> \| sed -n '<newline>p'` contains the quoted identifier | no |
| M8 | Retire `kwojdalski/snake-case-function-names` — **IRREVERSIBLE** | M5, M6, M7 | `git log <old>..<new>` accounts for `a27e785` + `a4a7dd4` | **yes — explicit sign-off required** |
| M9 | Re-apply crypto-fills work; re-target doc edits to `docs/guides/torq-demo.md` | M4 | `uv run pytest` in `python/torq_orchestrator`; pre-commit green | no |

M0 is the only issue that can be worked today. M1 is the critical path; M2 can be answered the
moment M1 lands and should be queued behind it immediately, because M6 is wasted effort until it is.

---

## Irreversible steps, listed separately

1. **Step 8 / M8 — retiring `kwojdalski/snake-case-function-names`.** The only one. Recoverable
   only via the Step 3 tag and the reflog; after reflog expiry the commits are unreachable and gone.
   Requires explicit sign-off. It is deliberately last and deliberately optional — the plan completes
   without it.

Two irreversible steps the drift report's framing implied are **not** in this plan, because
Correction 1 removed the need for them: no force-push to the mirror's master (the refresh is a
fast-forward), and no history rewrite to reconcile a divergence that exists only on a feature branch.

Path B is not an irreversible *step* but an irreversible *strategy*: a hand-reconstructed rename
history conflicts with the real one permanently and cannot be undone by reverting a commit.

---

## Assumed, not verified

Everything about the canonical tree's *contents*. From this machine the canonical commits do not
exist, so the following are taken on the drift report's word and each one gates a step:

1. That `src/etl/core/markout.q` and `src/etl/workers/markout.q` exist and do something comparable
   to the local `.qpipe` work. **Gates Step 2/M2 and therefore Step 6/M6** — the plan's largest
   unknown, flagged rather than guessed.
2. That upstream's own `.pre-commit-config.yaml` already handles the `python/uqf_client/` rename. If
   it does not, hazard 2 is live in upstream too and Step 4 is a real fix rather than a check.
3. That `scripts/test.sh q-unit` and `tests/q/run_unit.q` exist and are green on canonical. Step 3's
   gate verifies this on arrival; if red, the migration stops there by design.
4. That `tests/integration/` contains something that actually exercises a registered ETL worker.
   If not, Step 6 has no real gate and must be marked unverified.
5. That `docs/ROADMAP.md` survived the `docs/` reshuffle under some name (issue #46 cites it).
6. That the 15 mirror commits `HEAD..origin/master` (PRs #26–#41) are themselves ancestors of
   canonical. Highly likely given Correction 2, but only checkable after M1.

---

## First action

**Step 0 / M0: capture the working tree outside the repo** — a patch for the six modified files
(split: migration-related vs the user's crypto-fills work) and plain copies of
`scripts/torq_markout_etl.q`, `scripts/torq_posbook_etl.q`, `scripts/torq_fx_trades_feed.q`, which
are untracked, absent from all history, and unrecoverable by any git command (Correction 4).

It is the only step that needs no VPN, no decision and no upstream access, it makes every later step
reversible, and it costs minutes. **In parallel, ask for M1** — the mirror push is the critical path
and nothing past Step 2 can begin without it.
