"""The options of `uqs job attachment add` (attach.py), declared once each.

Split from attach.py, which had reached its line budget. Each is an
`Annotated` type the command's signature names; the help text is the option's
documentation, so it says what the default is and what the option changes.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

from uqs.attach import config


class SchemaWriter(StrEnum):
    """Who appends the marked schema block (#1845): uqs, here, or the tickerplant."""

    CLI = "cli"
    TICKERPLANT = "tickerplant"


JobOpt = Annotated[
    str | None, typer.Option("--job", help="The streaming job to attach (new attachment)")
]
BundleOpt = Annotated[
    Path | None,
    typer.Option("--bundle", help="The sidecar bundle holding the job (default: this tree)"),
]
StackIdOpt = Annotated[
    int | None,
    typer.Option(
        "--stack-id",
        help="The existing stack's base port (-stackid / KDBBASEPORT). No default: "
        "the runtime's own base port is exactly the wrong guess.",
    ),
]
HostOpt = Annotated[
    str | None,
    typer.Option("--host", help="Where the stack's processes listen (default: localhost)"),
]
DiscoveryOpt = Annotated[
    str | None,
    typer.Option(
        "--discovery",
        help="HOST:PORT of its discovery, on the --host machine (default: HOST:<stack-id>+1)",
    ),
]
TickerplantOpt = Annotated[
    str | None, typer.Option("--tickerplant", help="Its tickerplant, if several")
]
ConsumerOpt = Annotated[
    list[str] | None, typer.Option("--consumer", help="An rdb/wdb to attach (default: all)")
]
UserOpt = Annotated[
    str | None,
    typer.Option(
        "--user",
        help=f"User to log in to the stack's processes as (default: {config.DEFAULT_USER})",
    ),
]
PasswordEnvOpt = Annotated[
    str | None,
    typer.Option(
        "--password-env",
        help="Environment variable holding the password (default: TorQ's own demo "
        "credential). The password itself is never saved or printed.",
    ),
]
RuntimeOnlyOpt = Annotated[
    bool,
    typer.Option(
        "--runtime-only",
        help="Accept that restarting the tickerplant drops the registration "
        "(default: add a marked block to its schema file)",
    ),
]
SchemaWriterOpt = Annotated[
    SchemaWriter | None,
    typer.Option(
        "--schema-writer",
        help="Who appends the marked block to the tickerplant's schema file: cli (uqs, on "
        "this machine, which must be the stack's) or tickerplant (the tickerplant, to its "
        "own file, for a stack on another machine). Default: the saved choice, else cli.",
    ),
]
RetiredRemoteSchemaOpt = Annotated[
    bool,
    typer.Option("--remote-schema-file", hidden=True, help="Retired: --schema-writer tickerplant"),
]
ProcessFileOpt = Annotated[
    Path | None,
    typer.Option(
        "--process-file",
        help="Register the publisher in this existing TorQ process registry (the stack's "
        "process.csv, on this machine), so its native torq.sh can start it. Only this "
        "option writes to that file; the row added is recorded and removed by `remove`.",
    ),
]
StartWithAllOpt = Annotated[
    bool | None,
    typer.Option(
        "--start-with-all/--no-start-with-all",
        help="Whether the registered publisher is started by a native `start all` "
        "(startwithall). Default: the saved setting; a new registration is on demand. "
        "Needs --process-file, and durable registration to enable.",
        show_default=False,
    ),
]
TplogdirOpt = Annotated[str, typer.Option("--tplogdir", help="Pin the tickerplant log directory")]
SchemaFileOpt = Annotated[str, typer.Option("--schema-file", help="Pin the schema file")]
SshOpt = Annotated[
    str | None,
    typer.Option(
        "--ssh",
        help="The ssh destination, USER@HOST, that reaches the stack's files, for `uqs data "
        "check` and `alter --attach` (default: --host as the current user). Changes "
        "a saved attachment without --replace",
    ),
]
ReplaceOpt = Annotated[
    bool,
    typer.Option(
        "--replace",
        help="Rebuild a saved attachment from these options. Keeps its manifest and "
        "accepted risks; refused while the manifest holds changes on another stack.",
    ),
]
OverrideOpt = Annotated[
    list[str] | None,
    typer.Option(
        "--override",
        metavar="CODE:COMPONENT",
        help="Bypass ONE blocker, as CODE:COMPONENT (e.g. TORQ_VERSION:stp1; `uqs override "
        "list` names the codes); repeatable. It accepts a risk, it does not establish "
        "compatibility. Needs --reason.",
    ),
]
ReasonOpt = Annotated[
    str, typer.Option("--reason", help="Why the --override is acceptable: recorded with it")
]
AcceptRisksOpt = Annotated[
    bool,
    typer.Option(
        "--accept-risks",
        help="Confirm attaching with the accepted risks the plan lists. Separate from "
        "--yes, which never confirms them; --json needs it when there are any.",
    ),
]
ClearOverridesOpt = Annotated[
    bool,
    typer.Option(
        "--clear-overrides",
        help="Withdraw every accepted risk (saved even when that leaves a blocker)",
    ),
]
StartOpt = Annotated[
    bool,
    typer.Option(
        "--start/--no-start",
        help="Apply and verify, then start the publisher (the default) or leave it stopped. "
        "Changes only this run's start, never the saved --start-with-all.",
    ),
]

#: What `--remote-schema-file` became (#1845), said when the retired option is given.
REMOTE_SCHEMA_RETIRED = (
    "--remote-schema-file was replaced by --schema-writer tickerplant (#1845): run the same "
    "command with --schema-writer tickerplant instead. A saved attachment that used it keeps "
    "it - re-running `uqs job attachment add NAME` with neither option is enough"
)
