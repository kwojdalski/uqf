"""`uqs deploy push`'s component modes (#1557, deploy/components.py): how q,
TorQ and Python reach the server - an existing q's path and home, and a bundled
TorQ's, q's and Python's runtimes - and the Python runtime `uqs deploy build`
can carry. Beside cli/deploy.py, which takes each as a parameter, so the push
command stays readable in one sitting."""

from __future__ import annotations

from typing import Annotated

import typer

from uqs.cli import completion
from uqs.cli.choices import alternatives
from uqs.deploy.components import BUNDLED, EXISTING, MODES


def _mode(component: str, existing: str, bundled: str) -> typer.models.OptionInfo:
    """`--<component>-mode`: its help and completion from deploy/components.MODES,
    each mode with what it means for `component` (#1893)."""
    notes = {EXISTING: f"{existing} already on the server, found by preflight", BUNDLED: bundled}
    return typer.Option(
        f"--{component}-mode",
        metavar="MODE",
        help=alternatives(MODES, notes, default=DEFAULT),
        autocompletion=completion.choices(*MODES),
    )


DEFAULT = EXISTING
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
PythonMode = Annotated[
    str,
    _mode(
        "python",
        "the Python uv finds",
        "the CPython of --python-runtime, installed once under shared/runtimes/python/, "
        "which .venv is made from",
    ),
]
TorqRuntime = Annotated[
    str | None,
    typer.Option(
        "--torq-runtime",
        metavar="FILE",
        help="With --torq-mode bundled: the TorQ runtime `uqs deploy component build torq` made",
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
        help="With --q-mode bundled: the PeachQ runtime `uqs deploy component build q` made",
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
PythonRuntime = Annotated[
    str | None,
    typer.Option(
        "--python-runtime",
        metavar="FILE",
        help="With --python-mode bundled: the CPython `uqs deploy component build python` made; "
        "default: the one the release carries",
    ),
]
PythonRuntimeSha256 = Annotated[
    str | None,
    typer.Option(
        "--python-runtime-sha256",
        metavar="SHA256",
        help="Refuse a Python runtime whose sha256 is anything else (usually a target's)",
    ),
]
#: `uqs deploy build --python-runtime`: carried in the release, for
#: scripts/install_release.py on a server with no Python of the release's version.
CarriedPythonRuntime = Annotated[
    str | None,
    typer.Option(
        "--python-runtime",
        metavar="FILE",
        help="Carry this CPython (`uqs deploy component build python`) in the release, for "
        "scripts/install_release.py, or a push with --python-mode bundled, to make .venv "
        "from; built for --arch and --python",
    ),
]
Qcmd = Annotated[
    str | None,
    typer.Option(
        "--qcmd",
        help="The q executable on the server, an absolute path. Default: the deploying "
        "account's $QCMD there, else q on that account's PATH - never this machine's",
    ),
]
Qhome = Annotated[
    str | None,
    typer.Option(
        "--qhome",
        help="QHOME on the server, where q finds its licence. Default: the deploying "
        "account's $QHOME there; refused when that is unset too",
    ),
]
