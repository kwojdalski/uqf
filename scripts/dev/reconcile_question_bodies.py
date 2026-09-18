#!/usr/bin/env python3
"""Move answered questions out of `## Blocking` in the question-bank issues.

The bank answers questions in comments, but a comment does not edit the body
it sits under. So twenty-two questions were answered while their issues still
listed them under `## Blocking` and still counted them in the title - #72
read "9 open, 4 blocking" with all four blocking questions answered. That is
not cosmetic: the body is what anyone reads to decide what is still blocked,
so it overstated the blockage by exactly the amount of progress made (#108).

This reuses `build_decision_log.py`'s parser rather than re-deriving what
counts as answered, so the two tools cannot disagree. For each issue it:

  1. moves every answered `### X-NN` block to a `## Answered` section, with
     a link to the answering comment - the answer is not deleted from the
     body, because the body should stay readable on its own
  2. recomputes the `**N open** - a blocking, b shaping, c deferrable` line
  3. rewrites the `(N open, M blocking)` counter in the title

Idempotent: a second run finds nothing to move. `--dry-run` prints the
diffs and edits nothing, and is the default; pass `--apply` to write.
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

# parents[2], not parent.parent: this file sits one level deeper since
# scripts/ was foldered (#241). Getting it wrong does not raise - it
# resolves to scripts/ and the checker reports over an empty tree.
REPO_ROOT = Path(__file__).resolve().parents[2]

# Loaded by path so this script stays a plain system hook with no package.
# Registered in sys.modules before exec, because @dataclass resolves its
# annotations through sys.modules[cls.__module__].
_spec = importlib.util.spec_from_file_location(
    "build_decision_log", REPO_ROOT / "scripts" / "generate" / "build_decision_log.py"
)
assert _spec and _spec.loader
bdl = importlib.util.module_from_spec(_spec)
sys.modules["build_decision_log"] = bdl
_spec.loader.exec_module(bdl)

#: One `### X-NN - text` block, through to the next heading or end of body.
BLOCK_RE = re.compile(r"^### (?P<id>[A-Z]-\d{2}) [—-] .*?(?=^## |^### |\Z)", re.M | re.S)
COUNTER_LINE_RE = re.compile(r"^\*\*\d+ open\*\* [—-] .*$", re.M)
TITLE_COUNTER_RE = re.compile(r" \(\d+ open, \d+ blocking\)$")
SECTION_RE = re.compile(r"^## (?P<name>Blocking|Shaping|Deferrable|Answered)\s*$", re.M)


def answered_ids_by_issue(bank: bdl.Bank) -> dict[int, dict[str, bdl.Answer]]:
    """issue number -> {question id -> the answer that settled it}, for
    questions the body still lists under an OPEN section."""
    out: dict[int, dict[str, bdl.Answer]] = {}
    open_in_body = {(q.issue, q.id) for q in bank.questions if q.state != "answered"}
    for a in bank.answers:
        for qid in a.ids:
            if (a.issue, qid) in open_in_body:
                out.setdefault(a.issue, {})[qid] = a
    return out


def comment_url(issue: int, answer: bdl.Answer, comments: list[dict]) -> str:
    """The permalink of the comment carrying this answer, or the issue itself.

    Matched on date and on the answer's own id appearing in the comment, since
    Answer does not carry the comment id. Falls back to the issue URL rather
    than guessing a wrong comment, because a link to the wrong comment is
    worse than a link to the thread.
    """
    for c in comments:
        if (c.get("createdAt") or "")[:10] != answer.date:
            continue
        if any(qid in (c.get("body") or "") for qid in answer.ids):
            url = c.get("url")
            if url:
                return url
    return f"https://github.com/kwojdalski/uqf/issues/{issue}"


def reconcile(body: str, answered: dict[str, bdl.Answer], comments: list[dict], issue: int) -> str:
    moved: list[tuple[str, str]] = []

    def take(m: re.Match) -> str:
        qid = m.group("id")
        if qid not in answered:
            return m.group(0)
        # Already reconciled on a previous run: leave it where it is. Without
        # this the second run re-moved every answered block to a second
        # `## Answered` section beneath the first, which is why the first
        # draft was not idempotent.
        if bdl.state_at(body, m.start()) == "answered":
            return m.group(0)
        a = answered[qid]
        block = m.group(0).rstrip() + "\n\n"
        link = comment_url(issue, a, comments)
        note = f"> **Answered {a.date}** — {a.headline} ([comment]({link}))\n\n"
        moved.append((qid, block + note))
        return ""

    new_body = BLOCK_RE.sub(take, body)
    if not moved:
        return body

    # Drop any section heading left with nothing under it.
    new_body = re.sub(
        r"^## (Blocking|Shaping|Deferrable)\s*\n(?=\s*(?:## |\Z))", "", new_body, flags=re.M
    )

    answered_section = "## Answered\n\n" + "".join(text for _, text in moved)
    # Insert before the trailing "---" footer if there is one, else append.
    if "\n---\n" in new_body:
        head, _, tail = new_body.rpartition("\n---\n")
        new_body = head.rstrip() + "\n\n" + answered_section + "---\n" + tail
    else:
        new_body = new_body.rstrip() + "\n\n" + answered_section

    # Recount from what remains, never from the old numbers.
    counts = open_states(new_body)
    total = sum(counts.values())
    parts = [f"{n} {k.lower()}" for k, n in counts.items() if n]
    counter = f"**{total} open** — " + (", ".join(parts) if parts else "none") + "."
    new_body = COUNTER_LINE_RE.sub(counter, new_body, count=1)
    return new_body


def open_states(body: str) -> dict[str, int]:
    """Count the still-open questions by state, EXCLUDING `## Answered`.

    `build_decision_log.state_at` reports the most recent section heading
    above a block, and it knows nothing about `## Answered` - so once the
    answered blocks sit at the bottom of the body, every one of them read as
    whatever section preceded them. The first run of this script counted
    #72 as "9 open, 6 deferrable" because four answered questions inherited
    `## Deferrable` from above. A question under `## Answered` is not open,
    and it is not in any state the counter should see.
    """
    counts = {"Blocking": 0, "Shaping": 0, "Deferrable": 0}
    for m in BLOCK_RE.finditer(body):
        section = None
        for sm in SECTION_RE.finditer(body[: m.start()]):
            section = sm.group("name")
        if section in counts:
            counts[section] += 1
    return counts


def new_title(title: str, body: str) -> str:
    counts = open_states(body)
    total = sum(counts.values())
    return TITLE_COUNTER_RE.sub(f" ({total} open, {counts['Blocking']} blocking)", title)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply", action="store_true", help="write the edits (default is dry-run)")
    args = ap.parse_args()

    issues = bdl.fetch_issues()
    bank = bdl.parse(issues)
    per_issue = answered_ids_by_issue(bank)
    if not per_issue:
        print("nothing to reconcile: no answered question is still listed as open")
        return 0

    by_num = {i["number"]: i for i in issues}
    for num in sorted(per_issue):
        issue = by_num[num]
        body, title = issue.get("body") or "", issue["title"]
        answered = per_issue[num]
        updated = reconcile(body, answered, issue.get("comments") or [], num)
        if updated == body:
            continue
        title2 = new_title(title, updated)
        print(f"#{num}: move {sorted(answered)} -> Answered; title '{title}' -> '{title2}'")
        if args.apply:
            subprocess.run(
                ["gh", "issue", "edit", str(num), "--title", title2, "--body-file", "-"],
                input=updated,
                text=True,
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
            )
            print("  applied")
    if not args.apply:
        print("\ndry run - pass --apply to write these")
    return 0


if __name__ == "__main__":
    sys.exit(main())
