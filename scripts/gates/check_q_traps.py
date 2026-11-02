#!/usr/bin/env python3
"""The q traps this repository checks for that `qlinter` does not.

Most of them it does. This file used to hold fifteen hand-written rules over
`.q` source; thirteen of those moved to the standalone q linter
(https://github.com/kwojdalski/q-lint) as QE/QF/QA/QB/QP codes, and keeping a
second implementation of a rule is keeping a second place for it to be wrong.
So they are delegated: this script runs `qlinter` and reports what it finds -
now every correctness rule it has, not only the thirteen (`_gated`), and only
from the version pinned in `.qlinter-version` (`_check_version`).

## What did NOT move, and why

One rule has no counterpart in the linter, and dropping it to tidy the
delegation would be losing a check to gain a diagram:

  rule_reserved_name_in_embedded_q  reads PYTHON files. The linter discovers
                                  `.q` files only - `qlinter some.py` returns
                                  nothing - and q does not only live in `.q`
                                  files here: the orchestrator and the gateway
                                  build q expressions in Python and send them
                                  over IPC. A table literal using `cols` as a
                                  column name threw 'assign against a live
                                  process, which is the bug this rule exists
                                  for and which no `.q` rule could have seen.

It is a candidate for upstreaming into the linter - which needs it to scan
`.py` too - at which point this file becomes a two-line wrapper. Until then
it is the smaller half of a split, not a duplicate.

Run directly, or via the pre-commit hook. Exits 1 on any finding.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# parents[2], not parent.parent: this file sits one level deeper since
# scripts/ was foldered (#241). Getting it wrong does not raise - it
# resolves to scripts/ and the checker reports over an empty tree.
REPO = Path(__file__).resolve().parents[2]

#: Vendored trees are not ours to fix, and `build/` is generated.
EXCLUDED_PREFIXES = ("lib/", "build/")

#: Where to find the linter. `$QLINTER` first so a checkout can point at a
#: build that is not on PATH, which is the same escape hatch pytest
#: gives for the q interpreter.
QLINTER_ENV = "QLINTER"

#: The one place the qlinter version is written (#969). CI and install.sh read
#: the file; README.md and CLAUDE.md quote it, and a test holds them equal.
VERSION_FILE = REPO / ".qlinter-version"


def _pinned_version() -> str:
    """The pinned release tag, e.g. `v0.14.14`."""
    return VERSION_FILE.read_text(encoding="utf-8").strip()


INSTALL_HINT = (
    "cargo install --git https://github.com/kwojdalski/q-lint "
    f"--tag {_pinned_version()} --locked\n"
    f"    or set ${QLINTER_ENV} to a built binary"
)

#: q's own words - `.Q.res` and every name in the `.q` context - which the one
#: rule that reads this, reserved-name-in-embedded-q, refuses as a column name
#: in a q table literal built in Python: a table literal's column names are
#: assignments, so EVERY one of these throws `'assign`, and none is noise.
#: It began as the names someone would plausibly reach for, recalled rather
#: than read, and so held two that are not q words (`fill`, `find`) and missed
#: eighty-three that are (#1795). The list is now all of them: KDB-X's, plus
#: PeachQ's one word KDB-X lacks (`rlike`), since the PeachQ lane loads the
#: same tree. scripts/ cannot import uqs, so it stays a copy;
#: python/uqs/tests/test_qnames.py holds it equal to uqs.model.qnames.Q_WORDS
#: and to a running q, in both directions.
RISKY_PARAM_NAMES = frozenset(
    """abs acos aj aj0 ajf ajf0 all and any asc asin asof atan attr avg avgs bin binr by
    ceiling cols cor cos count cov cross csv cut delete deltas desc dev differ distinct div
    do dsave each ej ema enlist eval except exec exit exp fby fills first fkeys flip floor
    from get getenv group gtime hclose hcount hdel hopen hsym iasc idesc if ij ijf in insert
    inter inv key keys last like lj ljf load log lower lsq ltime ltrim mavg max maxs mcount
    md5 mdev med meta min mins mmax mmin mmu mod msum neg next not null or over parse peach
    pj prd prds prev prior rand rank ratios raze read0 read1 reciprocal reval reverse rlike
    rload rotate rsave rtrim save scan scov sdev select set setenv show signum sin sqrt ss
    ssr string sublist sum sums sv svar system tables tan til trim type uj ujf ungroup union
    update upper upsert use value var view views vs wavg where while within wj wj1 wsum ww
    xasc xbar xcol xcols xdesc xexp xgroup xkey xlog xprev xrank""".split()
)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    detail: str
    why: str


def _tracked_q_files() -> list[Path]:
    """Every `.q` file git knows about, TRACKED OR NOT YET ADDED.

    `git ls-files` alone was the original implementation, and it skipped
    exactly the files that need checking most. A brand-new module is where a
    reserved-name collision or a `/`-only line is most likely, and it is
    unstaged for the whole time it is being written - so the author gets no
    signal until after `git add`, by which point they have already debugged
    the symptom by hand. `heartbeat.q` was written, hit `'prior` at load
    time, and reported clean twice before this was noticed.

    `--others --exclude-standard` adds untracked files while still honouring
    .gitignore, so build output and vendored trees stay out.
    """
    tracked = subprocess.run(
        ["git", "ls-files", "*.q"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.split()
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "*.q"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    paths = dict.fromkeys(tracked + untracked)
    return [REPO / p for p in paths if not p.startswith(EXCLUDED_PREFIXES)]


def _python_files() -> list[Path]:
    """Every `.py` file git knows about, tracked or not yet added.

    Same reasoning as `_tracked_q_files`: a brand-new module is where a
    collision is most likely and it is unstaged the whole time it is being
    written.
    """
    tracked = subprocess.run(
        ["git", "ls-files", "*.py"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.split()
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "*.py"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    paths = dict.fromkeys(tracked + untracked)
    return [REPO / q for q in paths if not q.startswith(EXCLUDED_PREFIXES)]


#: A q table literal opens with `([` - `([]` unkeyed, `([k:...]` keyed.
#: Deliberately the only thing treated as embedded q: it is unambiguous, it is
#: where a column name is written, and it is exactly where the collision this
#: rule exists for occurred. A broader heuristic - "contains a colon", "is
#: passed to query()" - would start firing on English prose, and a
#: documentation gate that cries wolf gets switched off rather than fixed.
_TABLE_LITERAL = "(["

#: `name:` in a position a q table literal takes a COLUMN NAME, which is an
#: assignment as far as q is concerned: right after `([]`, after `([` (a keyed
#: literal's first key column), after the `]` that closes the key columns, or
#: after a `;` inside one. A keyed literal throws `'assign` for a builtin in
#: either half, key or value.
_EMBEDDED_COLUMN = re.compile(r"(?:\(\[\]?|;|\])\s*([a-zA-Z][a-zA-Z0-9_]*)\s*:")

#: What stands in for a value Python fills in at runtime - an f-string's
#: `{...}`, or a non-literal operand of `+`. A plain name that is no builtin,
#: so a placeholder in a column position is never reported, while the text on
#: either side of it is still read as the one string q will receive.
_PLACEHOLDER = "x"


def _as_q_text(node: ast.AST) -> str | None:
    """The string `node` builds, with each runtime value as `_PLACEHOLDER`.

    A string constant, an f-string, or a `+` chain with at least one string
    in it; None for anything else. An f-string's literal text after its first
    `{...}` is a SEPARATE constant, and so is each operand of a `+`, so reading
    constants one at a time misses a column name that is not in the same piece
    as the `([` that opens the literal.
    """
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
            else _PLACEHOLDER
            for part in node.values
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _as_q_text(node.left), _as_q_text(node.right)
        if left is None and right is None:
            return None
        return (left if left is not None else _PLACEHOLDER) + (
            right if right is not None else _PLACEHOLDER
        )
    return None


def rule_reserved_name_in_embedded_q(path: str, text: str) -> list[Finding]:
    r"""A q builtin used as a column name in q embedded in a Python string.

    THE GAP THIS CLOSES. Every other rule here reads `.q` files, because
    `_tracked_q_files` globs `*.q`. The orchestrator builds q expressions in
    Python and sends them over IPC, and those were invisible to all eleven
    rules - including the one that already knew the name.

    `uqs schema` shipped with:

        expr = "([] name:string tables `; rows:...; cols:count each cols each tables `)"

    `cols` was ALREADY in RISKY_PARAM_NAMES. The literal threw `'assign`
    against a live process, because a table literal's column names are
    assignments and the checker was not looking in Python.

    Keyed literals count (`([k:..] v:..)` throws for a builtin in either
    half), and so does q assembled from pieces - an f-string, or a `+` chain
    of strings - which is read as the one string q receives (#1332).

    Only the column-name position is checked. A builtin READ inside an
    expression (`count each cols each tables`) is correct and common - it is
    being used as the function it is. Flagging that would make the rule
    useless.
    """
    findings: list[Finding] = []
    try:
        tree = ast.parse(text, filename=path)
    except SyntaxError:
        # Not this rule's job to report unparseable Python; ruff already does.
        return findings

    # Docstrings are prose ABOUT code, not code that reaches q - and this
    # rule's own docstring quotes the expression it exists to catch, so
    # without this the checker reports itself. Skipping them is not an
    # exemption for convenience: a string in a docstring position is never
    # sent anywhere.
    docstrings = set()
    for holder in ast.walk(tree):
        if not isinstance(
            holder, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
        ):
            continue
        body = getattr(holder, "body", [])
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            docstrings.add(id(body[0].value))

    # The pieces of an f-string or a `+` chain already read as part of the
    # whole. `ast.walk` visits a parent before its children, so the whole is
    # read first and its pieces are then skipped, not reported a second time.
    read: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.expr) or id(node) in read or id(node) in docstrings:
            continue
        q_text = _as_q_text(node)
        if q_text is None:
            continue
        read.update(id(sub) for sub in ast.walk(node))
        if _TABLE_LITERAL not in q_text:
            continue
        for match in _EMBEDDED_COLUMN.finditer(q_text):
            name = match.group(1)
            if name not in RISKY_PARAM_NAMES:
                continue
            findings.append(
                Finding(
                    path=path,
                    line=node.lineno,
                    rule="reserved-name-in-embedded-q",
                    detail=f"q table literal uses the builtin `{name}` as a column name",
                    why=(
                        "a table literal's column names are assignments, so this throws "
                        "'assign when the expression reaches q - and it reaches q at "
                        "runtime, against a live process, not at import. Rename the "
                        "column (`n"
                        f"{name}` or similar) and rename it back on the Python side."
                    ),
                )
            )
    return findings


def _qlinter() -> str | None:
    """The linter binary, or None when it is not installed."""
    explicit = os.environ.get(QLINTER_ENV)
    if explicit:
        return explicit if Path(explicit).is_file() else None
    return shutil.which("qlinter")


def _linter_findings(binary: str) -> list[Finding]:
    """Everything `qlinter` reports over this repository's `.q` files.

    `--profile uqf` because two of the rules that moved - the bare-slash
    comment block and the legacy datetime - are this repository's policy
    rather than general q advice, and live behind that profile.

    Exclusions are NOT passed here: the linter reads them from `.qlinter.toml`
    itself, and passing them twice would be two places to change one list.
    """
    result = subprocess.run(
        [binary, "src", "tests", "scripts", "--format", "json", "--profile", "uqf"]
        + ["--extend-select", ",".join(CORRECTNESS_PREFIXES)],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    # 0 = clean, 1 = findings; anything else is the linter failing rather
    # than the code being wrong, and must not read as a clean run.
    if result.returncode not in (0, 1):
        raise SystemExit(
            f"check_q_traps: qlinter failed ({result.returncode}): {result.stderr.strip()}"
        )
    return [
        Finding(
            path=rel,
            line=item["line"],
            rule=f"{item['code']} {item['rule']}",
            detail=item["detail"],
            why=item["why"],
        )
        for item in json.loads(result.stdout or "[]")
        for rel in [_relative(item["path"])]
        if _gated(item["code"]) and not _exempt(item["code"], rel)
    ]


#: What this hook gates on: every correctness rule, by CATEGORY (#965).
#:
#: It used to be a list of thirteen codes - the rules that had lived in this
#: file - and it stopped growing the day it was written: qlinter went on to
#: forty correctness rules, and QB010 (apply-not-subtract, the production
#: timer bug) was not among the thirteen. A prefix enforces a new upstream rule
#: the day the pin in .qlinter-version moves, and moving the pin is the
#: deliberate edit: `_check_version` refuses any other installed version, so
#: an upstream release alone cannot turn a commit here red.
#:
#: Style, naming-convention and disclosure codes (QS, QL, QP004/QP006, QF016,
#: ...) stay out: they are advice, and `qlinter src tests scripts` shows them.
CORRECTNESS_PREFIXES = ("QE", "QA", "QB", "QD")

#: Codes outside those categories that moved here from this file, and stay.
EXTRA_CODES = frozenset(
    {
        "QF001",  # reserved-parameter
        "QF002",  # underscore-parameter
        "QF003",  # reserved-local
        "QF004",  # reserved-definition
        "QP001",  # bare-slash-block
        "QP002",  # datetime-type
    }
)

#: Correctness codes this hook does NOT gate on, each with its reason. Empty
#: today - all forty run clean over the tree. An entry needs the reason, the
#: way .qlinter.toml's `ignore` carries one for style.
OPTED_OUT: dict[str, str] = {}


def _gated(code: str) -> bool:
    if code in OPTED_OUT:
        return False
    return code in EXTRA_CODES or code.startswith(CORRECTNESS_PREFIXES)


def _catalogue(binary: str) -> list[str]:
    """Every rule code the installed linter knows, from `qlinter --rules`."""
    listed = subprocess.run([binary, "--rules"], capture_output=True, text=True, check=True)
    return [line.split()[0] for line in listed.stdout.splitlines() if line.strip()]


def _check_version(binary: str) -> str | None:
    """None when the installed linter is the pinned one, else why not (#969).

    Rules change between releases (QB003 at 0.14.6 and 0.14.9, QB010 at
    0.14.10), so a hook on another version and CI on the pin pass and fail
    different code. Refused rather than warned: a warning in a hook's output
    is read as a pass.
    """
    out = subprocess.run([binary, "--version"], capture_output=True, text=True, check=False)
    words = out.stdout.split()
    installed = f"v{words[-1]}" if out.returncode == 0 and words else "unknown"
    pinned = _pinned_version()
    if installed == pinned:
        return None
    return (
        f"check_q_traps: {binary} is qlinter {installed}, but this repository pins\n"
        f"{pinned} (.qlinter-version). Rules differ between releases, so this\n"
        "hook and CI would pass and fail different code. Install the pin:\n\n"
        f"    {INSTALL_HINT}"
    )


#: Rule codes this repository does not apply to its own tests, and why.
#:
#: QP002 forbids the legacy datetime type, which has no legitimate use here -
#: except in the test that PROVES the trap, which has to construct the value
#: the trap needs. `tests/q/test_time_zone.q` does exactly that, seven times.
#: The rule carried this scoping when it lived in this file; the linter's
#: port of it does not, so it is applied here rather than lost.
#:
#: Narrowing the scope beats a suppression comment: a rule with an escape
#: hatch gets the hatch used, and then it protects nothing.
TEST_EXEMPT_CODES = frozenset({"QP002"})


def _relative(path: str) -> str:
    """The linter reports absolute paths; findings here are repo-relative."""
    try:
        return str(Path(path).resolve().relative_to(REPO))
    except ValueError:
        return path


def _exempt(code: str, rel: str) -> bool:
    return code in TEST_EXEMPT_CODES and rel.startswith("tests/")


def main() -> int:
    binary = _qlinter()
    if binary is None:
        print(
            "check_q_traps: qlinter is not installed, and it owns all but one of\n"
            "the rules this hook checks. Skipping would leave the hook\n"
            "reporting success over checks that did not run.\n\n"
            f"    {INSTALL_HINT}",
            file=sys.stderr,
        )
        return 1
    mismatch = _check_version(binary)
    if mismatch:
        print(mismatch, file=sys.stderr)
        return 1

    q_files = _tracked_q_files()
    if not q_files:
        print("check_q_traps: no tracked .q files found - refusing to pass vacuously")
        return 1

    findings = _linter_findings(binary)

    # q does not only live in .q files. The orchestrator and the gateway build
    # q expressions in Python and send them over IPC, and those are invisible
    # both to every .q rule and to the linter, which discovers .q files only.
    python_files = _python_files()
    for path in python_files:
        rel = str(path.relative_to(REPO))
        text = path.read_text(encoding="utf-8", errors="replace")
        findings.extend(rule_reserved_name_in_embedded_q(rel, text))

    if findings:
        print(f"check_q_traps: {len(findings)} finding(s)\n")
        for f in sorted(findings, key=lambda f: (f.path, f.line)):
            print(f"{f.path}:{f.line}: [{f.rule}] {f.detail}")
            print(f"    {f.why}\n")
        return 1

    print(
        f"check_q_traps: {len(q_files)} .q + {len(python_files)} .py file(s) clean "
        f"({sum(map(_gated, _catalogue(binary)))} rules through qlinter, 1 here)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
