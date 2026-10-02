#!/usr/bin/env python3
"""doc_examples.py - the q blocks in docs/ that a reader is told will run.

A ```q block in the docs is a claim, and until this existed nothing checked
one: the q-examples lane runs the @eg lines in q docstrings, but not a line
of Markdown. Most blocks cannot run on their own - they need a running stack,
an HDB, ODBC, or are fragments of a larger file - so blocks are OPTED IN, one
HTML comment above the fence, invisible when rendered:

    <!-- q-example: run -->          run the block; any error fails
    <!-- q-example: transcript -->   run each `q)` line; where one output line
                                     follows, it is a q literal the result must
                                     match (`~`). Longer output - a table, a
                                     dictionary - is the console display: the
                                     result is rendered with `.Q.s` and compared
                                     line by line, trailing spaces trimmed, and
                                     `...` in a shown line matches the rest of
                                     that line
    ... kdbx-only: REASON            appended to either: the block runs on
                                     KDB-X only, with the reason it cannot on
                                     PeachQ

A file's marked blocks run in ONE q process, in document order, the way a
reader types them - so a guide's later block may use what an earlier one
defined. Every file gets its own process, which loads only what its blocks
use: `src/init.q` when a block names one of the library's `.q<ns>.`
namespaces, and the ETL stack (`torq_pipeline.q`, `src/etl/init.q`) too when
one names `.qetl` or `.qpipe`. A block that uses neither runs on a bare q -
which is what lets it run on an interpreter the library does not load on.

scripts/test.py's q-docs lane runs what `write_sessions` writes. Run by a bare
python3, like test.py itself, so this imports nothing outside the stdlib.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
#: Where q blocks are looked for.
DOC_GLOBS = ("docs/**/*.md", "README.md", "python/uqs/README.md")
MODES = ("run", "transcript")

_MARKER = re.compile(r"^\s*<!--\s*q-example:\s*(?P<body>.*?)\s*-->\s*$")
_OPEN = re.compile(r"^(?P<indent>\s*)```+\s*q\s*$")
_CLOSE = re.compile(r"^\s*```+\s*$")
_PROMPT = re.compile(r"^\s*q\)\s?")
_ETL = re.compile(r"\.(qetl|qpipe)\.")
#: A name in one of this library's namespaces: `.qfwd.`, `.qalloc.`, ...
_LIBRARY = re.compile(r"(?<![\w.])\.q[a-z]+\.")

PREAMBLE = ("src/init.q",)
ETL_PREAMBLE = ("scripts/processes/torq_pipeline.q", "src/etl/init.q")


class MarkerError(ValueError):
    """A q-example marker that does not say something runnable."""


@dataclass(frozen=True)
class Block:
    path: Path  #: relative to the repository root
    line: int  #: 1-based line of the opening fence
    mode: str  #: "run" or "transcript"
    kdbx_only: str  #: the reason, or "" when it runs on every interpreter
    body: str

    @property
    def where(self) -> str:
        return f"{self.path}:{self.line}"


def _doc_files(root: Path) -> list[Path]:
    found: set[Path] = set()
    for pattern in DOC_GLOBS:
        found.update(p for p in root.glob(pattern) if p.is_file())
    return sorted(found)


def _parse_marker(text: str, where: str) -> tuple[str, str]:
    mode, _, rest = text.partition(" ")
    if mode not in MODES:
        raise MarkerError(f"{where}: q-example mode {mode!r} - it is one of {', '.join(MODES)}")
    rest = rest.strip()
    if not rest:
        return mode, ""
    if not rest.startswith("kdbx-only:") or not rest.removeprefix("kdbx-only:").strip():
        raise MarkerError(f"{where}: after {mode!r}, only `kdbx-only: REASON` may follow")
    return mode, rest.removeprefix("kdbx-only:").strip()


def blocks_in(text: str, path: Path) -> list[Block]:
    """The marked q blocks of one document. Refuses a marker that is not
    directly above a ```q fence, and a block whose shape contradicts its mode."""
    lines = text.splitlines()
    out: list[Block] = []
    for i, line in enumerate(lines):
        marker = _MARKER.match(line)
        if not marker:
            continue
        where = f"{path}:{i + 1}"
        mode, reason = _parse_marker(marker.group("body"), where)
        if i + 1 >= len(lines) or not _OPEN.match(lines[i + 1]):
            raise MarkerError(f"{where}: a q-example marker must sit directly above a ```q fence")
        end = next((j for j in range(i + 2, len(lines)) if _CLOSE.match(lines[j])), None)
        if end is None:
            raise MarkerError(f"{where}: the ```q block it marks is never closed")
        body = "\n".join(lines[i + 2 : end])
        prompts = sum(1 for b in lines[i + 2 : end] if _PROMPT.match(b))
        if mode == "transcript" and not prompts:
            raise MarkerError(f"{where}: a transcript has `q)` lines to run, and this has none")
        if mode == "run" and prompts:
            raise MarkerError(f"{where}: `q)` lines are a transcript - mark it `transcript`")
        out.append(Block(path, i + 2, mode, reason, body))
    return out


def all_blocks(root: Path = REPO) -> list[Block]:
    return [b for f in _doc_files(root) for b in blocks_in(f.read_text(), f.relative_to(root))]


def _q_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _q_strings(lines: list[str]) -> str:
    """A q list of strings, each one a string even at one character."""
    return "(" + ";".join(("enlist " if len(s) == 1 else "") + _q_string(s) for s in lines) + ")"


def transcript_q(body: str) -> str:
    """A transcript as q: each `q)` line checked against what follows it.

    One output line is a q literal, compared with `~` - exact, and blind to
    console formatting. Two or more are the console display of a table or
    dictionary, compared as text by `.docex.display`.
    """
    steps: list[tuple[str, list[str]]] = []
    for line in body.splitlines():
        if _PROMPT.match(line):
            steps.append((_PROMPT.sub("", line, count=1).strip(), []))
        elif steps and line.strip():
            steps[-1][1].append(line.rstrip())
    out = []
    for expr, shown in steps:
        if len(shown) > 1:
            out.append(f".docex.display[{_q_string(expr)};{_q_strings(shown)}];")
        else:
            expected = shown[0].strip() if shown else ""
            out.append(f".docex.check[{_q_string(expr)};{_q_string(expected)}];")
    return "\n".join(out) + "\n"


#: The q side of a transcript check. `display` renders with `.Q.s`, drops blank
#: lines (the shown output has none: Markdown would end the block's paragraph
#: flow) and trims trailing spaces, then names the first line that differs. A
#: shown line holding `...` must only match up to it - `sublist`, not `#`,
#: because `#` past the end of a string wraps round and pads with its start.
CHECK_Q = (
    ".docex.check:{[e;x] r:value e; if[count x; if[not r~value x;",
    '    \'"`",e,"` gave ",(-3!r),", the doc shows ",x]]};',
    '.docex.same:{[s;l] $[count i:s ss "..."; (i0#s)~(i0:first i) sublist l; s~l]};',
    '.docex.display:{[e;x] a:rtrim each "\\n" vs .Q.s value e; a:a where 0<count each a;',
    '    n:count[a]|count x; d:where not .docex.same\'[n#x,n#enlist"";n#a,n#enlist""];',
    '    if[count d; i:first d; \'"`",e,"` line ",string[1+i]," shows ",',
    '        (-3!$[i<count a;a i;"(nothing)"]),", the doc shows ",',
    '        -3!$[i<count x;x i;"(nothing)"]]};',
)


def session_q(blocks: list[Block], block_files: list[Path]) -> str:
    """One document's blocks as one q script: the preamble, then each block
    loaded under a trap that names where it came from."""
    etl = any(_ETL.search(b.body) for b in blocks)
    library = etl or any(_LIBRARY.search(b.body) for b in blocks)
    loads = [*(PREAMBLE if library else ()), *(ETL_PREAMBLE if etl else ())]
    head = [f"/ generated by scripts/dev/doc_examples.py from {blocks[0].path}"]
    head += [f"\\l {p}" for p in loads]
    head += [
        '.docex.where:"";',
        '.docex.fail:{-2 "FAIL ",.docex.where,": ",x; exit 1};',
        *CHECK_Q,
    ]
    body = []
    for block, file in zip(blocks, block_files, strict=True):
        body.append(f".docex.where:{_q_string(block.where)};")
        body.append(f"@[system;{_q_string('l ' + str(file))};.docex.fail];")
    tail = [f'-1 "ok {blocks[0].path}: {len(blocks)} block(s)";', "exit 0"]
    return "\n".join([*head, *body, *tail]) + "\n"


def write_sessions(root: Path, out_dir: Path, impl: str) -> tuple[list[Path], list[Block]]:
    """One q script per document under `out_dir`, and the blocks skipped on
    `impl` because they are KDB-X only."""
    by_doc: dict[Path, list[Block]] = {}
    skipped: list[Block] = []
    for block in all_blocks(root):
        if block.kdbx_only and impl != "kdbx":
            skipped.append(block)
            continue
        by_doc.setdefault(block.path, []).append(block)
    sessions = []
    for n, (doc, blocks) in enumerate(sorted(by_doc.items())):
        files = []
        for block in blocks:
            f = out_dir / f"doc{n}_{block.line}.q"
            f.write_text(
                transcript_q(block.body) if block.mode == "transcript" else block.body + "\n"
            )
            files.append(f)
        session = out_dir / f"doc{n}_{re.sub(r'[^A-Za-z0-9]+', '_', str(doc))}.q"
        session.write_text(session_q(blocks, files))
        sessions.append(session)
    return sessions, skipped


def main() -> int:
    blocks = all_blocks()
    total = sum(
        1 for f in _doc_files(REPO) for line in f.read_text().splitlines() if _OPEN.match(line)
    )
    for b in blocks:
        extra = f" (kdbx-only: {b.kdbx_only})" if b.kdbx_only else ""
        print(f"{b.where}  {b.mode}{extra}")
    print(f"{len(blocks)} of {total} q blocks are marked to run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
