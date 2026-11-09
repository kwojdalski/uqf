"""The options of `uqs job attachment add` (attach.py), declared once each.

Split from attach.py, which had reached its line budget. Each is an
`Annotated` type the command's signature names; the help text is the option's
documentation, so it says what the default is and what the option changes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from uqs.attach import config, service
from uqs.cli import completion
from uqs.cli.choices import ChoiceOpt
from uqs.paths import UqsError, repo_root

JobOpt = Annotated[
    str | None, typer.Option("--job", help="The streaming job to attach (new attachment)")
]
BundleOpt = Annotated[
    Path | None,
    typer.Option("--bundle", help="The sidecar bundle holding the job (default: this tree)"),
]
BasePortOpt = Annotated[
    int | None,
    typer.Option(
        "--base-port",
        min=1,
        max=65535,
        help="The existing stack's base port (-stackid / KDBBASEPORT). No default: "
        "the runtime's own base port is exactly the wrong guess.",
    ),
]


def _discovery_offset() -> str:
    """discovery's `+N`, for --discovery's help: read, never typed (#1893)."""
    try:
        return str(service.discovery_offset(repo_root()))
    except UqsError:
        return "N"


HostOpt = Annotated[
    str | None,
    typer.Option("--host", help="Where the stack's processes listen (default: localhost)"),
]
DiscoveryOpt = Annotated[
    str | None,
    typer.Option(
        "--discovery",
        help="HOST:PORT of its discovery, on the --host machine (default: HOST:<base-port>"
        f"+{_discovery_offset()}, as the starter pack's process.csv puts it)",
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
        help=f"The user to log in to the stack's processes as (default: {config.DEFAULT_USER})",
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
TransientOpt = Annotated[
    bool,
    typer.Option(
        "--transient",
        help="Register the table in the running tickerplant only, accepting that "
        "restarting it drops the registration (default: add a marked block to its schema file)",
    ),
]
SchemaWriterOpt = ChoiceOpt(
    config.SCHEMA_WRITERS,
    "--schema-writer",
    "Who appends the marked block to the tickerplant's schema file: {values}. Default: the "
    f"saved choice, else {config.DEFAULT_SCHEMA_WRITER}.",
    notes=config.SCHEMA_WRITER_NOTES,
)
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
        "check` and `alter --attachment` (default: --host as the current user). Changes "
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
        metavar="NAME:COMPONENT",
        help="Bypass ONE blocker, as NAME:COMPONENT (e.g. torq-version:stp1; `uqs override "
        "list` names them); repeatable. It accepts a risk, it does not establish "
        "compatibility. Needs --reason.",
        autocompletion=completion.overrides,
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
