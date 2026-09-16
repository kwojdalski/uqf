#!/usr/bin/env python3
"""Derive ``docs/decisions/README.md`` from the GitHub ``decision`` issues.

The reimplementation question bank lives on GitHub: one issue per area
(``Design questions D: Backfill semantics``), the open questions in the issue
body, and the answers in comments. That is a reasonable place to *make*
decisions and a terrible place to *read* them - the answer to D-11 is three
comments deep in issue #72, and a plain clone of this repository contains no
trace of it.

So the decisions are not copied here by hand. They are **derived**, by the two
shapes the maintainer already writes answers in:

1. a comment heading - ``## D-11 answered: append with source_version``
2. a bold line start  - ``**E-05 answered and built** - see #106.``
3. on an older per-question issue whose *title* carries the id
   (``F-19: Decide the frontend audience``), a bare ``**Answered: ...**``

The ids *before* the word "answered" are the subject, so
``## B-02 and B-03 answered by #67`` files two records while
``## E-09 answered by A-04`` files one - A-04 there is the reason, not the
subject, and it has its own record elsewhere. A decision written in any other
shape is invisible to this script by design: the rule is the format, and a
format nothing enforces is a format that drifts.

Three things it reports that a hand-maintained file would not:

- **Answered but still listed as open** - the issue body still carries a
  question its own comments have answered. The body is the stale half.
- **Cited in docs, no record** - prose in ``docs/`` leans on an id that has no
  answer anywhere on GitHub, i.e. a decision someone believes was made.
- **Answered off-repo** - a record whose only detail is a path outside this
  tree (``~/.claude/plans/...``) is flagged, because that content is
  unreachable to anyone else and will be unreachable to you on another machine.

Usage::

    python3 scripts/build_decision_log.py            # rewrite docs/decisions/README.md
    python3 scripts/build_decision_log.py --check    # exit 1 if it is stale

``--check`` needs network and an authenticated ``gh``, so it belongs in CI or
a pre-release step, not in pre-commit.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
#: The combined register. README.md rather than decisions.md so GitHub
#: renders it when anyone browses docs/decisions/.
OUT = REPO_ROOT / "docs" / "decisions" / "README.md"

#: One file per answered decision, alongside it. Generated, never
#: hand-edited: P-01 asked for per-decision ADRs and J-01 had already settled
#: that a document derived from an authority is generated and checked rather
#: than maintained. ~105 hand-written files that can disagree with GitHub,
#: with nothing to notice, is the drift J-01 exists to prevent - so the
#: linkability is provided by emitting them instead.
ADR_DIR = REPO_ROOT / "docs" / "decisions"

#: Issue links are written relative to the file that holds them. Both the
#: register and the ADRs now sit one level deeper than docs/decisions.md did,
#: which is why this is a constant rather than inlined at two call sites.
ISSUE_REL = "../../../issues"

ID = r"[A-Z]-\d{2}"
ID_RE = re.compile(rf"\b({ID})\b")
# A comment heading, or a bold run at the start of a line, that says "answered".
# "subject" is everything up to that word - the ids being answered; "rest" is the
# answer itself.
RECORD_RE = re.compile(
    rf"^(?:##+\s+|\*\*)(?P<subject>[^\n]*?\b{ID}\b[^\n]*?)\banswered\b(?P<rest>[^\n]*)$",
    re.M,
)
AREA_COUNTER_RE = re.compile(r"\s*\([^()]*\bopen\b[^()]*\)\s*$")
# "- **E-05** - Publish a nonempty page..." - how the requirement documents
# *define* an id, as opposed to merely citing one.
DEFINITION_RE = re.compile(rf"^[-*]\s*\*\*({ID})\b", re.M)
# "### D-02 - question text", the open-question entries in an issue body.
QUESTION_RE = re.compile(rf"^###\s+(?P<id>{ID})\s*[-—–]+\s*(?P<text>[^\n]+)$", re.M)
# `Answered` is a state too. Once reconcile_question_bodies.py moves a settled
# question under `## Answered`, that block still matches QUESTION_RE - it is
# still a `### X-NN` heading - so without this the parser reported it under
# whatever section preceded it and the register listed 22 questions as "still
# listed as open" after every one had been moved. A question under
# `## Answered` is neither open nor stale; it is the reconciled state.
STATE_RE = re.compile(r"^##\s+(Blocking|Shaping|Deferrable|Answered)\s*$", re.M)
AREA_RE = re.compile(r"^Design questions\s+([A-Z]):\s*(.+)$")
# The older per-question issues carry the id in the *title* ("F-19: Decide the
# frontend audience") and answer with a bare "**Answered: both audiences.**".
TITLE_ID_RE = re.compile(rf"^({ID})\b")
TITLE_ANSWER_RE = re.compile(r"^(?:##+\s+|\*\*)Answered\b(?P<rest>[^\n]*)$", re.M)
OFF_REPO_RE = re.compile(r"~/\.claude/[^\s`]+")


@dataclass
class Answer:
    ids: list[str]
    headline: str
    issue: int
    date: str
    off_repo: str | None
    # True when the id came from the issue *title* rather than from a comment
    # heading - i.e. one of the older per-question issues, whose ids belong to a
    # requirement document's numbering and not to the question bank's areas.
    from_title: bool = False


@dataclass
class Question:
    id: str
    text: str
    state: str
    issue: int
    area: str


@dataclass
class Bank:
    areas: dict[str, str] = field(default_factory=dict)  # letter -> area name
    questions: list[Question] = field(default_factory=list)
    answers: list[Answer] = field(default_factory=list)


def fetch_issues() -> list[dict]:
    out = subprocess.run(
        [
            "gh",
            "issue",
            "list",
            "--label",
            "decision",
            "--state",
            "all",
            "--limit",
            "200",
            "--json",
            "number,title,state,body,comments",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        sys.exit(f"gh failed: {out.stderr.strip()}")
    return json.loads(out.stdout)


def clean_headline(rest: str) -> str:
    """The answer itself, from the text trailing 'answered' on the record line."""
    stripped = rest.strip()
    # "D-11 answered: <the answer>" carries the answer after the colon; the
    # "answered by #67" form has no colon and only reads correctly with the verb.
    verb = "" if stripped.startswith(":") else "answered "
    text = stripped.lstrip(":-\u2014\u2013 ").strip()
    text = verb + text.replace("**", "").strip()
    # Bold-form records run the answer into the following prose; one sentence is
    # enough for a table cell, and the issue link carries the rest.
    for stop in (". ", " - see", " \u2014 see"):
        if stop in text:
            text = text.split(stop, 1)[0]
    text = text.strip().rstrip(".").strip()
    return text or "answered"


def state_at(body: str, pos: int) -> str:
    """The most recent '## Blocking|Shaping|Deferrable' heading above pos."""
    last = "unstated"
    for m in STATE_RE.finditer(body):
        if m.start() > pos:
            break
        last = m.group(1).lower()
    return last


def parse(issues: list[dict]) -> Bank:
    bank = Bank()
    for issue in issues:
        num, title, body = issue["number"], issue["title"], issue.get("body") or ""
        area_m = AREA_RE.match(title)
        area = area_m.group(2).strip() if area_m else title.strip()
        # Titles carry a live counter - "Strategy & scope (8 open, 5 blocking)" -
        # which is stale the moment a question is answered. Keep the name only.
        area = AREA_COUNTER_RE.sub("", area)
        if area_m:
            bank.areas[area_m.group(1)] = area

        for m in QUESTION_RE.finditer(body):
            bank.questions.append(
                Question(
                    m.group("id"), m.group("text").strip(), state_at(body, m.start()), num, area
                )
            )

        title_id_m = TITLE_ID_RE.match(title)
        for comment in issue.get("comments") or []:
            cbody = comment.get("body") or ""
            found_here = False
            for m in RECORD_RE.finditer(cbody):
                found_here = True
                ids = ID_RE.findall(m.group("subject"))
                if not ids:
                    continue
                headline = clean_headline(m.group("rest"))
                tail = cbody[m.end() : m.end() + 600]
                off = OFF_REPO_RE.search(tail)
                bank.answers.append(
                    Answer(
                        ids,
                        headline,
                        num,
                        (comment.get("createdAt") or "")[:10],
                        off.group(0) if off else None,
                    )
                )

            if found_here or not title_id_m:
                continue
            tm = TITLE_ANSWER_RE.search(cbody)
            if tm:
                off = OFF_REPO_RE.search(cbody)
                bank.answers.append(
                    Answer(
                        [title_id_m.group(1)],
                        clean_headline(tm.group("rest")),
                        num,
                        (comment.get("createdAt") or "")[:10],
                        off.group(0) if off else None,
                        from_title=True,
                    )
                )
    return bank


def scan_docs() -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """(cited, defined): id -> doc paths, for docs other than the register itself.

    The requirement documents run their own ``E-``/``F-`` numbering that has
    nothing to do with the question bank's areas E and F, so a citation only
    counts as unrecorded if no document *defines* the id either.
    """
    cited: dict[str, list[str]] = defaultdict(list)
    defined: dict[str, list[str]] = defaultdict(list)
    for path in sorted((REPO_ROOT / "docs").rglob("*.md")):
        if path == OUT:
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        body = path.read_text(encoding="utf-8", errors="replace")
        for found in sorted(set(ID_RE.findall(body))):
            cited[found].append(rel)
        for found in sorted(set(DEFINITION_RE.findall(body))):
            defined[found].append(rel)
    return cited, defined


def render(bank: Bank) -> str:
    answered: dict[str, Answer] = {}
    for a in bank.answers:
        for i in a.ids:
            answered.setdefault(i, a)
    open_q = [q for q in bank.questions if q.id not in answered and q.state != "answered"]
    # Stale means: answered in a comment, yet the body still shows it under an
    # OPEN section. A block already under `## Answered` is the fixed state,
    # not a stale one.
    stale = [q for q in bank.questions if q.id in answered and q.state != "answered"]
    cited, defined = scan_docs()
    known_prefixes = {i[0] for i in answered} | {q.id[0] for q in bank.questions}
    # Ids the question bank itself owns. A title-form answer (#53's "F-19") is a
    # requirement document's id, so it must not be counted as a bank id - doing
    # so would report every F- requirement as colliding with itself.
    bank_ids = {q.id for q in bank.questions} | {i for i, a in answered.items() if not a.from_title}
    orphans = {
        i: paths
        for i, paths in sorted(cited.items())
        if i not in bank_ids and i not in answered and i not in defined and i[0] in known_prefixes
    }
    collisions = {i: paths for i, paths in sorted(defined.items()) if i in bank_ids}
    off_repo = sorted({(i, a.issue, a.off_repo) for i, a in answered.items() if a.off_repo})

    L: list[str] = []
    add = L.append
    add("# Decision register")
    add("")
    add("**Generated by `scripts/build_decision_log.py` - do not edit by hand.**")
    add("Rerun it, or `--check` it, after answering anything on GitHub.")
    add("")
    add("The reimplementation question bank is answered in GitHub issue comments.")
    add("This file is the derived, in-repo, `git`-diffable view of those answers, so")
    add("a plain clone can see what was decided without network access - the same")
    add("argument `snapshot` makes for keeping `CHANGELOG.md` alongside the GitHub")
    add("release notes.")
    add("")
    add("Decisions deliberately do **not** live in `docs/drift-reports/drift-ledger.md`.")
    add("That ledger tracks divergence from canonical, its rows are numbered `D1..D14`,")
    add("and the question bank's backfill area is numbered `D-01..D-12` - one file")
    add("holding both would put `D8` and `D-08` in adjacent tables meaning unrelated")
    add("things.")
    add("")
    add(f"**{len(answered)} answered, {len(open_q)} still open** across {len(bank.areas)} areas.")
    add("")

    add("## Answered")
    add("")
    add("| ID | Area | Answer | Issue | Date |")
    add("|---|---|---|---|---|")
    area_of = {q.id: q.area for q in bank.questions}
    doc_area = {i: f"`{paths[0]}`" for i, paths in defined.items()}
    for i, a in sorted(answered.items()):
        if a.from_title:
            # Not a bank question: name the document whose numbering it belongs to.
            area = doc_area.get(i, "-")
        else:
            area = area_of.get(i, bank.areas.get(i[0], "-"))
        add(f"| `{i}` | {area} | {a.headline} | [#{a.issue}]({ISSUE_REL}/{a.issue}) | {a.date} |")
    add("")

    if off_repo:
        add("### Answered off-repo")
        add("")
        add("The GitHub record points at a path outside this tree, unreachable from a")
        add("clone and from any other machine. Where a repository document has since")
        add("recorded the substance, that is named too.")
        add("")
        for i, issue, path in off_repo:
            here = defined.get(i)
            add(
                f"- `{i}` (#{issue}) - `{path}`"
                + (
                    f"; recorded in {', '.join(f'`{p}`' for p in here)}"
                    if here
                    else "; not recorded in this repository"
                )
            )
        add("")

    if stale:
        add("### Answered but still listed as open")
        add("")
        add("The issue body still carries these as open questions while its own comments")
        add("answer them. The comment is the newer half; the body needs editing.")
        add("")
        for q in sorted(stale, key=lambda q: q.id):
            add(
                f"- `{q.id}` - answered in #{answered[q.id].issue}, still under "
                f"`{q.state}` in that issue's body"
            )
        add("")

    add("## Open")
    add("")
    add("| ID | Area | State | Question | Issue |")
    add("|---|---|---|---|---|")
    order = {"blocking": 0, "shaping": 1, "deferrable": 2, "unstated": 3}
    for q in sorted(open_q, key=lambda q: (order.get(q.state, 9), q.id)):
        add(
            f"| `{q.id}` | {q.area} | `{q.state}` | {q.text} | "
            f"[#{q.issue}]({ISSUE_REL}/{q.issue}) |"
        )
    add("")

    if orphans:
        add("## Cited in docs, no record on GitHub")
        add("")
        add("Prose that leans on a decision id with no answer and no open question")
        add("behind it - either the decision was made somewhere unrecorded, or the")
        add("citation is wrong.")
        add("")
        for i, paths in orphans.items():
            add(f"- `{i}` - cited in {', '.join(f'`{p}`' for p in paths)}")
        add("")

    if collisions:
        add("## Ids that mean two different things")
        add("")
        add("These ids are both a question-bank id (above) and a requirement defined")
        add("in a requirement document. The requirement documents number their own")
        add("`E-`/`F-` requirements independently of the bank's areas E and F, so a")
        add("bare id in prose is ambiguous - say which namespace you mean.")
        add("")
        for i, paths in collisions.items():
            add(f"- `{i}` - defined as a requirement in {', '.join(f'`{p}`' for p in paths)}")
        add("")

    add("## How a decision gets in here")
    add("")
    add("Answer on the area issue, in one of the two shapes this script reads:")
    add("")
    add("```markdown")
    add("## D-11 answered: append with `source_version`")
    add("**E-05 answered and built** - see #106.")
    add("```")
    add("")
    add("On an older per-question issue whose *title* carries the id, a bare")
    add("`**Answered: ...**` is enough - the id comes from the title.")
    add("")
    add("Every `[A-Z]-NN` id on that line is filed, so `## B-02 and B-03 answered by")
    add("#67` records both. Then rerun this script. An answer written in any other")
    add("shape will not appear - which is the point: the format is the rule.")
    add("")
    return "\n".join(L)


def render_adr(bank: Bank, answer: Answer, decision_id: str) -> str:
    """One answered decision as its own page.

    Deliberately thin. The headline IS the decision - the reasoning lives in
    the issue comment, which is the authority, and copying it here would give
    two texts to keep in step. What this file adds is a stable path to link
    to, which is the only thing P-01 actually wanted that the combined
    register could not provide.
    """
    area = bank.areas.get(decision_id[0], "-")
    lines = [
        f"# {decision_id} - {answer.headline}",
        "",
        "<!-- GENERATED BY scripts/build_decision_log.py - DO NOT EDIT. -->",
        "<!-- The answer lives in the GitHub issue comment; this is derived. -->",
        "",
        f"**Area:** {area}",
        "",
        f"**Decided:** {answer.date}",
        "",
        f"**Answered on:** [#{answer.issue}]({ISSUE_REL}/{answer.issue})",
    ]
    if answer.off_repo:
        lines += ["", f"**Recorded off-repo:** {answer.off_repo}"]
    lines += [
        "",
        "## Decision",
        "",
        answer.headline,
        "",
        "---",
        "",
        "The reasoning is in the issue comment linked above, which is the",
        "authority; this page is generated from it. Every decision in one",
        "table: [the register](README.md).",
    ]
    return "\n".join(lines).rstrip("\n") + "\n"


def render_adrs(bank: Bank) -> dict[Path, str]:
    """path -> content for every answered decision.

    Keyed by path so main() can diff the WHOLE SET rather than each file:
    a decision whose id changed, or an answer withdrawn, leaves a file behind
    that no longer corresponds to anything. Detecting those is the half a
    per-file check would miss, and the half that makes "never hand-edited"
    true rather than aspirational.
    """
    out: dict[Path, str] = {}
    for answer in bank.answers:
        for decision_id in answer.ids:
            out[ADR_DIR / f"{decision_id}.md"] = render_adr(bank, answer, decision_id)
    return out


def existing_adrs() -> set[Path]:
    """The per-decision files on disk. README.md is the register, not an ADR."""
    if not ADR_DIR.is_dir():
        return set()
    return {p for p in ADR_DIR.glob("*.md") if p.name != "README.md"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if docs/decisions/README.md differs from what GitHub says",
    )
    args = ap.parse_args()

    bank = parse(fetch_issues())
    text = render(bank)
    adrs = render_adrs(bank)

    if args.check:
        problems: list[str] = []
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != text:
            problems.append(f"{OUT.relative_to(REPO_ROOT)} is stale")
        for path, content in sorted(adrs.items()):
            rel = path.relative_to(REPO_ROOT)
            if not path.exists():
                problems.append(f"{rel} is missing")
            elif path.read_text(encoding="utf-8") != content:
                problems.append(f"{rel} is stale")
        # A file for a decision that no longer exists: an id that was
        # renamed, or an answer withdrawn. Nothing else would notice.
        for path in sorted(existing_adrs() - set(adrs)):
            problems.append(f"{path.relative_to(REPO_ROOT)} corresponds to no answer")
        if problems:
            for problem in problems:
                print(problem, file=sys.stderr)
            print("\nrerun scripts/build_decision_log.py", file=sys.stderr)
            return 1
        print(f"{OUT.relative_to(REPO_ROOT)} and {len(adrs)} decision page(s) match GitHub")
        return 0

    ADR_DIR.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    written = 0
    for path, content in sorted(adrs.items()):
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            path.write_text(content, encoding="utf-8")
            written += 1
    removed = 0
    for path in sorted(existing_adrs() - set(adrs)):
        path.unlink()
        removed += 1
    print(
        f"wrote {OUT.relative_to(REPO_ROOT)}, {len(adrs)} decision page(s) "
        f"({written} changed, {removed} removed)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
