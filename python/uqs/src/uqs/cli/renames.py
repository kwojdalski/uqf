"""Rule 6 of #1738: a renamed option keeps its old spelling for one release.

The old spelling is a hidden option (`old_spelling`) beside the new one, and
`from_old` takes its value when the new one was not given - logging, each time
it is used, what to type instead (`renamed`). The release after removes it.
"""

from __future__ import annotations

from typing import Any

import typer

from uqs.cli import shared


def renamed(old: str, new: str) -> None:
    """Say that `old` is now spelled `new`."""
    shared.log.warning("{} is renamed {}; the old spelling goes in the next release", old, new)


def old_spelling(new: str, *old: str) -> Any:
    """The hidden option that keeps `old` working; `from_old` reads it."""
    return typer.Option(*old, hidden=True, help=f"Renamed: {new}")


def from_old[T](value: T | None, old_value: T | None, old: str, new: str) -> T | None:
    """`value`, or - when only the old spelling was given - `old_value`, saying
    what to type instead."""
    if old_value is None:
        return value
    renamed(old, new)
    return old_value if value is None else value
