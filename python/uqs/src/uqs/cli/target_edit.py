"""`uqs target edit`: deploy_targets.toml in $VISUAL or $EDITOR, as a copy
that is checked before it replaces the file (#1756). An invalid copy reopens
with the reason on top, or is discarded; the file is untouched either way.
The save is the same transaction as every other change
(deploy/target_changes.py), so it is logged and can be reverted.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Annotated

import typer

from uqs.cli import safety
from uqs.cli.safety import ChangesState, YesOpt, dry_run_note
from uqs.cli.shared import _die, console
from uqs.cli.target import DryRun, _change, _command, _done, target_app
from uqs.deploy import target_changes, targets
from uqs.deploy.config import DeployError
from uqs.paths import UqsError, repo_root

#: The comment lines `uqs target edit` puts above the copy to say what was
#: wrong with the last save; removed again before the copy is checked.
_EDIT_NOTE = "# uqs: "


def _editor() -> list[str]:
    return shlex.split(os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi")


def _strip_note(text: str) -> str:
    lines = text.splitlines(keepends=True)
    while lines and lines[0].startswith(_EDIT_NOTE):
        lines.pop(0)
    return "".join(lines)


@target_app.command("edit", cls=ChangesState)
def edit(dry_run: DryRun = False) -> None:
    """Edit the file in $VISUAL or $EDITOR: a copy, checked when the editor
    exits and only then swapped in. An invalid copy reopens with the reason
    on top - or is discarded, leaving the file as it was."""
    root = repo_root()
    path = targets.declaration_path(root)
    original = path.read_text() if path.is_file() else ""
    text, problem = original, None
    with tempfile.TemporaryDirectory(prefix="uqs-target-edit-") as scratch:
        copy = Path(scratch) / path.name
        while True:
            note = "".join(f"{_EDIT_NOTE}{ln}\n" for ln in (problem or "").splitlines())
            if problem:
                note += f"{_EDIT_NOTE}fix it and save, or empty the file to discard this edit\n"
            copy.write_text(note + text)
            try:
                done = subprocess.run([*_editor(), str(copy)], check=False)
            except OSError as exc:
                _die(UqsError(f"could not run the editor ({exc}) - set $VISUAL or $EDITOR"))
                return
            if done.returncode:
                _die(UqsError(f"the editor exited {done.returncode} - {path} is unchanged"))
            text = _strip_note(copy.read_text())
            if text == original:
                console.print(f"no change: {path} is as it was")
                if dry_run:
                    console.print(dry_run_note())
                return
            if not text.strip() and problem:
                _die(UqsError(f"edit discarded - {path} is unchanged"))
            try:
                result = target_changes.replace_file(
                    root, text, original, _command("edit"), dry_run=dry_run
                )
            except target_changes.Conflict as exc:
                _die(UqsError(str(exc)))
                return
            except DeployError as exc:
                problem = str(exc)
                console.print(f"[red]✗[/] {problem}", highlight=False)
                again = safety.interactive() and safety.ask(
                    "Reopen the editor to fix it?", default=True
                )
                if not again:
                    _die(UqsError(f"edit discarded - {path} is unchanged"))
                continue
            _done(result, f"{path.name} edited", dry_run)
            return


@target_app.command("remove", cls=ChangesState)
def remove(
    name: Annotated[str, typer.Argument(help="The target")],
    dry_run: DryRun = False,
    yes: YesOpt = False,
) -> None:
    """Delete one target's table, and the comment directly above it; every
    other line stays. `uqs target revert` brings it back.

    Shows the change and asks first (#1854); `--yes` does not ask."""
    if not dry_run and not yes:
        try:
            planned = target_changes.remove(
                repo_root(), name, _command("remove", name), dry_run=True
            )
        except (DeployError, UqsError) as exc:
            _die(UqsError(str(exc)))
            return
        if planned.entry is not None:
            console.print(planned.entry["diff"].rstrip("\n"), markup=False, highlight=False)
            try:
                safety.confirm(f"Remove target {name}?", yes=False)
            except UqsError as exc:
                _die(exc)
    _change(
        lambda: target_changes.remove(repo_root(), name, _command("remove", name), dry_run=dry_run),
        f"target {name} removed",
        dry_run,
    )
