"""`uqs deploy build-runtime torq`: the artifact a bundled TorQ ships as
(#1557, step 5; uqs.deploy.torq_runtime). Beside cli/deploy.py, whose
`build` makes the release it runs under."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from uqs.cli.deploy import _failed, deploy_app
from uqs.deploy import torq_runtime
from uqs.deploy.artifact import ReleaseError
from uqs.deploy.build import DEFAULT_OUTPUT
from uqs.logger import get_logger
from uqs.paths import repo_root

log = get_logger()


@deploy_app.command("build-runtime")
def build_runtime(
    component: Annotated[str, typer.Argument(help="The component to build: torq")],
    output: Annotated[
        Path | None,
        typer.Option(
            "--output", help="Directory to write it into; default: dist/ at the repo root"
        ),
    ] = None,
) -> None:
    """Build TorQ and its starter pack, exactly as vendored, into a runtime artifact
    for `uqs deploy push --torq-mode bundled --torq-runtime FILE`."""
    if component != "torq":
        _failed("uqs deploy build-runtime", "arguments", ValueError(f"no {component} runtime"))
    root = repo_root()
    try:
        built = torq_runtime.build_runtime(root, output or root / DEFAULT_OUTPUT)
    except ReleaseError as exc:
        _failed("uqs deploy build-runtime", exc.stage, exc)
        return
    log.info("TorQ {} runtime, sha256 {}", built.version or "of unknown version", built.sha256)
    print(built.path)
