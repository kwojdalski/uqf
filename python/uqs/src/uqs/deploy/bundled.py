"""A push's bundled components (#1557, steps 5 to 7): the TorQ, q and Python
runtimes it ships, from loading each to installing it.

  attach    loads each runtime the config bundles, checked whole, holds it to
            its pin (overrides.for_runtime) and, for q and Python, to the
            release: the platform it was built for, a q for a runtime declared
            on PeachQ, a Python of the version the release's wheels are for.
  records   each one's components entry, with what the push would do with it
            (runtime_install.state): for the plan and the report.
  install   under the lock, before the release is transferred: each one
            installed write-once or reused, and the q and the Python run once
            there.

A bundled q is PeachQ for a runtime declared on PeachQ (uqs.runtimes): the
issue's rule that nothing assumes PeachQ runs a workload until its tests pass
on it. A release of a KDB-X runtime keeps `q_mode = "existing"` - and KX's q is
never bundled at all (deploy/q_runtime.py).
"""

from __future__ import annotations

import os
from pathlib import Path

from uqs.deploy import components, overrides, python_runtime, runtime_install
from uqs.deploy.artifact import Artifact, ReleaseError
from uqs.deploy.config import Config, DeployError
from uqs.deploy.python_runtime import PythonRuntime
from uqs.deploy.q_runtime import KX_REFUSAL, QRuntime
from uqs.deploy.report import Report
from uqs.deploy.stages import Deployment
from uqs.interpreter import PEACHQ
from uqs.logger import get_logger
from uqs.runtimes import RUNTIMES

log = get_logger()

#: The components that can be bundled so far, in the order they install.
BUNDLABLE = ("torq", "q", "python")


def attach(cfg: Config, dep: Deployment, pkg: Artifact) -> list[dict]:
    """Each bundled runtime onto `dep`, after `dep.runtime` is the release's;
    the policy exceptions their pins needed."""
    exceptions: list[dict] = []
    for component in BUNDLABLE:
        if getattr(cfg, components.mode_key(component)) != components.BUNDLED:
            continue
        path = getattr(cfg, f"{component}_runtime")
        if component == "python" and not path:
            rt = _carried(pkg)
        else:
            rt = runtime_install.load(component, path)
        exceptions += overrides.for_runtime(cfg, component, rt.path.name, rt.sha256, rt.pinned)
        setattr(dep, f"{component}_runtime", rt)
    if dep.q_runtime is not None:
        _fits(dep.q_runtime, pkg, dep.runtime)
    if dep.python_runtime is not None:
        try:
            dep.python_runtime.fits(pkg.manifest["target"])
        except ReleaseError as exc:
            raise DeployError(exc.stage, str(exc)) from None
    return exceptions


def carried_dir() -> Path:
    """Where a release's carried Python runtime is extracted to, keyed by its
    sha256: beside fetch.py's downloaded releases."""
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "uqf" / "runtimes" / "python"


def _carried(pkg: Artifact) -> PythonRuntime:
    """A bundled Python with no `python_runtime`: the one the release carries
    (#1646), or the refusal naming both ways to give one."""
    try:
        rt = python_runtime.from_release(pkg.path, pkg.manifest, carried_dir())
    except ReleaseError as exc:
        raise DeployError(exc.stage, str(exc)) from None
    if rt is None:
        raise DeployError(
            "arguments",
            f"python_mode is bundled, and needs python_runtime: the artifact `uqs deploy "
            f"build-runtime python` makes - {pkg.path.name} carries none (build it with "
            "`uqs deploy build --python-runtime FILE` to carry one)",
        )
    log.info("the bundled Python is the one {} carries: {}", pkg.path.name, rt.sha256[:12])
    return rt


def _fits(rt: QRuntime, pkg: Artifact, runtime: str) -> None:
    """A bundled q runs this release: built for its platform, and the release's
    runtime declared on PeachQ."""
    target = pkg.manifest["target"]
    if rt.platform != (target["os"], target["arch"]):
        raise DeployError(
            "artifact",
            f"{rt.path.name} is PeachQ for {' '.join(rt.platform)}, and {pkg.path.name} is built "
            f"for {target['os']} {target['arch']} - build the q runtime on such a host",
        )
    if RUNTIMES[runtime].interpreter != PEACHQ:
        on_peachq = ", ".join(n for n, r in RUNTIMES.items() if r.interpreter == PEACHQ)
        raise DeployError(
            "artifact",
            f"{pkg.path.name} runs the {runtime} runtime, declared on KDB-X, and a bundled q is "
            f"PeachQ - for a runtime declared on it ({on_peachq}), as nothing assumes PeachQ "
            f"runs a workload its tests have not passed on. {KX_REFUSAL}",
        )


def _runtimes(dep: Deployment) -> list[tuple[str, runtime_install.Runtime]]:
    found = ((c, getattr(dep, f"{c}_runtime")) for c in BUNDLABLE)
    return [(c, rt) for c, rt in found if rt is not None]


def records(dep: Deployment) -> dict[str, dict]:
    """{component: its runtime's record}, read-only: preflight and the plan."""
    dest = dep.cfg.dest
    return {c: rt.record(dest, runtime_install.state(dep, rt)) for c, rt in _runtimes(dep)}


def install(dep: Deployment, rid: str, report: Report) -> None:
    """Install or reuse each bundled runtime, under the lock; run the q and
    the Python once installed, recording the version each reports."""
    for component, rt in _runtimes(dep):
        log.info("installing the {} runtime, or reusing it", rt.label)
        report.components[component]["install"] = runtime_install.install(dep, rt, rid)
        if isinstance(rt, QRuntime):
            version = runtime_install.probe(dep, rt)
            report.components[component]["version"] = report.qversion = version
        if isinstance(rt, PythonRuntime):
            report.components[component]["version"] = runtime_install.probe_python(dep, rt)
        dep.beat()
