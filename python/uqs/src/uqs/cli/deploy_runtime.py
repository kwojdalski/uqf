"""`uqs deploy build-runtime torq|q|python`: the artifacts a bundled TorQ, a
bundled q and a bundled Python ship as (#1557, steps 5 to 7;
uqs.deploy.torq_runtime, uqs.deploy.q_runtime, uqs.deploy.python_runtime).
Beside cli/deploy.py, whose `build` makes the release they run under."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from uqs.cli.deploy import _failed, deploy_app
from uqs.deploy import python_runtime, q_runtime, torq_runtime
from uqs.deploy.artifact import PLATFORMS, ReleaseError
from uqs.deploy.build import DEFAULT_OUTPUT
from uqs.deploy.payload import default_python
from uqs.interpreter import KDBX
from uqs.logger import get_logger
from uqs.paths import repo_root

log = get_logger()

#: What an operator may call KX's q: refused by name, never "no such runtime".
KX_NAMES = frozenset({KDBX, "kx", "kdb", "kdb+"})


@deploy_app.command("build-runtime")
def build_runtime(
    component: Annotated[
        str, typer.Argument(help="The component to build: torq, q (PeachQ) or python (CPython)")
    ],
    output: Annotated[
        Path | None,
        typer.Option(
            "--output", help="Directory to write it into; default: dist/ at the repo root"
        ),
    ] = None,
    arch: Annotated[
        str, typer.Option("--arch", help=f"python only: the servers' {', '.join(PLATFORMS)}")
    ] = "x86_64",
    python: Annotated[
        str | None,
        typer.Option("--python", help="python only: major.minor; default requires-python's"),
    ] = None,
) -> None:
    """Build a runtime artifact: `torq`, TorQ and its starter pack exactly as vendored,
    for --torq-mode bundled; `q`, the pinned PeachQ, for --q-mode bundled; `python`, the
    CPython uv installs for linux --arch, for --python-mode bundled and `uqs deploy build
    --python-runtime`. KX's q is never bundled."""
    if component.lower() in KX_NAMES:
        _failed("uqs deploy build-runtime", "arguments", ValueError(q_runtime.KX_REFUSAL))
    if component not in ("torq", "q", "python"):
        _failed("uqs deploy build-runtime", "arguments", ValueError(f"no {component} runtime"))
    root = repo_root()
    out = output or root / DEFAULT_OUTPUT
    try:
        if component == "python":
            source = python_runtime.fetch(python or default_python(root), arch)
            prt = python_runtime.build_runtime(source, out)
            log.info("Python {} runtime for {}, sha256 {}", prt.version, " ".join(prt.platform),
                     prt.sha256)  # fmt: skip
            print(prt.path)
            return
        if component == "torq":
            built = torq_runtime.build_runtime(root, out)
            version = built.version or "of unknown version"
            log.info("TorQ {} runtime, sha256 {}", version, built.sha256)
            print(built.path)
            return
        qrt = q_runtime.build_runtime(q_runtime.pinned_peachq(root / "scripts"), out)
    except ReleaseError as exc:
        _failed("uqs deploy build-runtime", exc.stage, exc)
        return
    log.info("PeachQ {} runtime for {}, sha256 {}", qrt.manifest["commit"][:12],
             " ".join(qrt.platform), qrt.sha256)  # fmt: skip
    print(qrt.path)
