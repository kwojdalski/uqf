"""`uqs data compress` without --report (cli/compress.py): the HDB's
compression - planned, estimated and, with --apply, run one partition at a
time by TorQ's .cmp (#2147, #2148).

Without --apply nothing is changed: the plan is TorQ's `.cmp.showcomp` over
the policy (`.qtorq.compress.plan`), each table's policy is named - its own or
the stack default - the saving is estimated by compressing a sample through
the same wrapper in a temporary directory, and the free disk of each
filesystem is set against what a run would write there (stack/compress.py).

With --apply, after asking: refused first when a filesystem is short; then the
runtime's HDB write lock, and `.qtorq.compress.run` one partition at a time,
oldest first - .cmp's swap, the read-back check, the HDB reload - stopping at
the first named error, with every partition already done still valid. Each
partition is recorded on one server row of the activity history
(`uqs activity list --action hdb.compress`).

`uqs --target T data compress --apply` runs it on the release and asks there,
and an inspection-only target refuses it before it leaves this machine - the
rules every confirms-on-server command follows (cli/target_confirm.py).
"""

from __future__ import annotations

import getpass
import os
import time
from pathlib import Path
from typing import Any

import typer
from rich.table import Table

from uqs import activity_log, timerange
from uqs.cli import safety
from uqs.cli.choices import ChoiceOpt
from uqs.cli.hdb_scope import tables_given
from uqs.cli.json_out import emit
from uqs.cli.shared import _paths, console
from uqs.logger import get_logger
from uqs.paths import UqsError, UqsPaths, UqsUsageError
from uqs.stack import compress as stack_compress
from uqs.stack import hdb_alter

log = get_logger()

#: The activity history's action for a run.
ACTION = "hdb.compress"


#: --algo: TorQ's .cmp algorithms by name, `none` decompressing.
AlgoOpt = ChoiceOpt(
    tuple(stack_compress.ALGOS.values()),
    "--algo",
    "This run only: the algorithm, instead of the policy's - set on the rows of .cmp's own "
    "plan, checked by .cmp.checkcsv",
)


def scope_given(
    table: list[str] | None,
    older_than: int | None,
    base_port: int | None,
    range_from: str | None,
    range_to: str | None,
    day: str | None,
    last: float | None,
    algo: str | None,
    blocksize: int | None,
    level: int | None,
) -> stack_compress.Scope:
    """The scope the command line names: days only when a range option was given
    (the shared --from/--to/--date/--last, half-open and whole days), else the
    policy's."""
    window = timerange.resolve(range_from, range_to, day, last, open_ended=True)
    named = any(v is not None for v in (range_from, range_to, day, last))
    days = window.partition_dates("`data compress` compresses") if named else None
    return stack_compress.scope(
        tables_given(table), older_than, base_port, days, algo, blocksize, level,
    )  # fmt: skip


def plan_and_apply(sc: stack_compress.Scope, *, apply: bool, yes: bool, as_json: bool) -> None:
    """The plan; with `apply`, after asking, the run."""
    paths = _paths()
    doc = stack_compress.survey(paths, sc)
    days = None if sc.days is None else [None if d is None else d.isoformat() for d in sc.days]
    doc.update(
        hdb=str(paths.hdb_dir),
        process=stack_compress.PROCESS,
        scope={
            "tables": list(sc.tables), "older_than_days": sc.older_days,
            "days": days, "change": dict(sc.change),
        },
    )  # fmt: skip
    if not as_json:
        show(doc)
    todo = stack_compress.partitions_to_run(doc)
    result = None
    if apply and todo:
        stack_compress.refuse_short_disk(doc["disk"])
        _confirm(paths, todo, yes=yes, as_json=as_json)
        result = run(paths, sc, todo, as_json=as_json)
    finish(doc, result, as_json=as_json, applied=apply)


def _confirm(paths: UqsPaths, todo: list[str], *, yes: bool, as_json: bool) -> None:
    """Ask once before the first partition; under --json, --yes or nothing."""
    if yes:
        return
    if as_json:
        raise UqsUsageError(
            "--json cannot ask for confirmation: add --yes to compress. Nothing was changed."
        )
    safety.confirm(f"Compress {len(todo)} partition(s) of {paths.hdb_dir}?", yes=False)


def run(paths: UqsPaths, sc: stack_compress.Scope, todo: list[str], *, as_json: bool) -> dict:
    """Each partition in turn, under the HDB write lock, recorded as it goes;
    the first failure stops the run."""
    audit = Audit.open(paths)
    done: list[dict[str, Any]] = []
    failed = None
    started = time.monotonic()
    reloaded = None
    with hdb_alter.write_lock(paths):
        for part in todo:
            try:
                rows = stack_compress.run_partition(paths, sc, part)
            except UqsError as exc:
                failed = {"partition": part, "error": str(exc)}
                audit.progress(done, failed)
                break
            done.append(stack_compress.summary(part, rows))
            audit.progress(done, None)
            if not as_json:
                d = done[-1]
                console.print(
                    f"  [green]compressed[/] {part}: {d['files']} file(s), "
                    f"{_size(d['bytes_before'])} -> {_size(d['bytes_after'])}"
                )
        # once, after the last partition - and after a failure too, since every
        # partition before it changed
        reloaded = stack_compress.reload(paths, sc) if any(d["files"] for d in done) else None
    if reloaded is not None and not as_json:
        console.print(
            f"  HDB reload: {reloaded.get('reloaded')} of {reloaded.get('registered')} "
            "registered HDB(s) reloaded"
        )
    recorded = audit.settle(done, failed, (time.monotonic() - started) * 1000)
    return {"partitions": done, "failed": failed, "reload": reloaded, "audit": recorded}


class Audit:
    """One server row of the activity history for a run: `pending` before the
    first partition, an item per partition as it ends, settled at the end. A
    history that cannot be written does not stop the run - the frontend's own
    rule (uqf_frontend/activity.py) - and the result says it is unrecorded."""

    def __init__(self, store: Any, event_id: str | None, where: str) -> None:
        self.store, self.event_id, self.where = store, event_id, where

    @classmethod
    def open(cls, paths: UqsPaths) -> Audit:
        path = _history(paths)
        try:
            from uqf_frontend import activity
            from uqf_frontend import config as fe_config

            bounds = {
                level: activity.Bound(days, rows)
                for level, (days, rows) in {
                    "actions": (
                        fe_config.DEFAULT_ACTIVITY_RETENTION_DAYS,
                        fe_config.DEFAULT_ACTIVITY_MAX_KEPT,
                    ),
                    "interactions": (
                        fe_config.DEFAULT_ACTIVITY_INTERACTIONS_RETENTION_DAYS,
                        fe_config.DEFAULT_ACTIVITY_INTERACTIONS_MAX_KEPT,
                    ),
                    "debug": (
                        fe_config.DEFAULT_ACTIVITY_DEBUG_RETENTION_DAYS,
                        fe_config.DEFAULT_ACTIVITY_DEBUG_MAX_KEPT,
                    ),
                }.items()
            }
            store = activity.ActivityStore(path, bounds=bounds)
            event_id, _ = store.add(
                source="server",
                kind=activity.SERVER_KIND,
                action=ACTION,
                target=str(paths.hdb_dir),
                outcome="pending",
                claimed_user=getpass.getuser(),
                method="CLI",
                detail={"process": stack_compress.PROCESS},
            )
        except Exception as exc:  # noqa: BLE001 - an audit that cannot be kept never stops the run
            log.warning(
                "the activity history at {} cannot be written ({}): this run is unrecorded",
                path,
                exc,
            )
            return cls(None, None, f"unrecorded: {exc}")
        return cls(store, event_id, str(path))

    def _write(self, done: list[dict], failed: dict | None, **fields: Any) -> None:
        if self.store is None or self.event_id is None:
            return
        items = [{"target": d["partition"], "outcome": "succeeded"} for d in done]
        if failed:
            items.append({"target": failed["partition"], "outcome": "failed"})
        detail = {
            "process": stack_compress.PROCESS,
            "item_count": len(done),
            "size_bytes": max(0, sum(d["bytes_before"] - d["bytes_after"] for d in done)),
        }
        if failed:
            detail["error"] = "QError"
        self.store.update(self.event_id, items=items, detail=detail, **fields)

    def progress(self, done: list[dict], failed: dict | None) -> None:
        self._write(done, failed, acting=1)

    def settle(self, done: list[dict], failed: dict | None, duration_ms: float) -> str:
        outcome = "succeeded" if failed is None else ("unknown" if done else "failed")
        self._write(done, failed, outcome=outcome, duration_ms=duration_ms)
        if self.store is not None:
            self.store.close()
        return self.where


def _history(paths: UqsPaths) -> Path:
    """The activity history `uqs activity list` reads: UQF_FRONTEND_ACTIVITY_DB,
    else this stack's."""
    named = os.environ.get("UQF_FRONTEND_ACTIVITY_DB")
    return Path(named).expanduser() if named else activity_log.history_file(paths.torqdata)


def _size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unit == "GB":
            return f"{n:,.0f} {unit}" if unit == "B" else f"{n:,.1f} {unit}"
        n /= 1024
    return str(n)


def _cols(names: list[str], most: int = 6) -> str:
    if not names:
        return "-"
    more = f" +{len(names) - most}" if len(names) > most else ""
    return ", ".join(names[:most]) + more


def _policy_line(p: dict[str, Any]) -> str:
    if "algo" not in p:
        return "no row in the policy and no default"
    base = (
        "left uncompressed"
        if p["algo"] == "none"
        else f"{p['algo']}, block 2^{p['blocksize']}, level {p['level']}"
    )
    cols = "".join(f"; {c}: {v['algo']} level {v['level']}" for c, v in p["columns"].items())
    return f"{base}, from {p['minage']} days{cols}"


def show(doc: dict[str, Any]) -> None:
    """The plan as a person reads it."""
    console.print(
        f"HDB        {doc['hdb']}  [dim](compressed by TorQ's .cmp in {doc['process']})[/]"
    )
    for p in doc["policies"]:
        how = (
            "[yellow]stack default[/] (no policy declared)"
            if p["policy"] != "declared"
            else "declared"
        )
        console.print(f"policy     {p['table']}: {how} - {_policy_line(p)}")
    for p in doc.get("partitions") or []:
        if p.get("early") and stack_compress.changes(p) and p["status"] == "act":
            console.print(
                f"[yellow]named      {p['table']} {p['partition']}: compressing it although the "
                f"policy's minage is {p['policy_minage']} (it is {p['age']} days old)[/]"
            )
    for h in doc.get("held") or []:
        console.print(f"[yellow]held       {h['dataset']} {h['date']}: still being written[/]")
    rows = stack_compress.by_table(doc.get("partitions") or [])
    if rows:
        t = Table(box=None, pad_edge=False)
        for col in (
            "table",
            "status",
            "partitions",
            "compress",
            "already compressed",
            "now",
            "after",
        ):
            t.add_column(col, justify="right" if col in ("now", "after") else "left")
        for r in rows:
            ps = r["partitions"]
            span = "-" if not ps else ps[0] if len(ps) == 1 else f"{len(ps)}: {ps[0]} .. {ps[-1]}"
            act = r["compress"] + [f"{c} (decompress)" for c in r["decompress"]]
            t.add_row(
                r["table"],
                r["status"],
                span,
                _cols(act),
                _cols(r["compressed"]),
                _size(r["bytes_now"]),
                _size(r["bytes_after"]),
            )
        console.print(t)
    sample = doc.get("sample") or []
    if sample:
        rows_ = max(s["rows"] for s in sample)
        console.print(
            f"[dim]estimate   .cmp on up to {rows_:,} rows of each column of {len(sample)} "
            "table(s)' newest planned partition, in a temporary directory[/]"
        )
    for d in doc.get("disk") or []:
        style = "green" if d["ok"] else "red"
        console.print(
            f"disk       {d['filesystem']}: one partition writes up to {_size(d['needed_bytes'])}, "
            f"[{style}]{_size(d['free_bytes'])} free[/]"
        )
    tot = doc["totals"]
    console.print(
        f"total      {tot['partitions']} partition(s), {tot['files']} file(s): "
        f"{_size(tot['bytes_now'])} -> about {_size(tot['bytes_after'])} "
        f"(saves about {_size(tot['bytes_saved'])})"
    )


def finish(doc: dict[str, Any], result: dict | None, *, as_json: bool, applied: bool) -> None:
    """The outcome, and exit 1 when a partition failed."""
    failed = (result or {}).get("failed")
    if as_json:
        emit({"ok": failed is None, **doc, "result": result})
    elif failed:
        console.print(f"[red]FAILED[/] at {failed['partition']}: {failed['error']}")
    elif result is not None:
        console.print(f"[green]compressed[/] {len(result['partitions'])} partition(s)")
        console.print(f"[dim]recorded   {result['audit']}[/]")
    elif not stack_compress.partitions_to_run(doc):
        console.print("[green]nothing to do[/]")
    elif not applied:
        console.print(safety.dry_run_note("with --apply, which asks first, to compress"))
    if failed:
        raise typer.Exit(code=1)
