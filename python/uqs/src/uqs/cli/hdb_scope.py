"""What `uqs data check` and `uqs data alter` share: an HDB's tables,
checked, filled and changed one table at a time, dry run first (#1755).

Which HDB is named once, by what already knows it - never a path to remember:

  uqs --target TARGET data ...   a deployed release: the command runs THERE, on its
                               own configured HDB (deploy/control.py routes it)
  uqs data ... --attachment ATTACHMENT   an attached TorQ stack: its pinned HDB, else its live
                               wdb's .wdb.hdbdir, on the stack's host over ssh
  uqs data ... --hdb DIR       a directory on this machine, for anything else
  uqs --target TARGET data compress --report --hdb DIR
                               a directory on the target, read over its ssh by a
                               fixed helper - no uqf needed there (#2219); the
                               only command --target and --hdb reach together

With none of them, this runtime's own HDB - which is what a release runs when
`--target` reaches it. Every `data` command that reads or writes one HDB takes
the same `--hdb`/`--attachment` pair, with this one help (#2237); a command
that cannot act on an attachment's HDB refuses it saying why (`local_hdb`),
rather than not knowing the option. Every decision about the database is
scripts/hdb/alter.q's (stack/hdb_alter.py); this module resolves the HDB,
shows the plan, asks, checks the writers, runs it and reloads the readers.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Annotated, Any

import typer

from uqs.attach import config as attach_config
from uqs.attach import hdb_where, service
from uqs.attach.hdb_where import AttachedHdb
from uqs.cli import safety
from uqs.cli.concepts import attach_opt
from uqs.cli.json_out import emit
from uqs.cli.safety import dry_run_note
from uqs.cli.shared import _paths, console
from uqs.cli.target_confirm import AMBIGUOUS
from uqs.deploy import control
from uqs.paths import UqsError, UqsPaths, UqsUsageError, repo_root
from uqs.stack import hdb_alter, hdb_remote
from uqs.stack.hdb_alter import Request
from uqs.stack.hdb_compression import SshHost

AttachOpt = attach_opt("The attachment whose HDB to use: its pinned one, else its live wdb's")
HdbOpt = Annotated[
    Path | None,
    typer.Option("--hdb", help="An HDB directory on this machine, when nothing else names it"),
]
#: The target `uqs --target TARGET` named, while it runs a command here that
#: reads a directory on that target (cli/target.py forward, #2219); None else.
ON_TARGET: ContextVar[str | None] = ContextVar("uqs_hdb_on_target", default=None)
#: The --apply model's options, safety's own (#1854).
ApplyOpt = safety.ApplyOpt
YesOpt = safety.ApplyYesOpt


@dataclass
class Handle:
    """The HDB a command acts on, and everything about reaching it."""

    kind: str  # runtime | attach | hdb
    hdb: str
    source: str
    paths: UqsPaths
    where: AttachedHdb | None = None
    warnings: list[str] = field(default_factory=list)
    #: kind "target": how to reach the target the directory is on
    target: SshHost | None = None

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


def ssh_host(name: str) -> SshHost:
    """How to reach target `name`'s shell: its declared ssh, account and connect
    timeout (deploy/control.py's Remote) and its q_cmd and q_home, if it sets them."""
    label, settings = control.settings_for(name, repo_root())
    r = control.remote_from(label, settings)
    q_cmd, q_home = settings.get("q_cmd"), settings.get("q_home")
    return SshHost(
        r.name, r.host, r.remote_user, r.connect_timeout,
        str(q_cmd) if q_cmd else None, str(q_home) if q_home else None,
    )  # fmt: skip


def resolve(attach: str | None, hdb: Path | str | None) -> Handle:
    """The one HDB named: --attachment, --hdb, or (neither) this runtime's own;
    --hdb on the target `uqs --target` named, when it named one (ON_TARGET)."""
    paths = _paths()
    if attach and hdb:
        raise UqsUsageError(AMBIGUOUS)
    if (name := ON_TARGET.get()) is not None:
        if attach or not hdb:
            raise UqsUsageError("with --target, name the directory on the target: --hdb DIR")
        # a path on the target, never checked against this machine (#2219): its
        # helper checks it there
        ssh = ssh_host(name)
        return Handle("target", str(hdb), f"--hdb on target {ssh.name} ({ssh.host}), over ssh",
                      paths, target=ssh)  # fmt: skip
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


def local_hdb(attach: str | None, hdb: Path | None, *, command: str, why: str) -> Path | None:
    """The HDB a command that writes only on this machine was named: `--hdb DIR`,
    or None for its own default. `--attachment` is refused, saying `why` the
    command cannot act on an attached stack's HDB (#2237) - check and alter
    can, because they check the attached stack's writers first."""
    if attach and hdb:
        raise UqsUsageError(AMBIGUOUS)
    if attach:
        raise UqsUsageError(f"{command} cannot take --attachment {attach}: {why}")
    return Path(hdb).expanduser() if hdb else None


def tables_given(values: list[str] | None) -> list[str]:
    """The tables a repeatable `--table` names (#1856): each value, or a
    comma-separated list in one - one way to choose tables on every command."""
    return [t for v in values or () for t in v.split(",") if t.strip()]


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
    safety.json_needs_yes(as_json, yes, "write")
    safety.confirm(f"{what} in {handle.hdb}?", yes=yes)


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


def readers(handle: Handle) -> list[dict[str, Any]]:
    """Reload the HDB processes serving the directory; for --hdb, say how."""
    if handle.kind == "attach":
        assert handle.where is not None
        return hdb_where.reload_readers(handle.where, service.ipc_for(handle.where.att))
    if handle.kind == "runtime":
        return hdb_alter.reload_own_readers(handle.paths)
    how = f'reload each HDB process serving {handle.hdb}: `system "l ."` (TorQ: `reload[]`)'
    return [hdb_alter.reader_row("-", "not asked", how)]


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
        console.print(dry_run_note("with --apply, which asks first, to write it"))
    else:
        console.print(f"[green]applied[/] in {len(result.get('partitions_touched') or [])} "
                      "partition(s); final check clean")  # fmt: skip
        for r in result.get("readers", []):
            style = {"reloaded": "green", "failed": "red"}.get(r["outcome"], "dim")
            console.print(f"  reload {r['process']}: [{style}]{r['outcome']}[/] {r['detail']}")
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
