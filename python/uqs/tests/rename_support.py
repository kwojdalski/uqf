"""Rule 6 of #1738, under test: a renamed option's old spelling still works,
hidden, and says its new one. `said(monkeypatch)` returns the list that
`uqs.cli.renames.warn_renamed` appends each (old, new) warning to."""

from __future__ import annotations

from typing import Any

from uqs.cli import renames


class _Said:
    """renames.log, recording `warn_renamed`'s warnings and passing the rest through."""

    def __init__(self, real: Any) -> None:
        self.real = real
        self.renames: list[str] = []

    def warning(self, fmt: str, *args: Any) -> None:
        self.renames.append(fmt.format(*args))

    def __getattr__(self, name: str) -> Any:
        return getattr(self.real, name)


def said(monkeypatch) -> list[str]:
    log = _Said(renames.log)
    monkeypatch.setattr(renames, "log", log)
    return log.renames


def renamed(old: str, new: str) -> str:
    """The warning `warn_renamed(old, new)` logs."""
    return f"`{old}` is renamed `{new}`; the old spelling goes in the next release"
