"""`uqs job contract ...`: the receiver contracts a remote HDB receiver writes under (#1717).

`export` runs where the worker is and prints its contract; `approve` runs on the
stack's machine and binds it to an attachment's destination; `list` shows what is
approved there. What a contract is, and why, is in uqs.attach.receiver_contract.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from uqs.attach import receiver_contract
from uqs.cli import completion
from uqs.cli.shared import JOB_OPERATIONS_PANEL, _die, _paths, console, job_app
from uqs.paths import UqsError

contract_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Receiver contracts: export one where a worker is, approve it where its HDB is, so "
    "`uqs job receive` writes the worker's rows without the worker's code.",
)
job_app.add_typer(contract_app, name="contract", rich_help_panel=JOB_OPERATIONS_PANEL)


@contract_app.command("export")
def export(
    worker: Annotated[
        str,
        typer.Argument(
            help="The bounded worker whose batches the receiver will take",
            autocompletion=completion.backfill_workers,
        ),
    ],
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Write it to this file, not stdout")
    ] = None,
) -> None:
    """Print WORKER's receiver contract, derived from its declarations.

    Run where the worker's source, transform and worker definitions are - the
    extraction machine. The contract is data: the dataset, its columns and types,
    the timestamp column, the row key, the partition and the conflict policies the
    declarations admit, with an identity that changes whenever any of them does.
    Take the file to the stack's machine and `uqs job contract approve` it there.
    """
    try:
        contract = receiver_contract.export(_paths(), worker)
    except UqsError as exc:
        _die(exc)
        return
    if output is None:
        typer.echo(contract.text(), nl=False)
    else:
        output.write_text(contract.text())
        said = f"contract {contract.identity} for {contract.dataset} -> {output}"
        console.print(said, markup=False)


@contract_app.command("approve")
def approve(
    file: Annotated[Path, typer.Argument(help="The contract file `uqs job contract export` wrote")],
    attach: Annotated[
        str, typer.Option("--attach", help="The saved, verified attachment whose HDB it may write")
    ],
    conflict: Annotated[
        list[str] | None,
        typer.Option(
            "--conflict",
            help="A conflict policy to permit; repeatable. Default: every one the contract "
            "permits. Narrows the contract, never widens it",
        ),
    ] = None,
    max_rows: Annotated[
        int, typer.Option("--max-rows", help="The most rows one message may carry")
    ] = receiver_contract.DEFAULT_MAX_ROWS,
    as_json: Annotated[
        bool, typer.Option("--json", help="Print the approved record as JSON")
    ] = False,
) -> None:
    """Approve a receiver contract for an attachment on THIS machine, the stack's.

    Refuses, storing nothing, a contract that is malformed, of a version this uqs
    does not read, or altered since it was exported; an attachment that is not
    verified, does not publish the contract's dataset in the contract's shape, or
    pins no HDB here. The approval binds the contract to the attachment's
    destination and stack user, and to the limits given here: `uqs job receive
    --contract ID --attach NAME` then serves it, and refuses if the attachment
    has since been pointed at another HDB.
    """
    try:
        contract = receiver_contract.load_file(file)
        record = receiver_contract.approve(
            _paths(), contract, attach, conflict_policies=conflict, max_rows=max_rows
        )
    except UqsError as exc:
        _die(exc)
        return
    if as_json:
        typer.echo(json.dumps(record, indent=2, sort_keys=True))
        return
    b = record["binding"]
    console.print(
        f"approved contract {contract.identity} ({contract.dataset}) for attachment {attach}: "
        f"into {b['hdb_dir']}, writer {', '.join(b['users'])}, "
        f"policies {', '.join(b['conflict_policies'])}, at most {b['max_rows']} rows a message; "
        f"`uqs job receive --contract {contract.identity} --attach {attach} --port PORT` serves it",
        markup=False,
    )


@contract_app.command("list")
def list_(
    attach: Annotated[str, typer.Option("--attach", help="The attachment whose approvals to list")],
    as_json: Annotated[bool, typer.Option("--json", help="Print them as JSON")] = False,
) -> None:
    """The receiver contracts approved for an attachment on this machine."""
    try:
        found = receiver_contract.records(attach)
    except UqsError as exc:
        _die(exc)
        return
    if as_json:
        typer.echo(json.dumps(found, indent=2, sort_keys=True))
        return
    if not found:
        console.print(f"no contract is approved for attachment {attach}", markup=False)
        return
    for r in found:
        c, b = r.get("contract", {}), r.get("binding", {})
        console.print(
            f"{c.get('identity')}  {c.get('dataset')}"
            f"  from {c.get('origin', {}).get('worker', '?')}"
            f"  approved {b.get('approved_at')} by {b.get('approved_by')}",
            markup=False,
        )
