"""`uqs deploy push`'s component modes (#1557, deploy/components.py): how q,
TorQ and Python reach the server, and a bundled TorQ's and q's runtimes. Beside
cli/deploy.py, which takes each as a parameter, so the push command stays
readable in one sitting."""

from __future__ import annotations

from typing import Annotated

import typer

from uqs.deploy.components import EXISTING


def _mode(component: str, fields: str, bundled: str) -> typer.models.OptionInfo:
    return typer.Option(
        f"--{component}-mode", metavar="MODE", help=f"existing: {fields} already on the server, "
        f"found by preflight. bundled: {bundled}"
    )  # fmt: skip


QMode = Annotated[
    str,
    _mode(
        "q",
        "the q of --qcmd/--qhome",
        "the PeachQ of --q-runtime, installed once under shared/runtimes/q/ (never KX's q)",
    ),
]
TorqMode = Annotated[
    str,
    _mode(
        "torq",
        "the TorQ of --torq-home/--torq-app-home",
        "the vendored TorQ of --torq-runtime, installed once under shared/runtimes/torq/",
    ),
]
PythonMode = Annotated[str, _mode("python", "the Python uv finds", "not yet supported (#1557)")]
DEFAULT = EXISTING
TorqRuntime = Annotated[
    str | None,
    typer.Option(
        "--torq-runtime",
        metavar="FILE",
        help="With --torq-mode bundled: the TorQ runtime `uqs deploy build-runtime torq` made",
    ),
]
TorqRuntimeSha256 = Annotated[
    str | None,
    typer.Option(
        "--torq-runtime-sha256",
        metavar="SHA256",
        help="Refuse a TorQ runtime whose sha256 is anything else (usually a target's)",
    ),
]
QRuntime = Annotated[
    str | None,
    typer.Option(
        "--q-runtime",
        metavar="FILE",
        help="With --q-mode bundled: the PeachQ runtime `uqs deploy build-runtime q` made",
    ),
]
QRuntimeSha256 = Annotated[
    str | None,
    typer.Option(
        "--q-runtime-sha256",
        metavar="SHA256",
        help="Refuse a q runtime whose sha256 is anything else (usually a target's)",
    ),
]
