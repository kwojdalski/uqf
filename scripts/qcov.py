"""qcov - statement-level code coverage for q, in the shape coverage.py has.

WHAT THIS IS FOR. q has no coverage tool. `scripts/test.py coverage` shipped
with a function-level one - wrap every declared function with a counter, ask
which were never called - and that answers a coarse question: which functions
does nothing run? It cannot answer the finer one that finds most real gaps:
which BRANCHES inside a function that IS called have never executed. A
function whose error path has never run reports as fully covered there.

So this measures STATEMENTS, by instrumenting the source: a probe is injected
before every statement inside every lambda, the suite is run against the
instrumented copy, and the probes that never fired are the uncovered lines.
That is what coverage.py, istanbul and gcov all do; nothing here is novel
except that the language is q.

THE THREE PARTS

  lex()         a q tokenizer. The whole tool rests on it, because a `;` or a
                `[` inside a string or a comment must not be mistaken for
                syntax, and q's comment rules are unusually easy to get
                wrong (see Lexer's own docstring).
  instrument()  injects probes at STATEMENT boundaries only, never inside an
                expression. `$[c;a;b]` is a conditional EXPRESSION and its
                arms are not statements; `if[c;a;b]` is a control statement
                and its arms are. Confusing the two produces q that does not
                parse, or worse, that parses differently.
  report()      terminal (coverage.py's term-missing shape) and LCOV, so the
                result can go to a CI coverage service unchanged.

WHAT IT DELIBERATELY DOES NOT INSTRUMENT

Top-level statements. Everything at the top level of a `.q` file runs when
the file loads, so every one of them would report as covered and the
denominator would be padded with lines that cannot be uncovered. The
interesting question is what runs INSIDE functions, so that is the
denominator.

HOW IT AVOIDS TOUCHING YOUR TREE

The instrumented sources are written into a throwaway copy of the repository,
never over the originals. A crashed run leaves the real tree untouched.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

#: The q namespace the probes call into. NOT `.qcov`: this repository already
#: has a `.qcov`, the ETL coverage LEDGER, and two things called coverage in
#: one process is exactly the collision that makes a test suite mysterious.
RUNTIME_NS = ".qcv"

#: Instrumented by default. `lib/` is vendored and never edited (H-01);
#: `tests/` is the thing doing the measuring.
DEFAULT_SOURCES = ("src",)


# --------------------------------------------------------------------- lexing


@dataclass(frozen=True)
class Token:
    kind: str
    text: str
    line: int  # 1-based


class LexError(Exception):
    pass


#: A q name: letters, digits, underscore and dot. `.qfwd.fwd_cont` is one name.
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")

#: Bracketing that the instrumenter tracks. Everything else is `other`.
_OPEN = "([{"
_CLOSE = ")]}"


def lex(src: str) -> list[Token]:
    """Tokenize q source.

    Only as precise as the instrumenter needs: it must never mistake a
    bracket or a semicolon inside a string, a comment or a system command
    for syntax, and it must know where names are so `if[` can be told from
    `f[`. Numeric literals are not decomposed - `2026.09.17D00:00` is one
    `other` token and nothing downstream cares.

    THE THREE COMMENT FORMS, each of which this repository has been bitten by:

      `/` to end of line, but ONLY when it begins a token. `a/b` is the
      `over` adverb applied to `a`, not a comment, so the rule is "preceded
      by whitespace or at the start of a line".

      A line whose only content is `/` opens a BLOCK comment that runs until
      a line whose only content is `\\`. Six such lines were once written as
      ordinary comments in this repository and silently commented out the
      code beneath them.

      A `\\` at the start of a line is a system command (`\\d .qfwd`,
      `\\l file.q`, `\\c 400 1000`) and runs to end of line. A bare `\\` on
      its own line ends the script.
    """
    out: list[Token] = []
    i, line, n = 0, 1, len(src)
    at_line_start = True

    while i < n:
        ch = src[i]

        # --- newline
        if ch == "\n":
            out.append(Token("newline", "\n", line))
            i += 1
            line += 1
            at_line_start = True
            continue

        # --- horizontal whitespace
        if ch in " \t\r":
            j = i
            while j < n and src[j] in " \t\r":
                j += 1
            out.append(Token("ws", src[i:j], line))
            i = j
            continue

        # --- a line that is exactly `/` opens a block comment
        if at_line_start and ch == "/" and _rest_of_line(src, i).strip() == "/":
            i, line = _skip_block_comment(src, i, line)
            at_line_start = True
            continue

        # --- a line starting with `\` is a system command (or ends the script)
        if at_line_start and ch == "\\":
            rest = _rest_of_line(src, i)
            j = i + len(rest)
            out.append(Token("system", rest, line))
            i = j
            continue

        # --- `/` comment to end of line, when it begins a token
        if ch == "/" and (at_line_start or (out and out[-1].kind in ("ws", "newline"))):
            rest = _rest_of_line(src, i)
            out.append(Token("comment", rest, line))
            i += len(rest)
            continue

        at_line_start = False

        # --- string
        if ch == '"':
            j = i + 1
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == '"':
                    j += 1
                    break
                if src[j] == "\n":
                    raise LexError(f"unterminated string on line {line}")
                j += 1
            else:
                raise LexError(f"unterminated string on line {line}")
            out.append(Token("string", src[i:j], line))
            i = j
            continue

        # --- symbol: a backtick and whatever name-ish text follows it,
        # --- including `:path/like/this and the bare ` null symbol.
        if ch == "`":
            j = i + 1
            if j < n and src[j] == '"':
                k = j + 1
                while k < n and src[k] != '"':
                    k += 2 if src[k] == "\\" else 1
                j = min(k + 1, n)
            else:
                while j < n and (src[j].isalnum() or src[j] in "._:/" or src[j] == "-"):
                    j += 1
            out.append(Token("symbol", src[i:j], line))
            i = j
            continue

        # --- name
        m = _NAME.match(src, i)
        if m:
            out.append(Token("name", m.group(), line))
            i = m.end()
            continue

        # --- structure
        if ch in _OPEN:
            out.append(Token("open", ch, line))
            i += 1
            continue
        if ch in _CLOSE:
            out.append(Token("close", ch, line))
            i += 1
            continue
        if ch == ";":
            out.append(Token("semi", ";", line))
            i += 1
            continue

        out.append(Token("other", ch, line))
        i += 1

    return out


def _rest_of_line(src: str, i: int) -> str:
    end = src.find("\n", i)
    return src[i:] if end == -1 else src[i:end]


def _skip_block_comment(src: str, i: int, line: int) -> tuple[int, int]:
    """From a lone `/` line to just past the closing lone `\\` line.

    An unterminated block comment runs to end of file, which is q's own
    behaviour - it is not an error.
    """
    while i < len(src):
        end = src.find("\n", i)
        if end == -1:
            return len(src), line
        text = src[i:end].strip()
        i = end + 1
        line += 1
        if text == "\\":
            return i, line
    return i, line


# -------------------------------------------------------------- instrumenting


#: The three q control words whose bracket contains STATEMENTS after its
#: first argument. Everything else that follows `[` - a function call, an
#: index, `$[c;a;b]` - contains expressions, and a probe injected there
#: either fails to parse or changes what the expression evaluates to.
#:
#: `$` is deliberately absent and is the distinction worth stating: `$[c;a;b]`
#: is a conditional EXPRESSION whose arms produce a value, while `if[c;a;b]`
#: is a statement that produces none. They look alike and behave differently.
CONTROL_WORDS = frozenset({"if", "do", "while"})


@dataclass
class Probe:
    """One instrumented statement."""

    id: int
    line: int


@dataclass
class FileCoverage:
    """Per-file probe map and hit counts."""

    path: str
    probes: list[Probe] = field(default_factory=list)
    hits: dict[int, int] = field(default_factory=dict)

    @property
    def statements(self) -> int:
        return len(self.probes)

    @property
    def covered(self) -> int:
        return sum(1 for p in self.probes if self.hits.get(p.id, 0) > 0)

    @property
    def missing_lines(self) -> list[int]:
        return sorted({p.line for p in self.probes if self.hits.get(p.id, 0) == 0})

    @property
    def percent(self) -> float:
        return 100.0 if not self.probes else 100.0 * self.covered / self.statements


@dataclass
class _Frame:
    """One level of bracket nesting, and whether its contents are statements."""

    closer: str
    statements: bool


def instrument(src: str, *, first_id: int = 0) -> tuple[str, list[Probe]]:
    """Return instrumented source and the probes injected into it.

    A probe goes immediately before a statement's first token, so the LINE it
    records is the line a reader would point at. Probes are injected:

      * as the first statement of every lambda body, and
      * after every `;` that separates statements - inside a lambda body, or
        inside `if`/`do`/`while` after its first argument.

    Never inside `(...)`, never inside a call or index `f[...]`, and never
    inside `$[...]`. Those hold expressions.

    A trailing `;` before the closing brace separates nothing, so it gets no
    probe - `{a;}` has one statement, not two.
    """
    tokens = lex(src)
    out: list[str] = []
    probes: list[Probe] = []
    stack: list[_Frame] = []
    next_id = first_id
    # Set when the next meaningful token begins a statement that should carry
    # a probe. Held across whitespace, newlines and comments so the probe
    # lands on the code rather than on the blank line above it.
    pending = False

    def meaningful(idx: int) -> Token | None:
        for t in tokens[idx:]:
            if t.kind not in ("ws", "newline", "comment"):
                return t
        return None

    i = 0
    while i < len(tokens):
        tok = tokens[i]

        if pending and tok.kind not in ("ws", "newline", "comment"):
            # An empty statement - `;}` or `;]` - separates nothing.
            if not (tok.kind == "close" and tok.text in "}]"):
                out.append(f"{RUNTIME_NS}.h[{next_id}];")
                probes.append(Probe(next_id, tok.line))
                next_id += 1
            pending = False

        if tok.kind == "open":
            if tok.text == "{":
                out.append(tok.text)
                i += 1
                # A parameter list belongs to the lambda, not to its body.
                nxt = meaningful(i)
                if nxt is not None and nxt.kind == "open" and nxt.text == "[":
                    while i < len(tokens) and not (
                        tokens[i].kind == "open" and tokens[i].text == "["
                    ):
                        out.append(tokens[i].text)
                        i += 1
                    depth = 0
                    while i < len(tokens):
                        t = tokens[i]
                        out.append(t.text)
                        if t.kind == "open":
                            depth += 1
                        elif t.kind == "close":
                            depth -= 1
                            if depth == 0:
                                i += 1
                                break
                        i += 1
                stack.append(_Frame("}", statements=True))
                pending = True
                continue

            if tok.text == "[":
                prev = _prev_meaningful(tokens, i)
                is_control = prev is not None and prev.kind == "name" and prev.text in CONTROL_WORDS
                # Statements only AFTER the first argument: `if[cond; ...]`.
                # The condition itself is an expression.
                stack.append(_Frame("]", statements=is_control))
            else:
                stack.append(_Frame(")", statements=False))
            out.append(tok.text)
            i += 1
            continue

        if tok.kind == "close":
            if stack:
                stack.pop()
            out.append(tok.text)
            i += 1
            continue

        if tok.kind == "semi":
            out.append(tok.text)
            if stack and stack[-1].statements:
                pending = True
            i += 1
            continue

        out.append(tok.text)
        i += 1

    return "".join(out), probes


def _prev_meaningful(tokens: list[Token], idx: int) -> Token | None:
    for t in reversed(tokens[:idx]):
        if t.kind not in ("ws", "newline", "comment"):
            return t
    return None


# ------------------------------------------------------------------- runtime

#: The q side. Deliberately tiny and allocation-free in the hot path: `h` is
#: called once per executed statement, so anything expensive here shows up as
#: a slower suite and gets the tool switched off.
#:
#: `.z.exit` rather than a call at the end of the runner: it fires on EVERY
#: exit path, including the non-zero one a failing suite takes. Having to fix
#: the suite before you can measure it is the ordering that stops people
#: measuring.
RUNTIME_TEMPLATE = """\
/ qcov runtime - generated by scripts/qcov.py. Not part of the tree.
\\d {ns}
n:{count};
hits:n#0;
h:{{[i] hits[i]+:1;}}
out:"{out}";
dump:{{[]
    idx:where 0<hits;
    (hsym `$out) 0: {{[i] (string i),",",string hits i}} each idx;
    }}
\\d .
.z.exit:{{[c] @[{{{ns}.dump[]}};::;{{[e] -2 "qcov: dump failed: ",e;}}]; }}
"""


def runtime_source(probe_count: int, out_path: Path) -> str:
    return RUNTIME_TEMPLATE.format(ns=RUNTIME_NS, count=probe_count, out=out_path)


# -------------------------------------------------------------------- running


def _q_files(root: Path, sources: tuple[str, ...]) -> list[Path]:
    found: list[Path] = []
    for rel in sources:
        base = root / rel
        if base.is_file() and base.suffix == ".q":
            found.append(base)
        else:
            found.extend(sorted(base.rglob("*.q")))
    return found


def build_shadow(repo: Path, dest: Path, sources: tuple[str, ...]) -> dict[str, FileCoverage]:
    """Materialise an instrumented copy of *repo* at *dest*.

    Everything is SYMLINKED except the instrumented sources, which are real
    copies. That keeps the shadow cheap - this repository carries a vendored
    TorQ tree, a node_modules and a .venv - and, more importantly, means the
    originals cannot be written to even if instrumentation goes wrong.
    """
    dest.mkdir(parents=True, exist_ok=True)
    instrumented_roots = {s.split("/")[0] for s in sources}
    for entry in repo.iterdir():
        if entry.name == ".git" or entry.name in instrumented_roots:
            continue
        (dest / entry.name).symlink_to(entry)

    for root in instrumented_roots:
        shutil.copytree(repo / root, dest / root, symlinks=True)

    files: dict[str, FileCoverage] = {}
    next_id = 0
    for path in _q_files(dest, sources):
        rel = str(path.relative_to(dest))
        text = path.read_text()
        try:
            new_text, probes = instrument(text, first_id=next_id)
        except LexError as exc:
            print(f"qcov: skipping {rel}: {exc}", file=sys.stderr)
            continue
        if probes:
            _write_real(path, new_text)
            next_id += len(probes)
        files[rel] = FileCoverage(rel, probes)
    return files


def _write_real(path: Path, text: str) -> None:
    """Write *path*, replacing a symlink rather than following it.

    THE BUG THIS EXISTS TO PREVENT, which this tool shipped with for one
    run: the shadow tree symlinks everything it does not instrument, so
    writing to `shadow/tests/run_tests.q` wrote THROUGH the link and edited
    the real repository. A coverage tool that modifies the tree it is
    measuring is worse than no coverage tool.
    """
    if path.is_symlink():
        path.unlink()
    path.write_text(text)


#: Bootstrap script name, written into the shadow root. Deliberately a NEW
#: file rather than an edit to the runner: the runner may be a symlink into
#: the real tree, and there is no version of "edit it carefully" that is
#: safer than not editing it.
BOOTSTRAP = "_qcov_bootstrap.q"


def run_suite(shadow: Path, runner: list[str], runtime: Path, env: dict[str, str]) -> int:
    """Run the suite inside the shadow tree, with the runtime preloaded.

    The runtime has to load BEFORE any instrumented file, or the first probe
    is a call to an undefined function. A bootstrap script that loads the
    runtime and then the runner does that without touching either.
    """
    boot = shadow / BOOTSTRAP
    _write_real(
        boot,
        f'system"l {runtime}";\nsystem"l {runner[0]}";\n',
    )
    result = subprocess.run(
        [os.environ.get("Q", str(Path.home() / ".kx" / "bin" / "q")), BOOTSTRAP, *runner[1:]],
        cwd=shadow,
        env={**os.environ, **env},
        check=False,
    )
    return result.returncode


def collect(files: dict[str, FileCoverage], hits_path: Path) -> None:
    if not hits_path.exists():
        return
    counts: dict[int, int] = {}
    for line in hits_path.read_text().splitlines():
        if not line.strip():
            continue
        pid, _, count = line.partition(",")
        counts[int(pid)] = int(count)
    for cov in files.values():
        for probe in cov.probes:
            if probe.id in counts:
                cov.hits[probe.id] = counts[probe.id]


# ------------------------------------------------------------------ reporting


def report_terminal(files: dict[str, FileCoverage], *, show_missing: bool = True) -> str:
    """coverage.py's `term-missing` shape, because that is the report every
    reader of a coverage number already knows how to read."""
    rows = sorted(files.values(), key=lambda c: c.path)
    width = max([len(c.path) for c in rows] + [len("Name")])
    lines = [
        f"{'Name':<{width}} {'Stmts':>6} {'Miss':>6} {'Cover':>6}"
        + ("  Missing" if show_missing else ""),
        "-" * (width + 22 + (9 if show_missing else 0)),
    ]
    for cov in rows:
        if not cov.statements:
            continue
        miss = cov.statements - cov.covered
        line = f"{cov.path:<{width}} {cov.statements:>6} {miss:>6} {cov.percent:>5.0f}%"
        if show_missing and cov.missing_lines:
            line += "  " + _ranges(cov.missing_lines)
        lines.append(line)
    total = sum(c.statements for c in rows)
    covered = sum(c.covered for c in rows)
    pct = 100.0 if not total else 100.0 * covered / total
    lines.append("-" * (width + 22 + (9 if show_missing else 0)))
    lines.append(f"{'TOTAL':<{width}} {total:>6} {total - covered:>6} {pct:>5.0f}%")
    return "\n".join(lines)


def _ranges(lines: list[int]) -> str:
    """`12-15, 19` rather than `12, 13, 14, 15, 19` - the same compression
    coverage.py uses, and for the same reason: an uncovered block is one
    fact, not eight."""
    out: list[str] = []
    start = prev = lines[0]
    for n in lines[1:]:
        if n == prev + 1:
            prev = n
            continue
        out.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = n
    out.append(str(start) if start == prev else f"{start}-{prev}")
    return ", ".join(out)


def report_lcov(files: dict[str, FileCoverage]) -> str:
    """LCOV, so this can go to a coverage service unchanged.

    Line-oriented (`DA:`) rather than function-oriented: a probe is a
    statement, and several statements can share a line in q, so the count for
    a line is the maximum of its probes' counts - the line ran if any
    statement on it ran.
    """
    out: list[str] = []
    for cov in sorted(files.values(), key=lambda c: c.path):
        if not cov.statements:
            continue
        per_line: dict[int, int] = {}
        for probe in cov.probes:
            n = cov.hits.get(probe.id, 0)
            per_line[probe.line] = max(per_line.get(probe.line, 0), n)
        out.append("TN:")
        out.append(f"SF:{cov.path}")
        for line in sorted(per_line):
            out.append(f"DA:{line},{per_line[line]}")
        out.append(f"LH:{sum(1 for v in per_line.values() if v > 0)}")
        out.append(f"LF:{len(per_line)}")
        out.append("end_of_record")
    return "\n".join(out) + "\n"


def report_json(files: dict[str, FileCoverage]) -> str:
    payload = {
        "files": {
            cov.path: {
                "statements": cov.statements,
                "covered": cov.covered,
                "percent": round(cov.percent, 2),
                "missing_lines": cov.missing_lines,
            }
            for cov in sorted(files.values(), key=lambda c: c.path)
            if cov.statements
        }
    }
    total = sum(c.statements for c in files.values())
    covered = sum(c.covered for c in files.values())
    payload["total"] = {
        "statements": total,
        "covered": covered,
        "percent": round(100.0 if not total else 100.0 * covered / total, 2),
    }
    return json.dumps(payload, indent=2)


# ------------------------------------------------------------------------ cli


def measure(
    repo: Path,
    runner: list[str],
    sources: tuple[str, ...],
    env: dict[str, str] | None = None,
) -> tuple[dict[str, FileCoverage], int]:
    """Instrument, run, collect. Returns the coverage and the suite's exit code."""
    with tempfile.TemporaryDirectory(prefix="qcov-") as tmp:
        tmpdir = Path(tmp)
        shadow = tmpdir / "tree"
        files = build_shadow(repo, shadow, sources)
        total = sum(c.statements for c in files.values())
        hits = tmpdir / "hits.csv"
        runtime = tmpdir / "runtime.q"
        runtime.write_text(runtime_source(total, hits))
        code = run_suite(shadow, runner, runtime, env or {})
        collect(files, hits)
    return files, code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scripts/qcov.py",
        description="Statement-level code coverage for q.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Instruments a throwaway copy of the tree, runs the suite against\n"
            "it and reports which statements never executed. The originals are\n"
            "never written to."
        ),
    )
    parser.add_argument(
        "--runner",
        default="tests/run_tests.q",
        help="q script that runs the suite (default: tests/run_tests.q)",
    )
    parser.add_argument(
        "--source",
        action="append",
        default=None,
        help="path to instrument, repeatable (default: src)",
    )
    parser.add_argument(
        "--format",
        choices=("term", "term-missing", "lcov", "json"),
        default="term-missing",
    )
    parser.add_argument("--output", type=Path, help="write the report here instead of stdout")
    parser.add_argument(
        "--fail-under",
        type=float,
        default=None,
        help="exit 2 if total coverage is below this percentage",
    )
    args = parser.parse_args(argv)

    sources = tuple(args.source) if args.source else DEFAULT_SOURCES
    files, suite_code = measure(REPO, [args.runner], sources)

    if args.format == "lcov":
        text = report_lcov(files)
    elif args.format == "json":
        text = report_json(files)
    else:
        text = report_terminal(files, show_missing=args.format == "term-missing")

    if args.output:
        args.output.write_text(text if text.endswith("\n") else text + "\n")
        print(f"qcov: wrote {args.output}")
    else:
        print(text)

    total = sum(c.statements for c in files.values())
    covered = sum(c.covered for c in files.values())
    pct = 100.0 if not total else 100.0 * covered / total

    if suite_code != 0:
        print(
            f"\nqcov: the suite exited {suite_code}; coverage above is still real.", file=sys.stderr
        )
    if args.fail_under is not None and pct < args.fail_under:
        print(f"qcov: {pct:.1f}% is below --fail-under {args.fail_under}", file=sys.stderr)
        return 2
    return suite_code


if __name__ == "__main__":
    raise SystemExit(main())
