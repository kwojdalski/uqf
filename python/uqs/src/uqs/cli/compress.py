"""`uqs data compress`: the HDB's compression, through TorQ's own `.cmp`.

Without --report, the plan - and with --apply, the run - of
cli/compress_plan.py (#2147, #2148): what TorQ's .cmp would compress, the
estimate, the disk it needs; then one partition at a time, audited.

`--report` reads what the HDB holds now (stack/hdb_compression.py): per table
and column the algorithm, block size and ratio, the largest uncompressed
partitions, and the partitions out of the order, or without the attribute,
that sort.csv declares. It only reports; it never compresses and never sorts.

`uqs --target TARGET data compress --report --hdb DIR` reads DIR on the target,
by a fixed helper over the target's ssh - for a TorQ server that has q and an
HDB but no uqf (#2219, stack/hdb_compression.py). It needs no remote uqs and no
deploy.env, and it is a read, so an inspection-only target runs it too. Without
--hdb the command is the release's own, run there, as before. `--apply` never
reaches a directory --hdb names on a target (cli/target.py refuses it): the
writer lock, the coverage hold and the reload are a managed deployment's.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer
from rich.table import Table

from uqs.cli import completion, compress_plan, safety
from uqs.cli.compress_plan import AlgoOpt
from uqs.cli.hdb_scope import AttachOpt, Handle, HdbOpt, resolve
from uqs.cli.json_out import Verbatim, emit, json_opt
from uqs.cli.range_options import DateOpt, DayFromOpt, DayLastOpt, DayToOpt
from uqs.cli.safety import ConfirmsOnServer
from uqs.cli.shared import BasePortOpt, _die, console, data_app
from uqs.cli.vocabulary import duration
from uqs.paths import UqsError, UqsUsageError
from uqs.stack import hdb_compression

#: How many of the largest uncompressed partitions `--report` lists by default.
DEFAULT_LIMIT = 10

CompressJson = json_opt(
    "the plan's {ok, hdb, process, scope, config, policies, held, partitions, sample, ratios, "
    "disk, totals, result}; with --report {hdb, target, present, columns: [{table, column, "
    "files, compressed, algos, blocksizes, bytes, "
    "raw_bytes, ratio, compressed_ratio}], largest_uncompressed: [{table, partition, files, "
    "bytes}], sort_checked, sort_csv, unsorted: [{table, partition, rows, sort_by, sorted, "
    "missing_attr, declared_attr}], or null when sort_checked is false}"
)

#: Said once before a report reads: it writes nothing, but it is not free.
READ_IO_NOTE = (
    "the report writes nothing, but it reads every column file's header and the sort "
    "columns of every partition: on a large HDB that is substantial read I/O on {where}"
)
#: What the text report says when no sort.csv was given to judge by.
SORT_NOT_CHECKED = (
    "sorting not checked: no sort.csv to judge it by - give one with --sort-csv FILE "
    "(a path on the target, under --target)"
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
    if bad is None:
        console.print(f"[yellow]{SORT_NOT_CHECKED}[/]")
        return
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


@data_app.command("compress", cls=ConfirmsOnServer)
def compress(
    table: Annotated[
        list[str] | None,
        typer.Option(
            "--table",
            metavar="TABLE",
            help="Compress only this table; repeatable, and TABLE,TABLE also works",
            autocompletion=completion.plant_table,
        ),
    ] = None,
    older_than: Annotated[
        int | None,
        duration(
            "--older-than",
            integer=True,
            help="Only partitions at least this old, a DURATION in whole days (7d), on top "
            "of the policy's own minimum age",
        ),
    ] = None,
    range_from: DayFromOpt = None,
    range_to: DayToOpt = None,
    day: DateOpt = None,
    last: DayLastOpt = None,
    algo: AlgoOpt = None,
    blocksize: Annotated[
        int | None,
        typer.Option(
            "--blocksize",
            help="This run only: the logical block size, a power of two (16 is 64 KB), "
            "instead of the policy's",
        ),
    ] = None,
    level: Annotated[
        int | None,
        typer.Option(
            "--gzip-level",
            help="This run only: gzip's level, 0-9, instead of the policy's (`--level` is "
            "the log level everywhere)",
        ),
    ] = None,
    base_port: BasePortOpt = None,
    apply: safety.ApplyOpt = False,
    yes: safety.ApplyYesOpt = False,
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
    attach: AttachOpt = None,
    sort_csv: Annotated[
        str | None,
        typer.Option(
            "--sort-csv",
            metavar="FILE",
            help="With --report: judge sorting by this sort.csv, a path on the machine the "
            "report reads (the target's, under --target). Without it --hdb on a target is "
            "reported as sorting not checked",
        ),
    ] = None,
    as_json: CompressJson = False,
) -> None:
    """The HDB's compression, through TorQ's own `.cmp`: plan, run or report it.

    Without --report: what TorQ's .cmp, in housekeeping1, would compress -
    the partitions and columns and their state now, which policy each table
    takes, the saving estimated on a sample, the free disk the run needs.
    Partitions the coverage ledger says are still being written are held.
    --date/--from/--to/--last name whole days, compressed whatever the
    policy's minimum age - a bound off a midnight is refused, and --last 3d is
    the three days before today; --algo/--blocksize/--gzip-level change this run alone. With
    --apply, after asking: one partition at a time, each checked, stopping at
    the first failure, then one HDB reload.

    `--report` lists, per table and column, the algorithm, block size and
    compression ratio `-21!` reads; the largest uncompressed partitions; and
    the partitions that are not sorted, or lack the attribute, that sort.csv
    declares - the order and attribute the wdb gives a partition at end of
    day. It reads file headers and the sort columns and writes nothing - on
    a large HDB that is still substantial read I/O. Without --hdb or
    --attachment (an attachment whose HDB is on this machine): this runtime's
    HDB (`uqs --target TARGET data compress --report` is a release's own).
    `uqs --target TARGET data compress --report --hdb DIR` reads DIR on the
    target, over its ssh with its q, and needs no uqf there; its sorting is
    checked only against --sort-csv's file.
    """
    try:
        if not report:
            given = (("--hdb", hdb), ("--attachment", attach), ("--sort-csv", sort_csv))
            if flags := [f for f, v in given if v is not None]:
                raise UqsUsageError(
                    f"{flags[0]} is for --report: the plan and the run are housekeeping1's, on "
                    "this runtime's HDB (`uqs --target TARGET data compress` for a release's)"
                )
            sc = compress_plan.scope_given(
                table, older_than, base_port, range_from, range_to, day, last,
                algo, blocksize, level,
            )  # fmt: skip
            compress_plan.plan_and_apply(sc, apply=apply, yes=yes, as_json=as_json)
            return
        plan_options = {
            "--apply": apply, "--table": table, "--older-than": older_than, "--date": day,
            "--from": range_from, "--to": range_to, "--last": last, "--algo": algo,
            "--blocksize": blocksize, "--gzip-level": level,
        }  # fmt: skip
        if given := [f for f, v in plan_options.items() if v]:
            raise UqsUsageError(
                f"--report reads the whole HDB and changes nothing: not with {', '.join(given)}"
            )
        handle = resolve(attach, hdb)
        if handle.ssh:
            raise UqsUsageError(
                f"--report reads the HDB's files on this machine, and {attach}'s is on "
                f"{handle.ssh}: run `uqs data compress --report --hdb {handle.hdb}` there"
            )
        if handle.target is not None:
            r, shown = _target_report(handle, limit, sort_csv, as_json), handle.hdb
        else:
            root = Path(handle.hdb)
            if not root.is_dir():
                if as_json:
                    emit({"hdb": str(root), "present": False})
                else:
                    console.print(f"[yellow]no HDB at {root}[/] - nothing to report")
                return
            if not as_json:
                console.print(f"[dim]{READ_IO_NOTE.format(where='this machine')}[/]")
            sort = Path(sort_csv).expanduser().resolve() if sort_csv else None
            sort = sort or hdb_compression.sort_csv(handle.paths)
            r = hdb_compression.report(handle.paths, limit, hdb=root, sort=sort)
            shown = str(root)
    except UqsError as exc:
        _die(exc)
    target = handle.target.name if handle.target else None
    if as_json:
        unsorted = r["unsorted"]
        if unsorted is not None:
            unsorted = [{**u, "declared_attr": Verbatim(u["declared_attr"])} for u in unsorted]
        emit({"hdb": shown, "target": target, "present": True, **r, "unsorted": unsorted})
        return
    _print_report(r, f"{shown} on target {target}" if target else shown)


def _target_report(
    handle: Handle, limit: int, sort_csv: str | None, as_json: bool
) -> dict[str, Any]:
    """The report of a directory on a target, by the fixed helper over its ssh."""
    assert handle.target is not None
    if not as_json:
        where = f"target {handle.target.name}"
        console.print(f"[dim]{READ_IO_NOTE.format(where=where)}[/]")
    return hdb_compression.remote_report(
        handle.paths, handle.target, handle.hdb, limit, sort=sort_csv
    )
