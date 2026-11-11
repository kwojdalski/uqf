"""`uqs target config history|show|diff|restore`: a target's config push
history (#2002).

A push through the frontend's file browser keeps, on the server and only
there, what each file replaced and what it wrote, under
`<dest>/shared/config/.uqf-history/<push-id>/` (uqf_frontend.remote_helper,
PUSH HISTORY). These commands read that history and restore from it over the
same ssh, with the same helper the API runs, sent with every call - nothing
goes through the API. A restore made here is a push of its own in the
server's history, recorded with this machine's user and `via: cli`; the
frontend's activity audit belongs to the API process and does not see it.

uqf_frontend is imported when a command runs, not when the CLI loads: the
helper is the frontend's one program for the server, and a second copy here
would drift from it.
"""

from __future__ import annotations

import getpass
import subprocess
from collections.abc import Callable
from typing import Annotated, Any

import typer
from rich.markup import escape
from rich.table import Table

from uqs.cli import safety
from uqs.cli.concepts import TargetArg
from uqs.cli.json_out import emit, json_opt
from uqs.cli.safety import ChangesState, DryRunOpt, ReadOnly, YesOpt, dry_run_note
from uqs.cli.shared import ExportOpt, InteractiveOpt, _die, _export, _show, console
from uqs.cli.sorting import ReverseOpt, SortOpt, _sorted_items
from uqs.cli.target import target_app
from uqs.deploy import control, inspection, target_keys, targets
from uqs.deploy.config import DeployError
from uqs.paths import UqsError, repo_root

config_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="A target's config push history: history, show, diff, restore.\n\n"
    "What each push through the frontend's file browser replaced and wrote, kept on the "
    "server under shared/config/.uqf-history/.",
)
target_app.add_typer(config_app, name="config")

#: How ssh is run: injectable, so a test plays the server with no ssh.
RUNNER: Callable[..., subprocess.CompletedProcess] = subprocess.run

PushArg = Annotated[
    str,
    typer.Argument(
        metavar="PUSH",
        help="A push's id, as `uqs target config history TARGET` lists it",
        show_default=False,
    ),
]
HistoryJson = json_opt(
    '{"target", "dest", "pushes": [{push_id, kind, restores, created, closed, user, via, '
    "config_dir, declared, file_count, total_bytes, states, reported_count, error}], "
    'newest first, "total", '
    '"truncated", "pruned": [{push_id, why, pruned_at}]}'
)
ShowJson = json_opt(
    '{"target", "dest", "summary", "push": {push_id, kind, restores, created, closed, user, '
    "via, config_dir, declared, reported, files: [{path, action, size, sha256, "
    "previous_size, previous_sha256, state, error, now}]}}"
)
DiffJson = json_opt(
    '{"target", "push_id", "diff_cap", "files": [{path, action, kind, diff, previous_size, '
    "size, previous_sha256, sha256}]}"
)


def _call(work: Callable[[Any], dict[str, Any]], name: str) -> dict[str, Any]:
    """Run `work` on target `name`'s control.Remote; a refusal ends the command."""
    from uqf_frontend.errors import FrontendError

    root = repo_root()
    try:
        declared = targets.read(root) if targets.declaration_path(root).is_file() else {}
    except DeployError as exc:
        _die(UqsError(str(exc)))
    if name not in declared:
        _die(target_keys.unknown_target(name, declared, targets.declaration_path(root)))
    try:
        return work(control.remote_from(name, declared[name]))
    except (FrontendError, UqsError) as exc:
        _die(UqsError(str(exc)))


def _short(sha: str | None) -> str:
    return sha[:12] if sha else "-"


@config_app.command("history", cls=ReadOnly)
def history(
    name: TargetArg,
    limit: Annotated[int, typer.Option("--limit", help="Show at most N pushes")] = 50,
    export: ExportOpt = None,
    sort: SortOpt = None,
    reverse: ReverseOpt = False,
    interactive: InteractiveOpt = False,
    as_json: HistoryJson = False,
) -> None:
    """List a target's config pushes, newest first, from the server's history.

    Each push through the frontend - and each restore, a push of its own -
    with who made it, where, its files and bytes, and the pushes retention
    has pruned."""
    from uqf_frontend import remote_files

    answer = _call(lambda r: remote_files.history_list(r, name, limit, runner=RUNNER), name)
    rows = [
        {
            "push_id": p["push_id"],
            "created": p.get("created"),
            "user": p.get("user"),
            "via": p.get("via"),
            "what": p.get("error")
            or (
                f"restore of {p.get('restores')}"
                if p.get("kind") == "restore"
                else f"push to {p.get('config_dir')}"
            ),
            "files": p.get("file_count"),
            "bytes": p.get("total_bytes"),
            "pending": (p.get("states") or {}).get("pending"),
            "failed": (p.get("states") or {}).get("failed"),
        }
        for p in answer["pushes"]
    ]
    try:
        rows = _sorted_items(rows, sort, reverse)
    except UqsError as exc:
        _die(exc)
    _export(rows, export)
    if as_json:
        emit({k: answer[k] for k in ("dest", "pushes", "total", "truncated", "pruned")}
             | {"target": name})  # fmt: skip
        return
    if not rows:
        console.print(f"no config pushes recorded on {escape(name)}")
    else:
        table = Table("push", "when", "who", "what", "files", "bytes", "pending", "failed")
        for row in rows:
            table.add_row(
                escape(str(row["push_id"])), escape(str(row["created"] or "")),
                escape(f"{row['user'] or 'unknown'} ({row['via'] or '?'})"),
                escape(str(row["what"])),
                str(row["files"] or 0), str(row["bytes"] or 0), str(row["pending"] or 0),
                str(row["failed"] or 0),
            )  # fmt: skip
        _show(table, interactive)
    if answer["truncated"]:
        console.print(f"showing {len(rows)} of {answer['total']} pushes - raise --limit")
    for pruned in answer["pruned"][:5]:
        said = f"pruned {pruned['push_id']} ({pruned['why']}) {pruned['pruned_at']}"
        console.print(f"[dim]{escape(said)}[/]")


@config_app.command("show", cls=ReadOnly)
def show(name: TargetArg, push_id: PushArg, as_json: ShowJson = False) -> None:
    """Show one config push: each file's change, old and new sha256, and what it is now.

    `now` hashes the file as it is: `after` it is as the push left it,
    `before` as it was before (an upload that never landed), `changed`
    something else has written it since."""
    from uqf_frontend import remote_files

    answer = _call(lambda r: remote_files.history_show(r, name, push_id, runner=RUNNER), name)
    manifest = answer["manifest"]
    if as_json:
        emit({"target": name, "dest": answer["dest"], "summary": answer["summary"],
              "push": {k: v for k, v in manifest.items() if k != "version"}})  # fmt: skip
        return
    kind = manifest["kind"] + (f" of {manifest['restores']}" if manifest["restores"] else "")
    # every value here is held by the server, and the config_dir, user and
    # via came from whoever opened the push: printed literally, never as
    # rich markup (#2025)
    console.print(
        escape(
            f"push {push_id} ({kind}) to {manifest['config_dir']} by "
            f"{manifest.get('user') or 'unknown'} via {manifest.get('via') or '?'}, "
            f"opened {manifest['created']}, closed {manifest.get('closed') or 'never'}"
        )
    )
    table = Table("file", "change", "old sha256", "new sha256", "state", "now")
    for f in manifest["files"]:
        table.add_row(
            escape(repr(f["path"])[1:-1]), escape(str(f["action"])),
            _short(f.get("previous_sha256")), _short(f.get("sha256")),
            escape(f["state"] + (f" ({f['error']})" if f.get("error") else "")),
            escape(str(f.get("now") or "")),
        )  # fmt: skip
    console.print(table)
    for item in manifest.get("reported") or []:
        console.print(escape(f"never recorded by the server: {item['path']!r} ({item['outcome']})"))


@config_app.command("diff", cls=ReadOnly)
def diff(
    name: TargetArg,
    push_id: PushArg,
    path: Annotated[
        str | None,
        typer.Argument(
            metavar="FILE",
            help="Only this file, as `show` names it (shared/config/...)",
            show_default=False,
        ),
    ] = None,
    as_json: DiffJson = False,
) -> None:
    """Diff a config push's files, before against after, from the server's history.

    Text under the server's cap is a unified diff; a binary or larger file
    is shown by its sizes and sha256s only."""
    from uqf_frontend import remote_files

    answer = _call(lambda r: remote_files.history_diff(r, name, push_id, path, runner=RUNNER), name)
    if as_json:
        emit({"target": name, "push_id": push_id, "diff_cap": answer["diff_cap"],
              "files": answer["files"]})  # fmt: skip
        return
    for f in answer["files"]:
        if f["kind"] == "text":
            # plain print: the diff is the file's own text, never markup
            print(f["diff"] or f"(no change: {f['path']})", end="" if f["diff"] else "\n")
            continue
        print(
            f"{f['kind']}: {f['path']!r} {f['previous_size'] or 0} bytes "
            f"({_short(f['previous_sha256'])}) -> {f['size'] or 0} bytes ({_short(f['sha256'])})"
        )


@config_app.command("restore", cls=ChangesState)
def restore(
    name: TargetArg, push_id: PushArg, dry_run: DryRunOpt = False, yes: YesOpt = False
) -> None:
    """Restore a config push: every file back to how it was before it.

    A replaced file gets its earlier content back and a file the push
    created is deleted (a regular file, never a link). The restore is itself
    a push, so it can be restored in turn. Refused, writing nothing, when a
    file has changed since the push or the push's history was pruned. Asks
    first; a lost answer is never retried - read `history` before trying again."""
    from uqf_frontend import config, remote_files

    try:
        max_kept, retention_days = config.history_bound()
    except ValueError as exc:
        _die(UqsError(str(exc)))
    user = getpass.getuser()

    def ask(dry: bool) -> Callable[[Any], dict[str, Any]]:
        return lambda r: remote_files.restore(
            r, name, push_id, dry_run=dry, user=user, via="cli", max_kept=max_kept,
            retention_days=retention_days, runner=RUNNER,
        )  # fmt: skip

    plan = _call(ask(True), name)["plan"]
    for step in plan:
        console.print(f"  {escape(str(step['step'])):<9} {escape(repr(step['path']))}"
                      + (f" ({escape(step['error'])})" if step.get("error") else ""))  # fmt: skip
    conflicts = [s for s in plan if s["step"] == "conflict"]
    if conflicts:
        _die(UqsError(f"{len(conflicts)} file(s) changed since push {push_id} - restoring "
                      "would overwrite those changes, so nothing was written"))  # fmt: skip
    steps = [s for s in plan if s["step"] in ("write", "delete")]
    if not steps:
        _die(UqsError(f"nothing to restore - every file of push {push_id} is as it was before it"))
    if dry_run:
        console.print(dry_run_note())
        return
    try:
        # the plan above only read; writing it is refused (#2032)
        inspection.refuse(repo_root(), f"`uqs target config restore {name}`", name=name)
        safety.confirm(
            f"Restore {len(steps)} file(s) on {name} to how they were before push {push_id}?",
            yes=yes,
        )
    except UqsError as exc:
        _die(exc)
    answer = _call(ask(False), name)
    for result in answer["results"]:
        said = "ok" if result["outcome"] == "succeeded" else f"FAILED: {result['error']}"
        console.print(
            f"  {escape(str(result['step'])):<9} {escape(repr(result['path']))} {escape(said)}"
        )
    console.print(f"restored as push {escape(str(answer['push_id']))}")
    if any(r["outcome"] != "succeeded" for r in answer["results"]):
        raise typer.Exit(code=1)
