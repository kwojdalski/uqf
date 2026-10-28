"""`uqs deploy component build torq|q|python`: the artifacts a bundled TorQ, a
bundled q and a bundled Python ship as (#1557, steps 5 to 7;
uqs.deploy.torq_runtime, uqs.deploy.q_runtime, uqs.deploy.python_runtime).
Beside cli/deploy.py, whose `build` makes the release they run under.

These are a release's bundled components - the interpreter and framework a
push installs with --q-mode/--torq-mode/--python-mode bundled - not a stack
runtime (`uqs --runtime`, `uqs runtime ...`), which is why the group is
`component` (#1740). `uqs deploy build-runtime`, its name before that, stays
for one release as a hidden alias that prints the new spelling."""

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

component_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="A release's bundled components - TorQ, q (PeachQ), Python: build their artifacts.",
)
deploy_app.add_typer(component_app, name="component")

#: The components that can be bundled.
COMPONENTS = ("torq", "q", "python")

#: What an operator may call KX's q: refused by name, never "no such runtime".
KX_NAMES = frozenset({KDBX, "kx", "kdb", "kdb+"})


Component = Annotated[
    str, typer.Argument(help="The component to build: torq, q (PeachQ) or python (CPython)")
]
Output = Annotated[
    Path | None,
    typer.Option("--output", help="Directory to write it into; default: dist/ at the repo root"),
]
Arch = Annotated[
    str, typer.Option("--arch", help=f"python only: the servers' {', '.join(PLATFORMS)}")
]
Python = Annotated[
    str | None,
    typer.Option("--python", help="python only: major.minor; default requires-python's"),
]


@component_app.command("build")
def build(
    component: Component,
    output: Output = None,
    arch: Arch = "x86_64",
    python: Python = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run", "-n", help="Check the component and say what it would build; build nothing"
        ),
    ] = False,
) -> None:
    """Build a bundled component's artifact: `torq`, TorQ and its starter pack exactly as
    vendored, for --torq-mode bundled; `q`, the pinned PeachQ, for --q-mode bundled;
    `python`, the CPython uv installs for linux --arch, for --python-mode bundled and
    `uqs deploy build --python-runtime`. KX's q is never bundled."""
    _build("uqs deploy component build", component, output, arch, python, dry_run)


@deploy_app.command("build-runtime", hidden=True)
def build_runtime(
    component: Component,
    output: Output = None,
    arch: Arch = "x86_64",
    python: Python = None,
) -> None:
    """Renamed `uqs deploy component build` (#1740); kept for one release."""
    log.warning(
        "`uqs deploy build-runtime` is renamed `uqs deploy component build {}` - "
        "the old name goes in the next release",
        component,
    )
    _build("uqs deploy build-runtime", component, output, arch, python)


def _build(
    command: str,
    component: str,
    output: Path | None,
    arch: str,
    python: str | None,
    dry_run: bool = False,
) -> None:
    if component.lower() in KX_NAMES:
        _failed(command, "arguments", ValueError(q_runtime.KX_REFUSAL))
    if component not in COMPONENTS:
        _failed(
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
        print(f"would build the {component} runtime{detail} into {out}; built nothing (dry run)")
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
        _failed(command, exc.stage, exc)
        return
    log.info("PeachQ {} runtime for {}, sha256 {}", qrt.manifest["commit"][:12],
             " ".join(qrt.platform), qrt.sha256)  # fmt: skip
    print(qrt.path)
