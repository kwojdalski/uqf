#!/usr/bin/env python3
"""peachq.py - the pinned PeachQ, found or built, for the lanes that run on it.

PeachQ is an MIT-licensed q. KDB-X stays the interpreter this tree targets;
PeachQ is a SECOND interpreter that a few lanes (q-docs-peachq locally, the
PeachQ steps in CI) are required to pass on too. This module is how those
lanes get a PeachQ binary, in this order:

  1. $UQF_PEACHQ, when set. It must name a runnable binary that identifies
     as PeachQ; anything else fails, with no fallback to the steps below - an
     override that quietly lost to a cache would make the override a guess.
  2. The cache, ${XDG_CACHE_HOME:-~/.cache}/uqf/peachq/<entry>/q, an entry
     per pinned commit, platform and build options, so moving the pin or the
     options selects a new entry rather than reusing the old binary. A cached
     binary is identified before it is used. A warm cache needs no network.
  3. A build: fetch the pinned commit, check the checkout IS that commit,
     `make` it with this platform's options, identify the result, and publish
     it into the cache by one rename. A lock serialises builders, so two runs
     starting together build once; a failed or interrupted build publishes
     nothing, and the next run starts it again.

The pin and the build options live in scripts/peachq.json, which CI reads
through this same module - there is no second copy of either.

Nothing here puts PeachQ on PATH or touches QCMD: the caller gets an
absolute path and hands it to the one lane that asked for it.

Standard library only: scripts/test.py runs under a bare python3 that cannot
import uqs, and loads this file by path.

Usage:
    python3 scripts/peachq.py            # print the binary's path, building if needed
    python3 scripts/peachq.py --commit   # print the pinned commit
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path

PIN_FILE = Path(__file__).resolve().with_name("peachq.json")
OVERRIDE_ENV = "UQF_PEACHQ"
CACHE_ENV = "XDG_CACHE_HOME"

#: Asks a binary which q it is. scripts/test.py takes it from here, and
#: test_interpreter.py holds it equal to uqs.interpreter's.
IDENTIFY_SCRIPT = '-1 $[-7h=type @[value;`.pq.load_natives;{0N}];"kdbx";"peachq"];\nexit 0\n'

_COMMIT = re.compile(r"[0-9a-f]{40}")
#: Build options reach `make` as NAME=VALUE arguments; nothing else gets in.
_OPTION = re.compile(r"[A-Za-z0-9_.+-]+")
#: uname spellings that name the same architecture.
_ARCH_ALIASES = {"aarch64": "arm64", "amd64": "x86_64"}

Log = Callable[[str], None]


class PeachQError(Exception):
    """PeachQ could not be provided; the message says why and what to do."""


def _stderr(message: str) -> None:
    print(f"peachq: {message}", file=sys.stderr, flush=True)


# ------------------------------------------------------------- pin and policy


def load_pin(path: Path = PIN_FILE) -> dict:
    """The pin and build policy, validated: a full commit, a repository, a
    make target, and per-platform options of safe NAME=VALUE pairs."""
    try:
        pin = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise PeachQError(f"cannot read the PeachQ pin {path}: {exc}") from exc
    commit = pin.get("commit")
    if not isinstance(commit, str) or not _COMMIT.fullmatch(commit):
        raise PeachQError(f"{path}: commit must be a full 40-character hash, not {commit!r}")
    for key in ("repository", "make_target"):
        if not isinstance(pin.get(key), str) or not pin[key]:
            raise PeachQError(f"{path}: {key} must be a non-empty string")
    platforms = pin.get("platforms")
    if not isinstance(platforms, dict) or not platforms:
        raise PeachQError(f"{path}: platforms must name at least one supported platform")
    for name, options in platforms.items():
        if not isinstance(options, dict) or not all(
            isinstance(k, str)
            and isinstance(v, str)
            and _OPTION.fullmatch(k)
            and _OPTION.fullmatch(v)
            for k, v in options.items()
        ):
            raise PeachQError(f"{path}: {name}'s build options must be NAME: VALUE strings")
    return pin


def platform_key(system: str | None = None, machine: str | None = None) -> str:
    """`<OS>-<arch>` as the pin spells it, e.g. Linux-x86_64 or Darwin-arm64."""
    machine = (machine or platform.machine()).lower()
    return f"{system or platform.system()}-{_ARCH_ALIASES.get(machine, machine)}"


def build_options(pin: dict, plat: str) -> dict[str, str]:
    """This platform's options. An undeclared platform is refused rather than
    given another platform's: CI's x86-64-v2 floor means nothing on arm64."""
    try:
        return dict(pin["platforms"][plat])
    except KeyError:
        supported = ", ".join(sorted(pin["platforms"]))
        raise PeachQError(
            f"PeachQ is not built automatically on {plat} (supported: {supported}). "
            f"Build it yourself and set {OVERRIDE_ENV} to the binary, or declare "
            f"{plat} with its build options in {PIN_FILE.name}."
        ) from None


def cache_root(env: Mapping[str, str]) -> Path:
    base = env.get(CACHE_ENV) or str(Path(env.get("HOME") or Path.home()) / ".cache")
    return Path(base) / "uqf" / "peachq"


def entry_name(commit: str, plat: str, options: Mapping[str, str]) -> str:
    """One cache entry per commit, platform and options. The options are
    hashed so the name stays short; the manifest inside spells them out."""
    digest = hashlib.sha256(json.dumps(dict(sorted(options.items()))).encode()).hexdigest()[:12]
    return f"{commit}-{plat}-{digest}"


def _manifest(pin: dict, plat: str, options: Mapping[str, str]) -> dict:
    return {
        "repository": pin["repository"],
        "commit": pin["commit"],
        "platform": plat,
        "make_target": pin["make_target"],
        "options": dict(sorted(options.items())),
    }


# ------------------------------------------------------- the real operations


def identify(binary: Path | str) -> str:
    """`peachq` or `kdbx` as the binary itself answers, else ``""``."""
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "identify.q"
        script.write_text(IDENTIFY_SCRIPT)
        try:
            result = subprocess.run(
                [str(binary), str(script), "-q"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except OSError, subprocess.TimeoutExpired:
            return ""
    return (result.stdout.strip().splitlines()[-1:] or [""])[0]


def missing_prerequisites(env: Mapping[str, str]) -> list[str]:
    """The build tools not on PATH: git, make and a C compiler."""
    tools = ["git", "make", env.get("CC") or "cc"]
    return [tool for tool in tools if shutil.which(tool, path=env.get("PATH")) is None]


def git_fetch(src: Path, repository: str, commit: str, log: Path) -> str:
    """Check out exactly `commit` into `src`; returns the HEAD it checked out."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    steps = [
        ["git", "init", "-q", str(src)],
        ["git", "-C", str(src), "fetch", "-q", "--depth", "1", repository, commit],
        ["git", "-C", str(src), "checkout", "-q", "FETCH_HEAD"],
    ]
    with log.open("a") as out:
        for argv in steps:
            if subprocess.run(argv, stdout=out, stderr=out, env=env, timeout=600).returncode:
                raise PeachQError(
                    f"could not fetch {repository}@{commit} ({' '.join(argv[3:5])} failed). "
                    f"Offline, or the commit is gone upstream? Details: {log}"
                )
    head = subprocess.run(
        ["git", "-C", str(src), "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    return head.stdout.strip()


def make_build(src: Path, target: str, options: Mapping[str, str], log: Path) -> Path:
    """`make <target> NAME=VALUE...` in `src`; returns the built binary."""
    argv = ["make", "-C", str(src), f"-j{os.cpu_count() or 2}", target]
    argv += [f"{k}={v}" for k, v in sorted(options.items())]
    with log.open("a") as out:
        code = subprocess.run(argv, stdout=out, stderr=out, check=False).returncode
    if code:
        tail = "\n".join(log.read_text(errors="replace").splitlines()[-20:])
        raise PeachQError(f"building PeachQ failed (make exit {code}); log: {log}\n{tail}")
    return src / target


# ------------------------------------------------------------------ resolve


def _override(binary: str, identify: Callable[[Path | str], str]) -> Path:
    found = shutil.which(binary)
    if found is None:
        raise PeachQError(f"{OVERRIDE_ENV}={binary!r} is not a runnable file")
    actual = identify(found)
    if actual != "peachq":
        what = f"is {actual}" if actual else "did not say which q it is"
        raise PeachQError(
            f"{OVERRIDE_ENV}={binary!r} {what}, not PeachQ - unset it to use the pinned build"
        )
    return Path(found).resolve()


def _valid_entry(entry: Path, manifest: dict, identify: Callable[[Path | str], str]) -> bool:
    binary = entry / "q"
    try:
        recorded = json.loads((entry / "manifest.json").read_text())
    except OSError, ValueError:
        return False
    return recorded == manifest and os.access(binary, os.X_OK) and identify(binary) == "peachq"


@contextlib.contextmanager
def _locked(path: Path, log: Log) -> Iterator[None]:
    with path.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log(f"another run is preparing this PeachQ; waiting for it ({path})")
            fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def resolve(
    env: Mapping[str, str] | None = None,
    *,
    pin_path: Path = PIN_FILE,
    plat: str | None = None,
    fetch: Callable[[Path, str, str, Path], str] = git_fetch,
    build: Callable[[Path, str, Mapping[str, str], Path], Path] = make_build,
    identify: Callable[[Path | str], str] = identify,
    prerequisites: Callable[[Mapping[str, str]], list[str]] = missing_prerequisites,
    log: Log = _stderr,
) -> Path:
    """The absolute path of a PeachQ binary: the override, else the cached
    pinned build, else a fresh build of it. Raises PeachQError otherwise."""
    env = os.environ if env is None else env
    if env.get(OVERRIDE_ENV):
        return _override(env[OVERRIDE_ENV], identify)

    pin = load_pin(pin_path)
    plat = plat or platform_key()
    options = build_options(pin, plat)
    manifest = _manifest(pin, plat, options)
    root = cache_root(env)
    entry = root / entry_name(pin["commit"], plat, options)
    # Published by one rename, so an entry that exists is a whole one.
    if _valid_entry(entry, manifest, identify):
        return entry / "q"

    root.mkdir(parents=True, exist_ok=True)
    with _locked(root / f"{entry.name}.lock", log):
        if _valid_entry(entry, manifest, identify):  # another run built it meanwhile
            return entry / "q"
        if entry.exists():
            log(f"discarding an unusable cache entry: {entry}")
            shutil.rmtree(entry)
        # Under the lock no live build owns these: they are interrupted ones.
        for stale in root.glob(f".build-{entry.name}-*"):
            shutil.rmtree(stale, ignore_errors=True)
        missing = prerequisites(env)
        if missing:
            raise PeachQError(
                f"PeachQ is not cached at {entry}, and building it needs "
                f"{', '.join(missing)} on PATH. Install them, or set {OVERRIDE_ENV} "
                "to a PeachQ binary you already have."
            )
        return _build_and_publish(pin, options, manifest, entry, fetch, build, identify, log)


def _build_and_publish(pin, options, manifest, entry, fetch, build, identify, log) -> Path:
    work = Path(tempfile.mkdtemp(prefix=f".build-{entry.name}-", dir=entry.parent))
    build_log = entry.parent / f"{entry.name}.log"
    build_log.write_text("")
    commit = pin["commit"]
    try:
        log(
            f"no cached build of {commit[:12]} for {manifest['platform']} "
            f"({' '.join(f'{k}={v}' for k, v in manifest['options'].items()) or 'default options'})"
        )
        log(f"fetching {pin['repository']}@{commit} - first run only")
        src = work / "src"
        head = fetch(src, pin["repository"], commit, build_log)
        if head != commit:
            raise PeachQError(
                f"fetched {head or 'nothing'}, not the pinned {commit}; refusing to build it"
            )
        log(f"building PeachQ (about a minute); log: {build_log}")
        built = build(src, pin["make_target"], options, build_log)
        out = work / "out"
        out.mkdir()
        shutil.copy2(built, out / "q")
        actual = identify(out / "q")
        if actual != "peachq":
            raise PeachQError(
                f"the freshly built {built} identifies as {actual or 'nothing'}, not PeachQ"
            )
        (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        os.rename(out, entry)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    log(f"cached at {entry / 'q'}")
    return entry / "q"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        if args == ["--commit"]:
            print(load_pin()["commit"])
        elif not args:
            print(resolve())
        else:
            print(__doc__.split("Usage:")[1].rstrip(), file=sys.stderr)
            return 2
    except PeachQError as exc:
        _stderr(str(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
