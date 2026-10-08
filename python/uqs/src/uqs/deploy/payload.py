"""What a release is made of: the tracked files it ships, and the Python
wheels the server installs offline. uqs.deploy.build packages them."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

from uqs.deploy.artifact import RELEASE_DIR, ReleaseError, Target

Runner = Callable[..., subprocess.CompletedProcess]

#: What a release is made of: tracked files under these paths, and nothing
#: else. The three Python packages are all here because uv's workspace lock
#: needs every member's pyproject to resolve, though only uqs is installed.
ALLOWLIST = (
    "pyproject.toml",
    "uv.lock",
    "src",
    "scripts",
    "python/uqs",
    "python/uqf_frontend",
    "python/uqf_airflow_provider",
)

#: Never shipped, even when tracked under the allowlist: secrets and
#: secret-bearing configuration, licences, environments, data, logs and
#: generated output. gateway_users.csv holds the demo gateway passwords; a
#: server that wants ordinary gateway users supplies its own in
#: shared/config/ (see uqs.deploy.config.SHARED_CONFIG), and uqs runs without one.
EXCLUDED_PATTERNS = (
    r"(^|/)\.env($|\.)(?!example$)",
    r"(^|/)\.envrc$",
    r"\.env$",
    r"(^|/)\.venv/",
    r"(^|/)node_modules/",
    r"(^|/)__pycache__/",
    r"(^|/)output/",
    r"(^|/)dist/",
    r"(^|/)tests/",
    r"\.(lic|pem|key|log|pyc)$",
    r"(^|/)id_(rsa|ed25519|ecdsa)",
    r"(^|/)passwords/",
    r"^scripts/torqconfig/permissions/gateway_users\.csv$",
    r"^lib/",
    rf"^{re.escape(RELEASE_DIR)}/",
)
_EXCLUDED = [re.compile(p) for p in EXCLUDED_PATTERNS]


def is_excluded(path: str) -> bool:
    return any(p.search(path) for p in _EXCLUDED)


def tracked_files(
    root: Path, runner: Runner = subprocess.run, paths: Sequence[str] = ALLOWLIST
) -> list[str]:
    r = runner(
        ["git", "-C", str(root), "ls-files", "-z", "--", *paths],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode:
        raise ReleaseError("package", f"git ls-files failed: {r.stderr.strip()}")
    return sorted(f for f in r.stdout.split("\0") if f and not is_excluded(f))


def python_payload(root: Path, into: Path, target: Target, runner: Runner = subprocess.run) -> None:
    """requirements.txt and every wheel the target needs, under `into`.

    The pins come from the committed uv.lock (`--frozen`, never re-resolved),
    with hashes, so the server's install refuses a wheel that is not the one
    locked. Dependency wheels are binary-only for the target's platform and
    Python - a source distribution would need a compiler and the network.
    """
    wheels = into / "wheels"
    wheels.mkdir(parents=True)
    requirements = into / "requirements.txt"
    steps = [
        (
            "exporting the locked dependencies",
            [
                "uv", "export", "--package", "uqs", "--no-dev", "--frozen",
                "--no-emit-workspace", "--format", "requirements-txt",
                "--quiet", "-o", str(requirements),
            ],
        ),
        (
            "building the uqs wheel",
            ["uv", "build", "--package", "uqs", "--wheel", "--quiet", "--out-dir", str(wheels)],
        ),
        (
            "fetching the dependency wheels",
            [
                "uvx", "--from", "pip", "pip", "download", "--quiet",
                "--requirement", str(requirements), "--dest", str(wheels),
                "--only-binary=:all:", "--implementation", "cp",
                "--python-version", target.python,
                *[arg for tag in target.platforms for arg in ("--platform", tag)],
            ],
        ),
    ]  # fmt: skip
    for what, argv in steps:
        try:
            r = runner(argv, cwd=root, capture_output=True, text=True, check=False)
        except OSError as exc:
            raise ReleaseError("python", f"{what}: could not run {argv[0]}: {exc}") from None
        if r.returncode:
            tail = "\n".join((r.stderr or r.stdout or "").strip().splitlines()[-15:])
            raise ReleaseError("python", f"{what} failed (exit {r.returncode}):\n{tail}")
    apps = sorted(wheels.glob("uqs-*.whl"))
    if len(apps) != 1:
        raise ReleaseError("python", f"expected one uqs wheel, found {len(apps)}")
    if not requirements.is_file():
        raise ReleaseError("python", "uv export wrote no requirements file")
