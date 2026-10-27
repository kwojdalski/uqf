"""What a release is made of: the tracked files it ships, the revision and
Python it names, and the wheels the server installs offline.
uqs.deploy.build packages them."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import InvalidWheelFilename, canonicalize_name, parse_wheel_filename
from packaging.version import Version

from uqs.deploy.artifact import RELEASE_DIR, ReleaseError, Target
from uqs.logger import get_logger

log = get_logger()

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
    "lib/kx-dbmaint",
)

#: The one directory of lib/ a release carries (#1423): KX's dbmaint.q, which
#: scripts/gates/fill_hdb_partitions.q loads for `uqs data hdb-check --fix`,
#: with the Apache-2.0 licence and NOTICE that must travel with it. Every other
#: lib/ tree is a reference or a runtime installed from elsewhere. Files here
#: ship byte-for-byte: portable.convert leaves them alone.
VENDORED_IN_RELEASE = "lib/kx-dbmaint"

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
    r"^python/uqs/process_overrides\.csv$",
    r"^lib/(?!kx-dbmaint/)",
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


def revision(root: Path, runner: Runner = subprocess.run) -> tuple[str, bool]:
    head = runner(
        ["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    status = runner(
        ["git", "-C", str(root), "status", "--porcelain", "--", *ALLOWLIST],
        capture_output=True,
        text=True,
        check=False,
    )
    if head.returncode or status.returncode:
        raise ReleaseError(
            "package", "this is not a git checkout - a release must name its revision"
        )
    return head.stdout.strip(), bool(status.stdout.strip())


def release_id(rev: str, now: datetime | None = None) -> str:
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{rev[:12]}"


def default_python(root: Path) -> str:
    """The Python a release targets: the lower bound of requires-python."""
    text = (root / "python" / "uqs" / "pyproject.toml").read_text()
    m = re.search(r'requires-python\s*=\s*">=\s*(3\.\d+)', text)
    if not m:
        raise ReleaseError(
            "arguments", "cannot read requires-python from python/uqs/pyproject.toml"
        )
    return m.group(1)


def target_environment(target: Target) -> dict[str, str]:
    """The PEP 508 marker environment of the target's Python, not this one's.

    A build on macOS for linux/x86_64 must keep `jeepney ; sys_platform ==
    'linux'` and drop `colorama ; sys_platform == 'win32'` (#1713): pip
    evaluates markers against the machine it runs on, whatever `--platform` says.
    """
    full = f"{target.python}.0"
    return {
        "implementation_name": "cpython",
        "implementation_version": full,
        "os_name": "posix",
        "platform_machine": target.arch,
        "platform_python_implementation": "CPython",
        "platform_release": "",
        "platform_system": target.os.title(),
        "platform_version": "",
        "python_full_version": full,
        "python_version": target.python,
        "sys_platform": target.os,
    }


def for_target(requirements: Path, target: Target) -> tuple[list[Requirement], list[str]]:
    """Rewrite `requirements` for the target: the pins whose marker holds there,
    markers stripped and hashes kept, and none of the others.

    Returns the pins kept and the lines dropped, so neither is silent.
    """
    env = target_environment(target)
    kept: list[Requirement] = []
    dropped: list[str] = []
    out: list[str] = []
    for line in requirements.read_text().replace("\\\n", " ").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-"):
            out.append(line)
            continue
        spec, _, rest = line.partition(" --")
        try:
            req = Requirement(spec)
        except InvalidRequirement as exc:
            raise ReleaseError(
                "python", f"cannot read exported requirement {spec!r}: {exc}"
            ) from None
        if req.marker is not None and not req.marker.evaluate(env):
            dropped.append(spec.strip())
            continue
        req.marker = None
        kept.append(req)
        options = [f"--{o.strip()}" for o in rest.split(" --") if o.strip()] if rest else []
        out.append(" \\\n    ".join([str(req), *options]))
    requirements.write_text("\n".join(out) + "\n")
    return kept, dropped


def missing_wheels(kept: Sequence[Requirement], wheels: Path) -> list[str]:
    """The kept pins with no wheel of that name and version among `wheels`."""
    have = set()
    for w in wheels.glob("*.whl"):
        try:
            name, version, *_ = parse_wheel_filename(w.name)
        except InvalidWheelFilename:
            continue
        have.add((name, version))
    missing = []
    for req in kept:
        pins = [s.version for s in req.specifier if s.operator in ("==", "===")]
        if not any((canonicalize_name(req.name), Version(v)) in have for v in pins):
            missing.append(str(req))
    return missing


def _run(root: Path, runner: Runner, what: str, argv: list[str]) -> None:
    try:
        r = runner(argv, cwd=root, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise ReleaseError("python", f"{what}: could not run {argv[0]}: {exc}") from None
    if r.returncode:
        tail = "\n".join((r.stderr or r.stdout or "").strip().splitlines()[-15:])
        raise ReleaseError("python", f"{what} failed (exit {r.returncode}):\n{tail}")


def python_payload(
    root: Path,
    into: Path,
    target: Target,
    runner: Runner = subprocess.run,
    extras: Sequence[str] = (),
) -> None:
    """requirements.txt and every wheel the target needs, under `into`.

    `extras` are uqs extras whose locked dependencies join the default ones
    (`kafka`: confluent-kafka, for `--with-kafka-connector`); none by default.

    The pins come from the committed uv.lock (`--frozen`, never re-resolved),
    with hashes, so the server's install refuses a wheel that is not the one
    locked. Dependency wheels are binary-only for the target's platform and
    Python - a source distribution would need a compiler and the network.

    The export keeps every platform's markers; they are evaluated for the
    TARGET (for_target), not the build host, and the pins that hold are
    downloaded with --no-deps (#1713). A pin left without a wheel - one the
    target has only as a source distribution - refuses the build here, never
    on the server.
    """
    wheels = into / "wheels"
    wheels.mkdir(parents=True)
    requirements = into / "requirements.txt"
    _run(
        root, runner, "exporting the locked dependencies",
        [
            "uv", "export", "--package", "uqs", "--no-dev", "--frozen",
            "--no-emit-workspace", "--format", "requirements-txt",
            *[arg for e in extras for arg in ("--extra", e)],
            "--quiet", "-o", str(requirements),
        ],
    )  # fmt: skip
    if not requirements.is_file():
        raise ReleaseError("python", "uv export wrote no requirements file")
    kept, dropped = for_target(requirements, target)
    if dropped:
        log.info(
            "{} locked pin(s) not for {}/{} Python {}: {}",
            len(dropped), target.os, target.arch, target.python, ", ".join(dropped),
        )  # fmt: skip
    _run(
        root, runner, "building the uqs wheel",
        ["uv", "build", "--package", "uqs", "--wheel", "--quiet", "--out-dir", str(wheels)],
    )  # fmt: skip
    _run(
        root, runner, "fetching the dependency wheels",
        [
            "uvx", "--from", "pip", "pip", "download", "--quiet", "--no-deps",
            "--requirement", str(requirements), "--dest", str(wheels),
            "--only-binary=:all:", "--implementation", "cp",
            "--python-version", target.python,
            *[arg for tag in target.platforms for arg in ("--platform", tag)],
        ],
    )  # fmt: skip
    apps = sorted(wheels.glob("uqs-*.whl"))
    if len(apps) != 1:
        raise ReleaseError("python", f"expected one uqs wheel, found {len(apps)}")
    missing = missing_wheels(kept, wheels)
    if missing:
        raise ReleaseError(
            "python",
            f"no {target.os}/{target.arch} Python {target.python} wheel for "
            f"{len(missing)} locked pin(s), so the server could not install them "
            f"offline: {', '.join(missing)}. A source distribution is not shipped: "
            "it needs a compiler and the network on the server.",
        )
