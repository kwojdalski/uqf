#!/usr/bin/env python3
"""Rewrite q so it needs no nested working contexts, for kdb+ 4.0 (#856).

KDB-X 5.0 accepts `\\d .qetl.status`; kdb+ 4.0 does not. This writes a copy of
the selected files in which every nested context block runs at the root and
names everything it means explicitly:

    \\d .example.inner                \\d .
    offset:2                     ->  .example.inner.offset:2
    add:{[amount] amount+offset}     .example.inner.add:{[amount] amount+.example.inner.offset}
    \\d .                             \\d .

Qualifying the definitions is not enough. A lambda's unqualified globals bind
to the context it was defined in, so `offset` inside `add` meant
`.example.inner.offset` and has to say so once the context is gone. That
includes a global the lambda amends in place (`seen[k]:v`, `n+:1`) or assigns
with `::`. Everything else is left alone: parameters, locals, implicit x/y/z,
builtins, absolute names, and whatever is inside comments, strings and
symbols. The nested `\\d` line becomes `\\d .`, so every line keeps its number.

WHAT IS NOT REWRITTEN, AND WHY. A name looked up by symbol or string at run
time - `get`/`set`/`value` of `` `x ``, `` `t insert ``, `@[`x;...]`,
`value "code"` - resolves against the working context current WHEN IT RUNS,
not the one its lambda was defined in. After load that is the root either
way, so such a lookup inside a function means the same before and after. It
differs only while the file is LOADING, when the original ran with the nested
context active. So the tool follows every call made at load time - by a
top-level statement in a nested context, through the functions it reaches in
the inputs - and refuses one that reaches such a lookup.

REFUSALS. It lexes and parses rather than pattern-matching, and refuses
rather than emit a partial conversion, naming the file, line, column and
reason:

  - load-time code in a nested context that looks a relative name up by
    symbol or string, directly or through a function it calls;
  - load-time code that looks up a COMPUTED name (`get n`, `n set v`), whose
    meaning depends on its value - `--allow-computed-names` turns these into
    reported warnings instead;
  - a qSQL column phrase naming something its context also defines: a column
    if the table has one, else the context's global;
  - `\\l` or another system command inside a nested context, a context switch
    hidden in a string, a dotted name relative to the context, assigning a
    reserved word, and `name::` where `name` is also local.

`--target 5.0` runs it backwards: a block that starts at `\\d .` and ends at
the next `\\d` line, and whose definitions all belong to one nested context,
goes back inside that context, `.example.inner.offset` becoming `offset`
wherever the bare name binds the same. A block that would mean anything
different there - a free name that is the root's, a qSQL name that could be a
root global, a run-time lookup while the file loads - stays flat and the
report says why: flat code is valid on 5.0. Native nested code is left as
written. Converting the result back to 4.0 gives exactly the 4.0 output.

Only the selected inputs are read and the originals are never written.
Output goes to a staging directory beside `--out` and is moved into place
only when every file converted and every `--check` passed, so a failure
publishes nothing; `--dry-run` writes nothing at all. Output is deterministic,
and converting it again changes nothing.

Flattening contexts is not 4.0 compatibility: other syntax and runtime
differences are not checked. The report says so, and only a `--check` run on
the target interpreter is evidence that the result works.

    python3 scripts/portable/flatten_contexts.py src --out build/portable --dry-run
    python3 scripts/portable/flatten_contexts.py src --out build/portable \\
        --check scripts/portable/checks/status_intervals.q --q "$Q4"
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeGuard

REPO = Path(__file__).resolve().parents[2]
TARGETS = ("4.0", "5.0")
#: Vendored trees: never edited, so never converted unless asked for.
VENDORED = ("lib/",)
#: What a --check script prints when it passed. The exit code alone is not
#: evidence: q exits 0 after a script that errored part-way.
CHECK_OK = "PORTABLE_CHECK_OK"
CHECK_FAILED = "PORTABLE_CHECK_FAILED"
CLAIMS = {
    "4.0": "nested working contexts flattened; nothing else about kdb+ 4.0 compatibility "
    "is checked - only a --check run on the target interpreter shows the result works",
    "5.0": "flattened blocks nested again where every name keeps its binding; the rest "
    "is left as written - only a --check run on the target interpreter shows the result works",
}

#: Names q's parser owns: never a context's global, so never qualified (and
#: assigning one is refused). kdb+ 4.0's `.q` namespace and `.Q.res`, plus the
#: qSQL words.
BUILTINS = frozenset(
    """abs acos aj aj0 ajf ajf0 all and any asc asin asof atan attr avg avgs bin binr by
    ceiling cols cor cos count cov cross csv cut delete deltas desc dev differ distinct div
    do dsave each ej ema enlist eval except exec exit exp fby fills first fkeys flip floor
    from get getenv group gtime hclose hcount hdel hopen hsym iasc idesc if ij ijf in insert
    inter inv key keys last like lj ljf load log lower lsq ltime ltrim mavg max maxs mcount
    md5 mdev med meta min mins mmax mmin mmu mod msum neg next not null or over parse peach
    pj prd prds prev prior rand rank ratios raze read0 read1 reciprocal reval reverse rload
    rotate rsave rtrim save scan scov sdev select set setenv show signum sin sqrt ss ssr
    string sublist sum sums sv svar system tables tan til trim type uj ujf ungroup union
    update upper upsert value var view views vs wavg where while within wj wj1 wsum xasc
    xbar xcol xcols xdesc xexp xgroup xkey xlog xprev xrank""".split()
)
QSQL_START = frozenset({"select", "exec", "update", "delete"})
IMPLICIT = frozenset({"x", "y", "z"})
#: Keywords that apply the function on their left.
ITERATORS = frozenset({"each", "peach", "over", "scan", "prior"})
#: Single-character verbs that form a modified assignment: `a+:1`, `a,:x`.
ASSIGN_OPS = frozenset("+-*%&|^=<>,#_~!?@.$")
#: How a construct resolves a name at run time, least to most context-bound.
COMPUTED, RELATIVE = 1, 2


# ------------------------------------------------------------------ lexing


@dataclass
class Tok:
    kind: str  # ws nl comment block tail sys str sym num name op
    text: str
    line: int
    col: int


class LexError(Exception):
    def __init__(self, line: int, col: int, reason: str) -> None:
        super().__init__(reason)
        self.line, self.col, self.reason = line, col, reason


def _name_start(c: str) -> bool:
    return c.isascii() and c.isalpha()


def _name_char(c: str) -> bool:
    return c.isascii() and (c.isalnum() or c == "_")


def lex(text: str) -> list[Tok]:
    """q source as tokens; joining every token's text gives back `text`.

    One pass over the whole text, because a string may run over several
    lines. What a line holds is decided at its start - a block comment, the
    exit line, a comment or a system command - unless a string is still open
    there."""
    toks: list[Tok] = []
    i, n, line, bol = 0, len(text), 1, 0

    def add(kind: str, j: int) -> None:
        nonlocal line, bol
        piece = text[i:j]
        toks.append(Tok(kind, piece, line, i - bol))
        if "\n" in piece:
            line += piece.count("\n")
            bol = i + piece.rindex("\n") + 1

    def eol(j: int) -> int:
        k = text.find("\n", j)
        return n if k < 0 else k

    while i < n:
        if i == bol:
            body = text[i : eol(i)].rstrip("\r")
            bare = body.rstrip(" \t")
            if bare == "/":
                # A block comment runs to a line holding only `\`, inclusive.
                j = eol(i)
                while j < n:
                    k = eol(j + 1)
                    if text[j + 1 : k].rstrip("\r").rstrip(" \t") == "\\":
                        j = k
                        break
                    j = k
                add("block", j)
                i = j
                continue
            if bare == "\\":
                # A lone `\` ends the script: q reads nothing after it.
                add("tail", n)
                i = n
                continue
            if body[:1] in ("/", "\\"):
                j = i + len(body)
                add("comment" if body[0] == "/" else "sys", j)
                i = j
                continue
        c = text[i]
        if c in "\r\n":
            j = i + 1 if c == "\n" else i + (2 if text[i : i + 2] == "\r\n" else 1)
            add("nl", j)
        elif c in " \t":
            j = i
            while j < n and text[j] in " \t":
                j += 1
            add("ws", j)
            if j < n and text[j] == "/":
                i = j
                add("comment", eol(j))
                i = eol(j)
                continue
            i = j
            continue
        elif c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            if j >= n:
                raise LexError(line, i - bol, "a string is never closed")
            add("str", j + 1)
            j += 1
        elif c == "`":
            j = i + 1
            while j < n and (_name_char(text[j]) or text[j] in ".:/"):
                j += 1
            add("sym", j)
        elif c.isdigit() or (c == "." and i + 1 < n and text[i + 1].isdigit()):
            # Numbers, dates and times: digits, letters, `.` and `:` - but not
            # `_`, so `1_x` stays a drop of x.
            j = i + 1
            while j < n and (text[j].isalnum() or text[j] in ".:"):
                if text[j] in "eE" and j + 2 < n and text[j + 1] in "+-" and text[j + 2].isdigit():
                    j += 2
                j += 1
            add("num", j)
        elif _name_start(c) or (c == "." and i + 1 < n and _name_start(text[i + 1])):
            j = i + 1
            while j < n and (
                _name_char(text[j]) or (text[j] == "." and j + 1 < n and _name_char(text[j + 1]))
            ):
                j += 1
            add("name", j)
        elif text[i : i + 2] in ("::", "/:", "\\:", "':"):
            j = i + 2
            add("op", j)
        else:
            j = i + 1
            add("op", j)
        i = j
    return toks


# ----------------------------------------------------------------- parsing


@dataclass
class Finding:
    path: str
    line: int
    col: int
    code: str
    reason: str

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "line": self.line,
            "column": self.col + 1,
            "code": self.code,
            "reason": self.reason,
        }


@dataclass
class Lambda:
    explicit: bool
    parent: Lambda | None = None
    applied: bool = False  # `{...}[x]`, `{...} each x`: it runs where it is written
    params: set[str] = field(default_factory=set)
    assigned: set[str] = field(default_factory=set)
    span: tuple[int, int] = (0, 0)  # token indices of its braces

    def runs_inline(self) -> bool:
        """Its body runs when the statement holding it runs: it, and every
        lambda around it, is applied where it is written."""
        lam: Lambda | None = self
        while lam is not None:
            if not lam.applied:
                return False
            lam = lam.parent
        return True

    def is_local(self, name: str) -> bool:
        return (
            name in self.params or name in self.assigned or (not self.explicit and name in IMPLICIT)
        )


@dataclass
class Use:
    idx: int  # into the file's tokens
    lam: Lambda | None  # None: the statement's own level, outside any lambda
    role: str  # ref assign gassign compound indexed column
    column_ctx: bool  # inside a qSQL column phrase
    called: bool = False  # applied to arguments, not just referred to


@dataclass
class Lookup:
    """A name resolved at run time by symbol or string."""

    idx: int
    lam: Lambda | None
    level: int  # COMPUTED or RELATIVE
    what: str


@dataclass
class Frame:
    kind: str  # top paren bracket lambda table tablekey
    lam: Lambda | None
    qsql: str | None = None  # cols by from where
    seg_start: bool = False


def _significant(toks: list[Tok], lo: int, hi: int) -> list[int]:
    return [i for i in range(lo, hi) if toks[i].kind not in ("ws", "nl", "comment", "block")]


def _relative_symbol(tok: Tok | None) -> TypeGuard[Tok]:
    return tok is not None and tok.kind == "sym" and len(tok.text) > 1 and _name_start(tok.text[1])


def _relative(name: str) -> bool:
    return not name.startswith(".")


class Statement:
    """One top-level statement: how each name in it is used, and every run-time
    lookup in it. Binding is decided later, by the caller, which knows the
    context."""

    def __init__(self, toks: list[Tok], lo: int, hi: int, path: str) -> None:
        self.toks = toks
        self.path = path
        self.sig = _significant(toks, lo, hi)
        self.uses: list[Use] = []
        self.lookups: list[Lookup] = []
        self.lambdas: list[Lambda] = []
        self.refusals: list[Finding] = []
        self._match = self._bracket_matches()
        if not self.refusals:
            self._walk()

    def definition(self) -> str | None:
        """The name, when the whole statement is `name:{...}` - a definition
        that runs nothing while the file loads."""
        s = self.sig
        if len(s) < 4 or self.toks[s[0]].kind != "name":
            return None
        if self.toks[s[1]].text != ":" or self.toks[s[2]].text != "{":
            return None
        end = len(s) - 1
        if self.toks[s[end]].text == ";":
            end -= 1
        return self.toks[s[0]].text if self._match.get(s[2]) == s[end] else None

    def _refuse(self, idx: int, code: str, reason: str) -> None:
        t = self.toks[idx]
        self.refusals.append(Finding(self.path, t.line, t.col, code, reason))

    def _bracket_matches(self) -> dict[int, int]:
        pairs = {"(": ")", "[": "]", "{": "}"}
        stack: list[int] = []
        match: dict[int, int] = {}
        for idx in self.sig:
            t = self.toks[idx]
            if t.kind != "op":
                continue
            if t.text in pairs:
                stack.append(idx)
            elif t.text in pairs.values():
                if not stack or pairs[self.toks[stack[-1]].text] != t.text:
                    self._refuse(idx, "unbalanced", f"unmatched `{t.text}`")
                    return {}
                match[stack.pop()] = idx
        if stack:
            self._refuse(stack[-1], "unbalanced", f"`{self.toks[stack[-1]].text}` is never closed")
        return match

    def _at(self, pos: int) -> Tok | None:
        return self.toks[self.sig[pos]] if 0 <= pos < len(self.sig) else None

    def _assigns_after(self, pos: int) -> bool:
        """`:`, `::` or a modified assignment such as `,:` starts at pos."""
        t = self._at(pos)
        if t is None or t.kind != "op":
            return False
        if t.text in (":", "::"):
            return True
        nxt = self._at(pos + 1)
        return (
            t.text in ASSIGN_OPS
            and nxt is not None
            and nxt.text == ":"
            and self.sig[pos + 1] == self.sig[pos] + 1
        )

    def _role(self, pos: int, frame: Frame) -> str:
        nxt = self._at(pos + 1)
        if nxt is None or nxt.kind != "op":
            return "ref"
        if (
            frame.seg_start
            and nxt.text == ":"
            and (frame.kind in ("table", "tablekey") or frame.qsql in ("cols", "by"))
        ):
            return "column"
        if nxt.text == "::":
            return "gassign"
        if nxt.text == ":":
            return "assign"
        if self._assigns_after(pos + 1):
            return "compound"
        if nxt.text == "[":
            close = self._match.get(self.sig[pos + 1])
            if close is not None and self._assigns_after(self.sig.index(close) + 1):
                return "indexed"
        return "ref"

    def _called(self, pos: int) -> bool:
        """The name is applied: `f[x]`, `f x`, `f each x`, `f/`, `f@x`. A
        name followed by `;`, a closing bracket or a verb is only referred
        to - passed as a value, which runs nothing."""
        prev, before = self._at(pos - 1), self._at(pos - 2)
        if prev is not None and prev.text == "[" and before is not None:
            if before.text in ("@", "."):
                return True  # @[f;x;handler] and .[f;args;handler] apply f
        nxt = self._at(pos + 1)
        if nxt is None:
            return False
        if nxt.kind == "name" and nxt.text in BUILTINS:
            # `f each x` applies f; `x in y` uses x as data.
            return nxt.text in ITERATORS
        if nxt.kind in ("name", "num", "str", "sym"):
            return True
        return nxt.text in ("[", "(", "{", "'", "/", "\\", "/:", "\\:", "':", "@", ".")

    def _operand(self, pos: int, step: int) -> list[Tok]:
        """The tokens of the operand beside pos: to the right, everything up
        to the end of the expression (q reads right to left); to the left,
        the one noun before - a name, a literal, or a bracketed group."""
        out: list[Tok] = []
        if step > 0:
            depth, j = 0, pos + 1
            while j < len(self.sig):
                t = self.toks[self.sig[j]]
                if t.text in ("(", "[", "{"):
                    depth += 1
                elif t.text in (")", "]", "}"):
                    if depth == 0:
                        break
                    depth -= 1
                elif t.text == ";" and depth == 0:
                    break
                out.append(t)
                j += 1
            return out
        j = pos - 1
        t = self._at(j)
        if t is None:
            return out
        if t.text in (")", "]"):
            opener = {v: k for k, v in self._match.items()}[self.sig[j]]
            k = self.sig.index(opener)
            out = [self.toks[i] for i in self.sig[k : j + 1]]
            before = self._at(k - 1)
            if t.text == "]" and before is not None and before.kind == "name":
                out.insert(0, before)
            return out
        return [t]

    @staticmethod
    def _absolute(operand: list[Tok]) -> bool:
        """A computed name that is absolute whatever its value: a file handle
        (`hsym`, `` `:path ``) or one joined onto an absolute symbol with sv."""
        texts = [t.text for t in operand]
        if "hsym" in texts or any(t.kind == "sym" and t.text.startswith("`:") for t in operand):
            return True
        return "sv" in texts and any(t.kind == "sym" and t.text.startswith("`.") for t in operand)

    def _column_ctx(self, stack: list[Frame]) -> bool:
        for f in reversed(stack):
            if f.qsql in ("cols", "by", "where"):
                return True
            if f.kind == "lambda":
                return False
        return False

    def _lookup(self, pos: int, lam: Lambda | None) -> None:
        """Record a name resolved at run time. A literal relative symbol or a
        code string is RELATIVE; a name only known at run time is COMPUTED."""
        idx = self.sig[pos]
        name = self.toks[idx].text
        prev, nxt = self._at(pos - 1), self._at(pos + 1)
        arg = self._at(pos + 2) if nxt is not None and nxt.text == "[" else nxt

        def note(level: int, what: str) -> None:
            self.lookups.append(Lookup(idx, lam, level, what))

        if name in ("get", "value", "eval", "reval", "parse"):
            if _relative_symbol(arg):
                note(RELATIVE, f"{name} {arg.text}")
            elif arg is not None and arg.kind == "str" and name != "get":
                note(RELATIVE, f"{name} of a code string")
            elif name == "get" and (arg is None or arg.kind != "sym"):
                if not self._absolute(self._operand(pos, 1)):
                    note(COMPUTED, "get of a computed name")
        elif name in ("set", "insert", "upsert"):
            if _relative_symbol(prev):
                note(RELATIVE, f"{prev.text} {name}")
            elif name == "set" and (prev is None or prev.kind != "sym"):
                if not self._absolute(self._operand(pos, -1)):
                    note(COMPUTED, "set of a computed name")
        elif name == "system" and arg is not None and arg.kind == "str":
            cmd = arg.text[1:].lstrip()
            if cmd[:1] in ("d", "l") and (len(cmd) == 1 or not _name_char(cmd[1])):
                note(RELATIVE, f"system {arg.text}")

    def _walk(self) -> None:
        stack = [Frame("top", None, seg_start=True)]
        pos = 0
        while pos < len(self.sig):
            idx = self.sig[pos]
            t = self.toks[idx]
            f = stack[-1]
            if t.kind == "name":
                self._name(pos, f, stack)
            elif t.kind == "op":
                pos = self._op(pos, f, stack)
            else:
                f.seg_start = False
            pos += 1

    def _name(self, pos: int, f: Frame, stack: list[Frame]) -> None:
        idx = self.sig[pos]
        name = self.toks[idx].text
        keyword = {
            ("cols", "from"): "from",
            ("by", "from"): "from",
            ("cols", "by"): "by",
            ("from", "where"): "where",
        }.get((f.qsql or "", name))
        if name in QSQL_START:
            f.qsql, f.seg_start = "cols", True
            return
        if keyword is not None:
            f.qsql, f.seg_start = keyword, keyword != "from"
            return
        if _relative(name) and "." in name:
            self._refuse(
                idx,
                "relative-dotted",
                f"`{name}` is a dotted name relative to the context; write it absolute",
            )
        self._lookup(pos, f.lam)
        role = self._role(pos, f)
        self.uses.append(Use(idx, f.lam, role, self._column_ctx(stack), self._called(pos)))
        if role == "assign" and f.lam is not None and _relative(name):
            f.lam.assigned.add(name)
        f.seg_start = False

    def _op(self, pos: int, f: Frame, stack: list[Frame]) -> int:
        idx = self.sig[pos]
        text = self.toks[idx].text
        nxt = self._at(pos + 1)
        if text == "{":
            close = self._match[idx]
            lam = Lambda(explicit=False, parent=f.lam, applied=self._called(self.sig.index(close)))
            lam.span = (idx, close)
            self.lambdas.append(lam)
            if nxt is not None and nxt.text == "[" and self.sig[pos + 1] == idx + 1:
                lam.explicit = True
                close = self._match[self.sig[pos + 1]]
                while self.sig[pos] != close:
                    pos += 1
                    p = self.toks[self.sig[pos]]
                    if p.kind == "name":
                        lam.params.add(p.text)
            stack.append(Frame("lambda", lam))
            return pos
        if text == "(":
            table = nxt is not None and nxt.text == "["
            stack.append(Frame("table" if table else "paren", f.lam))
            return pos
        if text == "[":
            prev = self._at(pos - 1)
            if f.kind == "table" and prev is not None and prev.text == "(":
                stack.append(Frame("tablekey", f.lam, seg_start=True))
            else:
                stack.append(Frame("bracket", f.lam))
            return pos
        if text in (")", "]", "}"):
            if stack.pop().kind == "tablekey":
                stack[-1].seg_start = True
            return pos
        if text == ";":
            f.qsql, f.seg_start = None, True
            return pos
        if text == "," and f.qsql in ("cols", "by", "where"):
            f.seg_start = True
            return pos
        if text in ("@", ".") and nxt is not None and nxt.text == "[":
            arg = self._at(pos + 2)
            if _relative_symbol(arg):
                self.lookups.append(Lookup(idx, f.lam, RELATIVE, f"{text}[{arg.text};...]"))
        f.seg_start = False
        return pos


# ---------------------------------------------------------------- converting


@dataclass
class Function:
    """A function defined in the inputs: what it calls, and the most
    context-bound run-time lookup it makes, directly or through a call."""

    path: str
    line: int
    level: int = 0
    why: str = ""
    calls: set[str] = field(default_factory=set)


@dataclass
class FileResult:
    path: str
    action: str = "unchanged"  # unchanged transformed
    output: str = ""
    contexts: list[dict] = field(default_factory=list)
    defined: dict[str, list[str]] = field(default_factory=dict)
    qualified: int = 0
    loads: list[dict] = field(default_factory=list)
    nested: list[dict] = field(default_factory=list)  # blocks the 5.0 target rebuilt
    notes: list[str] = field(default_factory=list)
    warnings: list[Finding] = field(default_factory=list)
    refusals: list[Finding] = field(default_factory=list)


def _context_of(cmd: str) -> str | None:
    """The context a `\\d` line switches to ("" for a bare `\\d`), or None
    for any other command."""
    parts = cmd.split(None, 1)
    if parts[0] != "\\d":
        return None
    return parts[1].strip() if len(parts) > 1 else ""


def _nested(ctx: str) -> bool:
    return ctx.startswith(".") and ctx.count(".") >= 2


def _qualified(ctx: str, name: str) -> str:
    """What a name means in a context: absolute names mean themselves."""
    if not _relative(name) or ctx in ("", "."):
        return name
    return f"{ctx}.{name}"


def _statements(toks: list[Tok]) -> list[tuple[int, int]]:
    """Top-level statements: each starts at a code token in column 0, or is
    one system line, and runs to the next."""
    begins = ("name", "num", "str", "sym", "op", "sys", "tail")
    starts = [i for i, t in enumerate(toks) if t.col == 0 and t.kind in begins]
    return list(zip(starts, starts[1:] + [len(toks)], strict=True))


def _contexts(toks: list[Tok]) -> Iterable[tuple[int, int, str]]:
    """Each statement with the working context it runs in."""
    ctx = "."
    for lo, hi in _statements(toks):
        yield lo, hi, ctx
        if toks[lo].kind == "sys":
            new = _context_of(toks[lo].text)
            if new:
                ctx = new


class Converter:
    def __init__(self, target: str, allow_computed: bool = False) -> None:
        self.target = target
        self.allow_computed = allow_computed
        #: context -> the names it defines, across every input
        self.defined: dict[str, set[str]] = {}
        #: absolute name -> what the function it holds looks up at run time
        self.functions: dict[str, Function] = {}

    # First pass: every file, before any is converted.

    def collect(self, path: str, text: str) -> None:
        try:
            toks = lex(text)
        except LexError:
            return
        for lo, hi, ctx in _contexts(toks):
            if toks[lo].kind in ("sys", "tail"):
                continue
            st = Statement(toks, lo, hi, path)
            if st.refusals:
                continue
            for use in st.uses:
                if use.role in ("assign", "gassign") and (use.lam is None or use.role == "gassign"):
                    full = _qualified(ctx, toks[use.idx].text)
                    if use.lam is not None and use.lam.is_local(toks[use.idx].text):
                        continue
                    parent, _, leaf = full.rpartition(".")
                    self.defined.setdefault(parent or ".", set()).add(leaf)
            name = st.definition()
            if name is not None:
                fn = Function(path, toks[lo].line)
                for lk in st.lookups:
                    if lk.level > fn.level:
                        fn.level, fn.why = lk.level, f"{lk.what} (line {toks[lk.idx].line})"
                fn.calls = self._calls(st, ctx)
                self.functions[_qualified(ctx, name)] = fn

    def _calls(self, st: Statement, ctx: str, inline_only: bool = False) -> set[str]:
        """The absolute names a statement's code applies - so may run. With
        inline_only, only what runs as the statement itself runs: not the
        body of a lambda it merely defines or passes on."""
        out = set()
        for use in st.uses:
            name = st.toks[use.idx].text
            if use.role != "ref" or not use.called or use.column_ctx or name in BUILTINS:
                continue
            if use.lam is not None and use.lam.is_local(name):
                continue
            if inline_only and use.lam is not None and not use.lam.runs_inline():
                continue
            out.add(_qualified(ctx, name))
        return out

    def propagate(self) -> None:
        """A function is as context-bound as the most context-bound function
        it calls, to a fixed point."""
        changed = True
        while changed:
            changed = False
            for fn in self.functions.values():
                for callee in sorted(fn.calls):
                    other = self.functions.get(callee)
                    if other is not None and other.level > fn.level:
                        fn.level, fn.why = other.level, f"calls {callee}: {other.why}"
                        changed = True

    # Second pass: one file.

    def convert(self, path: str, text: str) -> FileResult:
        res = FileResult(path)
        try:
            toks = lex(text)
        except LexError as e:
            res.refusals.append(Finding(path, e.line, e.col, "lex", e.reason))
            return res
        out = [t.text for t in toks]
        ctx = "."
        for lo, hi, ctx in _contexts(toks):
            head = toks[lo]
            if head.kind == "tail":
                continue
            if head.kind == "sys":
                self._system(res, out, lo, head, ctx)
                continue
            if self.target == "4.0":
                self._strings(res, toks, lo, hi)
                if _nested(ctx):
                    self._rewrite(res, out, toks, lo, hi, ctx)
        if self.target == "5.0":
            self._nest(res, out, toks)
        last = ctx
        if toks and toks[-1].kind == "sys" and _context_of(toks[-1].text):
            last = _context_of(toks[-1].text) or ctx
        if self.target == "4.0" and _nested(last):
            res.notes.append(f"ends inside {last}; the converted file ends at the root")
        if res.refusals:
            return res
        res.output = "".join(out)
        if res.output != text:
            res.action = "transformed"
        return res

    # The 5.0 target: the 4.0 conversion run backwards, where it provably can be.

    def _nest(self, res: FileResult, out: list[str], toks: list[Tok]) -> None:
        """Rebuild nested contexts. A candidate block starts at a `\\d .` line
        and runs to the next `\\d` line, so nesting it moves no line; one that
        ends anywhere else - another system command, the exit line, the end of
        the file - stays flat, which 5.0 runs as it is."""
        stmts = list(_contexts(toks))
        i = 0
        while i < len(stmts):
            lo = stmts[i][0]
            if toks[lo].kind != "sys" or _context_of(toks[lo].text) != ".":
                i += 1
                continue
            j = i + 1
            while j < len(stmts) and toks[stmts[j][0]].kind not in ("sys", "tail"):
                j += 1
            closes = j < len(stmts) and _context_of(toks[stmts[j][0]].text) is not None
            if closes and j > i + 1:
                self._nest_block(res, out, toks, lo, stmts[i + 1 : j])
            i = j

    def _nest_block(
        self,
        res: FileResult,
        out: list[str],
        toks: list[Tok],
        head: int,
        block: list[tuple[int, int, str]],
    ) -> None:
        """Nest one root block in the context its definitions share, or leave
        it exactly as it is."""
        sts = [Statement(toks, lo, hi, res.path) for lo, hi, _ in block]
        if any(st.refusals for st in sts):
            return
        homes = set()
        for st in sts:
            for use in st.uses:
                name = toks[use.idx].text
                if use.lam is None and use.role in ("assign", "gassign", "compound", "indexed"):
                    if _relative(name):
                        return  # a root global, which the block would take over
                    parent = name.rpartition(".")[0]
                    if _nested(parent):
                        homes.add(parent)
        if len(homes) != 1:
            if len(homes) > 1:
                res.notes.append(
                    f"line {toks[head].line}: stays flat - it defines in {', '.join(sorted(homes))}"
                )
            return
        ctx = homes.pop()
        for st in sts:
            why = self._unmovable(st, ctx)
            if why is not None:
                res.notes.append(f"line {toks[head].line}: stays flat - {why}")
                return
        shortened = 0
        for st in sts:
            for use in st.uses:
                leaf = self._short(st, use, ctx)
                if leaf is not None:
                    out[use.idx] = leaf
                    shortened += 1
        out[head] = f"\\d {ctx}"
        res.nested.append(
            {
                "line": toks[head].line,
                "context": ctx,
                "statements": len(sts),
                "shortened": shortened,
            }
        )

    def _unmovable(self, st: Statement, ctx: str) -> str | None:
        """Why running inside `ctx` instead of the root would change what the
        statement means, or None when it would not."""
        toks = st.toks
        for use in st.uses:
            name = toks[use.idx].text
            if not _relative(name) or name in BUILTINS or name in QSQL_START:
                continue
            if use.role == "column" or (use.lam is not None and use.lam.is_local(name)):
                continue
            if use.role == "assign" and use.lam is not None:
                continue
            line = toks[use.idx].line
            if use.column_ctx and name != "i":
                # A column, or else a global: the root's here, ctx's there.
                if name in self.defined.get(".", set()) or name in self.defined.get(ctx, set()):
                    return (
                        f"`{name}` in a qSQL phrase (line {line}) is a column or, failing "
                        f"that, a global the inputs define"
                    )
                continue
            if use.column_ctx:
                continue
            return f"`{name}` (line {line}) means the root's, and would mean {ctx}.{name}"
        if st.definition() is None:
            # It runs while the file loads: its run-time lookups would resolve
            # against ctx instead of the root.
            for lk in st.lookups:
                if lk.lam is None or lk.lam.runs_inline():
                    return f"{lk.what} (line {toks[lk.idx].line}) runs while the file loads"
            for callee in sorted(self._calls(st, ".", inline_only=True)):
                fn = self.functions.get(callee)
                if fn is not None and fn.level:
                    return f"it calls {callee} while the file loads ({fn.why})"
        return None

    @staticmethod
    def _short(st: Statement, use: Use, ctx: str) -> str | None:
        """The bare name `ctx.leaf` can become where it binds the same, else None."""
        name = st.toks[use.idx].text
        if not name.startswith(ctx + "."):
            return None
        leaf = name[len(ctx) + 1 :]
        if "." in leaf or leaf in BUILTINS or leaf in QSQL_START or leaf in ("from", "by", "where"):
            return None
        if use.column_ctx or use.role == "column":
            return None  # bare, it could be a column
        if use.lam is not None:
            if use.lam.is_local(leaf):
                return None  # bare, it is the local
            if use.role == "assign":
                return None  # bare, the assignment would make it local
        return leaf

    def _system(self, res: FileResult, out: list[str], idx: int, t: Tok, ctx: str) -> None:
        new = _context_of(t.text)
        if new is not None:
            if new and not new.startswith("."):
                res.refusals.append(
                    Finding(
                        res.path, t.line, 0, "context", f"`{t.text}` is not an absolute context"
                    )
                )
                return
            res.contexts.append(
                {"line": t.line, "context": new or "(query)", "nested": _nested(new)}
            )
            if self.target == "4.0" and _nested(new):
                out[idx] = "\\d ."
            return
        parts = t.text.split(None, 1)
        if parts[0] == "\\l":
            res.loads.append({"line": t.line, "load": parts[1].strip() if len(parts) > 1 else ""})
        if self.target == "4.0" and _nested(ctx):
            res.refusals.append(
                Finding(
                    res.path,
                    t.line,
                    0,
                    "system-in-context",
                    f"`{t.text}` runs inside nested context {ctx}; move it outside the block",
                )
            )

    def _strings(self, res: FileResult, toks: list[Tok], lo: int, hi: int) -> None:
        """A switch into a nested context hidden in a string can't be tracked."""
        for t in toks[lo:hi]:
            if t.kind != "str":
                continue
            body = t.text[1:-1].replace("\\\\", "\\").lstrip().removeprefix("\\")
            parts = body.split(None, 1)
            if len(parts) == 2 and parts[0] == "d" and _nested(parts[1].strip()):
                res.refusals.append(
                    Finding(
                        res.path,
                        t.line,
                        t.col,
                        "context-in-string",
                        f"{t.text} switches to a nested context at run time",
                    )
                )

    def _hazard(self, res: FileResult, tok: Tok, level: int, reason: str) -> None:
        """A lookup that runs while the file loads, in a nested context."""
        if level == RELATIVE:
            res.refusals.append(Finding(res.path, tok.line, tok.col, "load-time-lookup", reason))
        elif self.allow_computed:
            res.warnings.append(Finding(res.path, tok.line, tok.col, "load-time-computed", reason))
        else:
            res.refusals.append(
                Finding(
                    res.path,
                    tok.line,
                    tok.col,
                    "load-time-computed",
                    reason + "; pass --allow-computed-names to accept it as a warning",
                )
            )

    def _rewrite(
        self, res: FileResult, out: list[str], toks: list[Tok], lo: int, hi: int, ctx: str
    ) -> None:
        st = Statement(toks, lo, hi, res.path)
        res.refusals.extend(st.refusals)
        if st.refusals:
            return
        mine = self.defined.get(ctx, set())
        runs_now = st.definition() is None
        if runs_now:
            # Everything here, inline lambdas included, may run while `ctx`
            # is the working context - which the conversion takes away.
            for lk in st.lookups:
                if lk.lam is not None and not lk.lam.runs_inline():
                    continue
                self._hazard(
                    res,
                    toks[lk.idx],
                    lk.level,
                    f"{lk.what} runs while the file loads, inside {ctx}, where it resolves "
                    f"against {ctx}; after conversion it resolves against the root",
                )
            for callee in sorted(self._calls(st, ctx, inline_only=True)):
                fn = self.functions.get(callee)
                if fn is not None and fn.level:
                    first = next(
                        u
                        for u in st.uses
                        if u.called and _qualified(ctx, toks[u.idx].text) == callee
                    )
                    self._hazard(
                        res,
                        toks[first.idx],
                        fn.level,
                        f"calls {callee} while the file loads, inside {ctx}; it resolves a name "
                        f"at run time ({fn.why}), against {ctx} here but the root once converted",
                    )
        for use in st.uses:
            self._bind(res, out, toks, use, ctx, mine)

    def _bind(
        self,
        res: FileResult,
        out: list[str],
        toks: list[Tok],
        use: Use,
        ctx: str,
        mine: set[str],
    ) -> None:
        t = toks[use.idx]
        name = t.text
        if not _relative(name) or use.role == "column":
            return

        def refuse(code: str, reason: str) -> None:
            res.refusals.append(Finding(res.path, t.line, t.col, code, reason))

        def qualify() -> None:
            out[use.idx] = f"{ctx}.{name}"
            res.qualified += 1

        if use.role != "ref" and name in BUILTINS:
            refuse("reserved", f"assigns `{name}`, which q reserves")
            return
        local = use.lam is not None and use.lam.is_local(name)
        if use.role == "assign":
            if use.lam is None:
                qualify()
                res.defined.setdefault(ctx, []).append(name)
            return
        if use.role == "gassign":
            if local:
                refuse("ambiguous-assign", f"`{name}::` in a lambda where `{name}` is also local")
            else:
                qualify()
                if use.lam is None:
                    res.defined.setdefault(ctx, []).append(name)
            return
        if use.role in ("compound", "indexed"):
            # Amending a name the lambda never assigns plainly amends the
            # context's global, as tick's `w[x],:` does under `\d .u`.
            if not local:
                qualify()
            return
        if local or name in BUILTINS or name in QSQL_START:
            return
        if use.column_ctx:
            if name == "i":
                return
            if name in mine:
                refuse(
                    "ambiguous-qsql",
                    f"`{name}` in a qSQL phrase is a column if the table has one, else "
                    f"{ctx}.{name}; write {ctx}.{name} if the global is meant",
                )
            else:
                res.notes.append(
                    f"line {t.line}: `{name}` in a qSQL phrase is taken to be a column "
                    f"(no input defines {ctx}.{name})"
                )
            return
        qualify()


# --------------------------------------------------------------- selection


def _git_files(directory: Path) -> list[Path] | None:
    r = subprocess.run(
        [
            "git",
            "-C",
            str(directory),
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        return None
    return [directory / p for p in r.stdout.split("\0") if p]


def _vendored(rel: Path) -> bool:
    return any(rel.as_posix().startswith(v) for v in VENDORED)


def select_inputs(
    root: Path, paths: Sequence[str], include_vendored: bool
) -> tuple[list[Path], list[str]]:
    """The .q files to convert, and why any named path was refused. A
    directory gives the files git does not ignore; a file named explicitly is
    taken even if git ignores it, so a local bundle can be selected."""
    chosen: set[Path] = set()
    problems: list[str] = []
    for raw in paths:
        p = Path(raw).resolve()
        if not p.exists():
            problems.append(f"{raw}: does not exist")
            continue
        if not p.is_relative_to(root):
            problems.append(f"{raw}: outside the root {root}; pass --root")
            continue
        if p.is_file():
            if not include_vendored and _vendored(p.relative_to(root)):
                problems.append(f"{raw}: vendored; pass --include-vendored to convert it")
            else:
                chosen.add(p)
            continue
        files = _git_files(p)
        for f in sorted(p.rglob("*.q")) if files is None else files:
            f = f.resolve()
            if f.suffix == ".q" and f.is_file():
                if include_vendored or not _vendored(f.relative_to(root)):
                    chosen.add(f)
    return sorted(chosen), problems


# --------------------------------------------------------------------- run


def run_checks(stage: Path, checks: Iterable[Path], q: str, timeout: int) -> list[dict]:
    """Each check script, run with the converted tree as its working
    directory. Passing needs a zero exit, the OK marker, and no failure
    marker."""
    results = []
    for script in checks:
        try:
            r = subprocess.run(
                [q, str(script), "-q"],
                cwd=stage,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            output, code = r.stdout + r.stderr, r.returncode
            passed = code == 0 and CHECK_OK in r.stdout and CHECK_FAILED not in output
        except (OSError, subprocess.TimeoutExpired) as e:
            output, code, passed = str(e), None, False
        results.append(
            {
                "script": str(script),
                "q": q,
                "passed": passed,
                "exit_code": code,
                "output": output.strip().splitlines()[-20:],
            }
        )
    return results


def _publish(stage: Path, out: Path) -> None:
    if out.exists():
        old = out.with_name(out.name + ".replaced")
        if old.exists():
            shutil.rmtree(old)
        out.rename(old)
        stage.rename(out)
        shutil.rmtree(old)
    else:
        stage.rename(out)


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("paths", nargs="+", help="q files or directories to convert")
    ap.add_argument("--out", required=True, help="directory to write the converted tree to")
    ap.add_argument(
        "--target",
        choices=TARGETS,
        default="4.0",
        help="4.0 flattens nested contexts; 5.0 nests flattened blocks again where it safely can",
    )
    ap.add_argument("--root", default=str(REPO), help="paths are kept relative to this")
    ap.add_argument("--dry-run", action="store_true", help="report only; write nothing")
    ap.add_argument("--force", action="store_true", help="replace an existing, non-empty --out")
    ap.add_argument("--include-vendored", action="store_true", help=f"also convert {VENDORED}")
    ap.add_argument(
        "--allow-computed-names",
        action="store_true",
        help="report a load-time lookup of a computed name as a warning, not a refusal",
    )
    ap.add_argument("--report", help="also write the JSON report to this file")
    ap.add_argument("--check", action="append", default=[], help="q script to run in the result")
    ap.add_argument("--q", default=os.environ.get("QCMD") or "q", help="q binary for --check")
    ap.add_argument("--check-timeout", type=int, default=120)
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = Path(args.root).resolve()
    out = Path(args.out).resolve()
    files, problems = select_inputs(root, args.paths, args.include_vendored)
    inputs = [Path(p).resolve() for p in args.paths]
    if any(out == i or out.is_relative_to(i) or i.is_relative_to(out) for i in [root, *inputs]):
        problems.append(f"--out {args.out} overlaps the root or the inputs")
    elif out.exists() and any(out.iterdir()) and not args.force:
        problems.append(f"--out {args.out} exists and is not empty; pass --force to replace it")
    if args.dry_run and args.report:
        problems.append("--dry-run writes nothing, so it takes no --report; read stdout")

    conv = Converter(args.target, args.allow_computed_names)
    rels = {f: f.relative_to(root).as_posix() for f in files}
    texts = {f: f.read_text(encoding="utf-8") for f in files}
    for f in files:
        conv.collect(rels[f], texts[f])
    conv.propagate()
    results = [conv.convert(rels[f], texts[f]) for f in files]

    selected = set(rels.values())
    report: dict = {
        "target": args.target,
        "claim": CLAIMS[args.target],
        "root": str(root),
        "out": str(out),
        "dry_run": args.dry_run,
        "problems": problems,
        "files": [
            {
                "path": r.path,
                "action": r.action,
                "contexts": r.contexts,
                "defined": {k: sorted(set(v)) for k, v in sorted(r.defined.items())},
                "qualified": r.qualified,
                "nested": r.nested,
                "notes": r.notes,
            }
            for r in results
        ],
        "namespace_mappings": {
            r.path: sorted(
                {c["context"] for c in r.contexts if c["nested"]} | {b["context"] for b in r.nested}
            )
            for r in results
            if any(c["nested"] for c in r.contexts) or r.nested
        },
        "dependencies": [
            {"path": r.path, **ld} for r in results for ld in r.loads if ld["load"] not in selected
        ],
        "warnings": [w.as_dict() for r in results for w in r.warnings],
        "refusals": [x.as_dict() for r in results for x in r.refusals],
        "validation": [],
    }
    status = 1 if problems or report["refusals"] else 0
    if status == 0 and not args.dry_run:
        out.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=f".{out.name}.", dir=out.parent))
        try:
            for r in results:
                dest = stage / r.path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(r.output, encoding="utf-8")
            checks = [Path(c).resolve() for c in args.check]
            report["validation"] = run_checks(stage, checks, args.q, args.check_timeout)
            if all(v["passed"] for v in report["validation"]):
                _publish(stage, out)
            else:
                status = 2
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    report["status"] = ("ok", "refused", "validation-failed")[status]
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report and not args.dry_run:
        Path(args.report).write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return status


if __name__ == "__main__":
    sys.exit(main())
