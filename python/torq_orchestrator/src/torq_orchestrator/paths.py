"""Where everything lives, and whether it is runnable.

UqfStackPaths is the one place that knows the layout of the vendored trees
and the writable data directory; every other module takes it as a parameter
rather than recomputing paths, so a relocated demo is one change here."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from torq_orchestrator.logger import get_logger

log = get_logger(__name__)


class UqfStackError(RuntimeError):
    """Raised for anything that stops the demo from being runnable as-is."""


@dataclass(frozen=True)
class UqfStackPaths:
    repo_root: Path
    torqhome: Path
    torqapphome: Path
    torqdata: Path
    scripts_dir: Path
    orchestrator_dir: Path

    @property
    def generated_procs(self) -> Path:
        return self.torqdata / "process.csv"

    @property
    def generated_schema(self) -> Path:
        return self.torqdata / "database.q"

    @property
    def generated_setenv(self) -> Path:
        return self.torqdata / "setenv.sh"

    @property
    def overrides_path(self) -> Path:
        return self.orchestrator_dir / "process_overrides.csv"

    @property
    def extra_processes_path(self) -> Path:
        return self.orchestrator_dir / "extra_processes.csv"

    @property
    def extra_schema_path(self) -> Path:
        return self.orchestrator_dir / "extra_schema.q"

    @property
    def crypto_recorder_pid_path(self) -> Path:
        return self.orchestrator_dir / "crypto_recorder.pid"

    @property
    def crypto_recorder_config_path(self) -> Path:
        return self.torqdata / "crypto_recorder_config.yaml"

    @property
    def crypto_fills_recorder_pid_path(self) -> Path:
        return self.orchestrator_dir / "crypto_fills_recorder.pid"


def default_paths() -> UqfStackPaths:
    # this file: <repo_root>/python/torq_orchestrator/src/torq_orchestrator/core.py
    orchestrator_dir = Path(__file__).resolve().parents[2]
    repo_root = orchestrator_dir.parents[1]
    return UqfStackPaths(
        repo_root=repo_root,
        torqhome=repo_root / "lib" / "torq",
        torqapphome=repo_root / "lib" / "torq-finance-starter-pack",
        torqdata=repo_root / "scripts" / "output" / "uqf-stack",
        scripts_dir=repo_root / "scripts",
        orchestrator_dir=orchestrator_dir,
    )


def check_prerequisites(paths: UqfStackPaths) -> None:
    if not (paths.torqhome / "torq.q").is_file():
        raise UqfStackError(f"{paths.torqhome} not found or missing torq.q - is lib/torq vendored?")
    if not (paths.torqapphome / "database.q").is_file():
        raise UqfStackError(
            f"{paths.torqapphome} not found or missing database.q - "
            "is lib/torq-finance-starter-pack vendored?"
        )
    for tool in ("envsubst", "rlwrap"):
        if shutil.which(tool) is None:
            raise UqfStackError(
                f"'{tool}' not found on PATH - torq.sh needs it "
                "(macOS: brew install gettext rlwrap)"
            )


def clean(paths: UqfStackPaths) -> None:
    if paths.torqdata.exists():
        log.info("Removing {}", paths.torqdata)
        shutil.rmtree(paths.torqdata)
    else:
        log.info("{} does not exist, nothing to clean", paths.torqdata)
