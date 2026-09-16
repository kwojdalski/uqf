#!/usr/bin/env python3
"""Static checks for q traps that produce a WRONG ANSWER rather than an error.

The expensive q bugs in this repository have not been the ones that throw.
They have been the ones that succeed and return something plausible: a file
silently commented out whose load still reported success, a dry-run gate that
suppressed only the *recording* of an effect that had already happened, a
health check that reported "table absent" against a healthy source. Each cost
a debugging session; several cost more than one, in different files, months
apart.

`python/uqf_frontend/tests/test_q_programs.py` already guards the q programs
embedded in Python string constants. It cannot see `.q` files at all - and
every one of the seven bugs behind this checker was in a `.q` file.

## The bar for a rule here

A rule earns its place only if a match is **almost always wrong**. A checker
that cries wolf gets suppressed, and then it protects nothing - which is the
same failure mode as the alarms it is meant to replace. So each rule below
records its false-positive rate against this repository's existing working q,
and rules that could not clear the bar are listed at the bottom as
deliberately NOT implemented, with the reason.

One rule here (`rule_datetime_type`) guards a trap that has NOT yet bitten
this tree. It is included because the canonical repository's `z->p` cast
restoration says it bit there, that tree is unreachable so the bug itself
cannot be read, and the trap is measurable from first principles - see that
rule's docstring for the numbers. Every other rule is a post-mortem.

Run directly, or via the pre-commit hook. Exits 1 on any finding.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

#: Vendored trees are not ours to fix, and `build/` is generated.
EXCLUDED_PREFIXES = ("lib/", "build/")

#: q reserved words that are realistic parameter names. Deliberately NOT the
#: full 182-name list: a checker for `.q` files wants the names someone would
#: plausibly reach for, and flagging `count` or `select` as a parameter name
#: would be noise. Every name here has either already bitten this repository
#: or is an obvious candidate for a window/table/config variable.
RISKY_PARAM_NAMES = frozenset(
    {
        "desc",
        "tables",
        "sv",
        "load",
        "save",
        "count",
        "first",
        "last",
        "type",
        "key",
        "value",
        "max",
        "min",
        "sum",
        "avg",
        "get",
        "set",
        "next",
        "prev",
        "cols",
        "meta",
        "like",
        "in",
        "within",
        "bin",
        "cut",
        "fill",
        "find",
        "group",
        "iasc",
        "idesc",
        "insert",
        "upsert",
        "med",
        "mod",
        "neg",
        "not",
        "null",
        "or",
        "and",
        "raze",
        "read0",
        "read1",
        "reverse",
        "rotate",
        "select",
        "show",
        "signum",
        "ss",
        "ssr",
        "string",
        "sublist",
        "system",
        "til",
        "trim",
        "union",
        "var",
        "where",
        "xbar",
        "xcol",
        "xkey",
        "abs",
        "all",
        "any",
        "asc",
        "attr",
        "delete",
        "distinct",
        "div",
        "each",
        "enlist",
        "eval",
        "exec",
        "exit",
        "exp",
        "floor",
        "flip",
        "hopen",
        "hclose",
        "inv",
        "log",
        "lower",
        "upper",
        "sqrt",
        "reval",
        "update",
        "wsum",
        "wavg",
    }
)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    detail: str
    why: str


def _tracked_q_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "*.q"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.split()
    return [REPO / p for p in out if not p.startswith(EXCLUDED_PREFIXES)]


def _strip_comments(line: str) -> str:
    """Drop a trailing q comment, so a rule never fires on prose.

    q's comment rule is that `/` starts a comment when preceded by whitespace
    or at line start - NOT mid-token, or every `%`-style path and every `/`
    in `sv`/`vs` usage would vanish. `//` at line start is also a comment.
    """
    if line.lstrip().startswith(("/", "\\")):
        return ""
    return re.split(r"(?:^|\s)/", line, maxsplit=1)[0]


# --------------------------------------------------------------------- rules


def rule_bare_slash_comment_block(path: str, lines: list[str]) -> list[Finding]:
    """A line containing only `/` opens a MULTI-LINE comment block.

    It is ended only by a line containing `\\`. One of these in a file header
    commented out an entire file: the namespace had no names, every call gave
    a bare value error, and `system"l"` still reported the load succeeded.

    False positives in this repo: 0. A lone `/` is never what anyone means -
    `/ .` is the intended blank comment line, and this repo uses it.
    """
    findings = []
    for n, raw in enumerate(lines, 1):
        if raw.strip() == "/":
            findings.append(
                Finding(
                    path,
                    n,
                    "bare-slash-block",
                    "a line containing only `/`",
                    "opens a multi-line comment block, ended only by a line "
                    "containing `\\` - this silently comments out everything "
                    'below it while `system"l"` still reports success. '
                    "Use `/ .` for a blank comment line",
                )
            )
    return findings


def rule_reserved_parameter_names(path: str, lines: list[str]) -> list[Finding]:
    """A q builtin used as a lambda parameter gives a bare `nyi at CALL time.

    Not at definition, and whether or not the body references it. `desc`,
    `tables`, `sv` and `load` have all done this here.

    False positives in this repo: 0.
    """
    findings = []
    for n, raw in enumerate(lines, 1):
        code = _strip_comments(raw)
        for sig in re.finditer(r"\{\s*\[([^\]]*)\]", code):
            params = [p.strip() for p in sig.group(1).split(";") if p.strip()]
            bad = [p for p in params if p in RISKY_PARAM_NAMES]
            if bad:
                findings.append(
                    Finding(
                        path,
                        n,
                        "reserved-parameter",
                        f"parameter name(s) {bad}",
                        "shadows a q builtin, which gives a bare `nyi when the "
                        "lambda is CALLED - not when it is defined. Rename "
                        "(timer_desc, sub_tables, label)",
                    )
                )
    return findings


def rule_reserved_local_assignment(path: str, text: str) -> list[Finding]:
    """A q builtin assigned as a lambda LOCAL throws `assign at load time.

    And it aborts the rest of the file while the enclosing script carries on,
    leaving a half-populated namespace - the same silent-partial-load failure
    as the bare-slash comment block.

    Distinct from `rule_reserved_parameter_names`, which inspects signatures.
    This is the sixth reserved-name collision in this repository and the
    first as a local: `var:credential_var source` in source_contract.q, where
    `var` is variance. A parameter-only rule does not see it.

    Scoped to lambda BODIES, because the same name at namespace level is fine
    (a ``\\d .qsrc`` then ``var:1`` defines ``.qsrc.var``, legal). qSQL lines
    are skipped: `select max:...` is a column alias, not an assignment.

    False positives in this repo: 0.
    """
    findings = []
    for m in re.finditer(r"\{", text):
        end = _match_forward(text, m.start(), "{", "}")
        if end < 0:
            continue
        body = text[m.start() : end]
        # Only the outermost lambda of each nest needs scanning; an inner one
        # is inside this body already. Cheap dedupe: skip a `{` that is not
        # the first of its nesting level.
        if text.rfind("}", 0, m.start()) < text.rfind("{", 0, m.start()):
            continue
        for line_off, line in enumerate(body.split("\n")):
            code = _strip_comments(line)
            if re.search(r"\b(?:select|exec|update|delete|by)\b", code):
                continue
            for a in re.finditer(r"(?<![.`\w])([a-z][a-z0-9_]*)\s*:(?!:)", code):
                name = a.group(1)
                if name not in RISKY_PARAM_NAMES:
                    continue
                line_no = text.count("\n", 0, m.start()) + 1 + line_off
                findings.append(
                    Finding(
                        path,
                        line_no,
                        "reserved-local",
                        f"local assignment to `{name}`",
                        "shadows a q builtin as a lambda local, which throws "
                        "`assign at LOAD time and aborts the rest of the file "
                        "while the enclosing script carries on - leaving a "
                        "half-populated namespace. Rename it",
                    )
                )
    return findings


def rule_underscore_parameter(path: str, lines: list[str]) -> list[Finding]:
    """`_` as a lambda parameter makes application throw a bare `'match`.

    `_` is q's drop/cut operator, not an ordinary name. A lambda declaring it
    parses without complaint and even *projects* without complaint — it fails
    only when applied, with `'match`, which names nothing and points at
    nothing. So the file loads, the namespace populates, early calls succeed,
    and the failure surfaces at whichever call site happens to apply the
    projection first.

    That is exactly how it presented: `bounded_worker.q` loaded cleanly,
    planned its windows correctly, and died on the first publish. The
    conventional name for an ignored parameter here is `unused`.

    Distinct from `rule_reserved_parameter_names`, which checks q builtins by
    name — this is punctuation, and no name list contains it.

    False positives in this repo: 0.
    """
    findings = []
    for n, raw in enumerate(lines, 1):
        code = _strip_comments(raw)
        for sig in re.finditer(r"\{\s*\[([^\]]*)\]", code):
            params = [p.strip() for p in sig.group(1).split(";")]
            if any(p == "_" for p in params):
                findings.append(
                    Finding(
                        path,
                        n,
                        "underscore-parameter",
                        "`_` as a lambda parameter",
                        "`_` is q's drop/cut operator, so applying the lambda "
                        "throws a bare `'match` that names nothing - and only "
                        "at the first call site that applies it, long after the "
                        "file loaded cleanly. Name it `unused`",
                    )
                )
    return findings


def rule_niladic_dot_empty(path: str, lines: list[str]) -> list[Finding]:
    """`f . ()` is a type error; `f . enlist(::)` applies a niladic.

    False positives in this repo: 0.
    """
    findings = []
    for n, raw in enumerate(lines, 1):
        code = _strip_comments(raw)
        if re.search(r"\.\s*\(\s*\)", code):
            findings.append(
                Finding(
                    path,
                    n,
                    "dot-empty-list",
                    "`. ()`",
                    "applying a function to an empty list is a type error; "
                    "`f . enlist(::)` is what applies a niladic function",
                )
            )
    return findings


def _match_forward(text: str, start: int, opener: str, closer: str) -> int:
    """Index just past the `closer` matching the `opener` at `start`, or -1.

    Brace-matching rather than a regex, because the nesting is real: a lambda
    body contains braces, and a projection's argument list contains brackets.
    Skips over string and char literals so a `}` inside "..." does not close
    the body.
    """
    depth = 0
    i = start
    n = len(text)
    while i < n:
        c = text[i]
        if c == '"':
            i += 1
            while i < n and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
        elif c == opener:
            depth += 1
        elif c == closer:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return -1


def _split_top_level(text: str) -> list[str]:
    """Split on `;` at nesting depth 0."""
    parts, depth, cur = [], 0, []
    for c in text:
        if c in "[({":
            depth += 1
        elif c in "])}":
            depth -= 1
        if c == ";" and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(c)
    parts.append("".join(cur))
    return parts


def rule_multiparam_lambda_under_at(path: str, text: str) -> list[Finding]:
    """`@[{[a;b] ...};X;handler]` - `@` is UNARY apply.

    The second argument is passed as ONE argument, so a two-parameter lambda
    fails on rank and the handler fires. That made a health check report
    "table absent" against a perfectly healthy source: a false schema-drift
    alarm, which is how a real one gets ignored.

    Two things make this rule safe, and the second was learned the hard way -
    without it the rule had a 100% false-positive rate on this repository,
    flagging `cross_ref_price_at` and a correct test, and nothing else:

      1. It is ARITY-AWARE, firing only on an inline lambda whose signature it
         can read. `@[f;x;h]` on a named function is not flagged (the arity is
         not local information), and `.[f;(a;b);h]` is not flagged either,
         since that form genuinely takes an argument list.

      2. It is PROJECTION-AWARE. `@[{[a;b;c;d] ...}[a;b;c;];t;h]` is correct
         code: the trailing `;` leaves one slot open, so the projection is
         unary. Effective arity is parameters minus arguments actually
         supplied, and only that number being 2 or more is a bug.

    False positives in this repo: 0 (2 before the projection-awareness fix).
    """
    findings = []
    for m in re.finditer(r"@\[\s*\{\s*\[([^\]]*)\]", text):
        params = [p.strip() for p in m.group(1).split(";") if p.strip()]
        if len(params) < 2:
            continue

        # The lambda's own braces, so a following projection can be found.
        brace = text.index("{", m.start())
        after_body = _match_forward(text, brace, "{", "}")
        if after_body < 0:
            continue

        supplied = 0
        rest = text[after_body:]
        stripped = rest.lstrip()
        if stripped.startswith("["):
            open_at = after_body + (len(rest) - len(stripped))
            after_args = _match_forward(text, open_at, "[", "]")
            if after_args > 0:
                args = _split_top_level(text[open_at + 1 : after_args - 1])
                # An empty slot - `f[a;b;]` or `f[;x]` - is a gap, not an
                # argument. That is exactly what makes the projection unary.
                supplied = sum(1 for a in args if a.strip())

        effective = len(params) - supplied
        if effective >= 2:
            line = text.count("\n", 0, m.start()) + 1
            findings.append(
                Finding(
                    path,
                    line,
                    "multiparam-under-at",
                    f"`@[` with an effectively {effective}-argument lambda "
                    f"(params {params}, {supplied} supplied)",
                    "`@` is unary apply - it passes its second argument as ONE "
                    "argument, so this fails on rank and the handler fires, "
                    "reporting a failure that never happened. Supply the extra "
                    "arguments as a projection, or use `.` with an argument list",
                )
            )
    return findings


def _strip_comments_preserving_lines(text: str) -> str:
    """The file with q comments blanked out, newlines kept so lines still map.

    Needed because a comment can contain anything, including an apostrophe
    followed by a quote, and the throw scanner below must not mistake prose
    for code.
    """
    return "\n".join(_strip_comments(line) for line in text.split("\n"))


def _throw_starts(code: str) -> list[int]:
    """Indices of `'\"` sequences that are genuinely a throw.

    The naive `re.finditer(r"'\"")` also matches INSIDE a message: q code
    like `'\"cannot normalize '\",s,\"' to a pair\"` contains the sequence
    `'\"` in the middle of its own text, so the scan started from there and
    ran past the end of the function - reporting a 150-character message as
    5142, once it had swallowed the rest of the file.

    So this tracks string state and reports only a `'` that appears OUTSIDE
    a string literal and is immediately followed by one.
    """
    starts = []
    i = 0
    n = len(code)
    while i < n:
        c = code[i]
        if c == '"':
            i += 1
            while i < n and code[i] != '"':
                i += 2 if code[i] == "\\" else 1
            i += 1
            continue
        if c == "'" and i + 1 < n and code[i + 1] == '"':
            starts.append(i)
            # skip into the string so its contents are not rescanned
            i += 1
            continue
        i += 1
    return starts


def _throw_literal_length(code: str, start: int) -> int:
    """Total literal characters in the throw expression beginning at `start`.

    Scans the expression rather than "everything up to the next `];`". The
    first version did the latter and ran past the end of a `$[...]` closing
    with `]}`, swallowing quotes from the next function. The expression ends
    at the first `;`, `]`, `}` or `)` that is not inside a nested bracket or
    a string.
    """
    i = start + 1
    n = len(code)
    depth = 0
    total = 0
    while i < n:
        c = code[i]
        if c == '"':
            i += 1
            while i < n and code[i] != '"':
                if code[i] == "\\":
                    i += 1
                total += 1
                i += 1
            i += 1
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0:
                break
            depth -= 1
        elif c == ";" and depth == 0:
            break
        i += 1
    return total


def rule_overlong_throw(path: str, text: str) -> list[Finding]:
    """q truncates a thrown error string at 255 bytes, silently.

    So a long message loses its tail - and the tail is where the
    explanation lives, since the mechanical prefix ("function: value X is
    wrong") comes first by convention. The diagnosis disappears exactly when
    someone needs it, and nothing reports that anything was cut.

    Found by a test asserting an error message contained a phrase it should
    have: the message was 254 bytes and the phrase had been cut mid-word. It
    had been passing as prose review for three files.

    Counts only the LITERAL text of a throw, not interpolated values, so a
    match means the message is over the limit *before* any value is
    substituted - which is unambiguous. The real budget is tighter, since
    interpolation adds to it; the threshold is deliberately below 255 to
    leave room.

    False positives in this repo: 0 (3 genuine findings when first run).
    """
    findings = []
    budget = 200
    code = _strip_comments_preserving_lines(text)
    for start in _throw_starts(code):
        literal = _throw_literal_length(code, start)
        if literal > budget:
            line = code.count("\n", 0, start) + 1
            findings.append(
                Finding(
                    path,
                    line,
                    "overlong-throw",
                    f"thrown message with {literal} chars of literal text",
                    "q truncates a thrown string at 255 bytes silently, so the "
                    "tail - where the explanation lives - is lost. Put the "
                    "consequence FIRST and move the long form into a comment "
                    "at the definition",
                )
            )
    return findings


def rule_self_comparison(path: str, lines: list[str]) -> list[Finding]:
    """`where dataset=dataset` compares a column to itself and matches all rows.

    Any qSQL where-clause whose parameter shares a column's name is always
    true, so the filter silently does nothing. `leg_book_as_of` and
    `quotes_for_sym` both name around this.

    Scoped to qSQL context, and to `=`/`~` only - `x+x` and `x&x` are
    legitimate.

    False positives in this repo: 0.
    """
    findings = []
    for n, raw in enumerate(lines, 1):
        code = _strip_comments(raw)
        if not re.search(r"\b(?:where|select|exec|update|delete)\b", code):
            continue
        for m in re.finditer(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*(=|~)\s*\1\b", code):
            findings.append(
                Finding(
                    path,
                    n,
                    "self-comparison",
                    f"`{m.group(1)}{m.group(2)}{m.group(1)}`",
                    "compares a name to itself, which is always true and "
                    "matches every row - the filter silently does nothing. "
                    "Rename the parameter so it differs from the column",
                )
            )
    return findings


#: Every way to bring a q `datetime` into existence: the two casts, the
#: type-number cast that sidesteps them, the null and infinity literals, and
#: a datetime literal (`2026.09.15T10:00:00`, which the `T` distinguishes
#: from a timestamp's `D`).
DATETIME_PATTERNS = (
    (r'"z"\s*\$', '`"z"$` cast'),
    (r"`datetime\s*\$", "``datetime$` cast"),
    (r"\b15h\s*\$", "`15h$` cast"),
    (r"(?<![\w.])-?0[NW]z\b", "a datetime null/infinity literal"),
    (r"\b\d{4}\.\d{2}\.\d{2}T\d", "a datetime literal"),
)


def rule_datetime_type(path: str, lines: list[str]) -> list[Finding]:
    """q's `datetime` (type 15h, `z`) has no legitimate use in this tree.

    It is a FLOAT count of days, where a timestamp is a long count of
    nanoseconds, so every z->p conversion is a float-to-long rounding. It is
    quiet in the worst way: measured under KDB-X, 999 of 1000
    nanosecond-spaced instants do not survive a p->z->p round trip (largest
    error 629ns, up to 447ns of it *backwards*), while a full day of whole
    seconds survives exactly - so the bug passes every hand-check, demo and
    fixture built from round numbers and only shows up on real trade
    timestamps. `=` then says a z and a p at the same instant are equal while
    `~` says they do not match, and `distinct` keeps a value and its own
    round trip as two values, which is how a retry-safe dedupe silently
    stops recognising rows it already published. That is issue #80's L-03.

    This forbids the TYPE rather than the cast, and the distinction is the
    reason the rule can exist at all: telling a z->p cast from any other
    `"p"$` needs the input's type, which is not local information (the same
    obstacle that keeps the fully-applied-projection trap off this list). The
    type's mere presence is local, and this tree's whole convention -
    everything is UTC timestamps internally, ETL-08/R9.1 - means there is
    nothing a datetime can be here except a mistake.

    Scoped to non-test files, deliberately. A test that PROVES the trap has
    to construct the value the trap needs, and
    `tests/q/test_time_zone.q` does exactly that. Narrowing the scope is
    better than a suppression comment: a rule with an escape hatch gets the
    hatch used, and then it protects nothing.

    False positives in this repo: 0 on non-test files, and 0 across all
    tracked `.q` files except that one test - the datetime type appears
    nowhere else, in src/, scripts/ or tests/.
    """
    if path.startswith("tests/"):
        return []
    findings = []
    for n, raw in enumerate(lines, 1):
        code = _strip_comments(raw)
        for pattern, detail in DATETIME_PATTERNS:
            if re.search(pattern, code):
                findings.append(
                    Finding(
                        path,
                        n,
                        "datetime-type",
                        detail,
                        "q's datetime (type 15h) is a float count of days, so "
                        "converting it to a timestamp rounds sub-second values by "
                        "hundreds of nanoseconds without erroring - and whole "
                        "seconds survive, so it passes every round-number check. "
                        "Use a timestamp (`p`) throughout; everything is UTC "
                        "internally (ETL-08/R9.1, and #80's L-03)",
                    )
                )
    return findings


LINE_RULES = (
    rule_bare_slash_comment_block,
    rule_reserved_parameter_names,
    rule_underscore_parameter,
    rule_niladic_dot_empty,
    rule_self_comparison,
    rule_datetime_type,
)
TEXT_RULES = (
    rule_multiparam_lambda_under_at,
    rule_reserved_local_assignment,
    rule_overlong_throw,
)


# Deliberately NOT implemented, because no formulation cleared the bar above:
#
#   * The fully-applied-projection trap (`f[a;b]` is a CALL, not a deferred
#     one). Textually identical to a legitimate partial projection
#     `{[a;b] ...}[a]`, which this repo uses correctly and often. Telling them
#     apart needs the callee's arity at the call site, which is not local
#     information. The arity-aware `@[` subcase above is the part that CAN be
#     checked soundly.
#   * Right-to-left precedence (`d 1+0D12` is `d[1+0D12]`). Legitimate in
#     `til 3+1`, and flagging every unparenthesised arithmetic argument would
#     fire across the whole repository.
#   * The z->p cast DIRECTION (`"p"$` applied to something that is a
#     datetime). Indistinguishable from any other `"p"$` without the input's
#     type, which is the same obstacle as above. `rule_datetime_type`
#     forbids the datetime type outright instead, which is checkable and -
#     given that everything here is UTC timestamps - loses nothing.
#   * Atom-vs-string and int-vs-long mismatches under `~`. Needs types.
#   * `in` on two strings comparing per-character. Needs types.
#
# Those four live in the q-silent-traps memory and in reviewer attention
# instead. Pretending to check them with a noisy regex would be worse than
# leaving them to a human, because a suppressed checker protects nothing.


def main() -> int:
    files = _tracked_q_files()
    if not files:
        print("check_q_traps: no tracked .q files found - refusing to pass vacuously")
        return 1

    findings: list[Finding] = []
    for path in files:
        rel = str(path.relative_to(REPO))
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        for rule in LINE_RULES:
            findings.extend(rule(rel, lines))
        for trule in TEXT_RULES:
            findings.extend(trule(rel, text))

    if findings:
        print(f"check_q_traps: {len(findings)} finding(s)\n")
        for f in findings:
            print(f"{f.path}:{f.line}: [{f.rule}] {f.detail}")
            print(f"    {f.why}\n")
        return 1

    print(
        f"check_q_traps: {len(files)} tracked .q file(s) clean "
        f"({len(LINE_RULES) + len(TEXT_RULES)} rules)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
