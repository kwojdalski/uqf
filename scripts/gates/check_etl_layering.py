#!/usr/bin/env python3
"""Keep `src/etl/core/` from depending on `sources/`, `workers/` or `streaming/`.

Question the question bank settled the split: `core/` is framework, `sources/` and
`workers/` are declarations, and the question bank names the property that
split buys - adding a source is a file in `sources/` plus a registration,
with NO core change - and it is the reason the ETL tree can grow without the
framework learning about each new feed.

Nothing enforced it: the directory rule is a
convention, and a file dropped into `core/` that reached into `sources/`
would break the property silently, because every test would still pass. A
convention that only holds while everyone remembers it is the state this
repository keeps finding its bugs in.

The direction is one-way on purpose. `sources/` and `workers/` SHOULD call
into `core/` - that is what a declaration over a generic shell means - so
this checks only that the arrow never points back.

The second rule is the same arrow one level out (#229): nothing under
`src/etl/` may call `.qtorq`, the TorQ adapter in `scripts/processes/torq_pipeline.q`.
The ETL tree must load in a plain q process with no TorQ present,
and `.qtorq` is the one namespace allowed to know TorQ exists. The status
writer used to live there and `backfill_state.q` called it, which is why
`lock_dir` carried a try-with-fallback - the author knew the namespace might
not be loaded. A dependency that has to be guarded against being absent is
pointing the wrong way, and this stops it coming back.

The third rule is about who reads TorQ (#619). src/ may read TorQ's own
facilities - that is what runs it - but each one has ONE owner file: logging
(`.lg`) is src/etl/core/log.q, service discovery (`.servers`) is
src/etl/core/worker_runtime.q, and process identity (`.proc`) is
src/etl/core/run.q's `.qetl.run.proc_name`. Every other file goes through
those. Before, the process name was read four times in three files, each
with its own fallback, and nothing stopped a fifth. TorQ stays the authority;
this only says where src/ asks it.

STRING AND COMMENT AWARE, and it has to be. `bounded_worker.q` names
`.qpipe.job.demo_deals_backfill` inside an error message ("ns must be a namespace symbol such as
`.qpipe.job.demo_deals_backfill"), which is documentation, not a dependency. A naive search reports
it on the first run, and a checker that is wrong the day it lands gets
suppressed rather than fixed - after which it protects nothing.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# parents[2], not parent.parent: this file sits one level deeper since
# scripts/ was foldered (#241). Getting it wrong does not raise - it
# resolves to scripts/ and the checker reports over an empty tree.
REPO = Path(__file__).resolve().parents[2]
CORE = REPO / "src" / "etl" / "core"
ETL = REPO / "src" / "etl"
DECLARING_DIRS = ("sources", "transforms", "workers", "streaming")

#: The TorQ adapter. Lives in scripts/ because it is the one place TorQ is
#: allowed; nothing under src/etl/ may reach it.
ADAPTER_NS = ".qtorq"

#: Each TorQ namespace src/ may read, and the one file allowed to read it.
TORQ_OWNERS = {
    ".lg": "src/etl/core/log.q",
    ".servers": "src/etl/core/worker_runtime.q",
    ".proc": "src/etl/core/run.q",
}

#: A reference to one of them: `.lg.l`, `` `.proc ``. Not `.qetl.run.proc_name`
#: or `.foo.lg.x`, where the name is a segment of another namespace.
TORQ_RE = re.compile(r"(?<![\w.])(" + "|".join(re.escape(ns) for ns in TORQ_OWNERS) + r")\b")

#: A namespace declaration, e.g. `\d .qpipe.job.demo_deals_backfill`.
NAMESPACE_RE = re.compile(
    r"^\\d\s+(\.[a-zA-Z][a-zA-Z0-9_]*(?:\.[a-zA-Z][a-zA-Z0-9_]*)*)\s*$", re.MULTILINE
)


def declaring_namespaces() -> dict[str, str]:
    """namespace -> the file that declares it, for every sources/ and
    workers/ module. Discovered rather than listed, so a new declaration is
    covered the day it is added.
    """
    found: dict[str, str] = {}
    for sub in DECLARING_DIRS:
        directory = REPO / "src" / "etl" / sub
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.q")):
            for ns in NAMESPACE_RE.findall(path.read_text(encoding="utf-8")):
                if ns != ".":
                    found[ns] = str(path.relative_to(REPO))
    return found


def strip_comments_and_strings(text: str) -> str:
    """Blank out q comments and string literals, keeping line structure.

    A whole-line comment starts with `/` at the start of a line (after
    optional whitespace). Trailing comments are deliberately NOT stripped:
    telling ` / ` apart from a `/` inside a string needs a real parser, and
    getting it wrong would hide a genuine call rather than merely report an
    extra one.
    """
    out_lines = []
    for raw in text.splitlines():
        if raw.lstrip().startswith("/"):
            out_lines.append("")
            continue
        buf = []
        in_string = False
        i = 0
        while i < len(raw):
            ch = raw[i]
            if ch == '"':
                in_string = not in_string
                buf.append(" ")
                i += 1
                continue
            if in_string:
                # A valid escape consumes both characters, so an escaped
                # quote does not flip the state.
                if ch == "\\" and i + 1 < len(raw):
                    buf.append("  ")
                    i += 2
                    continue
                buf.append(" ")
                i += 1
                continue
            buf.append(ch)
            i += 1
        out_lines.append("".join(buf))
    return "\n".join(out_lines)


def torq_reach(root: Path) -> list[str]:
    """Every read of a TorQ namespace in `root`'s src/ outside its owner file."""
    hits = []
    for path in sorted((root / "src").rglob("*.q")):
        rel = path.relative_to(root).as_posix()
        code = strip_comments_and_strings(path.read_text(encoding="utf-8"))
        for lineno, line in enumerate(code.splitlines(), 1):
            for match in TORQ_RE.finditer(line):
                ns = match.group(1)
                if TORQ_OWNERS[ns] != rel:
                    hits.append(f"{rel}:{lineno}: reads {ns} - only {TORQ_OWNERS[ns]} may")
    return hits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="accepted for symmetry")
    parser.parse_args()

    if not CORE.is_dir():
        print(f"error: {CORE.relative_to(REPO)} is missing", file=sys.stderr)
        return 1

    declared = declaring_namespaces()
    if not declared:
        print("error: no sources/ or workers/ namespaces found - has the", file=sys.stderr)
        print("layout changed? refusing to report success over an empty set.", file=sys.stderr)
        return 1

    violations: list[str] = []
    for path in sorted(CORE.glob("*.q")):
        code = strip_comments_and_strings(path.read_text(encoding="utf-8"))
        for lineno, line in enumerate(code.splitlines(), 1):
            for ns, owner in declared.items():
                if re.search(rf"{re.escape(ns)}\b", line):
                    violations.append(
                        f"{path.relative_to(REPO)}:{lineno}: core reaches into "
                        f"{ns} (declared by {owner})"
                    )

    adapter_hits: list[str] = []
    for path in sorted(ETL.rglob("*.q")):
        code = strip_comments_and_strings(path.read_text(encoding="utf-8"))
        for lineno, line in enumerate(code.splitlines(), 1):
            if re.search(rf"{re.escape(ADAPTER_NS)}\b", line):
                adapter_hits.append(f"{path.relative_to(REPO)}:{lineno}: {line.strip()}")

    if adapter_hits:
        print(
            f"src/etl/ must not call {ADAPTER_NS} (the TorQ adapter in scripts/):", file=sys.stderr
        )
        for v in adapter_hits:
            print(f"  {v}", file=sys.stderr)
        print(
            "\nB-09: the ETL tree loads in a plain q process with no TorQ. Anything\n"
            "src/ needs from .qtorq is not TorQ plumbing and belongs under src/etl/core/.",
            file=sys.stderr,
        )
        return 1

    absent = [owner for owner in TORQ_OWNERS.values() if not (REPO / owner).is_file()]
    if absent:
        print(f"error: TorQ owner file(s) missing: {', '.join(absent)}", file=sys.stderr)
        return 1
    torq_hits = torq_reach(REPO)
    if torq_hits:
        print("src/ reads a TorQ facility outside the file that owns it:", file=sys.stderr)
        for v in torq_hits:
            print(f"  {v}", file=sys.stderr)
        print(
            "\n#619: go through the owner - .qetl.log for logging, .qetl.job.bounded.runtime\n"
            "for connected services, .qetl.run.proc_name for this process's name.",
            file=sys.stderr,
        )
        return 1

    if violations:
        print("src/etl/core/ must not depend on sources/ or workers/:", file=sys.stderr)
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        print(
            "\nPer the question bank core/ is framework and the other two are\n"
            "declarations; the question bank is\n"
            "the property that buys - a new source needs no core change. An arrow\n"
            "pointing this way removes it.",
            file=sys.stderr,
        )
        return 1

    print(
        f"check_etl_layering: {len(list(CORE.glob('*.q')))} core file(s) depend on none "
        f"of the {len(declared)} declaring namespace(s); nothing under src/etl/ calls "
        f"{ADAPTER_NS}; each of {', '.join(TORQ_OWNERS)} is read only by its owner file"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
