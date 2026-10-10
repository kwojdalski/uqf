#!/usr/bin/env python3
"""Hold every q registry under src/ to the enlist idiom (#1030).

A q dictionary of same-keyed dictionaries collapses into a keyed table on its
first insert, and from then on an entry with other keys is refused with a bare
`'mismatch` or `'type`. This tree's answer is to store each value enlisted -
`registry[name]:enlist decl`, read back with `first` - and that rule lived only
in comments, in three files. retention.q was written without it and shipped
unable to hold two kinds of declaration (#1028).

A REGISTRY is a name initialised as an empty symbol-keyed dictionary,
`name:(`symbol$())!()`. Every write that ADDS an entry to one must store an
enlisted value:

* `name[k]:enlist v`                  - indexed assignment
* `name,enlist[k]!enlist enlist v`    - a join, as `set` or `::` writes it

A registry whose values are never dictionaries (tables, symbol lists, status
lists) cannot collapse, and is listed in NOT_DICTS with the reason. That list
only shrinks: an entry naming a registry that no longer exists fails.

Writes that replace the whole registry (a reset, a filter, `_`) are not
additions and are not checked.

    python3 scripts/gates/check_registry_enlist.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

#: (file from the repo root, registry name) -> why its values are not dicts.
NOT_DICTS: dict[tuple[str, str], str] = {
    ("src/etl/core/react.q", "reactions"): "dataset -> a table of reactions, upserted",
    ("src/etl/core/stream_job.q", "carry_log"): "job -> a (status; rows; batches) list",
    ("src/etl/core/tick.q", "schemas"): "table -> its empty schema, a table",
    ("src/etl/core/config_audit.q", "watched"): "owner -> a symbol list of globals",
    ("src/etl/core/worker_runtime.q", "declared_dependencies"): "worker -> a symbol list",
    ("src/etl/generated/load_plan.q", "declares"): "file -> the names it declares, a symbol list",
    ("src/etl/generated/load_plan.q", "jobs"): "job -> the files it loads, a list of paths",
    ("src/etl/generated/load_plan.q", "procs"): "process -> the files it loads, a list of paths",
    ("src/etl/generated/load_plan.q", "sources"): "source -> the files it loads, a list of paths",
}

#: A registry's declaration: `name:(`symbol$())!()`, bare or fully qualified.
DECLARED = re.compile(r"^\s*(?P<name>[.\w]+)\s*:\s*\(`symbol\$\(\)\)!\(\)")


def _comment_free(line: str) -> str:
    """The line with a trailing ` / comment` and a whole-line `/` comment cut."""
    if line.lstrip().startswith("/"):
        return ""
    return re.split(r"\s/\s", line, maxsplit=1)[0]


def registries(root: Path) -> dict[tuple[str, str], Path]:
    """(file, name) for every registry declared under src/."""
    found: dict[tuple[str, str], Path] = {}
    for path in sorted((root / "src").rglob("*.q")):
        rel = path.relative_to(root).as_posix()
        for line in path.read_text().splitlines():
            m = DECLARED.match(_comment_free(line))
            if m:
                found[(rel, m.group("name").rsplit(".", 1)[-1])] = path
    return found


def _writes(name: str):
    """Patterns for writes that ADD to `name`, by its bare or qualified name."""
    ref = rf"(?<![\w])(?:\.[\w.]+\.)?{re.escape(name)}"
    indexed = re.compile(rf"{ref}\s*\[[^\]]*\]\s*:(?!:)\s*(?P<rhs>.*)")
    joined = re.compile(rf"{ref}\s*,\s*enlist\s*\[[^\]]*\]\s*!\s*(?P<rhs>.*)")
    return indexed, joined


def problems(root: Path = REPO) -> list[str]:
    out: list[str] = []
    found = registries(root)
    for (rel, name), path in found.items():
        if (rel, name) in NOT_DICTS:
            continue
        indexed, joined = _writes(name)
        for no, raw in enumerate(path.read_text().splitlines(), 1):
            line = _comment_free(raw)
            m = indexed.search(line)
            if m and not m.group("rhs").lstrip().startswith("enlist"):
                out.append(
                    f"{rel}:{no}: {name}[...] is assigned a bare value - store it "
                    f"enlisted ({name}[k]:enlist v), or a dictionary value collapses "
                    "the registry into a table (#1030)"
                )
            m = joined.search(line)
            if m and not re.match(r"enlist\s+enlist\b", m.group("rhs").lstrip()):
                out.append(
                    f"{rel}:{no}: {name},enlist[k]!... needs `enlist enlist v` - "
                    "one enlist is the list of values, the second keeps each value "
                    "from joining a table (#1028, #1030)"
                )
    for key in sorted(set(NOT_DICTS) - set(found)):
        out.append(
            f"NOT_DICTS lists {key[1]} in {key[0]}, which declares no such registry - remove it"
        )
    return out


def main() -> int:
    found = problems()
    for p in found:
        print(p, file=sys.stderr)
    if found:
        return 1
    print(f"check_registry_enlist: every registry write enlists, {len(NOT_DICTS)} hold no dicts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
