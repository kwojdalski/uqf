#!/usr/bin/env python3
"""Keep `src/etl/core/` from depending on the declaring directories under src/etl/.

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
src/etl/core/worker_dependencies.q, and process identity (`.proc`) is
src/etl/core/run.q's `.qetl.run.proc_name`. Every other file goes through
those. Before, the process name was read four times in three files, each
with its own fallback, and nothing stopped a fifth. TorQ stays the authority;
this only says where src/ asks it.

Every OTHER namespace TorQ defines is refused in src/ outright (#2156): no
file under src/ may know TorQ exists beyond those three reads. The set is
derived from TorQ itself - every `\\d .ns` and every `.ns.name` definition
under lib/torq/code and lib/torq/torq.q - not typed out here, because a
hand-kept list of the owned namespaces is how `.cmp`, `.hk`, `.dotz` and `.wdb`
went unguarded, and it would fall behind again the next time TorQ is updated.
What is let through is explicit and carries its reason: the namespaces q
itself reserves (TorQ only adds to them), and the remote calls in
TORQ_REMOTE_CALLS, each a symbol sent down a handle to a TorQ process rather
than a read of TorQ in this one.

The fourth rule is about outbound HTTP (#988). Under src/etl/ the one place
that speaks it is src/etl/core/webhook.q: `.Q.hp`, `.Q.hg`, `.Q.hmb`, `.Q.hap`
and a `:http(s):// address are refused everywhere else. A declaration that owned
its own client is how #987 happened: `.Q.hp` returns the body of a 500, the
job's contract assumed it signalled, and nothing outside the job's test fake
ever checked.

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
from collections.abc import Iterator
from pathlib import Path

# parents[2], not parent.parent: this file sits one level deeper since
# scripts/ was foldered (#241). Getting it wrong does not raise - it
# resolves to scripts/ and the checker reports over an empty tree.
REPO = Path(__file__).resolve().parents[2]
CORE = REPO / "src" / "etl" / "core"
ETL = REPO / "src" / "etl"
#: The subdirectories of src/etl/ that hold no declarations: the framework
#: itself, and what is generated from the declarations. EVERY other
#: subdirectory is a declaring one - sources/, transforms/, workers/,
#: streaming/, reactions/ - derived rather than listed, because a list left
#: out reactions/ and core could reach into a reaction unnoticed (#1333).
NON_DECLARING_DIRS = frozenset({"core", "generated"})

#: The TorQ adapter. Lives in scripts/ because it is the one place TorQ is
#: allowed; nothing under src/etl/ may reach it.
ADAPTER_NS = ".qtorq"

#: Each TorQ namespace src/ may read, and the one file allowed to read it.
TORQ_OWNERS = {
    ".lg": "src/etl/core/log.q",
    ".servers": "src/etl/core/worker_dependencies.q",
    ".proc": "src/etl/core/run.q",
}

#: The vendored TorQ, read only to learn which namespaces it defines.
TORQ = REPO / "lib" / "torq"

#: A namespace TorQ defines: `\d .cmp`, or a definition such as
#: `.hk.rmfiles:{...}` written from the root at the start of a line.
TORQ_DEF_RE = re.compile(
    r"^(?:\\d[ \t]+(\.[A-Za-z][A-Za-z0-9_]*)\b|(\.[A-Za-z][A-Za-z0-9_]*)\.[A-Za-z_])",
    re.MULTILINE,
)

#: Namespaces TorQ defines names in that are not TorQ's. q reserves every
#: single-letter namespace for itself; TorQ adds to them, but src/ using one
#: is using q, not TorQ.
NOT_TORQ = {
    ".h": "kdb+'s own HTTP/markup namespace; TorQ only adds helpers to it",
}

#: TorQ API src/ names on purpose, outside any owner: (file, namespace) ->
#: why. Each is a function name sent down an open handle to a TorQ process -
#: the wire contract of a stack src/ talks to - so it needs no TorQ loaded in
#: the calling process. An entry nothing uses any more is reported, so the
#: list cannot outlive its reason.
TORQ_REMOTE_CALLS = {
    ("src/etl/core/compare.q", ".gw"): (
        "reads each side's schema through the gateway's .gw.syncexec, over the handle "
        "the comparison opened to that side's gateway"
    ),
    ("src/etl/core/verify_complete.q", ".dataaccess"): (
        "the destination reader asks the gateway's .dataaccess.getdata, the entry point "
        "the query policies (docs/architecture/query-policies.md) apply to"
    ),
}


#: A read of a TorQ environment variable: getenv`TORQDATA, getenv[`TORQAPPHOME].
#: TorQ's own contract, so src/ either takes the value from its own UQF_*
#: variable or is listed in TORQ_ENV_READS (#2653).
TORQ_ENV_RE = re.compile(r"getenv\s*\[?\s*`(TORQ\w*)")

#: TorQ environment reads src/ makes on purpose: (file, variable) -> why. An
#: entry nothing uses any more is reported, as with TORQ_REMOTE_CALLS.
TORQ_ENV_READS = {
    ("src/etl/core/status.q", "TORQDATA"): (
        "the status directory's documented fallback when UQF_STATUS_DIR is unset, "
        "so a job under TorQ keeps its files beside TorQ's other state; with neither "
        "set it refuses by name rather than writing to the filesystem root"
    ),
    ("src/etl/plant_registry.q", "TORQAPPHOME"): (
        "read once at load to find the starter pack's database.q, which uqs sets for "
        "every process and a deployment points at an install the release does not ship "
        "(#773); the table set must be known at load, before any adapter could inject it, "
        "and the vendored path is the fallback when it is unset"
    ),
}


#: A literal `system "l <file>"` or `system "cd <dir>"`: a load or a directory
#: change that depends on the process's working directory (#2653). The target is
#: group 2; a load whose target is built at run time (`"l ",path`) has none here
#: and is the caller's own path, so only a literal is judged. A `cd` is judged
#: whatever it names: it moves every other relative path in the process.
CWD_CALL_RE = re.compile(r'system\s*"\s*(l|cd)\s+([^"]*)"')

#: Relative loads and directory changes src/ makes on purpose: (file, command) ->
#: why, the command spelt as it is written ("l odbc.k"). An entry nothing uses
#: any more is reported, as with TORQ_ENV_READS.
CWD_CALLS = {
    ("src/etl/init.q", "l lib/unparse/unparse.q"): (
        "the load plan is what names files from the repository root (like every \\l "
        "line in it); unparse is optional, so the load is conditional on the file, "
        "which a bare \\l could not be"
    ),
}


def torq_namespaces(torq: Path = TORQ) -> frozenset[str]:
    """Every namespace TorQ defines under `torq`'s code/ and torq.q, less NOT_TORQ.

    Whole-line comments are skipped: a definition behind `/` is not one.
    """
    found: set[str] = set()
    sources = [*sorted((torq / "code").rglob("*.q")), torq / "torq.q"]
    for path in sources:
        if not path.is_file():
            continue
        text = "\n".join(
            "" if line.lstrip().startswith("/") else line
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
        )
        for cd, defined in TORQ_DEF_RE.findall(text):
            found.add(cd or defined)
    return frozenset(found - NOT_TORQ.keys())


#: What src/ may not read except through an owner: TorQ's namespaces, plus the
#: owned ones in case the vendored tree is ever missing one of them.
TORQ_NAMESPACES = torq_namespaces() | TORQ_OWNERS.keys()

#: A reference to one of them: `.lg.l`, `` `.proc ``. Not `.qetl.run.proc_name`
#: or `.foo.lg.x`, where the name is a segment of another namespace. Longest
#: first, so `.dqc` is not read as `.dq`.
TORQ_RE = re.compile(
    r"(?<![\w.])("
    + "|".join(re.escape(ns) for ns in sorted(TORQ_NAMESPACES, key=lambda n: (-len(n), n)))
    + r")\b"
)

#: The one file under src/etl/ allowed to speak HTTP, and what speaking it looks like.
HTTP_OWNER = "src/etl/core/webhook.q"
HTTP_RE = re.compile(r"\.Q\.(?:hp|hg|hmb|hap)\b|`:https?://")

#: A namespace a declaration owns, read from its first name, e.g.
#: `.qpipe.job.demo_deals_backfill.quality_check:{...}` owns
#: `.qpipe.job.demo_deals_backfill`. Declarations write names fully qualified
#: from the root and open no `\\d` context (#1411).
NAMESPACE_RE = re.compile(
    r"^(\.[a-zA-Z][a-zA-Z0-9_]*\.[a-zA-Z][a-zA-Z0-9_]*\.[a-zA-Z][a-zA-Z0-9_]*)\.[a-zA-Z_][\w.]*\s*::?",
    re.MULTILINE,
)


def declaring_dirs(root: Path = REPO) -> list[Path]:
    """Every subdirectory of `root`'s src/etl/ except core/ and generated/."""
    etl = root / "src" / "etl"
    return sorted(d for d in etl.iterdir() if d.is_dir() and d.name not in NON_DECLARING_DIRS)


def declaring_namespaces(root: Path = REPO) -> dict[str, str]:
    """namespace -> the file that declares it, for every module in a
    declaring directory. Discovered rather than listed, so a new declaration
    is covered the day it is added.
    """
    found: dict[str, str] = {}
    for directory in declaring_dirs(root):
        for path in sorted(directory.rglob("*.q")):
            for ns in NAMESPACE_RE.findall(path.read_text(encoding="utf-8")):
                if ns != ".":
                    found[ns] = path.relative_to(root).as_posix()
    return found


def core_reach(root: Path = REPO) -> list[str]:
    """Every line of `root`'s src/etl/core/ that names a declaring namespace."""
    declared = declaring_namespaces(root)
    violations: list[str] = []
    for path in sorted((root / "src" / "etl" / "core").rglob("*.q")):
        code = strip_comments_and_strings(path.read_text(encoding="utf-8"))
        for lineno, line in enumerate(code.splitlines(), 1):
            for ns, owner in declared.items():
                if re.search(rf"{re.escape(ns)}\b", line):
                    violations.append(
                        f"{path.relative_to(root).as_posix()}:{lineno}: core reaches into "
                        f"{ns} (declared by {owner})"
                    )
    return violations


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


def _torq_reads(root: Path) -> Iterator[tuple[str, int, str]]:
    """(file, line number, namespace) for every TorQ name in `root`'s src/ code."""
    for path in sorted((root / "src").rglob("*.q")):
        rel = path.relative_to(root).as_posix()
        code = strip_comments_and_strings(path.read_text(encoding="utf-8"))
        for lineno, line in enumerate(code.splitlines(), 1):
            for match in TORQ_RE.finditer(line):
                yield rel, lineno, match.group(1)


def torq_reach(root: Path) -> list[str]:
    """Every read of a TorQ namespace in `root`'s src/ that is not allowed:
    an owned one outside its owner file, any other one outside TORQ_REMOTE_CALLS.
    """
    hits = []
    for rel, lineno, ns in _torq_reads(root):
        owner = TORQ_OWNERS.get(ns)
        if owner is not None:
            if owner != rel:
                hits.append(f"{rel}:{lineno}: reads {ns} - only {owner} may")
        elif (rel, ns) not in TORQ_REMOTE_CALLS:
            hits.append(f"{rel}:{lineno}: reads {ns}, a TorQ namespace - src/ may not know TorQ")
    return hits


def _torq_env_reads(root: Path) -> Iterator[tuple[str, int, str]]:
    """(file, line number, variable) for every getenv of a TORQ* name in `root`'s src/."""
    for path in sorted((root / "src").rglob("*.q")):
        rel = path.relative_to(root).as_posix()
        code = strip_comments_and_strings(path.read_text(encoding="utf-8"))
        for lineno, line in enumerate(code.splitlines(), 1):
            for match in TORQ_ENV_RE.finditer(line):
                yield rel, lineno, match.group(1)


def torq_env_reach(root: Path) -> list[str]:
    """Every getenv of a TorQ variable in `root`'s src/ that TORQ_ENV_READS does not allow."""
    return [
        f"{rel}:{lineno}: reads ${var}, TorQ's environment - src/ takes a UQF_* variable"
        for rel, lineno, var in _torq_env_reads(root)
        if (rel, var) not in TORQ_ENV_READS
    ]


def unused_env_reads(root: Path) -> list[str]:
    """Every TORQ_ENV_READS entry `root`'s src/ no longer makes."""
    made = {(rel, var) for rel, _, var in _torq_env_reads(root)}
    return [f"{rel}: {var}" for rel, var in sorted(TORQ_ENV_READS) if (rel, var) not in made]


def _cwd_calls(root: Path) -> Iterator[tuple[str, int, str]]:
    """(file, line number, command) for every cwd-relative `system "l"`/`"cd"` in `root`'s src/."""
    for path in sorted((root / "src").rglob("*.q")):
        rel = path.relative_to(root).as_posix()
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("/"):
                continue
            for match in CWD_CALL_RE.finditer(line):
                verb, target = match.group(1), match.group(2).strip()
                if verb == "l" and (not target or target[0] in "/~"):
                    continue
                yield rel, lineno, f"{verb} {target}".strip()


def cwd_reach(root: Path) -> list[str]:
    """Every relative load or directory change in `root`'s src/ that CWD_CALLS does not allow."""
    return [
        f'{rel}:{lineno}: system "{cmd}" depends on the working directory - '
        "load from the plan in src/etl/init.q, or name the path absolutely"
        for rel, lineno, cmd in _cwd_calls(root)
        if (rel, cmd) not in CWD_CALLS
    ]


def unused_cwd_calls(root: Path) -> list[str]:
    """Every CWD_CALLS entry `root`'s src/ no longer makes."""
    made = {(rel, cmd) for rel, _, cmd in _cwd_calls(root)}
    return [f"{rel}: {cmd}" for rel, cmd in sorted(CWD_CALLS) if (rel, cmd) not in made]


def unused_remote_calls(root: Path) -> list[str]:
    """Every TORQ_REMOTE_CALLS entry `root`'s src/ no longer makes."""
    made = {(rel, ns) for rel, _, ns in _torq_reads(root)}
    return [f"{rel}: {ns}" for rel, ns in sorted(TORQ_REMOTE_CALLS) if (rel, ns) not in made]


def http_reach(root: Path) -> list[str]:
    """Every outbound-HTTP call under `root`'s src/etl/ outside the transport file."""
    hits = []
    for path in sorted((root / "src" / "etl").rglob("*.q")):
        rel = path.relative_to(root).as_posix()
        if rel == HTTP_OWNER:
            continue
        code = strip_comments_and_strings(path.read_text(encoding="utf-8"))
        for lineno, line in enumerate(code.splitlines(), 1):
            for match in HTTP_RE.finditer(line):
                hits.append(f"{rel}:{lineno}: speaks HTTP via {match.group(0)}")
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
        print("error: no declaring namespaces found under src/etl/ - has the", file=sys.stderr)
        print("layout changed? refusing to report success over an empty set.", file=sys.stderr)
        return 1

    violations = core_reach(REPO)

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
    derived = torq_namespaces()
    unknown = sorted(set(TORQ_OWNERS) - derived)
    if not derived or unknown:
        print(
            f"error: lib/torq defines {len(derived)} namespace(s), and not the owned "
            f"{', '.join(unknown) or '(none)'} - has the vendored TorQ moved? refusing to "
            "report success over a set that no longer describes it.",
            file=sys.stderr,
        )
        return 1
    stale = unused_remote_calls(REPO)
    if stale:
        print("TORQ_REMOTE_CALLS allows calls src/ no longer makes:", file=sys.stderr)
        for v in stale:
            print(f"  {v}", file=sys.stderr)
        print(
            "\nRemove the entry: an allowance with no call is one waiting to be misused.",
            file=sys.stderr,
        )
        return 1
    torq_hits = torq_reach(REPO)
    if torq_hits:
        print("src/ reads a TorQ facility outside the file that owns it:", file=sys.stderr)
        for v in torq_hits:
            print(f"  {v}", file=sys.stderr)
        print(
            "\n#619: go through the owner - .qetl.log for logging, .qetl.job.bounded.runtime\n"
            "for connected services, .qetl.run.proc_name for this process's name.\n"
            "#2156: any other TorQ namespace stays out of src/ - TorQ's own machinery\n"
            "(.cmp, .hk, .wdb, ...) is driven from scripts/ and the TorQ config overlay.",
            file=sys.stderr,
        )
        return 1

    env_hits = torq_env_reach(REPO)
    stale_env = unused_env_reads(REPO)
    if env_hits or stale_env:
        print("src/ reads TorQ's environment contract (#2653):", file=sys.stderr)
        for v in env_hits:
            print(f"  {v}", file=sys.stderr)
        for v in stale_env:
            print(f"  TORQ_ENV_READS entry no longer used: {v}", file=sys.stderr)
        print(
            "\nTake the value from a UQF_* variable, or add a reasoned TORQ_ENV_READS entry.",
            file=sys.stderr,
        )
        return 1

    cwd_hits = cwd_reach(REPO)
    stale_cwd = unused_cwd_calls(REPO)
    if cwd_hits or stale_cwd:
        print("src/ depends on the working directory (#2653):", file=sys.stderr)
        for v in cwd_hits:
            print(f"  {v}", file=sys.stderr)
        for v in stale_cwd:
            print(f"  CWD_CALLS entry no longer used: {v}", file=sys.stderr)
        print(
            "\nA relative load or cd breaks under any other cwd. Load from src/etl/init.q,"
            "\nuse an absolute path, or add a reasoned CWD_CALLS entry.",
            file=sys.stderr,
        )
        return 1

    http_hits = http_reach(REPO)
    if http_hits:
        print(f"src/etl/ speaks HTTP outside {HTTP_OWNER}:", file=sys.stderr)
        for v in http_hits:
            print(f"  {v}", file=sys.stderr)
        print(
            "\n#988: go through .qetl.webhook.post, which checks the status line - .Q.hp\n"
            "returns the body of a 500 instead of signalling (#987).",
            file=sys.stderr,
        )
        return 1

    if violations:
        print(
            "src/etl/core/ must not depend on a declaring directory (sources/, workers/, ...):",
            file=sys.stderr,
        )
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
        f"check_etl_layering: {len(list(CORE.rglob('*.q')))} core file(s) depend on none "
        f"of the {len(declared)} declaring namespace(s); nothing under src/etl/ calls "
        f"{ADAPTER_NS}; each of {', '.join(TORQ_OWNERS)} is read only by its owner file "
        f"and no other of TorQ's {len(derived)} namespace(s) is read beyond "
        f"{len(TORQ_REMOTE_CALLS)} allowed remote call(s); "
        f"HTTP only in {HTTP_OWNER}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
