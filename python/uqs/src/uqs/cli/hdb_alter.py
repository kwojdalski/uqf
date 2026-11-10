"""`uqs data alter`: change one table across an HDB - reorder, rename,
attribute, cast, or undo one of those - dry run by default (#1755).

Only partitions holding the table are touched; partitions that disagree on
its shape are refused before any write; a journal is written beside the HDB
before the first write and `--undo JOURNAL` reverses the run; every partition
is read again at the end. All of that is scripts/gates/hdb_alter.q's
(stack/hdb_alter.py); cli/hdb_scope.py resolves the HDB and runs it.
"""

from __future__ import annotations

from typing import Annotated

import typer

from uqs.cli import completion, safety
from uqs.cli.hdb_scope import ApplyOpt, AttachOpt, HdbOpt, YesOpt, _table, resolve, run_scoped
from uqs.cli.json_out import json_opt
from uqs.cli.renames import ATTACHMENT, renamed, warn_renamed
from uqs.cli.safety import ConfirmsOnServer
from uqs.cli.shared import _die, data_app
from uqs.generated.q_facts import Q_ATTRIBUTES
from uqs.paths import UqsError, UqsUsageError
from uqs.stack.hdb_alter import Request

#: What `--attr COL:` takes to clear an attribute - scripts/gates/hdb_alter.q's word.
CLEAR_ATTRIBUTE = "none"

AlterJson = json_opt(
    '{"ok", "hdb", "resolved_from", "ssh", "warnings", "plan", "result"} - plan and result '
    "are hdb_alter.q's own: partitions, shapes, steps, journal, final_check"
)


@data_app.command("alter", cls=renamed(ATTACHMENT, ConfirmsOnServer))
def hdb_alter(
    table: Annotated[
        str | None,
        typer.Argument(
            metavar="TABLE",
            help="The table to change (default with --attachment: the attachment's own)",
            autocompletion=completion.plant_table,
            show_default=False,
        ),
    ] = None,
    attach: AttachOpt = None,
    hdb: HdbOpt = None,
    reorder: Annotated[
        str | None,
        typer.Option("--reorder", help="C1,C2,...: every existing column, in a new order"),
    ] = None,
    rename: Annotated[
        list[str] | None,
        typer.Option(
            "--rename", help="OLD:NEW - rename a column (file and .d); one operation per run"
        ),
    ] = None,
    attr: Annotated[
        list[str] | None,
        typer.Option(
            "--attr",
            help=f"COL:{'|'.join((*Q_ATTRIBUTES, CLEAR_ATTRIBUTE))} - set or clear a column's "
            "attribute; one per run",
        ),
    ] = None,
    cast: Annotated[
        list[str] | None,
        typer.Option(
            "--cast",
            help="COL:TYPE - cast a column (long, float, ... or a type letter); never "
            "to or from symbol; one per run",
        ),
    ] = None,
    undo: Annotated[
        str | None,
        typer.Option("--undo", help="A run's journal (as the plan names it): reverse that run"),
    ] = None,
    apply: ApplyOpt = False,
    dry_run: safety.DefaultDryRunOpt = False,
    yes: YesOpt = False,
    as_json: AlterJson = False,
) -> None:
    """Change one table in every partition that holds it, and only those.

    One operation per run: --reorder, --rename, --attr, --cast, or --undo of
    an earlier run. Name the HDB with --attachment ATTACHMENT (run on the stack's host),
    --hdb DIR, or `uqs --target TARGET data alter ...` for a release's own;
    with none, this runtime's. Shows the plan and writes nothing unless
    --apply, which asks first (--yes does not). Refused when the partitions
    holding the table disagree on its columns, order or types - `data check`
    repairs a missing table, anything else is for a person to decide.
    """
    try:
        given = {
            "reorder": reorder,
            "rename": _old_rename(_one("--rename", rename)),
            "attr": _one("--attr", attr),
            "cast": _one("--cast", cast),
            "undo": undo,
        }
        chosen = {k: v for k, v in given.items() if v is not None}
        if len(chosen) != 1:
            raise UqsUsageError(
                "give one operation: --reorder, --rename, --attr, --cast or --undo"
                + (f" (not {len(chosen)})" if chosen else "")
            )
        safety.dry_unless_applied(dry_run, apply)
        (op, value), *_ = chosen.items()
        handle = resolve(attach, hdb)
        name = _table(handle, table, required=op != "undo")
        req = Request(handle.hdb, name, op, value)
        what = f"Undo {value}" if op == "undo" else f"{op} {value} on {name}"
        run_scoped(handle, req, apply=apply, yes=yes, as_json=as_json, what=what)
    except UqsError as exc:
        _die(exc)


def _one(flag: str, values: list[str] | None) -> str | None:
    """The one value of a repeatable `data` option (#1856): `data migrate`
    takes several, and an alter is one operation per run."""
    if values and len(values) > 1:
        raise UqsError(f"{flag} once: an alter is one operation per run (not {len(values)})")
    return values[0] if values else None


def _old_rename(value: str | None) -> str | None:
    """`--rename OLD:NEW`, as `data migrate` spells it (#1856), in the OLD=NEW
    hdb_alter.q reads. `OLD=NEW`, this command's own spelling before, still
    parses for one release (cli/rename_expiry.py)."""
    if value is None:
        return None
    if ":" not in value and "=" in value:
        old, _, new = value.partition("=")
        warn_renamed(f"--rename {value}", f"--rename {old}:{new}")
        return value
    old, sep, new = value.partition(":")
    if not sep or not old or not new:
        raise UqsError(f"--rename {value!r} must be OLD:NEW")
    return f"{old}={new}"
