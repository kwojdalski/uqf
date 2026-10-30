"""Finding external publishers: the connectors `uqs connector start|stop|restart|status|logs` knows.

Every connector declares itself. A `uqs.external.*_feed` module defines
`FEED = ExternalFeed(...)`, or `FEEDS = (...)` when it holds several (the two
cryptorust recorders), and `discover()` finds every one: its start options,
the tables it publishes onto, and how to start, stop and read it. The CLI builds
`connector start`'s options from what is declared here (#1776), so a connector
with options reaches the CLI with no edit to it, and `uqs job remove` takes one
away by deleting its module.

What a connector `produces` is read twice, on purpose: by importing it here,
and from its text by `model.dependencies.external_producers`, which sits below
this layer and so cannot import it. `discover()` refuses a module where the two
disagree, so the dependency warnings cannot describe a connector other than the
one that runs.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

from uqs.model.dependencies import feed_produces
from uqs.paths import UqsError, UqsPaths

#: The parameters `connector start` has of its own; no option may take their names.
RESERVED_OPTIONS = frozenset({"name", "dry_run"})


def split(text: str) -> tuple[str, ...]:
    """A comma-separated option as a tuple, empty items dropped."""
    return tuple(s.strip() for s in text.split(",") if s.strip())


@dataclass(frozen=True)
class FeedOption:
    """One start option. `top_n_levels` is `--top-n-levels` on the command line."""

    name: str
    help: str
    type: type = str  # str or int
    default: Any = None  # applied by the feed when the option is not given
    many: bool = False  # comma-separated -> tuple[str, ...]
    secret: bool = False  # masked as *** in a dry run
    #: old spellings kept for one release after a rename (#1738 rule 6, cli/renames.py)
    aliases: tuple[str, ...] = ()

    @property
    def flag(self) -> str:
        return "--" + self.name.replace("_", "-")

    def value(self, given: Any) -> Any:
        """What `start` receives for `given`, the value as typed (None: not given).

        `is None`, not truthiness: `--interval-ms 0` or `--symbols ""` was typed,
        and reaches the feed as typed rather than as the default."""
        if given is None:
            return self.default
        return split(given) if self.many else given


@dataclass(frozen=True)
class ExternalFeed:
    """A publisher outside the plant: its name, its three verbs and its options."""

    name: str
    #: every declared option, defaults applied
    start: Callable[[UqsPaths, Mapping[str, Any]], int]
    stop: Callable[[UqsPaths], int | None]
    status: Callable[[UqsPaths], dict[str, str]]
    options: tuple[FeedOption, ...] = ()
    #: table -> who feeds it, for the dependency warnings
    produces: Mapping[str, str] = field(default_factory=dict)
    #: status table title; default "<name> feed status"
    title: str = ""
    #: appended to "<name> feed started (pid N)"
    started_note: str = ""

    @property
    def status_title(self) -> str:
        return self.title or f"{self.name} feed status"

    def start_options(self, given: Mapping[str, Any]) -> dict[str, Any]:
        """Every declared option for `start`: as given, else its default."""
        return {o.name: o.value(given.get(o.name)) for o in self.options}

    def started(self, pid: int) -> str:
        message = f"{self.name} feed started (pid {pid})"
        return f"{message} - {self.started_note}" if self.started_note else message


def _feeds_in(module: ModuleType) -> tuple[ExternalFeed, ...]:
    """The feeds `module` declares, as FEED or FEEDS - each checked."""
    declared: Iterable[Any] = getattr(module, "FEEDS", ())
    if hasattr(module, "FEED"):
        declared = (*declared, module.FEED)
    feeds = tuple(f for f in declared if isinstance(f, ExternalFeed))
    for feed in feeds:
        _check_start(feed, module.__name__)
        if clash := RESERVED_OPTIONS & {o.name for o in feed.options}:
            raise UqsError(f"{module.__name__}: feed {feed.name} declares {sorted(clash)}, "
                           "which `connector start` takes for itself - rename it")  # fmt: skip
    _check_produces(module, feeds)
    return feeds


def _check_start(feed: ExternalFeed, module: str) -> None:
    """Refuse a `start` that cannot take (paths, options) - one written before #1776."""
    try:
        inspect.signature(feed.start).bind(None, {})
    except TypeError:
        raise UqsError(
            f"{module}: feed {feed.name}'s start{inspect.signature(feed.start)} must take "
            "(paths, options) - the options it declares, with their defaults applied "
            "(#1776)"
        ) from None
    except ValueError:  # no signature to read: a builtin, which is never a feed's
        pass


def _check_produces(module: ModuleType, feeds: tuple[ExternalFeed, ...]) -> None:
    """What `module` produces, imported, must be what its text says (see module doc)."""
    path = getattr(module, "__file__", None)
    if not path:
        return
    imported = {t: who for feed in feeds for t, who in feed.produces.items()}
    try:
        read = feed_produces(Path(path))
    except ValueError as exc:
        raise UqsError(str(exc)) from None
    if read != imported:
        raise UqsError(
            f"{module.__name__}: what its feeds produce differs from what its text says - "
            f"write `produces=` as a dict of string literals or module constants"
        )


def check_options(feeds: Mapping[str, ExternalFeed]) -> dict[str, tuple[FeedOption, list[str]]]:
    """{option name: (its declaration, the feeds that take it)}, in declaration order.

    One option two feeds declare is one CLI option, so the two must agree on
    its type and `many`; their defaults stay their own."""
    out: dict[str, tuple[FeedOption, list[str]]] = {}
    for feed in feeds.values():
        for option in feed.options:
            if option.type not in (str, int):
                raise UqsError(f"feed {feed.name}'s {option.flag} is a {option.type.__name__}: "
                               "an option is a str or an int")  # fmt: skip
            if option.name not in out:
                out[option.name] = (option, [feed.name])
                continue
            first, takers = out[option.name]
            if (first.type, first.many) != (option.type, option.many):
                raise UqsError(
                    f"{option.flag} is declared by feeds {takers[0]} and {feed.name} with "
                    f"different types ({_shape(first)} and {_shape(option)}) - one CLI option "
                    "has one type"
                )
            takers.append(feed.name)
    return out


def _shape(option: FeedOption) -> str:
    return f"{option.type.__name__}{', many' if option.many else ''}"


def discover() -> dict[str, ExternalFeed]:
    """Every feed a `uqs.external.*_feed` module declares, by name."""
    import uqs.external as package

    found: dict[str, ExternalFeed] = {}
    for module in pkgutil.iter_modules(package.__path__):
        if not module.name.endswith("_feed"):
            continue
        for feed in _feeds_in(importlib.import_module(f"uqs.external.{module.name}")):
            if feed.name in found:
                raise UqsError(f"external feed {feed.name!r} is declared twice - rename one")
            found[feed.name] = feed
    check_options(found)
    return found
