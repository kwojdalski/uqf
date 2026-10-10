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
enlisted value, in each of the forms q adds one with (#1061):

* `name[k]:enlist v`                       - indexed assignment
* `@[`.ns.name;k;:;enlist v]`              - functional amend
* `name,enlist[k]!enlist enlist v`         - a join, as `set` or `::` writes it
* `name,:enlist[k]!enlist enlist v`        - append in place
* `` `.ns.name upsert enlist[k]!enlist enlist v `` - upsert

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


def _writes(name: str) -> dict[str, re.Pattern[str]]:
    """Patterns for writes that ADD to `name`, by its bare or qualified name,
    each capturing the value written as `rhs`."""
    ref = rf"(?<![\w])(?:\.[\w.]+\.)?{re.escape(name)}"
    sym = rf"`(?:\.[\w.]+\.)?{re.escape(name)}"
    return {
        # name[k]:v
        "indexed": re.compile(rf"{ref}\s*\[[^\]]*\]\s*:(?!:)\s*(?P<rhs>.*)"),
        # @[`.ns.name;k;:;v]
        "amended": re.compile(rf"@\[\s*{sym}\s*;[^;]*;\s*:\s*;\s*(?P<rhs>.*)"),
        # name,enlist[k]!v  - a join, as `set` or `::` writes it
        "joined": re.compile(rf"{ref}\s*,\s*enlist\s*\[[^\]]*\]\s*!\s*(?P<rhs>.*)"),
        # name,:v  - append in place
        "appended": re.compile(rf"{ref}\s*,:\s*(?P<rhs>.*)"),
        # `.ns.name upsert v
        "upserted": re.compile(rf"{sym}\s+upsert\s+(?P<rhs>.*)"),
    }


#: Writes whose value is ONE entry's value: it must be `enlist v`.
_ONE_VALUE = ("indexed", "amended")

#: Writes whose value is a dictionary of entries: its values must be
#: `enlist enlist v`, one enlist for the list of values and one per value.
_DICT_OF_ENTRIES = re.compile(r"!\s*enlist\s+enlist\b")


def problems(root: Path = REPO) -> list[str]:
    out: list[str] = []
    found = registries(root)
    for (rel, name), path in found.items():
        if (rel, name) in NOT_DICTS:
            continue
        writes = _writes(name)
        for no, raw in enumerate(path.read_text().splitlines(), 1):
            line = _comment_free(raw)
            for form, pattern in writes.items():
                m = pattern.search(line)
                if not m:
                    continue
                rhs = m.group("rhs").lstrip()
                if form in _ONE_VALUE:
                    if not rhs.startswith("enlist"):
                        out.append(
                            f"{rel}:{no}: {name} gets a bare value from an {form} write - store "
                            f"it enlisted ({name}[k]:enlist v), or a dictionary value "
                            "collapses the registry into a table (#1030, #1061)"
                        )
                elif form == "joined":
                    if not re.match(r"enlist\s+enlist\b", rhs):
                        out.append(
                            f"{rel}:{no}: {name},enlist[k]!... needs `enlist enlist v` - "
                            "one enlist is the list of values, the second keeps each "
                            "value from joining a table (#1028, #1030)"
                        )
                elif not _DICT_OF_ENTRIES.search(rhs):
                    out.append(
                        f"{rel}:{no}: {name} gets entries from an {form} write whose values are "
                        "not `enlist enlist v` - write enlist[k]!enlist enlist v, or "
                        "a dictionary value collapses the registry (#1061)"
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
