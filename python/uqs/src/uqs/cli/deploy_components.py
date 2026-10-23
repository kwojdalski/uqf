"""`uqs deploy push`'s component modes (#1557, deploy/components.py): how q,
TorQ and Python reach the server. Beside cli/deploy.py, which takes each as a
parameter, so the push command stays readable in one sitting."""

from __future__ import annotations

from typing import Annotated

import typer

from uqs.deploy.components import EXISTING


def _mode(component: str, fields: str) -> typer.models.OptionInfo:
    return typer.Option(
        f"--{component}-mode",
        metavar="MODE",
        help=f"existing: {fields} already on the server, found by preflight. bundled is "
        "not yet supported (#1557)",
    )


QMode = Annotated[str, _mode("q", "the q of --qcmd/--qhome")]
TorqMode = Annotated[str, _mode("torq", "the TorQ of --torq-home/--torq-app-home")]
PythonMode = Annotated[str, _mode("python", "the Python uv finds")]
DEFAULT = EXISTING
