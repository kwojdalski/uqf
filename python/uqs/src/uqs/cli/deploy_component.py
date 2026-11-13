"""`uqs deploy component build torq|q|python`: the artifacts a bundled TorQ, a
bundled q and a bundled Python ship as (#1557, steps 5 to 7;
uqs.deploy.torq_runtime, uqs.deploy.q_runtime, uqs.deploy.python_runtime).
Beside cli/deploy.py, whose `build` makes the release they run under.

These are a release's bundled components - the interpreter and framework a
push installs with --q-mode/--torq-mode/--python-mode bundled - not a stack
runtime (`uqs --runtime`, `uqs runtime ...`), which is why the group is
`component` (#1740)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from uqs.cli import deploy_output as deploy_out
from uqs.cli.choices import ChoiceOpt
from uqs.cli.concepts import ComponentArg
from uqs.cli.deploy import DEFAULT_ARCH, deploy_app
from uqs.cli.safety import ChangesState, dry_run_note
from uqs.cli.shared import console
from uqs.deploy import python_runtime, q_runtime, torq_runtime
from uqs.deploy.artifact import PLATFORMS, ReleaseError
from uqs.deploy.build import DEFAULT_OUTPUT
from uqs.deploy.components import COMPONENTS
from uqs.deploy.payload import default_python
from uqs.interpreter import KDBX
from uqs.logger import get_logger
from uqs.paths import repo_root

log = get_logger()

component_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="A release's bundled components - TorQ, q (PeachQ), Python: build their artifacts.",
)
deploy_app.add_typer(component_app, name="component")

#: What an operator may call KX's q: refused by name, never "no such runtime".
KX_NAMES = frozenset({KDBX, "kx", "kdb", "kdb+"})


Component = ComponentArg
Output = Annotated[
    Path | None,
    typer.Option(
        "--output-dir", help="Directory to write it into; default: dist/ at the repo root"
    ),
]
Arch = ChoiceOpt(
    PLATFORMS, "--arch", "Python only: the servers' architecture", default=DEFAULT_ARCH,
    required=True,
)  # fmt: skip
Python = Annotated[
    str | None,
    typer.Option("--python", help="Python only: major.minor; default requires-python's"),
]


@component_app.command("build", cls=ChangesState)
def build(
    component: Component,
    output: Output = None,
    arch: Arch = DEFAULT_ARCH,
    python: Python = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run", "-n", help="Check the component and say what it would build; build nothing"
        ),
    ] = False,
) -> None:
    """Build a bundled component's artifact: torq, q or python.

    `torq` is TorQ and its starter pack exactly as
    vendored, for --torq-mode bundled; `q`, the pinned PeachQ, for --q-mode bundled;
    `python`, the CPython uv installs for linux --arch, for --python-mode bundled and
    `uqs deploy build --python-component`. KX's q is never bundled."""
    _build("uqs deploy component build", component, output, arch, python, dry_run)


def _build(
    command: str,
    component: str,
    output: Path | None,
    arch: str,
    python: str | None,
    dry_run: bool = False,
) -> None:
    if component.lower() in KX_NAMES:
        deploy_out.failed(command, "arguments", ValueError(q_runtime.KX_REFUSAL))
    if component not in COMPONENTS:
        deploy_out.failed(
            command,
            "arguments",
            ValueError(f"no {component} component - one of {', '.join(COMPONENTS)}"),
        )
    root = repo_root()
    out = output or root / DEFAULT_OUTPUT
    if dry_run:
        detail = (
            f" (Python {python or default_python(root)}, {arch})" if component == "python" else ""
        )
        console.print(f"would build the {component} runtime{detail} into {out}", markup=False)
        console.print(dry_run_note())
        return
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
        deploy_out.failed(command, exc.stage, exc)
        return
    log.info("PeachQ {} runtime for {}, sha256 {}", qrt.manifest["commit"][:12],
             " ".join(qrt.platform), qrt.sha256)  # fmt: skip
    print(qrt.path)
