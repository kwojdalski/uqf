"""`uqs job attachment contract ...`: the receiver contracts a remote HDB
receiver writes under (#1717).

`export` runs where the worker is and prints its contract; `approve` runs on the
stack's machine and binds it to an attachment's destination; `list` shows what is
approved there. What a contract is, and why, is in uqs.attach.receiver_contract.

Under `job attachment` because a contract is approved FOR an attachment (#1737);
it was `uqs job contract ...`, which stays for one release, hidden, each command
logging its new spelling.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from uqs.attach import receiver_contract
from uqs.cli import completion
from uqs.cli.attachment import attachment_app
from uqs.cli.choices import ChoiceOpt
from uqs.cli.concepts import attach_opt
from uqs.cli.json_out import emit, json_opt
from uqs.cli.renames import ATTACHMENT, renamed
from uqs.cli.safety import ChangesState, Effect, ReadOnly, dry_run_note, marked
from uqs.cli.shared import (
    ExportOpt,
    InteractiveOpt,
    _die,
    _export,
    _paths,
    _show,
    console,
)
from uqs.cli.sorting import ReverseOpt, SortOpt, _sorted_items
from uqs.paths import UqsError

contract_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Receiver contracts: export, approve, list.\n\nExport one where a worker is, "
    "approve it for an attachment where its HDB is, list those approved - so "
    "`uqs job receiver start` writes the worker's rows without the worker's code.",
)
attachment_app.add_typer(contract_app, name="contract")


@contract_app.command(
    "export",
    cls=marked(
        Effect.CHANGES_STATE,
        own_output="prints the contract, a document another machine approves",
    ),
)
def export(
    worker: Annotated[
        str,
        typer.Argument(
            metavar="JOB",
            help="The bounded worker whose batches the receiver will take",
            autocompletion=completion.backfill_workers,
        ),
    ],
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Write it to this file, not stdout")
    ] = None,
) -> None:
    """Print JOB's receiver contract, derived from its declarations.

    Run where the worker's source, transform and worker definitions are - the
    extraction machine. The contract is data: the dataset, its columns and types,
    the timestamp column, the row key, the partition and the conflict policies the
    declarations admit, with an identity that changes whenever any of them does.
    Take the file to the stack's machine and `uqs job attachment contract approve`
    it there.
    """
    try:
        contract = receiver_contract.export(_paths(), worker)
    except UqsError as exc:
        _die(exc)
    if output is None:
        typer.echo(contract.text(), nl=False)
    else:
        output.write_text(contract.text())
        said = f"contract {contract.identity} for {contract.dataset} -> {output}"
        console.print(said, markup=False)


ApproveJson = json_opt(
    '{"contract", "binding": {attachment, destination, host, base_port, hdb_dir, users, '
    "conflict_policies, max_rows, approved_at, ...}}"
)


#: A conflict policy is `--on-conflict` on every command; `--max-rows` was a
#: row limit's name for something else, the rows one message carries (#1857).
_APPROVE_RENAMES = {
    "--conflict": "--on-conflict",
    "--max-rows": "--max-message-rows",
    **ATTACHMENT,
}


_AttachOpt = attach_opt(
    "The saved, verified attachment whose HDB it may write",
    required=True,
)
_ConflictOpt = ChoiceOpt(
    receiver_contract.STRATEGIES,
    "--on-conflict",
    "What a write may do with a row already there - a conflict policy to permit: "
    "{values}; repeatable. Default: every one the contract permits. Narrows the "
    "contract, never widens it",
    many=True,
)


@contract_app.command("approve", cls=renamed(_APPROVE_RENAMES, ChangesState))
def approve(
    file: Annotated[
        Path,
        typer.Argument(
            metavar="FILE", help="The contract file `uqs job attachment contract export` wrote"
        ),
    ],
    attach: _AttachOpt,
    conflict: _ConflictOpt = None,
    max_rows: Annotated[
        int, typer.Option("--max-message-rows", help="The most rows one message may carry")
    ] = receiver_contract.DEFAULT_MAX_ROWS,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Check it and show the record; store nothing"),
    ] = False,
    as_json: ApproveJson = False,
) -> None:
    """Approve a receiver contract for an attachment on THIS machine, the stack's.

    Refuses, storing nothing, a contract that is malformed, of a version this uqs
    does not read, or altered since it was exported; an attachment that is not
    verified, does not publish the contract's dataset in the contract's shape, or
    pins no HDB here. The approval binds the contract to the attachment's
    destination and stack user, and to the limits given here: `uqs job receiver start
    --contract CONTRACT --attachment ATTACHMENT` then serves it, and refuses if the attachment
    has since been pointed at another HDB.
    """
    try:
        contract = receiver_contract.load_file(file)
        record = receiver_contract.approve(
            _paths(), contract, attach, conflict_policies=conflict, max_rows=max_rows,
            dry_run=dry_run,
        )  # fmt: skip
    except UqsError as exc:
        _die(exc)
    if as_json:
        emit(record)
        return
    b = record["binding"]
    console.print(
        f"{'would approve' if dry_run else 'approved'} contract {contract.identity} "
        f"({contract.dataset}) for attachment {attach}: "
        f"into {b['hdb_dir']}, writer {', '.join(b['users'])}, "
        f"policies {', '.join(b['conflict_policies'])}, at most {b['max_rows']} rows a message; "
        f"`uqs job receiver start --contract {contract.identity} --attachment {attach} "
        "--listen-port PORT` serves it",
        markup=False,
    )
    if dry_run:
        console.print(dry_run_note())


_AttachOpt = attach_opt(
    "The attachment whose approvals to list",
    required=True,
)
ListJson = json_opt('{"contracts": [{contract, binding, path}]}, in the order the table shows')


@contract_app.command("list", cls=renamed(ATTACHMENT, ReadOnly))
def list_(
    attach: _AttachOpt,
    export: ExportOpt = None,
    sort: SortOpt = None,
    reverse: ReverseOpt = False,
    interactive: InteractiveOpt = False,
    as_json: ListJson = False,
) -> None:
    """List the receiver contracts approved for an attachment on this machine.

    One row each, with every listing's flags; --json prints the records."""
    try:
        found = receiver_contract.records(attach)
        rows = _sorted_items([_row(r) for r in found], sort, reverse)
    except UqsError as exc:
        _die(exc)
    _export(rows, export)
    if as_json:
        order = {row["identity"]: i for i, row in enumerate(rows)}
        found.sort(key=lambda r: order.get(r.get("contract", {}).get("identity"), 0))
        emit({"contracts": found})
        return
    if not found:
        console.print(f"no contract is approved for attachment {attach}", markup=False)
        return
    table = Table(title=f"contracts approved for {attach} ({len(rows)})")
    for col in rows[0]:
        table.add_column(col)
    for row in rows:
        table.add_row(*(str(v) for v in row.values()))
    _show(table, interactive)


def _row(record: dict) -> dict:
    c, b = record.get("contract", {}), record.get("binding", {})
    return {
        "identity": c.get("identity"),
        "dataset": c.get("dataset"),
        "worker": c.get("origin", {}).get("worker", "?"),
        "approved_at": b.get("approved_at"),
        "approved_by": b.get("approved_by"),
    }
