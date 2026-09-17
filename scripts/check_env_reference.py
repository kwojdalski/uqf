#!/usr/bin/env python3
"""Keep ``docs/reference/environment.md`` and the code's actual environment surface in
agreement, in both directions.

C-04 asked what the required-versus-optional environment set is and where it
is documented as one list. The list is hand-written, because "optional, and
here is what happens when it is unset" is a judgement no generator can read
off a ``os.environ.get`` call. A hand-written list decays, so this gate
checks it rather than a generator producing it - the same shape as
``check_hook_scopes.py``, and the split J-01 settled: generate what is
mechanical, check what is not.

Two directions, because each catches a different failure:

* **Undocumented** - a variable the code reads that the page never mentions.
  This is the state the repository was in before the page existed.
* **Stale** - a row for a variable nothing reads any more, which is how a
  reference page ends up describing a previous version of the system. This is
  the direction a generator would give for free and a hand-written page
  otherwise never gets.

Scope is the *operator's* surface: ``src/``, ``scripts/``, each package's
``src/``, ``web/``, and the smoke scripts under ``tests/q/``. Fixtures are
excluded on purpose - ``tests/q/test_worker_config.q`` sets eleven throwaway
keys to prove the precedence order, and demanding rows for ``UQF_X`` and
``UQF_ONLY_YAML`` would bury the twenty-three variables that matter.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOC = REPO / "docs" / "reference" / "environment.md"

#: Directories whose reads are an operator's business. `web/` is included
#: because a Vite config's `process.env` is as much a knob as a q `getenv`.
SCOPE_DIRS = ("src", "scripts", "web")
#: Package sources, found rather than listed so a new workspace member is
#: covered the day it is added instead of the day someone remembers.
PACKAGE_SRC_GLOB = "python/*/src"
#: Operator tools that happen to live in the test tree. Run by hand against a
#: real source, which is exactly why they are not in the unit lane.
EXTRA_FILES = ("tests/q/smoke_external_metadata.q",)

SCANNED_SUFFIXES = {".q", ".py", ".ts", ".js", ".sh", ".yaml", ".yml"}

#: A literal environment read. Only literals: `getenv \`$env_var` builds its
#: name at runtime and has no name to collect, which is what the doc's
#: pattern rows are for.
LITERAL_READS = (
    # q: getenv `NAME and getenv[`NAME]
    re.compile(r"getenv\s*\[?\s*`([A-Z][A-Z0-9_]*)"),
    # Python: os.environ.get("NAME"), os.getenv("NAME"), os.environ["NAME"]
    re.compile(r"os\.(?:environ\.get|getenv|environ)\s*[(\[]\s*[\"']([A-Z][A-Z0-9_]*)[\"']"),
    # uqf_frontend's typed helpers, which wrap os.environ.get one level down.
    # ANY `_<word>_env("NAME")`, not an enumerated list of them: the list read
    # `_(?:int|path)_env` and a third helper - `_flag_env`, for a boolean -
    # was therefore invisible the day it was written. Its variable reported as
    # documented-but-read-by-nothing, which points the reader at the docs
    # when the gate is what needs changing. A gate that must be edited
    # whenever the code grows a sibling of something it already understands
    # is a gate that will one day not be.
    re.compile(r"_[a-z]+_env\s*\(\s*[\"']([A-Z][A-Z0-9_]*)[\"']"),
    # A module constant naming a variable, e.g. CRYPTORUST_ROOT_ENV = "..."
    re.compile(r"^[A-Z][A-Z0-9_]*_ENV\s*=\s*[\"']([A-Z][A-Z0-9_]*)[\"']", re.MULTILINE),
    # A q process DECLARING the variables it requires as a symbol vector, then
    # reading them through a loop: `required_env:`A`B`C` with `getenv nm`.
    # The read itself carries no literal, so without this the names look
    # unread - which is how scripts/torq_backfill.q's four variables were
    # reported as documented-but-dead on their first run. Declaring the set is
    # better practice than four scattered getenv calls (it lets the process
    # refuse naming every missing one at once, per ETL-16), so the gate should
    # understand the better idiom rather than push code toward the worse one.
    re.compile(r"required_env\s*:\s*((?:`[A-Z][A-Z0-9_]*)+)"),
    # TypeScript: process.env.NAME
    re.compile(r"process\.env\.([A-Z][A-Z0-9_]*)"),
    # `.qdata.cfg` wraps getenv with a `.env`-file fallback, so its reads are
    # invisible to the getenv pattern above. Requires an upper-case backtick
    # name immediately, which keeps it off unrelated `cfg[` calls.
    re.compile(r"(?:\.qdata\.)?cfg\s*\[\s*`([A-Z][A-Z0-9_]*)"),
)

#: A whole-line q comment. `worker_config.q`'s own `@eg` line reads
#: `.qwcfg.explain[`backfill_from]`, which made the first run of this gate
#: demand a row for UQF_BACKFILL_FROM - a variable nothing sets, illustrating
#: the mechanical mapping rather than configuring anything. Documentation is
#: not a call site. Trailing comments are deliberately left alone: a `/`
#: inside a string literal is indistinguishable from one starting a comment
#: without parsing q properly, and stripping them wrongly would hide a real
#: read.
Q_COMMENT_LINE = re.compile(r"^\s*/.*$", re.MULTILINE)

#: `.qwcfg` maps a config key to UQF_ plus the upper-cased key, so a
#: production read of `dry_run` means UQF_DRY_RUN is live with that string
#: appearing in no file. This is the one case grep cannot answer and the
#: strongest reason the doc exists.
QWCFG_READ = re.compile(
    r"\.qwcfg\.(?:get_timestamp|get_positive|get_symbol|get_flag|raw|explain)\s*\[?\s*`([a-z_]+)"
)

#: `build_env`'s dict keys: produced for TorQ, never read back by us, so they
#: are exempt from the stale direction but still required to be documented.
ENV_PRODUCER = REPO / "python" / "torq_orchestrator" / "src" / "torq_orchestrator" / "env.py"

#: This file. Its own regexes contain example variable names, and scanning
#: itself made the first run report a variable called NAME - the same
#: self-inclusion bug `test_module_split.py` hit when it demanded the facade
#: export a symbol named `thing`.
SELF = Path(__file__).resolve()

#: Read to locate the interpreter and the user's home, not to configure
#: anything. Documented under "Prerequisites", which is prose, not a row.
PREREQUISITES = frozenset({"HOME", "QHOME", "PATH"})


def scanned_files() -> list[Path]:
    """Every file whose environment reads this gate holds the doc to."""
    out: list[Path] = []
    roots = [REPO / d for d in SCOPE_DIRS]
    roots += sorted(REPO.glob(PACKAGE_SRC_GLOB))
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix in SCANNED_SUFFIXES:
                if "node_modules" in path.parts or ".venv" in path.parts:
                    continue
                if path.resolve() == SELF:
                    continue
                out.append(path)
    out += [REPO / f for f in EXTRA_FILES if (REPO / f).is_file()]
    return out


def read_names() -> dict[str, list[str]]:
    """Variable name -> the files that read it, for a message that names a
    file rather than leaving the reader to grep for it."""
    found: dict[str, list[str]] = {}
    for path in scanned_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        if path.suffix == ".q":
            text = Q_COMMENT_LINE.sub("", text)
        rel = str(path.relative_to(REPO))
        names = set()
        for pattern in LITERAL_READS:
            for m in pattern.findall(text):
                # A backtick run like "`A`B`C" is one match; split it.
                names.update(x for x in m.split("`") if x) if "`" in m else names.add(m)
        names |= {"UQF_" + key.upper() for key in QWCFG_READ.findall(text)}
        for name in names:
            found.setdefault(name, []).append(rel)
    return found


def produced_names() -> set[str]:
    """The keys `build_env` returns, parsed from its own dict literal."""
    if not ENV_PRODUCER.is_file():
        return set()
    text = ENV_PRODUCER.read_text(encoding="utf-8")
    return set(re.findall(r'^\s*"([A-Z][A-Z0-9_]*)":\s', text, re.MULTILINE))


def documented() -> tuple[set[str], set[str]]:
    """(exact names, prefixes) declared by the doc's table rows.

    Only table rows count. Prose may name anything it likes - the stale check
    would otherwise fire on a sentence explaining why a variable was removed.
    """
    exact: set[str] = set()
    prefixes: set[str] = set()
    for line in DOC.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        cell = line.split("|")[1].strip()
        match = re.fullmatch(r"`([A-Z][A-Z0-9_]*)(<[A-Z_]+>)?`", cell)
        if not match:
            continue
        if match.group(2):
            prefixes.add(match.group(1))
        else:
            exact.add(match.group(1))
    return exact, prefixes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="accepted for symmetry with the other doc gates; this script only ever checks",
    )
    parser.parse_args()

    if not DOC.is_file():
        print(f"error: {DOC.relative_to(REPO)} is missing", file=sys.stderr)
        return 1

    reads = read_names()
    produced = produced_names()
    exact, prefixes = documented()

    def is_documented(name: str) -> bool:
        return name in exact or any(name.startswith(p) for p in prefixes)

    undocumented = {
        name: files
        for name, files in sorted(reads.items())
        if name not in PREREQUISITES and not is_documented(name)
    }
    missing_produced = sorted(name for name in produced if not is_documented(name))
    stale = sorted(
        name
        for name in exact
        if name not in reads and name not in produced and name not in PREREQUISITES
    )

    problems = 0
    if undocumented:
        problems += len(undocumented)
        print("Read by the code, absent from docs/reference/environment.md:", file=sys.stderr)
        for name, files in undocumented.items():
            print(f"  {name}  ({', '.join(sorted(set(files))[:3])})", file=sys.stderr)
    if missing_produced:
        problems += len(missing_produced)
        print(
            "\nProduced by build_env, absent from docs/reference/environment.md:",
            file=sys.stderr,
        )
        for name in missing_produced:
            print(f"  {name}", file=sys.stderr)
    if stale:
        problems += len(stale)
        print("\nListed in docs/reference/environment.md, read by nothing:", file=sys.stderr)
        for name in stale:
            print(f"  {name}", file=sys.stderr)
        print(
            "\nEither the variable came back under a new name, or the row should go.",
            file=sys.stderr,
        )

    if problems:
        print(
            f"\n{problems} problem(s). docs/reference/environment.md is the list C-04 asked for;",
            file=sys.stderr,
        )
        print("it is only worth having while it is complete.", file=sys.stderr)
        return 1

    read_count = len(reads) - len(PREREQUISITES & set(reads))
    print(
        f"docs/reference/environment.md matches the code: {read_count} read, "
        f"{len(produced)} produced, {len(prefixes)} pattern(s)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
