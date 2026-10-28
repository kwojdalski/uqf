"""What `uqs data hdb-check` and `uqs data hdb-alter` share: an HDB's tables,
checked, filled and changed one table at a time, dry run first (#1755).

Which HDB is named once, by what already knows it - never a path to remember:

  uqs --target NAME data ...   a deployed release: the command runs THERE, on its
                               own configured HDB (deploy/control.py routes it)
  uqs data ... --attach NAME   an attached TorQ stack: its pinned HDB, else its live
                               wdb's .wdb.hdbdir, on the stack's host over ssh
  uqs data ... --hdb DIR       a directory on this machine, for anything else

With none of them, this runtime's own HDB - which is what a release runs when
`--target` reaches it. Every decision about the database is
scripts/gates/hdb_alter.q's (stack/hdb_alter.py); this module resolves the HDB,
shows the plan, asks, checks the writers, runs it and reloads the readers.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Annotated, Any

import typer

from uqs.attach import config as attach_config
from uqs.attach import hdb_where, service
from uqs.attach.hdb_where import AttachedHdb
from uqs.cli.json_out import emit
from uqs.cli.shared import _paths, console
from uqs.paths import UqsError, UqsPaths
from uqs.stack import hdb_alter, hdb_remote
from uqs.stack.hdb_alter import Request

AttachOpt = Annotated[
    str | None,
    typer.Option(
        "--attach", help="The attachment whose HDB to use: its pinned one, else its live wdb's"
    ),
]
HdbOpt = Annotated[
    str | None,
    typer.Option("--hdb", help="An HDB directory on this machine, when nothing else names it"),
]
ApplyOpt = Annotated[
    bool,
    typer.Option(
        "--apply", help="Write the plan, after asking; without it, a dry run that writes nothing"
    ),
]
YesOpt = Annotated[
    bool, typer.Option("--yes", "-y", help="With --apply: do not ask before writing")
]


@dataclass
class Handle:
    """The HDB a command acts on, and everything about reaching it."""

    kind: str  # runtime | attach | hdb
    hdb: str
    source: str
    paths: UqsPaths
    where: AttachedHdb | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def ssh(self) -> str | None:
        return self.where.ssh if self.where else None

    def run(self, req: Request, *, apply: bool) -> dict[str, Any]:
        if self.ssh:
            assert self.where is not None
            return hdb_remote.run_ssh(
                self.ssh, self.where.ssh_setting, self.paths.repo_root, req, apply=apply
            )
        return hdb_alter.run_local(self.paths, req, apply=apply)


def resolve(attach: str | None, hdb: str | None) -> Handle:
    """The one HDB named: --attach, --hdb, or (neither) this runtime's own."""
    paths = _paths()
    if attach and hdb:
        raise UqsError("give one of --target, --attach and --hdb: each names an HDB")
    if attach:
        ipc = service.ipc_for(attach_config.load(attach))
        where = hdb_where.resolve(attach, ipc)
        on = f", on {where.ssh} over ssh" if where.ssh else ", on this machine"
        return Handle("attach", where.hdb, where.source + on, paths, where, list(where.warnings))
    if hdb:
        root = Path(hdb).expanduser()
        if not root.is_dir():
            raise UqsError(f"--hdb {hdb}: no such directory on this machine")
        unchecked = (
            "writers were not checked: nothing says which process writes a directory named by "
            "--hdb - stop or pause them before --apply"
        )
        return Handle("hdb", str(root.resolve()), "--hdb", paths, warnings=[unchecked])
    return Handle("runtime", str(paths.hdb_dir), f"this runtime's HDB ({paths.runtime})", paths)


def _table(handle: Handle, given: str | None, *, required: bool = True) -> str | None:
    if given:
        return given
    if handle.where and handle.where.table:
        return handle.where.table
    if required:
        names = [t.name for t in handle.where.att.tables] if handle.where else []
        more = f" (attachment publishes {', '.join(names)})" if names else ""
        raise UqsError(f"name the table{more}")
    return None


def _shape(s: dict[str, Any] | None) -> str:
    if not s:
        return "-"
    pairs = zip(s.get("cols", []), s.get("types", ""), strict=False)
    return ", ".join(f"{c}:{t}" for c, t in pairs)


def show_plan(handle: Handle, doc: dict[str, Any]) -> None:
    """The plan as a person reads it: the HDB and where it came from first."""
    console.print(f"HDB        {handle.hdb}  [dim]({handle.source})[/]")
    console.print(f"table      {doc.get('table')}")
    op = doc.get("operation", "")
    if op not in ("check", "fill"):
        undo = "undo of " if doc.get("mode") == "undo" else ""
        console.print(f"operation  {undo}{op} {doc.get('argument', '')}")
    held, lacking = doc.get("partitions_holding", []), doc.get("partitions_lacking", [])
    console.print(f"holding    {len(held)} partition(s){': ' + ', '.join(held) if held else ''}")
    if lacking:
        console.print(f"lacking    {len(lacking)} partition(s): {', '.join(lacking)}")
    if doc.get("partitions_without_d"):
        console.print(f"[yellow]no .d     {', '.join(doc['partitions_without_d'])} - left alone[/]")
    if doc.get("source"):
        console.print(f"shape      {_shape(doc.get('source'))}")
    if doc.get("target") and doc.get("target") != doc.get("source"):
        console.print(f"becomes    {_shape(doc.get('target'))}")
    for step in doc.get("steps") or []:
        console.print(f"  {step['partition']}: {', '.join(step['actions'])}")
    if doc.get("partitions_already_done"):
        console.print(f"  already done: {', '.join(doc['partitions_already_done'])}")
    if "bytes_rewritten" in doc:
        console.print(f"rewrites   {doc['bytes_rewritten']:,} bytes")
    if doc.get("lossy"):
        lossy = ", ".join(doc["lossy"])
        console.print(f"[yellow]lossy in {lossy}: once complete, --undo can only cast back[/]")
    if doc.get("journal"):
        console.print(f"journal    {doc['journal']}")
    for w in handle.warnings:
        console.print(f"[yellow]warning:   {w}[/]")


def confirm(handle: Handle, what: str, *, yes: bool, as_json: bool) -> None:
    """Ask once before the first write; without a terminal, --yes or nothing."""
    if yes:
        return
    if as_json or not sys.stdin.isatty():
        raise UqsError(
            "--apply without --yes needs a terminal to ask on - add --yes to write without "
            "asking. Nothing was written."
        )
    if not typer.confirm(f"{what} in {handle.hdb}?", default=False):
        raise UqsError("not applied - nothing was written")


def apply_plan(
    handle: Handle, req: Request, *, as_json: bool, yes: bool, what: str
) -> dict[str, Any]:
    """Check the writers, ask, write, reload: the result document."""
    if handle.kind == "attach":
        assert handle.where is not None
        hdb_where.refuse_running_writers(handle.where, service.ipc_for(handle.where.att))
    confirm(handle, what, yes=yes, as_json=as_json)
    if handle.kind == "runtime":
        with hdb_alter.write_lock(handle.paths):
            result = handle.run(req, apply=True)
    else:
        result = handle.run(req, apply=True)
    result["readers"] = readers(handle) if result.get("status") == "applied" else []
    return result


def readers(handle: Handle) -> list[dict[str, str]]:
    """Reload the HDB processes serving the directory; for --hdb, say how."""
    if handle.kind == "attach":
        assert handle.where is not None
        return hdb_where.reload_readers(handle.where, service.ipc_for(handle.where.att))
    if handle.kind == "runtime":
        return hdb_alter.reload_own_readers(handle.paths)
    return [{"procname": "-", "ok": "not asked",
             "detail": f'reload each HDB process serving {handle.hdb}: `system "l ."` '
                       "(TorQ: `reload[]`)"}]  # fmt: skip


def finish(
    handle: Handle, plan: dict[str, Any], result: dict[str, Any] | None, as_json: bool
) -> None:
    """Print the outcome, and exit 1 unless it is a valid plan or a clean write."""
    final = result or plan
    ok = final.get("status") in ("planned", "applied")
    if as_json:
        doc = {"ok": ok, "hdb": handle.hdb, "resolved_from": handle.source, "ssh": handle.ssh,
               "warnings": handle.warnings, "plan": plan, "result": result}  # fmt: skip
        if not ok:
            doc["error"] = final.get("refused") or final.get("failed")
        emit(doc)
    elif not ok:
        console.print(f"[red]{'refused' if final.get('refused') else 'FAILED'}:[/] "
                      f"{final.get('refused') or final.get('failed')}")  # fmt: skip
    elif result is None and not plan.get("steps"):
        console.print("[green]nothing to do[/]")
    elif result is None:
        console.print("[dim]dry run: nothing was written. --apply writes it, after asking.[/]")
    else:
        console.print(f"[green]applied[/] in {len(result.get('partitions_touched') or [])} "
                      "partition(s); final check clean")  # fmt: skip
        for r in result.get("readers", []):
            style = {"yes": "green", "no": "red"}.get(r["ok"], "dim")
            console.print(f"  reload {r['procname']}: [{style}]{r['ok']}[/] {r['detail']}")
    if not ok:
        raise typer.Exit(code=1)


def run_scoped(
    handle: Handle, req: Request, *, apply: bool, yes: bool, as_json: bool, what: str
) -> None:
    """Plan, show, and - with --apply - write: one table, any HDB."""
    plan = handle.run(req, apply=False)
    if not as_json:
        show_plan(handle, plan)
    if not apply or plan.get("status") != "planned" or not plan.get("steps"):
        finish(handle, plan, None, as_json)
        return
    # the journal the plan named is the one written, not another timestamp's
    req = replace(req, journal=plan.get("journal") or None)
    finish(handle, plan, apply_plan(handle, req, as_json=as_json, yes=yes, what=what), as_json)
