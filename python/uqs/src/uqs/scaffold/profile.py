"""Putting a scaffolded process into a start profile, or exempting it.

Every standing process has to be reachable by `--profile` or listed in
UNPROFILED (model/unprofiled.py) with a reason -
`test_every_standing_process_is_reachable_or_exempt` fails otherwise. A
scaffold used to leave that as a note, so every new job turned the suite red
until someone edited model/profiles.py by hand. Choosing
the profile is still a person's judgement; writing the line is not.

A process that starts with the stack also goes into `default`, because
`default` is defined as what `uqs start all` runs, and a test holds the two
equal.

The edit is to Python source, so it refuses rather than guesses: the profile
must be a single-line tuple, and the result must fit the line limit.
"""

from __future__ import annotations

import ast
import json
import re
import textwrap
from collections.abc import Iterable
from pathlib import Path

from uqs.paths import PACKAGE_DIR, UqsError
from uqs.scaffold.plan import FileAction, WriteMode

PROFILES_FILE = PACKAGE_DIR / "src" / "uqs" / "model" / "profiles.py"
#: Its own module, not profiles.py: an exemption adds lines, a member does not (#2462).
UNPROFILED_FILE = PACKAGE_DIR / "src" / "uqs" / "model" / "unprofiled.py"
DEFAULT_PROFILE = "default"
UNPROFILED_ANCHOR = "unprofiled"
_PROFILE_PREFIX = "profile:"
_LINE_LIMIT = 100
#: The note membership() leaves when given neither answer; a bundle rewrites it.
NO_CHOICE = re.compile(r"^add (\S+) to a profile \(--profile NAME\)")


def bundle_route(proc: str) -> str:
    """How a bundle's process joins a stack, where a tree's names a profile:
    --profile and --unprofiled are refused with --bundle (#2488)."""
    return (
        f"{proc} joins a stack through a runtime, never a profile: name the bundle in "
        "runtime_bundles.json, then `uqs deploy push --jobs`"
    )


def profile_names(known: Iterable[str] | None) -> tuple[str, ...]:
    """The profile names to validate against: the caller's, or the registry's."""
    if known is not None:
        return tuple(known)
    from uqs.model.profiles import PROFILES

    return tuple(PROFILES)


def membership(
    proc: str,
    *,
    profile: str | None,
    unprofiled: str | None,
    start_with_all: bool,
    known_profiles: Iterable[str],
) -> tuple[list[FileAction], list[str]]:
    """The profiles.py / unprofiled.py edits for `proc`, and their notes.

    With neither a profile nor a reason, nothing is written and the note says
    what to do - the scaffold cannot know which set a job belongs in.
    """
    known = set(known_profiles)
    if profile and unprofiled:
        raise UqsError("--profile and --unprofiled are two answers to one question - pick one")
    if start_with_all and unprofiled is not None:
        # --start-with-all is membership of the default profile (#1591).
        raise UqsError(
            "--start-with-all and --unprofiled are two answers to one question: starting "
            "with the stack puts the process in the default profile - pick one"
        )
    if profile is not None and profile not in known:
        raise UqsError(
            f"--profile {profile!r} is not a profile - there are {', '.join(sorted(known))}"
        )
    if profile == DEFAULT_PROFILE and not start_with_all:
        raise UqsError(
            "--profile default is what `uqs start all` runs, so it needs --start-with-all too"
        )
    targets = [p for p in (profile, DEFAULT_PROFILE if start_with_all else None) if p]
    actions = [
        FileAction(PROFILES_FILE, proc, mode=WriteMode.APPEND, anchor=f"{_PROFILE_PREFIX}{p}")
        for p in dict.fromkeys(targets)
    ]
    if unprofiled is not None:
        if not unprofiled.strip():
            raise UqsError("--unprofiled needs the reason, as the other exemptions state theirs")
        actions.append(
            FileAction(
                UNPROFILED_FILE,
                _unprofiled_entry(proc, unprofiled.strip()),
                mode=WriteMode.APPEND,
                anchor=UNPROFILED_ANCHOR,
            )
        )
    notes = []
    if start_with_all:
        notes.append(
            f"{proc} starts with the stack: pin it in test_core.py's always_on, and check "
            "`uqs profile list` - the default start must fit the connection budget, and "
            "everything it reads must start with the stack too"
        )
    if actions:
        # `default` alone, from --start-with-all, is enough: the process is
        # reachable by a profile, so nothing is red without another.
        return actions, notes
    return [], [
        f"add {proc} to a profile (--profile NAME), or exempt it with --unprofiled REASON - "
        "`uqs profile list` shows the sets and their connection budget"
    ]


def _unprofiled_entry(proc: str, reason: str) -> str:
    """One UNPROFILED entry, the reason wrapped as the existing ones are.

    JSON's string syntax is Python's only up to the BMP: an ASCII-escaped
    character beyond it is a surrogate pair, which Python reads back as two
    lone surrogates. Written unescaped, every character reads back as given."""
    pieces = textwrap.wrap(reason, width=70, break_on_hyphens=False)
    body = "\n".join(
        f"        {json.dumps(piece + (' ' if i < len(pieces) - 1 else ''), ensure_ascii=False)}"
        for i, piece in enumerate(pieces)
    )
    return f'    "{proc}": (\n{body}\n    ),\n'


def apply(existing: str, action: FileAction) -> str:
    """profiles.py, or unprofiled.py, with `action` applied at its anchor."""
    if action.anchor == UNPROFILED_ANCHOR:
        return _with_unprofiled(existing, action.body)
    if action.anchor.startswith(_PROFILE_PREFIX):
        return _with_member(existing, action.anchor.removeprefix(_PROFILE_PREFIX), action.body)
    raise UqsError(f"unknown profiles.py anchor {action.anchor!r}")


def _block(lines: list[str], header: str, path: Path = PROFILES_FILE) -> tuple[int, int]:
    """(first line after `header`, index of its closing `}`)."""
    starts = [i for i, line in enumerate(lines) if line.startswith(header)]
    if len(starts) != 1:
        raise UqsError(f"{path} has {len(starts)} `{header}` lines, expected 1 - edit it by hand")
    start = starts[0] + 1
    for i in range(start, len(lines)):
        if lines[i].rstrip("\n") == "}":
            return start, i
    raise UqsError(f"{path}'s `{header}` block has no closing brace - edit it by hand")


def _with_member(existing: str, profile: str, proc: str) -> str:
    lines = existing.splitlines(keepends=True)
    start, end = _block(lines, "PROFILES:")
    entry = re.compile(rf"^(\s+){re.escape(json.dumps(profile))}: \((.*)\),\s*$")
    for i in range(start, end):
        if m := entry.match(lines[i]):
            inner = m.group(2).strip().rstrip(",")
            members = ast.literal_eval(f"({inner},)") if inner else ()
            if proc in members:
                raise UqsError(f"{proc} is already in the {profile} profile")
            new = (*members, proc)
            inner = ", ".join(json.dumps(p) for p in new) + ("," if len(new) == 1 else "")
            line = f"{m.group(1)}{json.dumps(profile)}: ({inner}),\n"
            if len(line.rstrip("\n")) > _LINE_LIMIT:
                raise UqsError(
                    f"adding {proc} to the {profile} profile passes the line limit - "
                    f"add it by hand in {PROFILES_FILE}"
                )
            lines[i] = line
            return "".join(lines)
    raise UqsError(
        f"the {profile} profile is not a one-line tuple in {PROFILES_FILE} - add {proc} by hand"
    )


def _with_unprofiled(existing: str, entry: str) -> str:
    lines = existing.splitlines(keepends=True)
    start, end = _block(lines, "UNPROFILED:", UNPROFILED_FILE)
    proc = entry.split('"')[1]
    if any(line.startswith(f'    "{proc}":') for line in lines[start:end]):
        raise UqsError(f"{proc} is already exempt in UNPROFILED")
    lines[end:end] = [entry]
    return "".join(lines)
