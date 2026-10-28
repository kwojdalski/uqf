"""`uqs job attachment ...`: the group the attachment commands register on (#1737).

Its own module because three register on it - attach.py (add, remove),
attachment_show.py (show), lists.py (list) - and receiver_contract.py nests
the receiver contracts under it.
"""

from __future__ import annotations

import typer

from uqs.cli.shared import JOB_OPERATIONS_PANEL, job_app

#: A job put on a TorQ stack already running, as a noun: once `job attach` and
#: `job detach`.
attachment_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Attachments: a job put on a TorQ stack already running - add, remove, show, "
    "list, and the receiver contracts approved for one (contract).",
)
job_app.add_typer(attachment_app, name="attachment", rich_help_panel=JOB_OPERATIONS_PANEL)
