#!/usr/bin/env python3
"""Check that the q functions our prose cites actually exist, and are callable
with the argument count shown.

There were already gates keeping the GENERATED documentation honest -
``generate_man_registry.py --check`` holds ``docs/man.q`` to the qDoc blocks,
``check_env_reference.py`` holds the environment page to the code that reads
each variable. Nothing held the *prose* to anything. A guide could name
``.qcov.stage_coverage``, a function that has never existed, and every gate
would pass: the name appears in no generated artifact, so no generator
disagrees with it.

That is the failure this closes. A reader who types a documented call and
gets ``'stage_coverage`` learns that the documentation is untrustworthy, and
from then on reads all of it with suspicion - which costs far more than the
one wrong line.

Two checks, because they fail differently:

* **Unknown name** - the namespace exists and the name in it does not. Almost
  always a rename the prose did not follow, which is exactly what rots
  quietly.
* **Too many arguments** - the call shown passes more values than the
  function takes, so it would throw ``'rank`` if anyone ran it. Passing
  FEWER is not flagged: a partially applied q function is a projection, and a
  legitimate thing to write down.

WHAT IS DELIBERATELY NOT CHECKED

*Namespaces this tree does not own.* The pattern only matches a lowercase
``.q``-prefixed namespace, which is this repository's own convention (N-01).
TorQ's ``.servers``/``.hb``/``.u``/``.proc``/``.lg``, kdb's ``.Q``/``.z``/
``.j``, and Python references are all out of scope, because their surface is
not ours to verify and a gate that guesses about someone else's API is a
gate people learn to ignore.

*Namespaces absent from the contract surface.* ``.qpipe`` lives in
``scripts/`` rather than ``src/`` and the exporter does not carry it, so its
names cannot be confirmed OR denied here. Those are reported as skipped
rather than silently passed, so the blind spot stays visible - and so a
typo'd namespace shows up as an odd entry in that list.

*Documents about things that do not exist yet or no longer do.* Plans,
proposals, audit output and the generated decision pages are excluded by
directory below, each for a stated reason. A roadmap naming an unwritten
function is doing its job.

SOURCE OF TRUTH

``docs/migrations/surfaces/uqf-local/functions.csv``, which is itself held
current by ``contract_surface.py check``. Reading it rather than launching q
keeps this gate fast enough for a pre-commit hook; the cost is that a stale
surface would make this check wrong, which is why that gate runs too.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SURFACE = REPO / "docs" / "migrations" / "surfaces" / "uqf-local" / "functions.csv"

#: Where living documentation lives. Everything under these roots is checked.
DOC_ROOTS = (
    REPO / "docs",
    REPO / ".claude" / "agents",
    REPO / ".claude" / "skills",
)

#: Directories whose contents are deliberately not held to the current code,
#: each for a different reason. Excluding a directory is a claim that its
#: documents are not describing the system as it is now.
EXCLUDED_DIRS = {
    # Generated from the GitHub issues that are the authority; a decision page
    # records what was decided, including about code since changed.
    "decisions",
    # Agent output and agent input, dated and append-only. A record of a run,
    # not a description of the system.
    "audits",
    "prompts",
    # Provenance, frozen under A-03.
    "drift-reports",
    # Plans written BEFORE the work, kept afterwards as the reasoning. They
    # name things that did not exist when written, which is the point.
    "migrations",
}

#: Individual files excluded, with the reason each is exempt.
EXCLUDED_FILES = {
    # A plan. It describes what does not exist yet, so naming an unwritten
    # function is the document working correctly.
    "ROADMAP.md": "a roadmap names functions that are not written yet",
}

#: References that are deliberately not real, with why. A reference only
#: belongs here if the document is BETTER for naming something non-existent -
#: an illustration of what not to write, or a name being discussed rather
#: than used. Anything else should be fixed in the prose instead.
ALLOWED_MISSING = {
    (
        ".claude/agents/pipeline-developer.md",
        ".qcov.sub",
    ): "an illustration of the nested namespace N-01 forbids - it must not exist",
    (
        ".claude/agents/uqf-developer.md",
        ".qfwd.sub",
    ): "the same nested-namespace illustration, in the rule that forbids it",
    (
        ".claude/skills/antipattern/SKILL.md",
        ".qfwd.sub",
    ): "the same nested-namespace illustration",
    (
        ".claude/agents/extension-brainstormer.md",
        ".qfwd.sub",
    ): "the same nested-namespace illustration",
    (
        ".claude/agents/extension-brainstormer.md",
        ".qmicro.example_fn",
    ): "a placeholder in the template an idea must fill in, not a real name",
    # These two are the examples of what this gate catches, quoted in the
    # philosophy note. Both MUST stay non-existent: the sentence is about the
    # fact that they do not exist, so fixing them would delete the point.
    (
        "docs/architecture/pipeline-philosophy.md",
        ".qmicro.require_sorted_tape",
    ): "quoted as a reference this gate caught - the real name is require_tape",
    (
        "docs/architecture/pipeline-philosophy.md",
        ".qcoer.coerce",
    ): "quoted as a reference this gate caught - the real name is coerce_column",
}

#: A reference to this tree's own namespaces: a lowercase `.q` prefix, then a
#: name. The namespace part may itself be dotted, because worker instances
#: nest (`.qwrk.demo_deals_backfill.run`); without that, the middle segment
#: was read as the FUNCTION and every worker reference in every document
#: landed in the unverifiable bucket rather than being checked.
#: The NAMESPACE must be lowercase - `.Q.` is kdb's own and not ours to
#: verify - but the NAME may carry uppercase, because `src/integrations/data.q`
#: is deliberately left in its original camelCase (`.qdata.databentoDir`) and
#: a lowercase-only name pattern silently truncated it to `.qdata.databento`,
#: reporting a function that does exist as missing.
_REF = re.compile(r"\.q([a-z][a-z0-9]*(?:\.[a-z][a-z0-9_]*)*)\.([a-zA-Z_][a-zA-Z0-9_]*)")

#: The same, followed by a bracketed argument list, for the arity check.
_CALL = re.compile(
    r"\.q([a-z][a-z0-9]*(?:\.[a-z][a-z0-9_]*)*)\.([a-zA-Z_][a-zA-Z0-9_]*)\[([^\[\]]*)\]"
)


def load_surface() -> dict[str, dict[str, dict]]:
    """{namespace: {name: entry}} from the committed contract surface.

    `rank` is blank for a non-function, which is how the CSV distinguishes a
    value from a niladic function - see contract_surface.write_surface. A
    blank here becomes None, and the arity check skips it rather than
    treating a value as a zero-argument function.
    """
    if not SURFACE.is_file():
        sys.exit(f"contract surface not found at {SURFACE} - run contract_surface.py export")
    out: dict[str, dict[str, dict]] = {}
    with SURFACE.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            out.setdefault(row["namespace"], {})[row["name"]] = {
                "name": row["name"],
                "kind": row["kind"],
                "rank": int(row["rank"]) if row["rank"] else None,
            }
    return out


def doc_files() -> list[Path]:
    """Every living documentation file, in a stable order."""
    out: list[Path] = []
    for root in DOC_ROOTS:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.md")):
            rel = path.relative_to(REPO)
            if EXCLUDED_DIRS & set(rel.parts):
                continue
            if path.name in EXCLUDED_FILES:
                continue
            out.append(path)
    return out


def _arg_count(inside: str) -> int:
    """How many arguments a bracketed q argument list passes.

    `[]` is a niladic call, not one empty argument - so an empty or
    whitespace-only body counts as zero rather than one.
    """
    body = inside.strip()
    if not body:
        return 0
    return len(body.split(";"))


def check() -> tuple[list[str], dict[str, int]]:
    """Returns (problems, {unverifiable namespace: how many references}).

    The count matters as much as the names: a blind spot of two references
    is a footnote, and one of two hundred would mean this gate checks far
    less than its passing line implies.
    """
    surface = load_surface()
    problems: list[str] = []
    unverifiable: dict[str, int] = {}

    for path in doc_files():
        rel = str(path.relative_to(REPO))
        text = path.read_text()

        for line_no, line in enumerate(text.splitlines(), 1):
            for match in _REF.finditer(line):
                ns, name = match.group(1), match.group(2)
                ref = f".q{ns}.{name}"
                if (rel, ref) in ALLOWED_MISSING:
                    continue
                if f"q{ns}" not in surface:
                    unverifiable[f".q{ns}"] = unverifiable.get(f".q{ns}", 0) + 1
                    continue
                if name not in surface[f"q{ns}"]:
                    problems.append(
                        f"{rel}:{line_no}: {ref} does not exist - namespace .q{ns} has no {name}"
                    )

            for match in _CALL.finditer(line):
                ns, name, inside = match.group(1), match.group(2), match.group(3)
                ref = f".q{ns}.{name}"
                if (rel, ref) in ALLOWED_MISSING or f"q{ns}" not in surface:
                    continue
                entry = surface[f"q{ns}"].get(name)
                if entry is None or entry.get("kind") != "function":
                    continue
                rank = entry.get("rank")
                if rank is None:
                    continue
                passed = _arg_count(inside)
                if passed > rank:
                    problems.append(
                        f"{rel}:{line_no}: {ref}[...] is shown with {passed} argument(s) "
                        f"but takes {rank} - this call would throw 'rank"
                    )

    return problems, unverifiable


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="accepted for symmetry with the other gates; this script only ever checks",
    )
    parser.parse_args()

    problems, unverifiable = check()
    files = doc_files()

    if problems:
        print(f"check_doc_references: {len(problems)} stale reference(s)\n")
        for problem in problems:
            print(f"  {problem}")
        print(
            "\nFix the prose, or - if the document is better for naming something "
            "that does not exist - add it to ALLOWED_MISSING with the reason."
        )
        return 1

    print(f"check_doc_references: {len(files)} doc(s) cite only functions that exist")
    if unverifiable:
        total = sum(unverifiable.values())
        print(
            f"  {total} reference(s) across {len(unverifiable)} namespace(s) could not be "
            "verified - not carried by the contract surface:"
        )
        for ns, count in sorted(unverifiable.items()):
            print(f"    {ns:14} {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
