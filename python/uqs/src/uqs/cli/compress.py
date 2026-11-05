"""`uqs data compress`: the HDB's compression, through TorQ's own `.cmp`.

`--report` reads what the HDB holds now (stack/hdb_compression.py): per table
and column the algorithm, block size and ratio, the largest uncompressed
partitions, and the partitions out of the order, or without the attribute,
that sort.csv declares. It only reports; it never compresses and never sorts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer
from rich.table import Table

from uqs.cli.hdb_scope import HdbOpt, resolve
from uqs.cli.json_out import Verbatim, emit, json_opt
from uqs.cli.safety import ReadOnly
from uqs.cli.shared import _die, console, data_app
from uqs.paths import UqsError, UqsUsageError
from uqs.stack import hdb_compression

#: How many of the largest uncompressed partitions `--report` lists by default.
DEFAULT_LIMIT = 10

ReportJson = json_opt(
    "{hdb, present, columns: [{table, column, files, compressed, algos, blocksizes, bytes, "
    "raw_bytes, ratio, compressed_ratio}], largest_uncompressed: [{table, partition, files, "
    "bytes}], unsorted: [{table, partition, rows, sort_by, sorted, missing_attr, declared_attr}]}"
)


def _mb(n: float) -> str:
    return f"{n / 2**20:,.1f} MB"


def _ratio(x: float | None) -> str:
    return "-" if x is None else f"{x:,.1f}x"


def _ints(xs: list[int]) -> str:
    return ",".join(str(x) for x in xs) or "-"


def _why(u: dict[str, Any]) -> str:
    """What is wrong with one table directory, in words."""
    why = [] if u["sorted"] else [f"not sorted by {', '.join(u['sort_by'])}"]
    if u["missing_attr"]:
        declared = u["declared_attr"]
        why.append("missing " + ", ".join(f"{declared[c]}#{c}" for c in u["missing_attr"]))
    return "; ".join(why)


def _print_report(r: dict[str, Any], hdb: str) -> None:
    cols = r["columns"]
    if not cols:
        console.print(f"[yellow]no table files under {hdb}[/] - nothing to report")
        return
    t = Table(title=f"compression by column - {hdb}")
    right = ("files", "compressed", "on disk", "ratio")
    for h in ("table", "column", "files", "compressed", "algo", "block", "on disk", "ratio"):
        t.add_column(h, justify="right" if h in right else "left")
    for c in cols:
        t.add_row(
            c["table"], c["column"], str(c["files"]), str(c["compressed"]),
            _ints(c["algos"]), _ints(c["blocksizes"]), _mb(c["bytes"]),
            _ratio(c["compressed_ratio"]),
        )  # fmt: skip
    console.print(t)
    big = r["largest_uncompressed"]
    if big:
        b = Table(title="largest uncompressed partitions")
        for h in ("table", "partition", "files", "bytes"):
            b.add_column(h, justify="right" if h in ("files", "bytes") else "left")
        for p in big:
            b.add_row(p["table"], str(p["partition"]), str(p["files"]), _mb(p["bytes"]))
        console.print(b)
    else:
        console.print("[green]every column file is compressed[/]")
    bad = r["unsorted"]
    if not bad:
        console.print("[green]every partition is in its sort.csv order, with its attributes[/]")
        return
    for u in bad:
        console.print(f"[yellow]{u['table']} {u['partition']}[/] ({u['rows']} rows): {_why(u)}")
    console.print(
        "\n[dim]Sorted data compresses far better than unsorted (KX's whitepaper "
        "measures 669x against 10x). This report never re-sorts: TorQ's wdb sorts "
        "and applies the attributes at end of day, from sort.csv.[/]"
    )


@data_app.command("compress", cls=ReadOnly)
def compress(
    report: Annotated[
        bool,
        typer.Option(
            "--report",
            help="How the HDB is compressed now, per table and column, and which partitions "
            "are unsorted or lack their sort.csv attribute; changes nothing",
        ),
    ] = False,
    limit: Annotated[
        int,
        typer.Option(
            "--limit", min=1, help="Show at most N of the largest uncompressed partitions"
        ),
    ] = DEFAULT_LIMIT,
    hdb: HdbOpt = None,
    as_json: ReportJson = False,
) -> None:
    """The HDB's compression, through TorQ's own `.cmp`.

    `--report` lists, per table and column, the algorithm, block size and
    compression ratio `-21!` reads; the largest uncompressed partitions; and
    the partitions that are not sorted, or lack the attribute, that sort.csv
    declares - the order and attribute the wdb gives a partition at end of
    day. It reads file headers and the sort columns and writes nothing.
    Without --hdb: this runtime's HDB (`uqs --target TARGET data compress
    --report` is a release's own).
    """
    try:
        if not report:
            raise UqsUsageError(
                "`uqs data compress` needs a mode: --report reads how the HDB is compressed"
            )
        handle = resolve(None, hdb)
        root = Path(handle.hdb)
        if not root.is_dir():
            if as_json:
                emit({"hdb": str(root), "present": False})
            else:
                console.print(f"[yellow]no HDB at {root}[/] - nothing to report")
            return
        r = hdb_compression.report(handle.paths, limit, hdb=root)
    except UqsError as exc:
        _die(exc)
        return
    if as_json:
        unsorted = [{**u, "declared_attr": Verbatim(u["declared_attr"])} for u in r["unsorted"]]
        emit({"hdb": str(root), "present": True, **r, "unsorted": unsorted})
        return
    _print_report(r, str(root))
