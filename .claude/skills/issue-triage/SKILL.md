---
name: issue-triage
description: Pick the highest-severity open GitHub issue, investigate the codebase to determine if it is a real issue, and produce a triage verdict. Use when the user wants to evaluate whether an open issue is valid before acting on it.
---

# Issue Triage

You are a senior engineer triaging open GitHub issues for this q/kdb+ eFX quant library. Your job is not to fix issues — it is to determine whether each issue is real, reproducible, already addressed, or invalid. You base your verdict on code evidence, not on the issue author's assertion.

## Commands

```
Commands: ok/yes — confirm and label issue | n/next — triage next issue | skip — skip without labelling | close <reason> — close issue as invalid/duplicate/fixed
```

## Steps

### 1. Output the command reference above immediately.

### 2. Determine which issue to triage

If the user provided an issue number, use that. Otherwise:

```
gh issue list --state open --limit 100 --json number,title,labels,createdAt
```

Select the highest-severity open issue using this label priority order:
1. `critical` / `severity: critical` / `P0`
2. `high` / `severity: high` / `P1`
3. `bugfinder` (auto-created by the bugfinder skill)
4. `medium` / `severity: medium` / `P2`
5. `low` / `severity: low` / `P3`
6. No severity label (fall back to oldest open issue)

If multiple issues share the same severity tier, pick the oldest (lowest issue number).

### 3. Fetch full issue details

```
gh issue view <number> --json number,title,body,labels,comments,author,createdAt,url
```

Read the issue body and all comments carefully. Note:
- The exact symptom or claim being made
- Any file paths, line numbers, or function names mentioned
- Any reproduction steps provided
- Any error output embedded (q errors are typically a bare backtick-prefixed signal, e.g. `` `type``, `` `rank``, or a custom message thrown via `` '"..." ``)

### 4. Investigate the codebase

Use Read and Bash tools to inspect the relevant code. Do not guess — verify. Run the test suite if the claim is testable: `./q tests/run_tests.q` (PeachQ) and, if available, `q tests/run_tests.q` under real KDB-X (`export QHOME=~/.kx PATH="$HOME/.kx/bin:$PATH"`) — see the `kdb-q-conventions` skill for why both interpreters matter here.

For **bug reports**:
- Find the exact file and line the issue refers to (or the closest relevant code) — `src/*.q`, one module per topic
- Trace the execution path: input → computation → output. Remember q has **no operator precedence** (strictly right-to-left) — a claim of "wrong formula" is often exactly this class of bug; re-derive the evaluation order by hand before accepting or rejecting the claim
- Check whether the described wrong behavior can actually occur given the current code
- Check git log for recent changes to the file: `git log --oneline -20 -- <file>`
- If the bug was fixed in a recent commit, note the commit hash and message
- Check whether the claim is actually a PeachQ-vs-real-KDB-X interpreter gap rather than a library bug (see `kdb-q-conventions`'s interpreter-gaps notes) — reproduce on both before concluding it's a real defect

For **feature requests or enhancements**:
- Check if the feature already exists under a different name (this library snake_cases everything: `fwd_simple`, `cross_book_at_sizes`, etc.)
- Check `docs/ROADMAP.md` — the request may already be a scoped, not-yet-implemented candidate there
- Assess whether the request contradicts an existing design decision (e.g. this library is strictly scoped to eFX — see `kdb-q-conventions`'s scope note)

For **numerical/precision issues**:
- Locate the code path in question
- Check for null propagation (`0Nf`/`0n` silently flowing through arithmetic), a wrong `pip_factor`, or a sign-convention mixup (`side` is `1` buy / `-1` sell throughout — a flipped sign is a common, silent defect class here)

### 5. Produce a triage report

Output the report in this exact structure:

---

**ISSUE #<number>: <title>**
URL: <url>
Severity label: <label or "none">
Opened: <date>

**CLAIM**
One paragraph — what the issue author asserts is wrong or missing, in your own words.

**CODE EVIDENCE**
- File: `<path>:<line>`
- Relevant snippet (≤15 lines, verbatim):
  ```q
  <code>
  ```
- What the code actually does vs what the issue claims it does.

**VERDICT**

One of:

| Verdict | Meaning |
|---|---|
| CONFIRMED | The issue is real. The code has the defect described. |
| ALREADY FIXED | The defect was addressed in a recent commit. Cite the hash. |
| NOT REPRODUCIBLE | The code does not contain the described behavior. Explain why. |
| INVALID | The claim is wrong, based on a misunderstanding of the design. Explain. |
| DUPLICATE | Another open issue already covers this. Cite that issue number. |
| NEEDS MORE INFO | Cannot determine without reproduction steps or a specific revision. |

Verdict: **<one of the above>**

**JUSTIFICATION**
2–4 sentences. State the specific evidence from the code that supports the verdict. Quote line numbers.

**RECOMMENDED ACTION**
One of:
- Fix: describe what needs to change and where (do not implement here)
- Close with comment: draft the closing comment text
- Request more info: list the exact questions to ask
- Label and assign: suggest labels and owner

---

### 6. Wait for a command

- `ok` / `yes` — apply the recommended action: label the issue, post a comment, or close it using `gh`
- `n` / `next` — move to the next highest-severity open issue and repeat from step 2
- `skip` — move to the next issue without taking any action
- `close <reason>` — close the current issue with a comment explaining the reason
- Any other text — treat as a custom instruction and act on it

## Applying Actions

**Confirming an issue (ok on CONFIRMED verdict):**
```
/claim <number>  # Claim the issue before starting work
gh issue edit <number> --add-label "confirmed"
gh issue comment <number> --body "Triaged: confirmed. <one sentence summary of evidence>."
```

**Closing an invalid/fixed/duplicate issue:**
```
gh issue close <number> --comment "<closing comment>"
```

**Requesting more info:**
```
gh issue comment <number> --body "<drafted questions>"
gh issue edit <number> --add-label "needs-more-info"
```

Create labels if they do not exist:
```
gh label create confirmed --color "#0075ca" --description "Issue verified as real" 2>/dev/null || true
gh label create needs-more-info --color "#e4e669" --description "Waiting on reporter for more details" 2>/dev/null || true
```

## Important

- Base every verdict on code evidence. Never accept the issue author's assertion at face value — verify it.
- If the relevant file has changed recently, always check git log before declaring ALREADY FIXED.
- Do not implement fixes. This skill triages; fixes belong in `/bugfinder` or a direct edit task.
- If you cannot find the relevant code after a reasonable search, return NEEDS MORE INFO rather than guessing.
- Do not use emojis.
- Use `/claim` before starting work and `/release` when done to coordinate with other sessions.
