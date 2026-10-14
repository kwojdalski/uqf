#!/usr/bin/env python3
"""Fail if a `procname :port` label in a d2 diagram disagrees with the stack.

docs/diagrams/stack-topology.d2 hand-copies every procname and port into node
labels (`fxfeed1 :6069`). Its header names docs/reference/processes.md as the
authority, and that file is generated from scripts/processes/process_ports.csv
(uqf's processes) and the vendored process.csv (TorQ's). Nothing compared the
two, so the next port move would have left the picture showing the wrong
port while every gate passed (#1369; #890 fixed the names, not the ports).

The authority is read, not copied: the same composed process rows that
generate_operational_docs.py renders, resolved at the default base port with
the one port grammar (`uqs.portexpr`). A label whose procname is not a process
at all fails too - a renamed process whose box was left behind.

    uv run python scripts/gates/check_diagram_ports.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "python" / "uqs" / "src"))

DIAGRAMS = REPO / "docs" / "diagrams"

#: `fxfeed1 :6069` - a procname, one space, a four-digit port. Prose such as
#: "reachable on :6075" has no procname directly before the colon and is
#: deliberately not matched.
LABEL = re.compile(r"\b(?P<proc>[a-z][a-z0-9_]*) :(?P<port>\d{4,5})\b")


def authoritative_ports() -> dict[str, int]:
    """procname -> port at the default base, for every process in the stack."""
    from uqs import portexpr
    from uqs.model.registry import DEFAULT_BASE_PORT
    from uqs.paths import default_paths
    from uqs.stack import procs

    env = {"KDBBASEPORT": str(DEFAULT_BASE_PORT)}
    ports: dict[str, int] = {}
    for row in procs._composed_rows(default_paths()):
        port = portexpr.resolve(row.get("port"), env)
        if port is not None:
            ports[row["procname"]] = port
    return ports


def labels(text: str) -> list[tuple[int, str, int]]:
    """(line number, procname, port) for each label in *text*."""
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue  # a d2 comment is prose, not a label
        # d2 labels write a line break as the two characters `\n`; without
        # this the `n` of `\nwdb1` glues onto the procname.
        for m in LABEL.finditer(line.replace("\\n", " ")):
            out.append((n, m["proc"], int(m["port"])))
    return out


def problems(diagrams: Path, ports: dict[str, int]) -> list[str]:
    sources = sorted(diagrams.glob("*.d2"))
    if not sources:
        return [f"no .d2 sources under {diagrams}"]
    found: list[str] = []
    seen = 0
    for source in sources:
        for line, proc, port in labels(source.read_text()):
            seen += 1
            where = f"{source.name}:{line}"
            if proc not in ports:
                found.append(f"{where}: `{proc} :{port}` names a process that does not exist")
            elif ports[proc] != port:
                found.append(f"{where}: `{proc} :{port}` but {proc} is on {ports[proc]}")
    if seen == 0:
        # Refuse to pass over nothing: a changed label format would otherwise
        # quietly turn this gate into one that checks no label.
        found.append("no `procname :port` label found in any diagram")
    return found


def main() -> int:
    found = problems(DIAGRAMS, authoritative_ports())
    if found:
        print("check_diagram_ports: diagram ports disagree with the stack:", file=sys.stderr)
        for line in found:
            print(f"  {line}", file=sys.stderr)
        print(
            "\nAuthority: scripts/processes/process_ports.csv and the vendored process.csv "
            "(see docs/reference/processes.md).",
            file=sys.stderr,
        )
        return 1
    print("check_diagram_ports: every diagram port label matches the stack")
    return 0


if __name__ == "__main__":
    sys.exit(main())
