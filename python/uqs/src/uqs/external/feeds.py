"""Finding external publishers: the feeds `uqs feed start|stop|status` knows.

Databento, Kafka and the cryptorust recorders are listed in cli/external.py by
hand, each with its own start options. A feed scaffolded with
`uqs job new NAME --kind external` (#715) instead declares itself: its
`NAME_feed.py` defines `FEED = ExternalFeed(...)`, and `discover()` finds every
such module under uqs.external - so a new feed reaches the CLI with no edit to
it, and `uqs job remove` takes it away by deleting the module.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable
from dataclasses import dataclass

from uqs.paths import UqsPaths


@dataclass(frozen=True)
class ExternalFeed:
    """A publisher's name and its three verbs. It takes no options: a
    scaffolded feed's settings live in its own module."""

    name: str
    start: Callable[[UqsPaths], int]
    stop: Callable[[UqsPaths], int | None]
    status: Callable[[UqsPaths], dict[str, str]]


def discover() -> dict[str, ExternalFeed]:
    """Every `uqs.external.*_feed` module that declares a FEED, by name."""
    import uqs.external as package

    found: dict[str, ExternalFeed] = {}
    for module in pkgutil.iter_modules(package.__path__):
        if not module.name.endswith("_feed"):
            continue
        feed = getattr(importlib.import_module(f"uqs.external.{module.name}"), "FEED", None)
        if isinstance(feed, ExternalFeed):
            found[feed.name] = feed
    return found
