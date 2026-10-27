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
     target: the glibc its wheels need, which the manifest records;
  3. Python    - the interpreter is the version the artifact targets. A release
     built with `uqs deploy build --python-runtime` carries a CPython: unless
     --python names another, it is unpacked into .release/python, checked file
     by file against its own manifest, and is the interpreter - so the host
     needs only a python3 (3.8 or later) to run this script;
  4. install   - `.venv` is created in the release by `python -m venv`, and the
     locked dependencies then uqs itself (and, for a release built with
     --frontend, uqf-frontend) are installed from `.release/wheels`
     with `--no-index` and the lock's hashes required: no registry is reached,
     and a wheel that is missing or altered is refused rather than fetched;
  5. environment - with `--env-file`, `deploy.env` is written to load that
     site file, so `.venv/bin/uqs` (and `uqs --target`) run the release with it.

Nothing outside the release directory is written: not the system Python, not q,
not TorQ, not `current`. Switching the current release and starting processes
stay separate, explicit steps. `--check` runs steps 1-3 only, writing nothing: a
carried Python is unpacked and run by the install, not by --check. A failed
install removes the `.venv` it created, so a rerun starts clean.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

MANIFEST = "RELEASE_MANIFEST.json"
VENV = ".venv"
ENV_FILE = "deploy.env"
INSTALLED = ".release/INSTALLED.json"
#: Where a carried Python runtime is unpacked, and the manifest inside it.
RUNTIME_DIR = ".release/python"
RUNTIME_MANIFEST = "RUNTIME_MANIFEST.json"
#: What `uname -m` may call an architecture, by the name a release target uses
#: (uqs.deploy.artifact.ARCH_ALIASES, which this stdlib-only script cannot import).
ARCH_ALIASES = {"amd64": "x86_64", "arm64": "aarch64"}


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
    """The glibc the release's wheels need, as `uqs deploy build` recorded it
    from their manylinux tags; None when they need none."""
    floor = target.get("glibc")
    if not floor:
        return None
    major, minor = (int(p) for p in str(floor).split(".")[:2])
    return major, minor


def check_platform(target: dict, system: str, machine: str, libc: tuple[str, str]) -> None:
    want_os, want_arch = target.get("os"), target.get("arch")
    if want_os and system.lower() != want_os:
        raise Refused(f"this host runs {system}; the release was built for {want_os}")
    if want_arch and ARCH_ALIASES.get(machine, machine) != want_arch:
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


def _unpack(archive: Path, sha256: str, home: Path) -> None:
    """The carried runtime into `home`, whole or not at all: its sha256 first,
    then only regular files at relative paths, each written new."""
    if _sha256(archive) != sha256:
        raise Refused(f"{archive.name} is not the Python runtime the manifest records")
    partial = home.with_name(home.name + ".partial")
    shutil.rmtree(partial, ignore_errors=True)
    try:
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar:
                parts = Path(member.name).parts
                if not member.isfile() or member.name.startswith("/") or ".." in parts:
                    raise Refused(f"{archive.name}: member {member.name!r} is not a plain file")
                target = partial / member.name
                target.parent.mkdir(parents=True, exist_ok=True)
                source = tar.extractfile(member)
                if source is None:  # pragma: no cover - a regular file always extracts
                    raise Refused(f"{archive.name}: member {member.name!r} did not extract")
                with source, open(target, "xb") as out:
                    shutil.copyfileobj(source, out)
                target.chmod(0o755 if member.mode & 0o100 else 0o644)
                # its mtime too: a .pyc whose source seems newer is rewritten
                os.utime(target, (member.mtime, member.mtime))
        partial.rename(home)
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise


def _cache_of(rel: str, listed: dict) -> bool:
    """Whether `rel` is a __pycache__ .pyc of a source `listed` names."""
    parent, _, name = rel.rpartition("/")
    folder, _, cache = parent.rpartition("/")
    if cache != "__pycache__" or not name.endswith(".pyc"):
        return False
    source = name.split(".")[0] + ".py"
    return (f"{folder}/{source}" if folder else source) in listed


def carried_python(release: Path, record: dict) -> str:
    """The interpreter of the Python runtime the release carries: unpacked into
    RUNTIME_DIR once, and checked file by file against its own manifest."""
    home = release / RUNTIME_DIR
    if not home.exists():
        _unpack(release / record["archive"], record["sha256"], home)
    try:
        listed = json.loads((home / RUNTIME_MANIFEST).read_text())["files"]
    except (OSError, ValueError, KeyError) as exc:
        raise Refused(f"{home} holds no readable {RUNTIME_MANIFEST}: {exc}") from None
    found = {
        p.relative_to(home).as_posix(): p for p in home.rglob("*") if p.is_file() or p.is_symlink()
    }
    found.pop(RUNTIME_MANIFEST, None)
    # python-build-standalone ships no .pyc for some stdlib modules, so the
    # first run of one writes it beside its source; CPython checks it against
    # that (verified) source before use. A cache, not a change - the rule
    # uqs.deploy.runtime_install's own check applies on a server.
    found = {r: f for r, f in found.items() if r in listed or not _cache_of(r, listed)}
    bad = sorted(set(found) ^ set(listed))
    bad += sorted(r for r in listed if r in found and _sha256(found[r]) != listed[r])
    if bad:
        raise Refused(f"{home} is not the runtime it was unpacked from: {', '.join(bad[:5])}")
    return str(home / record["interpreter"])


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
        # what `uqs deploy build --frontend` adds: uqf-frontend, its dependencies
        # already installed above from the lock
        for extra in py.get("extra_wheels") or []:
            _run([*pip, "--no-deps", extra], f"installing {extra}", release)
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
    parser.add_argument(
        "--python",
        help="the interpreter to build .venv with; default: the CPython the release carries, "
        "else python3",
    )
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
        record = (manifest.get("python") or {}).get("runtime")
        if args.check and record and args.python is None:
            print(
                f"install_release: the platform matches; the release carries Python "
                f"{record['version']}, unpacked and run by the install"
            )
            return 0
        python = args.python or (carried_python(release, record) if record else "python3")
        version = check_python(python, target)
        print(f"install_release: platform and Python {version} ({python}) match the target")
        if args.check:
            return 0
        install(release, manifest, python)
        if args.env_file:
            write_env(release, args.env_file)
        (release / INSTALLED).write_text(
            json.dumps(
                {
                    "release": manifest.get("release"),
                    "python": version,
                    "interpreter": python,
                    "env_file": args.env_file,
                },
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
