"""`uqs data compress`: an HDB's compression, planned and run by TorQ's .cmp (#2147, #2148).

uqs compresses nothing itself. TorQ's `.cmp` (lib/torq/code/common/compress.q)
plans and compresses, `.qtorq.compress` (scripts/processes/torq_compress.q)
checks every file it changed and asks the HDBs to reload, and
scripts/processes/torq_compress_survey.q scopes one run in .cmp's own config
rows and estimates it on a sample. This module asks for those over IPC and
does the arithmetic a person needs before saying yes: the free disk each
filesystem has against what the run will write there.

WHERE .cmp RUNS: housekeeping1. TorQ's own HDB-maintenance process, already in
every stack with startwithall=1, already loading .cmp with TorQ's common code -
TorQ's housekeeping.q compresses with `.cmp.compress` itself. It runs
housekeeping.q unchanged; VENDORED_LOAD_OVERLAY (stack/procs.py) points .cmp
at the generated policy and connects to the HDBs when it reloads them
(uqs_housekeeping_config.q's .qtorq.hk.reload) before it, and loads the wrapper,
the survey and the coverage ledger's reader after it. TorQ's
`compression` process was the other candidate and is not used: it runs
`.cmp.compressmaxage` over the whole HDB as it loads and exits, unchecked and
with discovery switched off - so it can neither show a plan, nor scope a run,
nor reach the HDBs to reload them.

The HDB is this runtime's own: `uqs --target T data compress` runs the command
on the release, against the release's HDB and its housekeeping1, which is how
every confirms-on-server command reaches a target (cli/target_confirm.py).
"""

from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from uqs.paths import UqsError, UqsPaths, UqsUsageError
from uqs.stack.listing import _list_processes

#: The TorQ process .cmp runs in, and its type.
PROCESS = "housekeeping1"

#: The rows of each column the estimate compresses.
SAMPLE_ROWS = 100_000

#: Seconds a survey may take (it scans the whole HDB and compresses a sample),
#: and one partition's run.
SURVEY_TIMEOUT_S = 1800
PARTITION_TIMEOUT_S = 3 * 3600

#: A day, in seconds: TorQ ages partitions in whole days.
DAY_S = 86_400

#: A table name as q declares one, checked before it is spliced into q.
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*")

#: TorQ's .cmp algorithms, by number (src/etl/core/compression.q's).
ALGOS = {0: "none", 1: "qipc", 2: "gzip", 3: "snappy"}

Query = Callable[..., Any]


@dataclass(frozen=True)
class Scope:
    """What one run may reach: these tables (all when empty), partitions at
    least `older_days` old - or, when `days` names them, the partitions in
    [first, last] whatever the policy's minimum age - and, for a one-off run,
    another algorithm, block size or level on .cmp's plan rows."""

    tables: tuple[str, ...] = ()
    older_days: int = 0
    #: the stack's base port, KDBBASEPORT; None for the runtime's own
    base_port: int | None = None
    #: (first, last) partition dates, inclusive, either None for open; None for the policy's
    days: tuple[date | None, date | None] | None = None
    #: calgo, cblocksize, clevel for this run alone, by .cmp's plan columns
    change: tuple[tuple[str, int], ...] = ()


def older_days(seconds: int | None) -> int:
    """`--older-than`, a DURATION, as the whole days TorQ ages partitions in.

    @raise UqsUsageError for a span that is not whole days."""
    if seconds is None:
        return 0
    if seconds < 0 or seconds % DAY_S:
        raise UqsUsageError(
            f"--older-than: TorQ ages partitions in whole days - give a whole number of days, "
            f"e.g. 7d (not {seconds}s)"
        )
    return seconds // DAY_S


def scope(
    tables: Iterable[str],
    seconds: int | None,
    base_port: int | None = None,
    days: tuple[date | None, date | None] | None = None,
    algo: str | None = None,
    blocksize: int | None = None,
    level: int | None = None,
) -> Scope:
    """The scope a command line names, each table checked to be a q name, and a
    one-off algorithm, block size or level given as .cmp's plan columns
    (calgo, cblocksize, clevel) - .cmp.checkcsv judges them in q."""
    names = tuple(dict.fromkeys(t.strip() for t in tables if t.strip()))
    if bad := [t for t in names if not _NAME.fullmatch(t)]:
        raise UqsUsageError(f"--table {', '.join(bad)}: not a table name")
    if days is not None and days[0] is not None and days[1] is not None and days[0] > days[1]:
        raise UqsUsageError("the range holds no whole partition day")
    by_name = {v: k for k, v in ALGOS.items()}
    change = tuple(
        (k, v)
        for k, v in (
            ("calgo", None if algo is None else by_name[algo]),
            ("cblocksize", blocksize),
            ("clevel", level),
        )
        if v is not None
    )
    return Scope(names, older_days(seconds), base_port, days, change)


def _q_days(days: tuple[date | None, date | None] | None) -> str:
    if days is None:
        return "()"
    return "(" + ";".join("0Nd" if d is None else d.strftime("%Y.%m.%d") for d in days) + ")"


def _q_change(change: tuple[tuple[str, int], ...]) -> str:
    if not change:
        return "()!()"
    keys = "".join(f"`{k}" for k, _ in change)
    vals = ";".join(str(int(v)) for _, v in change)
    return f"(enlist {keys})!enlist {vals}" if len(change) == 1 else f"{keys}!({vals})"


def _q_scope(sc: Scope) -> str:
    """`only;older;days;change`, as survey and apply_partition take them."""
    return f"{_q_symbols(sc.tables)};{sc.older_days};{_q_days(sc.days)};{_q_change(sc.change)}"


def _q_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _q_symbols(names: tuple[str, ...]) -> str:
    return "".join(f"`{n}" for n in names) if names else "`symbol$()"


def _hdb(paths: UqsPaths) -> str:
    return f"(hsym `${_q_string(str(paths.hdb_dir))})"


def engine_port(paths: UqsPaths, base_port: int | None = None) -> int:
    """housekeeping1's port in this runtime's stack on `base_port` (default: the
    runtime's), or a refusal saying why there is none."""
    for row in _list_processes(paths, base_port):
        if row["procname"] == PROCESS:
            return int(row["port"])
    raise UqsError(
        f"this runtime declares no {PROCESS}: TorQ's .cmp runs there, so `data compress` "
        "needs a runtime with the core infrastructure"
    )


def _ask(paths: UqsPaths, sc: Scope, expr: str, timeout: int, query: Query | None) -> Any:
    """`expr` on housekeeping1, its JSON answer read back; every failure named."""
    if query is None:
        from uqs.stack.runtime import query
    port = engine_port(paths, sc.base_port)
    try:
        said = query(f".j.j {expr}", port, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - kola raises its own, untyped, errors
        text = str(exc)
        low = text.lower()
        if "refused" in low or "connect" in low:
            raise UqsError(
                f"{PROCESS} (port {port}) is not answering: `uqs start {PROCESS}` - "
                "TorQ's .cmp runs there"
            ) from None
        name = text.strip().lstrip("'")
        if name.startswith((".qtorq.compress.", ".qtorq.hk.", ".cmp.")) and " " not in text:
            raise UqsError(
                f"{PROCESS} has no {name}: it was started before it "
                f"loaded one - `uqs restart {PROCESS}`"
            ) from None
        raise UqsError(f"{PROCESS}: {text}") from None
    raw = said.decode() if isinstance(said, bytes) else str(said)
    return json.loads(raw)


def survey(paths: UqsPaths, sc: Scope, *, query: Query | None = None) -> dict[str, Any]:
    """What a run of `sc` would do, as `.qtorq.compress.survey` returns it,
    with the disk each filesystem needs and has, and which policy each table
    takes."""
    expr = f".qtorq.compress.survey[{_hdb(paths)};.cmp.inputcsv;{_q_scope(sc)};{SAMPLE_ROWS}]"
    doc = _ask(paths, sc, expr, SURVEY_TIMEOUT_S, query)
    parts = doc.get("partitions") or []
    acting = [p for p in parts if p.get("status") == "act"]
    doc["policies"] = policies(
        doc.get("config") or [], {p["table"] for p in parts} | set(sc.tables)
    )
    doc["disk"] = headroom(acting)
    changing = [p for p in acting if changes(p)]
    now = sum(p["bytes_now"] for p in changing)
    after = sum(p["bytes_after"] for p in changing)
    doc["totals"] = {
        "partitions": len({p["partition"] for p in changing}),
        "files": sum(len(p["compress"]) + len(p["decompress"]) for p in changing),
        "bytes_now": now,
        "bytes_after": after,
        "bytes_saved": now - after,
    }
    return doc


def changes(part: dict[str, Any]) -> bool:
    """Whether a table partition has a file to compress or decompress."""
    return bool(part.get("compress") or part.get("decompress"))


def _row_policy(row: dict[str, Any]) -> dict[str, Any]:
    algo = int(row["calgo"])
    return {
        "algo": ALGOS.get(algo, str(algo)),
        "blocksize": int(row["cblocksize"]),
        "level": int(row["clevel"]),
        "minage": int(row["minage"]),
    }


def policies(config: list[dict[str, Any]], tables: Iterable[str]) -> list[dict[str, Any]]:
    """Which policy each table takes: `declared` when compressionconfig.csv has
    rows of its own, else `stack default` - the `default` row, as .cmp applies
    it - with the columns it sets apart."""
    default = next(
        (r for r in config if r["table"] == "default" and r["column"] == "default"), None
    )
    out = []
    for t in sorted(set(tables)):
        own = [r for r in config if r["table"] == t]
        base = next((r for r in own if r["column"] == "default"), default)
        out.append(
            {
                "table": t,
                "policy": "declared" if own else "stack default",
                **(_row_policy(base) if base else {}),
                "columns": {r["column"]: _row_policy(r) for r in own if r["column"] != "default"},
            }
        )
    return out


def _device(directory: str) -> tuple[int, str]:
    """(st_dev, the directory) of the nearest existing directory."""
    p = Path(directory)
    while not p.exists() and p != p.parent:
        p = p.parent
    return os.stat(p).st_dev, str(p)


def headroom(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per filesystem: the most one partition writes there (`needed_bytes`)
    against what is free.

    A run takes one partition at a time and the HDB reloads after each, so a
    file .cmp replaced is let go of by then. Within one, .cmp writes each new
    file beside the old before swapping it in, and a reader may still map the
    old: so the bound is a whole partition's new files - its compressed
    columns at the estimate, its decompressed ones at full size - held at once."""
    per_partition: dict[tuple[int, str], int] = {}
    dirs: dict[int, list[str]] = {}
    for p in parts:
        if not changes(p):
            continue
        dev, d = _device(p["dir"])
        dirs.setdefault(dev, []).append(d)
        key = (dev, p["partition"])
        per_partition[key] = per_partition.get(key, 0) + int(p.get("bytes_written") or 0)
    out = []
    for dev, ds in dirs.items():
        needed = max(v for (k, _), v in per_partition.items() if k == dev)
        free = shutil.disk_usage(ds[0]).free
        shown = os.path.commonpath(ds)
        out.append(
            {"filesystem": shown, "needed_bytes": needed, "free_bytes": free, "ok": free >= needed}
        )
    return out


def refuse_short_disk(disk: list[dict[str, Any]]) -> None:
    """Refuse before anything is written when a filesystem has less free space
    than the run needs there."""
    short = [d for d in disk if not d["ok"]]
    if short:
        raise UqsError(
            "not enough free disk to compress: "
            + "; ".join(
                f"{d['filesystem']} has {d['free_bytes']:,} bytes free and one partition "
                f"needs {d['needed_bytes']:,}"
                for d in short
            )
            + ". Nothing was changed"
        )


def run_partition(
    paths: UqsPaths, sc: Scope, partition: str, *, query: Query | None = None
) -> list[dict[str, Any]]:
    """Compress one partition of the selection through
    `.qtorq.compress.apply_partition` (TorQ's .cmp.compressfromtable over its plan
    rows, then the read-back check): one row per file changed. The HDB is not
    reloaded: `reload` does that once, after the last.

    @raise UqsError with .qtorq.compress's named error when a file did not reach
    its target state - every file before it stays valid."""
    expr = (
        f".qtorq.compress.apply_partition[{_hdb(paths)};.cmp.inputcsv;{_q_scope(sc)};"
        f"{_q_string(partition)}]"
    )
    return list(_ask(paths, sc, expr, PARTITION_TIMEOUT_S, query))


def reload(paths: UqsPaths, sc: Scope, *, query: Query | None = None) -> dict[str, Any]:
    """Ask every HDB to reload, once, through housekeeping1's `.qtorq.hk.reload` -
    which connects to them, then sends `.qtorq.reload_hdb`'s (`reload;date) message:
    how many were registered, opened, reloaded."""
    return dict(_ask(paths, sc, ".qtorq.hk.reload[]", 120, query))


def partitions_to_run(doc: dict[str, Any]) -> list[str]:
    """The partitions a run takes, oldest first: those with a file to change."""
    return sorted(
        {p["partition"] for p in doc.get("partitions") or [] if p["status"] == "act" and changes(p)}
    )


def by_table(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The table partitions folded per table and status, for a person to read:
    how many partitions, their first and last, the columns compressed,
    decompressed or already compressed in any of them, and the bytes."""
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for p in parts:
        g = groups.setdefault(
            (p["table"], p["status"]),
            {"table": p["table"], "status": p["status"], "partitions": [], "compress": set(),
             "decompress": set(), "compressed": set(), "bytes_now": 0, "bytes_after": 0},
        )  # fmt: skip
        if p["status"] != "act" or changes(p):
            g["partitions"].append(p["partition"])
        for k in ("compress", "decompress", "compressed"):
            g[k] |= set(p[k])
        g["bytes_now"] += p["bytes_now"]
        g["bytes_after"] += p["bytes_after"]
    return [
        {**g, **{k: sorted(g[k]) for k in ("compress", "decompress", "compressed")},
         "partitions": sorted(g["partitions"])}
        for _, g in sorted(groups.items())
    ]  # fmt: skip


def summary(partition: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """One partition's outcome: its files and their bytes, before and after."""
    return {
        "partition": partition,
        "tables": sorted({r["table"] for r in rows}),
        "files": len(rows),
        "bytes_before": sum(int(r["bytes_before"]) for r in rows),
        "bytes_after": sum(int(r["bytes_after"]) for r in rows),
    }
