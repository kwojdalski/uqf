"""`uqs target log` and `uqs target revert`: the change log of
deploy_targets.toml, and undoing one change (#1756).

Every change `uqs` makes to the file appends an entry to
deploy_targets.log.jsonl (deploy/target_changes.py), and an edit made by hand
is recorded as "edited outside uqs" by the change after it. The file itself
is per machine and not in git, so this is its history.
"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table

from uqs.cli.json_out import emit, json_opt
from uqs.cli.safety import ChangesState, ReadOnly
from uqs.cli.shared import _die, console
from uqs.cli.target import DryRun, _change, _command, target_app
from uqs.deploy import target_changelog, target_changes
from uqs.deploy.config import DeployError
from uqs.paths import UqsError, repo_root

LogJson = json_opt(
    '{"changes": [{number, at, user, host, kind, command, target, tables: [{table, before, '
    'after, keys}], diff, hash}], newest first, "edited_outside": bool}'
)


def _about(entry: dict, name: str) -> bool:
    return f"targets.{name}" in {t.get("table") for t in entry.get("tables") or []} or (
        name in str(entry.get("target") or "").split(",")
    )


@target_app.command("log", cls=ReadOnly)
def log_(
    name: Annotated[
        str | None,
        typer.Argument(metavar="NAME", help="Only the changes to this target", show_default=False),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", help="The most recent N changes")] = 20,
    as_json: LogJson = False,
) -> None:
    """The changes made to deploy_targets.toml, newest first: by whom, from
    which machine, with which command, and which settings each changed.
    `uqs target revert N` undoes change N."""
    root = repo_root()
    try:
        entries = target_changelog.read_log(root)
        pending = target_changelog.pending_outside_edit(root)
    except DeployError as exc:
        _die(UqsError(str(exc)))
        return
    rows = [(n, e) for n, e in enumerate(entries, 1) if name is None or _about(e, name)][-limit:][
        ::-1
    ]
    if as_json:
        emit({
            "changes": [
                {
                    "number": n,
                    **{k: e.get(k) for k in ("at", "user", "host", "kind", "command", "target")},
                    "tables": [
                        {**t, "keys": target_changelog.keys_changed(t)}
                        for t in e.get("tables") or []
                    ],
                    "diff": e.get("diff"),
                    "hash": e.get("hash"),
                }
                for n, e in rows
            ],
            "edited_outside": pending is not None,
        })  # fmt: skip
        return
    if pending is not None:
        console.print(
            f"[yellow]![/] {target_changelog.log_path(root).name}: the file has been edited "
            "outside uqs since the last change - the next change records it"
        )
    if not rows:
        console.print(f"no changes recorded in {target_changelog.log_path(root)}")
        return
    table = Table("#", "when", "who", "change", "target", "what")
    for n, e in rows:
        what = "; ".join(
            f"{t['table']}: {', '.join(target_changelog.keys_changed(t))}"
            for t in e.get("tables") or []
        )
        table.add_row(
            str(n),
            str(e.get("at", "")),
            f"{e.get('user', '')}@{e.get('host', '')}",
            str(e.get("kind", "")),
            str(e.get("target") or ""),
            what or "(comments or layout only)",
        )
    console.print(table)


@target_app.command("revert", cls=ChangesState)
def revert(
    change: Annotated[
        str,
        typer.Argument(
            metavar="CHANGE",
            help="A change's number from `uqs target log`, or `last`",
            show_default=False,
        ),
    ],
    dry_run: DryRun = False,
) -> None:
    """Put back each table one change altered, exactly as it was before it -
    itself a change, checked and logged, so it can be reverted too. Refused
    when a table has changed again since."""
    ref = None if change == "last" else change
    _change(
        lambda: target_changes.revert(
            repo_root(), ref, _command("revert", change), dry_run=dry_run
        ),
        f"change {change} reverted",
        dry_run,
    )
