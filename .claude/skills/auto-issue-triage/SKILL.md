---
name: auto-issue-triage
description: Automatically triage and work on highest-severity GitHub issues
---

# Auto Issue Triage Skill

Automatically identifies, prioritizes, and begins working on the most severe GitHub issues in this q/kdb+ eFX quant library.

## Commands

```
/auto-issue-triage                  — Auto-triage and work on highest-severity issues
/auto-issue-triage --dry-run         — Preview actions without making changes
/auto-issue-triage --max-issues <n>     — Process at most N issues (default: 5)
/auto-issue-triage --severity <level>    — Only process issues at or above severity
/auto-issue-triage --skip-labels         — Skip auto-labeling (use existing labels only)
/auto-issue-triage --confirm              — Require manual confirmation before closing issues
```

## Workflow

### 1. Issue Discovery and Prioritization
```bash
gh issue list --state open --limit 100
```

**Priority Logic:**
1. `critical` → P0 (highest priority)
2. `high` → P1
3. `bugfinder` → P2 (auto-created triage)
4. `medium` → P3
5. `low` → P4
6. No severity label → P5 (lowest)

**Tie-breaking:**
- If multiple issues share same severity, pick newest issue (lowest issue number)
- Ignore issues >30 days old unless they're critical

### 2. Issue Investigation
```bash
gh issue view <number> --json number,title,body,labels,comments,author,createdAt,url
```

**Investigation Checklist:**
- Read issue body and all comments carefully
- Identify exact file paths, line numbers, or function names mentioned (`src/*.q`, snake_case function names)
- Any reproduction steps provided
- Any error output embedded

### 3. Codebase Verification

**Real Issues:** Confirmed bugs with code evidence

**For Bug Reports:**
- Find exact file and line the issue refers to
- Trace execution path: input → computation → output. q evaluates strictly right-to-left with no operator precedence — a "wrong formula" claim often traces back to this, not a typo
- Check whether described wrong behavior can actually occur given current code
- Check git log for recent changes: `git log --oneline -20 -- <file>`
- Run `./q tests/run_tests.q` (PeachQ) and, if available, real KDB-X to confirm/refute reproducibility on both interpreters before concluding it's a genuine library bug rather than an interpreter gap

**Verification Points:**
- Bug claims must match actual code logic
- Wrong line numbers indicate misunderstandings
- Feature requests require checking existing implementations and `docs/ROADMAP.md`

### 4. Triage Decision

**Real Issues:** Confirmed bugs or valid feature requests
**Feature Requests:** Already implemented or design conflicts
**Documentation Issues:** Missing qDoc comments or unclear behavior

**Verdict Categories:**
- `CONFIRMED` — Real bug, code evidence supports claim, reproducible
- `ALREADY FIXED` — Bug fixed in recent commit
- `NOT REPRODUCIBLE` — Code doesn't contain described behavior
- `INVALID` — Wrong claim, misunderstanding of design
- `DUPLICATE` — Issue already exists
- `NEEDS MORE INFO` — Cannot determine without reproduction steps

### 5. Issue Claiming

Before working on any issue, claim it using the `/claim` skill to coordinate with other Claude Code sessions:

```bash
/claim <issue_number>
```

If the issue is already claimed by another session, skip it and try the next one.

### 6. Work Assignment

**Automatic Assignment:**
- Pick top N issues (default: 5)
- For each issue:
  1. Check if already claimed with `/claim`
  2. If available, claim it and add to work queue
  3. If unavailable, skip and try next
- Create dedicated branch per issue if needed
- Link related issues (dependencies, blockers)

### 7. Automatic Updates

**Progress Tracking:** Update issue labels and status
- `CONFIRMED` → Add label: `triaged-confirmed`
- `IN_PROGRESS` → Use `/claim <number>` to claim the issue
- `FIXED` → Use `/release <number> --status resolved`
- `BLOCKED` → Use `/release <number> --status blocked`
- `INVALID` → Use `/release <number> --status invalid`

## Workflow Output

For each issue, the skill generates:

```
## Issue #<number>: <title>

**Status:** CONFIRMED

**Evidence:** [ ] Function exists and behaves as described

**Analysis:** This appears to be a real bug.

**Next Steps:**
1. /claim <number>  # Claim the issue before starting work
2. git checkout -b fix/issue-<number>
3. Implement fix and run `./q tests/run_tests.q` (both interpreters if available)
4. git push origin fix/issue-<number>
5. /release <number> --status resolved
```

## Integration

This skill uses `/claim` before starting work and `/release` when done. This prevents multiple Claude Code sessions from working on the same issue simultaneously.

## Important

- Always verify bug claims against actual code behavior
- Provide file paths and line numbers when claiming bugs
- Never accept issue author's assertions without evidence
- Test all fixes with the existing qUnit suite (`tests/run_tests.q`) before closing issues, on both PeachQ and real KDB-X where feasible
- Respect severity hierarchy when prioritizing
- Use labels like `bugfinder`, `triaged-confirmed`, `in-progress` for tracking progress
- Do not use emojis
