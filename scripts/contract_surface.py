#!/usr/bin/env python3
"""Export this tree's public contract surface, and diff two exports.

The half of WP2 (#139, "lock the contracts before implementation ports") that
does **not** need the authority checkout. Locking contracts means comparing
two surfaces; this produces one of them mechanically, so when the other tree
is readable the comparison is a diff rather than a person reading 29 q
namespaces and nine Python modules side by side.

Two subcommands:

    export              write this tree's surface as JSON
    diff A.json B.json  report what differs, by kind

WHAT COUNTS AS A CONTRACT. A public function's name, rank and parameter names;
a table's columns and types; a process name and its port offset; an
environment variable; a CLI command. **Not** implementation bodies - two trees
may implement a function differently and still honour the same contract, and
diffing bodies would bury the handful of differences that matter under
hundreds that do not.

Parameter names are included even though q does not enforce them at call
sites, because they are the closest thing q has to a signature. A silently
reordered pair of same-typed arguments compiles, passes every shape check, and
returns a wrong number - a defect this repository has already shipped and
fixed once.

The q half is produced by `scripts/export_contract_surface.q`, which must run
under KDB-X; this script invokes it and merges the result.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
Q_EXPORTER = REPO / "scripts" / "export_contract_surface.q"


def _kdbx() -> tuple[str, dict[str, str]]:
    """Locate KDB-X, or fail with the reason.

    Deliberately not falling back to the repository-root ./q: that is PeachQ,
    which cannot load the ETL tree, and a surface exported from a partial load
    would be a quietly incomplete contract - the worst kind for this purpose.
    """
    env = os.environ.copy()
    override = env.get("UQFQ")
    if override and Path(override).is_file():
        return override, env
    kdbx = Path.home() / ".kx" / "bin" / "q"
    if kdbx.is_file():
        env.setdefault("QHOME", str(Path.home() / ".kx"))
        return str(kdbx), env
    raise SystemExit(
        "no KDB-X interpreter (~/.kx/bin/q or $UQFQ). The repository-root ./q "
        "is PeachQ and cannot load the ETL tree, so it would export a partial "
        "surface rather than fail - which is why it is not a fallback."
    )


def export_q_surface() -> dict[str, Any]:
    """Run the q exporter and parse its JSON."""
    qbin, env = _kdbx()
    result = subprocess.run(
        [qbin, str(Q_EXPORTER.relative_to(REPO)), "-q"],
        cwd=REPO,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise SystemExit(
            f"the q exporter failed:\n{result.stdout.decode(errors='replace')}\n"
            f"{result.stderr.decode(errors='replace')}"
        )
    out = result.stdout.decode(errors="replace").strip()
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"the q exporter did not emit JSON: {exc}\n{out[:400]}") from None


def export_python_surface() -> dict[str, Any]:
    """The Python side: processes, their ports, and the CLI commands.

    Imported rather than parsed, because `PIPELINE_OFFSETS` is *computed* -
    an auto-allocated pipeline has `offset=None` and its real port is
    assigned at import. Reading the literal would export the declaration
    rather than the contract, and those differ for 8 of 9 processes.
    """
    sys.path.insert(0, str(REPO / "python" / "torq_orchestrator" / "src"))
    from torq_orchestrator.pipelines import (  # noqa: PLC0415
        PIPELINE_OFFSETS,
        PIPELINES,
    )

    processes = [
        {
            "procname": p.procname,
            "proctype": p.proctype,
            "offset": PIPELINE_OFFSETS[p.procname],
            "subscribes": sorted(p.subscribes),
            "publishes": sorted(p.published_tables),
        }
        for p in sorted(PIPELINES, key=lambda p: p.procname)
    ]
    return {"processes": processes}


def export_env_surface() -> dict[str, Any]:
    """Environment variables, from the reference page the gate already holds
    to the code (C-04). Reusing it rather than re-deriving means the two
    cannot disagree.
    """
    doc = REPO / "docs" / "reference" / "environment.md"
    if not doc.is_file():
        return {"variables": []}
    names = []
    for line in doc.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        cell = line.split("|")[1].strip()
        if cell.startswith("`") and cell.endswith("`"):
            inner = cell[1:-1]
            if inner and inner[0].isupper():
                names.append(inner)
    return {"variables": sorted(set(names))}


def build_surface() -> dict[str, Any]:
    surface = export_q_surface()
    surface.update(export_python_surface())
    surface.update(export_env_surface())
    return surface


# --------------------------------------------------------------------- diff


def _fn_index(surface: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """`.qns.name` -> its entry, flattened for comparison."""
    out: dict[str, dict[str, Any]] = {}
    for ns, entries in surface.get("functions", {}).items():
        for entry in entries:
            out[f".{ns}.{entry['name']}"] = entry
    return out


def diff_surfaces(a: dict[str, Any], b: dict[str, Any], a_name: str, b_name: str) -> list[str]:
    """Report every contract difference, grouped by kind.

    Ordered most-consequential first: a function that exists on one side only
    is a bigger fact than one whose parameter was renamed, and burying the
    former under fifty of the latter is how a reviewer misses it.
    """
    lines: list[str] = []
    fa, fb = _fn_index(a), _fn_index(b)

    only_a = sorted(set(fa) - set(fb))
    only_b = sorted(set(fb) - set(fa))
    both = sorted(set(fa) & set(fb))

    rank_changed = [n for n in both if fa[n].get("rank") != fb[n].get("rank")]
    params_changed = [
        n
        for n in both
        if fa[n].get("rank") == fb[n].get("rank") and fa[n].get("params") != fb[n].get("params")
    ]
    kind_changed = [n for n in both if fa[n].get("kind") != fb[n].get("kind")]

    if rank_changed:
        lines.append(f"## Rank changed ({len(rank_changed)}) - a caller WILL break")
        for n in rank_changed:
            lines.append(f"  {n}: {fa[n]['rank']} -> {fb[n]['rank']}")
    if kind_changed:
        lines.append(f"\n## Kind changed ({len(kind_changed)})")
        for n in kind_changed:
            lines.append(f"  {n}: {fa[n]['kind']} -> {fb[n]['kind']}")
    if only_a:
        lines.append(f"\n## Only in {a_name} ({len(only_a)})")
        lines += [f"  {n}" for n in only_a]
    if only_b:
        lines.append(f"\n## Only in {b_name} ({len(only_b)})")
        lines += [f"  {n}" for n in only_b]
    if params_changed:
        lines.append(
            f"\n## Parameter names changed ({len(params_changed)}) - same rank, so this "
            "compiles either way"
        )
        for n in params_changed:
            lines.append(f"  {n}: {fa[n]['params']} -> {fb[n]['params']}")

    ta, tb = a.get("tables", {}) or {}, b.get("tables", {}) or {}
    for name in sorted(set(ta) | set(tb)):
        if name not in ta:
            lines.append(f"\n## Table only in {b_name}: {name}")
        elif name not in tb:
            lines.append(f"\n## Table only in {a_name}: {name}")
        elif ta[name] != tb[name]:
            lines.append(f"\n## Table schema differs: {name}")
            # strict=True: a table whose column and type lists differ in
            # length is a malformed export, and silently truncating to the
            # shorter one would hide exactly the schema drift being hunted.
            lines.append(
                f"  {a_name}: {list(zip(ta[name]['columns'], ta[name]['types'], strict=True))}"
            )
            lines.append(
                f"  {b_name}: {list(zip(tb[name]['columns'], tb[name]['types'], strict=True))}"
            )

    pa = {p["procname"]: p for p in a.get("processes", [])}
    pb = {p["procname"]: p for p in b.get("processes", [])}
    for name in sorted(set(pa) | set(pb)):
        if name not in pa:
            lines.append(f"\n## Process only in {b_name}: {name}")
        elif name not in pb:
            lines.append(f"\n## Process only in {a_name}: {name}")
        elif pa[name] != pb[name]:
            lines.append(f"\n## Process differs: {name}")
            lines.append(f"  {a_name}: {pa[name]}")
            lines.append(f"  {b_name}: {pb[name]}")

    ea = set(a.get("variables", []))
    eb = set(b.get("variables", []))
    if ea - eb:
        lines.append(f"\n## Environment variables only in {a_name}")
        lines += [f"  {v}" for v in sorted(ea - eb)]
    if eb - ea:
        lines.append(f"\n## Environment variables only in {b_name}")
        lines += [f"  {v}" for v in sorted(eb - ea)]

    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)

    exp = sub.add_parser("export", help="write this tree's contract surface as JSON")
    exp.add_argument("-o", "--out", type=Path, help="output file (default: stdout)")

    dif = sub.add_parser("diff", help="report contract differences between two exports")
    dif.add_argument("a", type=Path)
    dif.add_argument("b", type=Path)

    args = ap.parse_args()

    if args.command == "export":
        surface = build_surface()
        text = json.dumps(surface, indent=2, sort_keys=True) + "\n"
        if args.out:
            args.out.write_text(text, encoding="utf-8")
            fns = sum(len(v) for v in surface.get("functions", {}).values())
            print(
                f"wrote {args.out}: {len(surface.get('namespaces', []))} namespace(s), "
                f"{fns} name(s), {len(surface.get('tables', {}))} table(s), "
                f"{len(surface.get('processes', []))} process(es), "
                f"{len(surface.get('variables', []))} env var(s)"
            )
        else:
            sys.stdout.write(text)
        return 0

    a = json.loads(args.a.read_text(encoding="utf-8"))
    b = json.loads(args.b.read_text(encoding="utf-8"))
    lines = diff_surfaces(a, b, args.a.stem, args.b.stem)
    if not lines:
        print("no contract differences")
        return 0
    print("\n".join(lines))
    return 1


if __name__ == "__main__":
    sys.exit(main())
