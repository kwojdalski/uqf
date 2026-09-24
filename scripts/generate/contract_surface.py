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

The q half is produced by `scripts/generate/export_contract_surface.q`, which must run
under KDB-X; this script invokes it and merges the result.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Literal

# parents[2], not parent.parent: this file sits one level deeper since
# scripts/ was foldered (#241). Getting it wrong does not raise - it
# resolves to scripts/ and the checker reports over an empty tree.
REPO = Path(__file__).resolve().parents[2]
Q_EXPORTER = REPO / "scripts" / "generate" / "export_contract_surface.q"

#: The committed export of this tree's own surface. Checked in so a
#: comparison against the authority needs only the authority's half, and
#: gated below so it cannot quietly go stale - a committed baseline nothing
#: verifies is the `.qmatz.require_schema` shape, where a file's existence
#: reads as protection it is not providing.
#:
#: A DIRECTORY OF CSVs rather than one JSON file, and the reason is review.
#: The surface is read by people comparing two trees, and JSON at indent=2
#: puts every list element on its own line: adding nine tables cost 208 diff
#: lines, where the same change is 54 rows here. A renamed function is one
#: changed line instead of a multi-line block, and the whole surface is ~670
#: rows rather than 4,911 lines.
#:
#: Not q, despite this being a q repository. Nothing in q reads this - it is
#: produced by Python merging three sources (q via subprocess, the pipeline
#: registry, an env scan) and consumed by Python alone. A q file no q process
#: loads is the shape this tree keeps finding and deleting. `docs/man.q` is a
#: q artifact because a q session loads it; this is not that. CSV keeps the
#: door open anyway: q reads it natively with `0:` if a consumer appears.
BASELINE = REPO / "docs" / "reference" / "surfaces" / "uqf-local"


def _kdbx() -> tuple[str, dict[str, str]]:
    """Locate KDB-X, or fail with the reason.

    No fallback interpreter. This tree targets KDB-X alone, and a surface
    exported from anything else would be a quietly incomplete contract - the
    worst kind for this purpose.
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
        "no KDB-X interpreter (~/.kx/bin/q or $UQFQ). There is deliberately no "
        "fallback: another interpreter would export a partial surface rather "
        "than fail, which is the one outcome this must not produce."
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
    sys.path.insert(0, str(REPO / "python" / "uqs" / "src"))
    from uqs.model.pipelines import PIPELINE_OFFSETS  # noqa: PLC0415
    from uqs.model.registry import PIPELINES  # noqa: PLC0415

    processes = [
        {
            "procname": p.procname,
            "proctype": p.proctype,
            "offset": PIPELINE_OFFSETS[p.procname],
            "subscribes": sorted(p.subscribed_tables),
            "publishes": sorted(p.published_tables),
        }
        for p in sorted(PIPELINES, key=lambda p: p.procname)
    ]
    return {"processes": processes}


def export_env_surface() -> dict[str, Any]:
    """Environment variables, from the reference page the gate already holds
    to the code. Reusing it rather than re-deriving means the two
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


# ------------------------------------------------------------ serialisation

#: One file per relation. The surface is four relations once flattened, and
#: one wide file with a `kind` discriminator and mostly-empty cells would be
#: worse than the JSON it replaces.
FILES = ("functions.csv", "table_columns.csv", "processes.csv", "variables.csv", "meta.csv")

#: Lists live in one cell, space-separated. Safe because no q identifier,
#: table name or parameter name contains a space - asserted by
#: test_contract_surface.py rather than assumed, since the day one does the
#: surface would silently mis-round-trip rather than fail.
_SEP = " "


def _join(values: list[str]) -> str:
    return _SEP.join(values)


def _split(cell: str) -> list[str]:
    return cell.split(_SEP) if cell else []


def write_surface(out_dir: Path, surface: dict[str, Any]) -> None:
    """Write the surface as one CSV per relation, sorted for stable diffs."""
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for ns in sorted(surface.get("functions", {})):
        for entry in sorted(surface["functions"][ns], key=lambda e: e["name"]):
            rows.append(
                {
                    "namespace": ns,
                    "name": entry["name"],
                    "kind": entry["kind"],
                    # Empty for a non-function. That is not just a blank: it is
                    # what tells the reader back that `params` was [] rather
                    # than [""], the q spelling of a niladic. The two always
                    # coincide, and test_contract_surface.py holds them to it.
                    "rank": "" if entry["rank"] is None else str(entry["rank"]),
                    "params": _join(entry["params"]),
                }
            )
    _write_csv(out_dir / "functions.csv", ["namespace", "name", "kind", "rank", "params"], rows)

    rows = []
    for table in sorted(surface.get("tables", {})):
        spec = surface["tables"][table]
        for column, qtype in zip(spec["columns"], spec["types"], strict=True):
            rows.append({"table": table, "column": column, "type": qtype})
    # QUOTE_ALL here and nowhere else. q's `meta` reports a GENERAL (mixed)
    # column's type as a literal SPACE, so `crypto_book,bid_prices, ` ends in
    # whitespace that is invisible in review and - worse - stripped by this
    # repository's own trailing-whitespace pre-commit hook, which would turn
    # the type into an empty string and leave `check` failing against a file
    # nothing could regenerate. Quoting makes the space survive.
    _write_csv(
        out_dir / "table_columns.csv",
        ["table", "column", "type"],
        rows,
        quoting=csv.QUOTE_ALL,
    )

    rows = [
        {
            "procname": p["procname"],
            "proctype": p["proctype"],
            "offset": str(p["offset"]),
            "subscribes": _join(p["subscribes"]),
            "publishes": _join(p["publishes"]),
        }
        for p in sorted(surface.get("processes", []), key=lambda p: p["procname"])
    ]
    _write_csv(
        out_dir / "processes.csv",
        ["procname", "proctype", "offset", "subscribes", "publishes"],
        rows,
    )

    _write_csv(
        out_dir / "variables.csv",
        ["name"],
        [{"name": v} for v in sorted(surface.get("variables", []))],
    )

    # Provenance has nowhere else to go: CSV has no comment syntax, and
    # dropping it would lose which interpreter produced the export.
    _write_csv(
        out_dir / "meta.csv",
        ["key", "value"],
        [{"key": "generated_by", "value": surface.get("generated_by", "")}],
    )


def _write_csv(
    path: Path,
    fieldnames: list[str],
    rows: list[dict[str, str]],
    quoting: Literal[0, 1, 2, 3, 4, 5] = csv.QUOTE_MINIMAL,
) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n", quoting=quoting)
        writer.writeheader()
        writer.writerows(rows)


def read_surface(in_dir: Path) -> dict[str, Any]:
    """Read a CSV surface back into the same shape `build_surface` produces.

    The round trip must be exact, because `diff_surfaces` compares the two and
    any asymmetry would read as a contract change that never happened.
    """
    functions: dict[str, list[dict[str, Any]]] = {}
    for row in _read_csv(in_dir / "functions.csv"):
        rank = int(row["rank"]) if row["rank"] else None
        # A blank `params` means [""] for a function and [] for anything
        # else - see write_surface's note on `rank`.
        if row["params"]:
            params = _split(row["params"])
        else:
            params = [] if rank is None else [""]
        functions.setdefault(row["namespace"], []).append(
            {"kind": row["kind"], "name": row["name"], "params": params, "rank": rank}
        )

    tables: dict[str, dict[str, list[str]]] = {}
    for row in _read_csv(in_dir / "table_columns.csv"):
        spec = tables.setdefault(row["table"], {"columns": [], "types": []})
        spec["columns"].append(row["column"])
        spec["types"].append(row["type"])

    processes = [
        {
            "procname": row["procname"],
            "proctype": row["proctype"],
            "offset": int(row["offset"]),
            "subscribes": _split(row["subscribes"]),
            "publishes": _split(row["publishes"]),
        }
        for row in _read_csv(in_dir / "processes.csv")
    ]

    meta = {row["key"]: row["value"] for row in _read_csv(in_dir / "meta.csv")}

    return {
        "functions": functions,
        "generated_by": meta.get("generated_by", ""),
        "namespaces": sorted(functions),
        "processes": processes,
        "tables": tables,
        "variables": [row["name"] for row in _read_csv(in_dir / "variables.csv")],
    }


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError(f"{path} is missing from the surface")
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


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

    exp = sub.add_parser("export", help="write this tree's contract surface as CSV")
    exp.add_argument("-o", "--out", type=Path, help="output directory (default: the baseline)")

    dif = sub.add_parser("diff", help="report contract differences between two exports")
    dif.add_argument("a", type=Path, help="a surface directory")
    dif.add_argument("b", type=Path, help="a surface directory")

    sub.add_parser(
        "check",
        help="fail if the committed baseline no longer matches this tree",
    )

    args = ap.parse_args()

    if args.command == "export":
        surface = build_surface()
        out = args.out or BASELINE
        write_surface(out, surface)
        fns = sum(len(v) for v in surface.get("functions", {}).values())
        print(
            f"wrote {out}/: {len(surface.get('namespaces', []))} namespace(s), "
            f"{fns} name(s), {len(surface.get('tables', {}))} table(s), "
            f"{len(surface.get('processes', []))} process(es), "
            f"{len(surface.get('variables', []))} env var(s)"
        )
        return 0

    if args.command == "check":
        missing = [f for f in FILES if not (BASELINE / f).is_file()]
        if missing:
            print(
                f"{BASELINE.relative_to(REPO)}/ is missing {', '.join(missing)} - run "
                f"`contract_surface.py export`",
                file=sys.stderr,
            )
            return 1
        committed = read_surface(BASELINE)
        current = build_surface()
        lines = diff_surfaces(committed, current, "committed", "current")
        if not lines:
            print(f"{BASELINE.relative_to(REPO)} matches this tree's contract surface")
            return 0
        # Report the CONTRACT difference, not a JSON diff. "is_covered gained
        # an argument" is actionable; "line 2118 differs" is not, and the
        # latter is what a plain file comparison would have said.
        print(f"{BASELINE.relative_to(REPO)} is stale:", file=sys.stderr)
        print("\n".join(lines), file=sys.stderr)
        print(
            "\nIf the change is intended, rerun:\n"
            "  uv run python scripts/generate/contract_surface.py export",
            file=sys.stderr,
        )
        return 1

    a = read_surface(args.a)
    b = read_surface(args.b)
    lines = diff_surfaces(a, b, args.a.name, args.b.name)
    if not lines:
        print("no contract differences")
        return 0
    print("\n".join(lines))
    return 1


if __name__ == "__main__":
    sys.exit(main())
