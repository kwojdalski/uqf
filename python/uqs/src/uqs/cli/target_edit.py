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
import tomllib
from pathlib import Path
from typing import Annotated

import typer

from uqs.cli import completion, safety
from uqs.cli.concepts import TargetArg
from uqs.cli.safety import ChangesState, YesOpt, dry_run_note
from uqs.cli.shared import _die, console
from uqs.cli.target import DryRun, _change, _command, _done, target_app
from uqs.deploy import target_changes, target_keys, targets
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


#: Editors that open a file at `+LINE`, as `uqs target edit NAME` asks.
_AT_LINE = frozenset({"vi", "vim", "nvim", "view", "nano", "emacs", "micro", "kak", "hx"})


@target_app.command("edit", cls=ChangesState)
def edit(
    name: Annotated[
        str | None,
        typer.Argument(
            metavar="TARGET",
            autocompletion=completion.targets,
            help="Open the file at this target's table, as every other `target` verb names "
            "one; the whole file is still edited and checked",
            show_default=False,
        ),
    ] = None,
    dry_run: DryRun = False,
) -> None:
    """Edit deploy_targets.toml in $VISUAL or $EDITOR, checked before it is saved.

    The editor gets a copy, checked when the editor exits and only then swapped
    in. An invalid copy reopens with the reason on top - or is discarded,
    leaving the file as it was. TARGET opens it at that target's table."""
    root = repo_root()
    path = targets.declaration_path(root)
    original = path.read_text() if path.is_file() else ""
    at = None
    if name is not None:
        at = targets.line_of(original, f"{targets.TARGETS}.{name}")
        if at is None:
            _die(_not_a_table(original, name, path))
    text, problem = original, None
    with tempfile.TemporaryDirectory(prefix="uqs-target-edit-") as scratch:
        copy = Path(scratch) / path.name
        while True:
            note = "".join(f"{_EDIT_NOTE}{ln}\n" for ln in (problem or "").splitlines())
            if problem:
                note += f"{_EDIT_NOTE}fix it and save, or empty the file to discard this edit\n"
            copy.write_text(note + text)
            try:
                editor = _editor()
                line = None if at is None else at + note.count("\n")
                where = [f"+{line}"] if line and Path(editor[0]).name in _AT_LINE else []
                done = subprocess.run([*editor, *where, str(copy)], check=False)
            except OSError as exc:
                _die(UqsError(f"could not run the editor ({exc}) - set $VISUAL or $EDITOR"))
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
    name: TargetArg,
    dry_run: DryRun = False,
    yes: YesOpt = False,
) -> None:
    """Remove one target from deploy_targets.toml.

    Deletes its table, and the comment directly above it; every
    other line stays. `uqs target revert` brings it back.

    Shows the change and asks first; `--yes` does not ask."""
    if not dry_run and not yes:
        try:
            planned = target_changes.remove(
                repo_root(), name, _command("remove", name), dry_run=True
            )
        except (DeployError, UqsError) as exc:
            _die(UqsError(str(exc)))
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


def _not_a_table(text: str, name: str, path: Path) -> UqsError:
    """Why `name` cannot be opened at its table: declared inline, or - the
    one refusal for an unknown target (#2124) - not declared at all. The file
    may not parse: fixing it is what `target edit` is for."""
    try:
        declared = tomllib.loads(text).get(targets.TARGETS, {})
    except tomllib.TOMLDecodeError:
        declared = {}
    if isinstance(declared, dict) and name in declared:
        return UqsError(
            f"target {name} is declared inline in {path}, not as a [{targets.TARGETS}.{name}] "
            "table, so it cannot be opened at its line - `uqs target edit` opens the whole file"
        )
    known = declared if isinstance(declared, dict) else {}
    return target_keys.unknown_target(name, known, path)
