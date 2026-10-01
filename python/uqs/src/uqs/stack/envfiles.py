"""Load the repository's `.env` and `.envrc` into this process's environment.

Every process uqs starts - torq.sh and the fleet behind it, a backfill, a
feed - inherits `os.environ`, so loading the two files here, once, before any
command runs, is what makes them reach all of those. Without it `.envrc`
applied only when uqs was started from a shell where direnv had already
loaded it, and `uqs start` from an IDE, a cron line or another directory ran
the fleet without the credentials sitting in the file.

Precedence, highest first:

1. The environment uqs was started with. `UQF_X=1 uqs start` must mean
   what it says, and a shell where direnv is active already holds `.envrc`'s
   values, so they agree.
2. `.envrc`, through `direnv export json` - never sourced by this module.
   direnv only evaluates a file the operator has approved with
   `direnv allow`, and re-asks after every edit; sourcing it here would skip
   that check for a file that is executed, not read. No direnv, or a blocked
   file, is a warning and the file is skipped.
3. `.env`, as inert KEY=VALUE data.

Values are never logged, only names: the point of `.envrc` is to hold
credentials.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import MutableMapping
from dataclasses import dataclass, field
from pathlib import Path

from uqs.logger import get_logger
from uqs.paths import UqsError, repo_root

log = get_logger(__name__)

_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

#: direnv's own bookkeeping - which file is loaded, and the diff to undo it.
#: Meaningful to a shell hook, noise to a child process.
_DIRENV_INTERNAL = "DIRENV_"

#: Long enough for an .envrc that runs a command, short enough that a hung one
#: does not hang `uqs summary`.
_DIRENV_TIMEOUT_S = 30.0


@dataclass
class Loaded:
    """What load_env_files did: names only, never values."""

    set_from_dotenv: list[str] = field(default_factory=list)
    set_from_envrc: list[str] = field(default_factory=list)
    #: Present in a file but already in the environment, which wins.
    shadowed: list[str] = field(default_factory=list)


def parse_dotenv(text: str) -> dict[str, str]:
    """KEY=VALUE lines, as `.env` holds them.

    Blank lines and `#` comments are skipped, an `export ` prefix is allowed,
    and a value in matching single or double quotes is taken verbatim. An
    unquoted value ends at ` #`, so a trailing comment is not part of it. A
    line that is not an assignment is skipped rather than refused: this file
    is read on every uqs command, and a stray line must not stop the fleet
    being inspected.
    """
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ").lstrip()
        name, sep, value = line.partition("=")
        name = name.strip()
        if not sep or not _KEY.fullmatch(name):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].rstrip()
        values[name] = value
    return values


def envrc_values(root: Path, environ: MutableMapping[str, str] = os.environ) -> dict[str, str]:
    """What `.envrc` in *root* would export, as direnv evaluates it.

    `direnv export json` prints the variables that differ from the current
    environment, so a shell where direnv already loaded this file gets an
    empty answer and nothing changes. A null value is direnv unloading some
    other directory's `.envrc` from the caller's shell - that is the shell's
    business, not a child process's, so it is dropped rather than unset.
    """
    if not (root / ".envrc").is_file():
        return {}
    direnv = shutil.which("direnv")
    if direnv is None:
        log.warning(".envrc not loaded: direnv is not installed (https://direnv.net)")
        return {}
    try:
        result = subprocess.run(  # noqa: S603 - a fixed argv, no shell
            [direnv, "export", "json"],
            cwd=root,
            env=dict(environ),
            capture_output=True,
            text=True,
            check=False,
            timeout=_DIRENV_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        log.warning(".envrc not loaded: direnv did not finish within {:g}s", _DIRENV_TIMEOUT_S)
        return {}
    if result.returncode != 0:
        # Blocked is the usual case: a new or edited file awaiting approval.
        reason = _last_line(result.stderr) or f"direnv exited {result.returncode}"
        log.warning(".envrc not loaded: {}", reason)
        return {}
    if not result.stdout.strip():
        return {}
    exported = json.loads(result.stdout)
    return {
        name: value
        for name, value in exported.items()
        if value is not None and not name.startswith(_DIRENV_INTERNAL)
    }


def _last_line(text: str) -> str:
    """direnv's message without its ANSI colour codes."""
    lines = [re.sub(r"\x1b\[[0-9;]*m", "", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    return lines[-1] if lines else ""


def load_env_files(root: Path, environ: MutableMapping[str, str] = os.environ) -> Loaded:
    """Put `.env` and `.envrc` from *root* into *environ*, under the precedence
    in this module's docstring. Returns what was set and what was shadowed."""
    loaded = Loaded()
    dotenv_path = root / ".env"
    dotenv = parse_dotenv(dotenv_path.read_text(encoding="utf-8")) if dotenv_path.is_file() else {}
    envrc = envrc_values(root, environ)
    for name, value in {**dotenv, **envrc}.items():
        if name in environ:
            if environ[name] != value:
                loaded.shadowed.append(name)
            continue
        environ[name] = value
        (loaded.set_from_envrc if name in envrc else loaded.set_from_dotenv).append(name)
    if loaded.set_from_dotenv or loaded.set_from_envrc:
        log.debug(
            "env files loaded from {}: .env {} | .envrc {}",
            root,
            sorted(loaded.set_from_dotenv),
            sorted(loaded.set_from_envrc),
        )
    if loaded.shadowed:
        log.debug(
            "already in the environment, so the file's value is not used: {}",
            sorted(loaded.shadowed),
        )
    return loaded


def load_repo_env_files() -> Loaded:
    """load_env_files for this repository's root, called by both entry points
    (`uqs`, `uqs-mcp`) before they do anything else. A uqs installed away
    from a checkout has no files to load, which is not an error."""
    try:
        root = repo_root()
    except UqsError as exc:
        log.debug("no env files loaded: {}", exc)
        return Loaded()
    return load_env_files(root)
