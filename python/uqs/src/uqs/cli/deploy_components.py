"""`uqs deploy push`'s component modes (#1557, deploy/components.py): how q,
TorQ and Python reach the server - an existing q's path and home, and a bundled
TorQ's, q's and Python's runtimes - and the Python runtime `uqs deploy build`
can carry. Beside cli/deploy.py, which takes each as a parameter, so the push
command stays readable in one sitting."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer

from uqs.cli import completion
from uqs.cli.choices import ChoiceOpt, alternatives
from uqs.deploy import portable
from uqs.deploy.components import BUNDLED, EXISTING, MODES

DEFAULT = EXISTING


def _mode(component: str, existing: str, bundled: str) -> Any:
    """`--<component>-mode`: a choice of deploy/components.MODES, each with what
    it means for `component` (#1893, #1884)."""
    notes = {EXISTING: f"{existing} already on the server, found by preflight", BUNDLED: bundled}
    return ChoiceOpt(
        MODES, f"--{component}-mode", "How it reaches the server", notes=notes, default=DEFAULT,
        required=True, metavar="MODE",
    )  # fmt: skip


QMode = _mode(
    "q",
    "the q of --q-cmd/--q-home",
    "the PeachQ of --q-component, installed once under shared/runtimes/q/ (never KX's q)",
)
TorqMode = _mode(
    "torq",
    "the TorQ of --torq-home/--torq-app-home",
    "the vendored TorQ of --torq-component, installed once under shared/runtimes/torq/",
)
PythonMode = _mode(
    "python",
    "the Python uv finds",
    "the CPython of --python-component, installed once under shared/runtimes/python/, "
    "which .venv is made from",
)
TorqRuntime = Annotated[
    str | None,
    typer.Option(
        "--torq-component",
        metavar="FILE",
        help="With --torq-mode bundled: the TorQ component `uqs deploy component build torq` made",
    ),
]
TorqRuntimeSha256 = Annotated[
    str | None,
    typer.Option(
        "--torq-component-sha256",
        metavar="SHA256",
        help="Refuse a TorQ component whose sha256 is anything else (usually a target's)",
    ),
]
QRuntime = Annotated[
    str | None,
    typer.Option(
        "--q-component",
        metavar="FILE",
        help="With --q-mode bundled: the PeachQ component `uqs deploy component build q` made",
    ),
]
QRuntimeSha256 = Annotated[
    str | None,
    typer.Option(
        "--q-component-sha256",
        metavar="SHA256",
        help="Refuse a q component whose sha256 is anything else (usually a target's)",
    ),
]
PythonRuntime = Annotated[
    str | None,
    typer.Option(
        "--python-component",
        metavar="FILE",
        help="With --python-mode bundled: the CPython `uqs deploy component build python` made; "
        "default: the one the release carries",
    ),
]
PythonRuntimeSha256 = Annotated[
    str | None,
    typer.Option(
        "--python-component-sha256",
        metavar="SHA256",
        help="Refuse a Python component whose sha256 is anything else (usually a target's)",
    ),
]
#: `uqs deploy build --python-component`: carried in the release, for
#: scripts/deploy/install_release.py on a server with no Python of the release's version.
CarriedPythonRuntime = Annotated[
    str | None,
    typer.Option(
        "--python-component",
        metavar="FILE",
        help="Carry this CPython (`uqs deploy component build python`) in the release, for "
        "scripts/deploy/install_release.py, or a push with --python-mode bundled, to make .venv "
        "from; built for --arch and --python",
    ),
]
Qcmd = Annotated[
    str | None,
    typer.Option(
        "--q-cmd",
        help="The q executable on the server, an absolute path. Default: the deploying "
        "account's $QCMD there, else q on that account's PATH - never this machine's",
    ),
]
Qhome = Annotated[
    Path | None,
    typer.Option(
        "--q-home",
        help="QHOME on the server, where q finds its licence. Default: the deploying "
        "account's $QHOME there; refused when that is unset too",
    ),
]
#: `uqs deploy build --q-version`: the kdb+ the release's q is converted for.
ConvertFor = Annotated[
    str | None,
    typer.Option(
        "--q-version",
        metavar="VERSION",
        help=f"The servers' kdb+ version, {alternatives(portable.Q_TARGETS)}: convert the "
        "release's q for it - one without nested contexts. Only the artifact changes, never "
        "the checkout",
        autocompletion=completion.choices(*portable.Q_TARGETS),
    ),
]
