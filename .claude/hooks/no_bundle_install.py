#!/usr/bin/env python3
"""Claude Code PreToolUse hook: never install a sidecar bundle into this checkout (#2731).

`uqs job install <bundle>` and `uqs runtime prepare` write tracked files - job
files under src/etl/, `/ BEGIN bundle` blocks, installed_bundles.json - and the
checkout and its worktrees are shared by several sessions. This blocks either
command, with exit 2 and the reason on stderr, unless it is a dry run or the
directory is an instance (an untracked INSTANCE_MARKER beside it). A plain
folder of jobs that is not a bundle may still be installed: that is adding a job
to the tree. Standard library only: it runs under a bare python3.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path

INSTANCE_MARKER = ".uqf-instance"
MANIFEST = "bundle.json"
#: What separates one shell command from the next.
SEPARATORS = frozenset({"&&", "||", ";", "|", "&"})
DRY_RUN = frozenset({"--dry-run", "-n"})
#: uqs's global options that take a value, before the command.
VALUED = frozenset({"--runtime"})

WHY = (
    "a sidecar bundle installed here writes tracked files every session sharing this "
    "checkout then sees (#2731). Test it with `uv run python scripts/dev/bundle_suite.py "
    "<bundle>`, ship it with `uqs deploy build --bundle <bundle>`, or install it in a "
    f"separate clone holding an untracked {INSTANCE_MARKER}; --dry-run is allowed"
)


def _segments(command: str) -> list[list[str]]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        words = list(lexer)
    except ValueError:  # an unbalanced quote: nothing this hook can read
        return []
    found: list[list[str]] = [[]]
    for word in words:
        if word in SEPARATORS:
            found.append([])
        else:
            found[-1].append(word)
    return [s for s in found if s]


def _uqs_args(words: list[str]) -> list[str] | None:
    """The arguments of the uqs invocation in `words`, or None when there is none."""
    for i, word in enumerate(words):
        if Path(word).name == "uqs":
            return words[i + 1 :]
        if word == "-m" and words[i + 1 : i + 2] == ["uqs"]:
            return words[i + 2 :]
    return None


def _positional(args: list[str]) -> list[str]:
    found, skip = [], False
    for word in args:
        if skip:
            skip = False
        elif word in VALUED:
            skip = True
        elif not word.startswith("-"):
            found.append(word)
    return found


def refusal(command: str, root: Path) -> str | None:
    """Why `command`, run in `root`, may not run; None when it may."""
    if (root / INSTANCE_MARKER).exists():
        return None
    for words in _segments(command):
        args = _uqs_args(words)
        if args is None or DRY_RUN & set(args):
            continue
        words = _positional(args)
        if words[:2] == ["runtime", "prepare"]:
            return f"`uqs runtime prepare` is blocked in this checkout: {WHY}"
        if words[:2] == ["job", "install"] and len(words) > 2:
            folder = Path(words[2])
            if ((folder if folder.is_absolute() else root / folder) / MANIFEST).is_file():
                return f"`uqs job install {words[2]}` is blocked in this checkout: {WHY}"
    return None


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    command = (event.get("tool_input") or {}).get("command") or ""
    root = Path(event.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or ".")
    why = refusal(command, root)
    if why:
        print(why, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
