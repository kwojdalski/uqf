"""`uqs target config push`: files into a target's shared/config through the
frontend's API, as its Files view pushes them (#2786).

Unlike `history`, `show`, `diff` and `restore` beside it, which reach the
server over ssh, this goes ONLY through the frontend API: the API is what
records a push in its activity history, so a push made here is one the
history shows as it shows a browser's. deploy/portal_push.py is the client.
"""

from __future__ import annotations

import getpass
import os
from pathlib import Path
from typing import Annotated

import typer
from rich.markup import escape

from uqs.cli import safety
from uqs.cli.concepts import TargetArg
from uqs.cli.json_out import emit, json_opt
from uqs.cli.safety import ChangesState, DryRunOpt, YesOpt, dry_run_note
from uqs.cli.shared import EXIT_PARTIAL, EXIT_STOPPED, EXIT_UNKNOWN, _die, console
from uqs.cli.target_config import config_app
from uqs.deploy import portal_push
from uqs.paths import UqsError

#: Where the API listens when --api is not given: `uvicorn` as the frontend's
#: README starts it.
DEFAULT_API = "http://localhost:8000"

#: How a request is sent: injectable, so a test plays the API in process.
SEND: portal_push.Send = portal_push.urllib_send

#: How each outcome is shown, as the browser's queue words it.
SHOWN = {
    "succeeded": "uploaded",
    "refused": "refused - nothing written",
    "failed": "failed - nothing written",
    "unknown": "OUTCOME UNKNOWN",
    "cancelled": "cancelled - not sent",
    "invalid": "not uploadable - not sent",
    "queued": "queued",
}

PushJson = json_opt(
    '{"target", "api", "dest", "config_dir", "overwrite", "dry_run", "push_id", '
    '"session_id", "interrupted", "closed", "close_error", "counts", "exit_code", '
    '"files": [{path, local, size, outcome, message, http_status, error, sha256, '
    "replaced, previous_sha256, created_dirs}]}"
)


def _exit_code(result: portal_push.Result) -> int:
    outcomes = {item.outcome for item in result.items}
    if "unknown" in outcomes:
        return EXIT_UNKNOWN
    if result.interrupted:
        return EXIT_STOPPED
    if outcomes != {"succeeded"} or result.close_error:
        return EXIT_PARTIAL
    return 0


def _counts(items: list[portal_push.Item]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        counts[item.outcome] = counts.get(item.outcome, 0) + 1
    return counts


def _line(item: portal_push.Item) -> str:
    said = SHOWN[item.outcome]
    sha = item.answer.get("sha256")
    extra = [f"sha256 {sha[:12]}" if sha else "", item.message]
    return escape(f"  {said:<26} {item.path!r} {item.size} bytes"
                  + "".join(f" - {e}" for e in extra if e))  # fmt: skip


@config_app.command("push", cls=ChangesState)
def push(
    name: TargetArg,
    paths: Annotated[
        list[Path],
        typer.Argument(
            metavar="FILE...",
            help="Files and folders to push; a folder's files keep their paths below its name",
            show_default=False,
        ),
    ],
    into: Annotated[
        str | None,
        typer.Option(
            "--into",
            metavar="DIR",
            help="The folder below shared/config to push into (default: shared/config itself)",
        ),
    ] = None,
    api: Annotated[
        str,
        typer.Option("--api", metavar="URL", help="The frontend API to push through"),
    ] = DEFAULT_API,
    claimed_user: Annotated[
        str | None,
        typer.Option(
            "--claimed-user",
            metavar="NAME",
            help="The name the activity history records as this push's claimed user "
            "(x-uqf-user; default: this machine's user)",
            show_default=False,
        ),
    ] = None,
    overwrite: Annotated[
        bool,
        typer.Option(
            "--overwrite",
            help="Replace a file the destination already holds (its permission bits are "
            "kept); without it, such a file is refused and the others still go",
        ),
    ] = False,
    dry_run: DryRunOpt = False,
    yes: YesOpt = False,
    as_json: PushJson = False,
) -> None:
    """Push files and folders into a target's shared/config through the frontend API.

    Does what the frontend's Files view does, through the same API, so the push
    is in its activity history and the server's push history as a browser's is:
    the files are checked as the browser checks them (16 MiB a file, 500 files
    and 256 MiB a push, a path the server takes), the push is shown and asked
    for, then opened, each file sent once, in turn, and the push closed with
    what was not sent. A file whose outcome is unknown - a timeout, a lost
    connection, a Ctrl-C while it was in flight - is never retried.

    The API's write token is read from UQF_FRONTEND_WRITE_TOKEN. Uploading
    never activates anything: a release reads shared/config when it next
    starts. Exits 0 when every file was uploaded, 3 when any was refused,
    failed, not uploadable or not sent (nothing written for those), 4 after a
    Ctrl-C, 5 when any file's outcome is unknown, 1 when nothing was sent."""
    base = portal_push.config_dir(into)
    token = os.environ.get(portal_push.TOKEN_ENV, "")
    portal = portal_push.Portal(api, token, claimed_user or getpass.getuser(), send=SEND)
    try:
        if not dry_run:
            safety.json_needs_yes(as_json, yes, "push the files")
        items = portal_push.queue(portal_push.expand(paths), base)
        queued = [item for item in items if item.outcome == "queued"]
        if not queued:
            raise UqsError("no file can be uploaded - nothing was sent:\n" + "\n".join(
                f"  {item.path!r}: {item.message}" for item in items))  # fmt: skip
        if not token and not dry_run:
            raise UqsError(f"set {portal_push.TOKEN_ENV} to the API's write token - every "
                           "push carries it. Nothing was sent.")  # fmt: skip
        dest = portal.target(name).get("dest", "?")
    except UqsError as exc:
        _die(exc)
    document = {"target": name, "api": api, "dest": dest, "config_dir": base,
                "overwrite": overwrite, "dry_run": dry_run, "push_id": None,
                "session_id": portal.session_id, "interrupted": False, "closed": False,
                "close_error": None}  # fmt: skip
    if not as_json:
        _show_plan(name, dest, base, items, overwrite)
    if dry_run:
        _report(document, items, 0, as_json)
        if not as_json:
            console.print(dry_run_note())
        return
    try:
        safety.confirm(f"Push {len(queued)} file(s) to {name}?", yes=yes)
        result = portal_push.run(portal, name, base, items, overwrite,
                                 done=None if as_json else _done)  # fmt: skip
    except UqsError as exc:
        _die(UqsError(f"the push could not be opened, so nothing was sent: {exc}"))
    code = _exit_code(result)
    document |= {"dest": result.dest or dest, "push_id": result.push_id,
                 "interrupted": result.interrupted, "closed": result.closed is not None,
                 "close_error": result.close_error}  # fmt: skip
    _report(document, items, code, as_json)
    if code:
        raise typer.Exit(code=code)


def _done(item: portal_push.Item) -> None:
    console.print(_line(item))


def _show_plan(name: str, dest: str, base: str, items: list[portal_push.Item],
               overwrite: bool) -> None:  # fmt: skip
    queued = [item for item in items if item.outcome == "queued"]
    console.print(escape(f"Push {len(queued)} file(s) ({sum(i.size for i in queued)} bytes) "
                         f"to {name} at {dest}/{base}/"))  # fmt: skip
    for item in items:
        console.print(_line(item))
    console.print(
        "Existing files with the same path WILL BE REPLACED (their permissions are kept)."
        if overwrite
        else "Existing files are left alone: an upload whose path exists is refused."
    )
    console.print("Uploading activates nothing: a release reads shared/config when it next starts.")


def _report(document: dict, items: list[portal_push.Item], code: int, as_json: bool) -> None:
    document |= {"counts": _counts(items), "exit_code": code,
                 "files": [item.doc() for item in items]}  # fmt: skip
    if as_json:
        emit(document)
        return
    if document["push_id"] is None:
        return
    push_id, name = document["push_id"], document["target"]
    if document["interrupted"]:
        console.print("[yellow]stopped on Ctrl-C: the file in flight is unknown, the rest "
                      "were not sent[/]")  # fmt: skip
    counts = ", ".join(
        f"{n} {SHOWN[o].split(' -')[0]}" for o, n in sorted(document["counts"].items())
    )
    console.print(escape(f"push {push_id}: {counts}"))
    if document["close_error"]:
        console.print(f"[red]{escape(document['close_error'])}[/]")
    if code == EXIT_UNKNOWN:
        console.print("[red]some outcomes are unknown: list the directory before pushing "
                      "those files again - nothing is retried[/]")  # fmt: skip
    console.print(escape(
        f"see it with `uqs target config show {name} {push_id}`, the frontend's push history, "
        f"or `uqs activity list --session {document['session_id']}`"))  # fmt: skip
