---
name: snapshot
description: Create a dated stable-version git tag (stable/YYYY-MM-DD), push it, append an AI-generated entry to CHANGELOG.md, create a GitHub Release with the same notes, and summarise changes. Use when the user wants to mark the current state as a stable checkpoint or review daily progress.
---

# Snapshot

You are a release engineer creating a daily stable checkpoint for this q/kdb+ eFX quant library (uqf).

## Steps — execute in order

### 1. Determine today's date tag

Today is available in the system context as `currentDate`. Construct the tag name:

```
stable/YYYY-MM-DD
```

### 2. Check whether the tag already exists

Run:
```bash
git tag --list "stable/YYYY-MM-DD"
```

If the tag already exists, skip tag/CHANGELOG creation and tell the user. Proceed to step 8 (GitHub Release) anyway, in case the release itself is still missing.

### 3. Find the previous snapshot tag from GitHub Releases

Query GitHub Releases to find the most recently published daily build (today's release does not exist yet at this point, so the first result is yesterday's):

```bash
gh release list --limit 50 --json tagName --jq '.[].tagName' | grep "^stable/" | head -1
```

If no previous `stable/*` release exists on GitHub, fall back to the first commit:
```bash
git rev-list --max-parents=0 HEAD
```

Store the result as PREV.

### 4. Analyse changes since PREV

Collect the raw material:

**Commit list with full messages** (for analysis):
```bash
git log PREV..HEAD --format="%h %s%n%b"
```

**Files changed** (stat summary):
```bash
git diff --stat PREV..HEAD
```

**Commit count:**
```bash
git log PREV..HEAD --oneline | wc -l
```

Now write the release notes. Structure them as follows:

- **Overview** (2-3 sentences): what was the main thrust of today's work — new q functions, bug fixes, new example scripts, test coverage, refactoring, or documentation? Be specific about which subsystems moved.
- **Changes by area**: group commits by module/directory. For each group, write 1-2 sentences describing what changed and why it matters. Focus on: `src/*.q` (the pricing/risk/execution modules, each its own namespace — stats, ccy, daycount, rates, forwards, options, risk, execution, book, microstructure, dqchecks), `tests/` (qUnit suite), `scripts/` (worked examples and dev tooling), `python/torq_orchestrator/` (the CLI/MCP demo orchestrator), `lib/` (vendored third-party dependencies), `docs/` (qDoc output), and config/dependency files (`README.md`, `.claude/skills/`). Skip areas with no changes.
- **Files changed**: include the `git diff --stat` summary line (e.g. "12 files changed, 340 insertions(+), 45 deletions(-)").

Keep the notes factual, concise, and under 40 lines total. No preamble, no sign-off. These same notes are reused verbatim for both the CHANGELOG.md entry (step 5) and the GitHub Release body (step 8) — write them once here.

If the tag already exists (step 2), skip this step too - there is nothing new to analyse, and step 8 will just verify the release exists.

### 5. Update CHANGELOG.md

This is the persistent, in-repo, git-diffable record - distinct from the GitHub Release notes in step 8, which only live on GitHub's Releases page and aren't visible in a plain clone. Do not skip this step in favor of step 8 alone.

If `CHANGELOG.md` doesn't exist at the repo root, create it with this header:
```
# Changelog

Daily stable snapshots of this repository. Newest first.
```

Prepend a new section immediately after the header (newest entry on top, reverse-chronological), using the exact notes written in step 4:

```
## stable/YYYY-MM-DD

<the notes from step 4>
```

Stage and commit just this file:
```bash
git add CHANGELOG.md
git commit -m "Add changelog entry for stable/YYYY-MM-DD"
```

If there's nothing to commit (e.g. re-running after the tag already existed), skip the commit.

### 6. Refresh the decision register

`docs/decisions/README.md` is derived from the GitHub `decision` issues, so it goes
stale silently whenever a question is answered in a comment. A snapshot is
the right moment to catch that, because the tag is what someone will clone.

```bash
python3 scripts/build_decision_log.py
```

If it changes the file, commit it before tagging:
```bash
git add docs/decisions/README.md && git commit -m "Refresh the decision register for stable/YYYY-MM-DD"
```

If `gh` is unauthenticated or offline the script exits non-zero - say so in the
output and carry on; a snapshot is not worth blocking on it, but a silently
stale register is exactly what this step exists to prevent.

### 7. Create the tag

Check if there are uncommitted changes first (the CHANGELOG.md commit above should be the only one, but confirm):
```bash
git status --short
```

If *other* uncommitted changes exist (beyond what steps 5 and 6 just committed), warn the user and ask whether to proceed. If they confirm (or there are none), create an annotated tag - now on top of the CHANGELOG.md commit, so the tagged state is self-describing:
```bash
git tag -a stable/YYYY-MM-DD -m "Daily stable snapshot YYYY-MM-DD"
```

Report the tag SHA: `git rev-parse stable/YYYY-MM-DD`

Push the branch (carrying the CHANGELOG.md commit) and the tag to GitHub:
```bash
git push origin HEAD
git push origin stable/YYYY-MM-DD
```

### 8. Create the GitHub Release

Compose the release body from the same notes written in step 4, then run:

```bash
gh release create stable/YYYY-MM-DD \
  --title "Daily Build YYYY-MM-DD" \
  --notes "RELEASE_BODY" \
  --target master
```

If the release already exists (exit code non-zero with "already exists" message), skip creation and note it.

Report the release URL returned by `gh release create`.

### 9. Output format

Print to the user:

```
Snapshot: stable/YYYY-MM-DD  (SHA: <sha>)
Previous: stable/PREV-DATE   (or "first commit")
Release:  <GitHub release URL>
Changelog: CHANGELOG.md updated (or "already up to date")

## Commits since last snapshot (<N> total)
<git log --oneline output>

## Files changed
<git diff --stat output>

## Release notes
<the notes written in step 4>
```
