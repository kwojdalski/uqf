#!/usr/bin/env python3
"""Install an unpacked uqf release in place, offline (#1646).

`uqs deploy push` installs a release over ssh. A release delivered some other
way - unpacked by an external release process into its own directory - needs
the same preparation without uqs, which is not installed yet. This does it with
the standard library alone, run by the server's Python from inside the release:

    python3 scripts/install_release.py [--python PATH] [--env-file PATH] [--check]

In order, each step refusing with what to do before the next one writes:

  1. integrity - every file RELEASE_MANIFEST.json lists has its recorded sha256;
  2. platform  - this host's OS, architecture and glibc meet the artifact's
     target, whose wheels were built for exactly that;
  3. Python    - the interpreter is the version the artifact targets;
  4. install   - `.venv` is created in the release by `python -m venv`, and the
     locked dependencies then uqs itself are installed from `.release/wheels`
     with `--no-index` and the lock's hashes required: no registry is reached,
     and a wheel that is missing or altered is refused rather than fetched;
  5. environment - with `--env-file`, `deploy.env` is written to load that
     site file, so `.venv/bin/uqs` (and `uqs --target`) run the release with it.

Nothing outside the release directory is written: not the system Python, not q,
not TorQ, not `current`. Switching the current release and starting processes
stay separate, explicit steps. `--check` runs steps 1-3 only. A failed install
removes the `.venv` it created, so a rerun starts clean.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

MANIFEST = "RELEASE_MANIFEST.json"
VENV = ".venv"
ENV_FILE = "deploy.env"
INSTALLED = ".release/INSTALLED.json"
#: A manylinux wheel tag's glibc floor: manylinux_2_28_x86_64 needs glibc 2.28.
MANYLINUX = re.compile(r"manylinux_(\d+)_(\d+)_")


class Refused(Exception):
    """A step's refusal, said to the operator as it is."""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_manifest(release: Path) -> dict:
    path = release / MANIFEST
    if not path.is_file():
        raise Refused(f"no {MANIFEST} in {release} - run this from an unpacked uqf release")
    try:
        return json.loads(path.read_text())
    except ValueError as exc:
        raise Refused(f"{path} is not JSON: {exc}") from None


def check_integrity(release: Path, manifest: dict) -> int:
    """Every listed file present with its sha256; returns how many."""
    files = manifest.get("files") or {}
    if not files:
        raise Refused(f"{MANIFEST} lists no files - not a release this installer can check")
    bad = []
    for name, digest in sorted(files.items()):
        path = release / name
        if not path.is_file():
            bad.append(f"{name}: missing")
        elif _sha256(path) != digest:
            bad.append(f"{name}: altered")
    if bad:
        more = f"\n  ... and {len(bad) - 20} more" if len(bad) > 20 else ""
        shown = "\n  ".join(bad[:20]) + more
        raise Refused(f"the release does not match its manifest:\n  {shown}")
    return len(files)


def glibc_floor(target: dict) -> tuple[int, int] | None:
    """The lowest glibc the target's wheel platforms need, or None."""
    floors = [
        (int(m.group(1)), int(m.group(2)))
        for tag in target.get("platforms", [])
        if (m := MANYLINUX.match(tag))
    ]
    return min(floors) if floors else None


def check_platform(target: dict, system: str, machine: str, libc: tuple[str, str]) -> None:
    want_os, want_arch = target.get("os"), target.get("arch")
    if want_os and system.lower() != want_os:
        raise Refused(f"this host runs {system}; the release was built for {want_os}")
    if want_arch and machine != want_arch:
        raise Refused(f"this host is {machine}; the release was built for {want_arch}")
    floor = glibc_floor(target)
    if floor is None:
        return
    lib, version = libc
    if lib != "glibc" or not version:
        raise Refused(
            f"the release's wheels need glibc {floor[0]}.{floor[1]} or later, and this "
            f"host's C library could not be identified as glibc ({lib or 'unknown'})"
        )
    have = tuple(int(p) for p in version.split(".")[:2])
    if have < floor:
        raise Refused(
            f"the release's wheels need glibc {floor[0]}.{floor[1]} or later; "
            f"this host has {version}"
        )


def check_python(python: str, target: dict) -> str:
    """The interpreter's version, when it is the one the release targets."""
    exe = shutil.which(python) if os.sep not in python else python
    if not exe or not os.access(exe, os.X_OK):
        raise Refused(f"no executable Python {python!r} - pass --python PATH")
    out = subprocess.run(
        [exe, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
        capture_output=True,
        text=True,
        check=False,
    )
    version = out.stdout.strip()
    want = str(target.get("python", ""))
    if out.returncode or not version:
        raise Refused(f"{exe} did not report its version: {out.stderr.strip()}")
    if want and version != want:
        raise Refused(
            f"{exe} is Python {version}; the release's wheels are for Python {want} - "
            "pass --python with that version"
        )
    return version


def _run(argv: list[str], what: str, release: Path) -> None:
    env = {
        **os.environ,
        "PIP_NO_INDEX": "1",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_NO_INPUT": "1",
    }
    done = subprocess.run(argv, cwd=release, env=env, capture_output=True, text=True, check=False)
    if done.returncode:
        said = "\n".join((done.stderr or done.stdout).strip().splitlines()[-12:])
        raise Refused(f"{what} failed (exit {done.returncode}):\n{said}")


def install(release: Path, manifest: dict, python: str) -> None:
    """.venv from the release's own wheels, offline; removed again on failure."""
    py = manifest.get("python") or {}
    requirements, wheels, app = py.get("requirements"), py.get("wheel_dir"), py.get("app_wheel")
    if not (requirements and wheels and app):
        raise Refused(f"{MANIFEST} names no requirements, wheel directory or uqs wheel")
    venv = release / VENV
    if venv.exists():
        raise Refused(f"{venv} exists already - this release is installed; remove it to reinstall")
    try:
        _run([python, "-m", "venv", VENV], "creating .venv", release)
        pip = [str(venv / "bin" / "python"), "-m", "pip", "install", "--quiet", "--no-index"]
        _run(
            [*pip, "--find-links", wheels, "--require-hashes", "-r", requirements],
            "installing the locked dependencies from the release's wheels",
            release,
        )
        _run([*pip, "--no-deps", app], "installing uqs from the release's wheel", release)
    except BaseException:
        shutil.rmtree(venv, ignore_errors=True)
        raise


def write_env(release: Path, env_file: str) -> None:
    """deploy.env, loading the site's own file - which stays where it is,
    outside the release, so no setting is copied into it."""
    source = Path(env_file)
    if not source.is_absolute() or not source.is_file():
        raise Refused(f"--env-file {env_file!r} must be an absolute path to a readable file")
    (release / ENV_FILE).write_text(
        "# written by scripts/install_release.py: the site's environment for this release\n"
        f"set -a; . {shlex.quote(str(source))}; set +a\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--python", default="python3", help="the interpreter to build .venv with")
    parser.add_argument("--env-file", help="absolute path of the site file deploy.env loads")
    parser.add_argument("--check", action="store_true", help="verify only; write nothing")
    parser.add_argument(
        "--release", default=str(Path(__file__).resolve().parents[1]), help=argparse.SUPPRESS
    )
    args = parser.parse_args(argv)
    release = Path(args.release)
    try:
        manifest = read_manifest(release)
        target = manifest.get("target") or {}
        n = check_integrity(release, manifest)
        print(f"install_release: {n} files match the manifest of {manifest.get('release')}")
        check_platform(target, platform.system(), platform.machine(), platform.libc_ver())
        version = check_python(args.python, target)
        print(f"install_release: platform and Python {version} match the release's target")
        if args.check:
            return 0
        install(release, manifest, args.python)
        if args.env_file:
            write_env(release, args.env_file)
        (release / INSTALLED).write_text(
            json.dumps(
                {"release": manifest.get("release"), "python": version, "env_file": args.env_file},
                indent=2,
            )
            + "\n"
        )
    except Refused as exc:
        print(f"install_release: refused: {exc}", file=sys.stderr)
        return 1
    print(f"install_release: {release} installed; switching current and starting are yours to do")
    return 0


if __name__ == "__main__":
    sys.exit(main())
