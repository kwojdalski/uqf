"""Every URL path the browser requests is a route the server serves (#2659).

test_api_types pairs the browser's types with the models; proxy.test.ts pairs
vite's proxy allowlist with the first segment of each path; openapi.json pins
the server's routes. Nothing paired the paths themselves: renaming
`/ops/backfill` in routes/ops.py and refreshing the snapshot kept every test
green while the browser's backfill view answered 404 - #199's bug, one layer
further in.

So every path literal under web/src (recursively, tests and fixtures aside) is
read from the source - a string starting `/`, or a template literal whose
`${...}` parts stand for one segment each - and held to the paths in
openapi.json. A `${...}` segment matches any one segment, as an OpenAPI
`{param}` does; a query string is not part of the path. Paths assembled at run
time beyond that (`/ops/${paths[view]}`, a push's `/close`) are checked as far
as their literal text goes.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

WEB_SRC = Path(__file__).resolve().parents[3] / "web" / "src"
SNAPSHOT = Path(__file__).with_name("openapi.json")

#: Paths the browser names that are not the API's: the app's own base path,
#: served by vite in development and by the static mount in production.
NOT_API = {"/ui/"}

_COMMENT = re.compile(r"/\*.*?\*/|(?<![:\"'`\w])//[^\n]*", re.S)
#: "/health", '/query', `/control/lifecycle/${action}` - a literal whose text
#: starts with a slash and a lower-case letter.
_LITERAL = re.compile(r"\"(/[a-z][^\"\n]*)\"|'(/[a-z][^'\n]*)'|`(/[a-z][^`]*)`")
_SUBST = re.compile(r"\$\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}")
_ANY = "{}"


def _sources() -> list[Path]:
    return sorted(
        p
        for p in WEB_SRC.rglob("*.ts*")
        if p.suffix in {".ts", ".tsx"}
        and not p.name.endswith((".test.ts", ".test.tsx", ".d.ts"))
        and "testing" not in p.relative_to(WEB_SRC).parts
    )


def _path(literal: str) -> str:
    """`literal` as a path: each `${...}` one wildcard segment, no query."""
    text = _SUBST.sub(_ANY, literal)
    return text.split("?", 1)[0]


def requested(text: str) -> set[str]:
    """The paths a module's source names, comments aside."""
    code = _COMMENT.sub("", text)
    return {_path(next(g for g in m.groups() if g is not None)) for m in _LITERAL.finditer(code)}


def _segments(path: str) -> list[str]:
    return path.strip("/").split("/")


def _matches(browser: str, server: str) -> bool:
    ours, theirs = _segments(browser), _segments(server)
    return len(ours) == len(theirs) and all(
        a == _ANY or b.startswith("{") or a == b for a, b in zip(ours, theirs, strict=True)
    )


def _requested_by_file() -> dict[str, set[str]]:
    found = {}
    for path in _sources():
        paths = requested(path.read_text()) - NOT_API
        if paths:
            found[path.relative_to(WEB_SRC).as_posix()] = paths
    return found


def test_every_path_the_browser_requests_is_served():
    served = list(json.loads(SNAPSHOT.read_text())["paths"])
    unserved = sorted(
        f"{file}: {path}"
        for file, paths in _requested_by_file().items()
        for path in paths
        if not any(_matches(path, route) for route in served)
    )
    assert not unserved, "the browser requests paths openapi.json does not serve:\n" + "\n".join(
        unserved
    )


def test_the_scan_finds_the_paths_it_is_meant_to():
    """A scan that found nothing would pass the test above vacuously."""
    every = set().union(*_requested_by_file().values())
    for path in (
        "/ops/backfill",  # App.tsx, the issue's example
        "/ops/processes",
        "/control/lifecycle/{}",
        "/services/{}/config/pushes",  # web/src/api/, which proxy.test.ts skips
        "/services/{}/files",
        "/activity/events",
    ):
        assert path in every, path
    assert len(every) >= 25, sorted(every)


def test_a_literal_reads_as_its_path():
    text = """
        // "/not/a/request" in a comment
        /** GET `/services/{target}/files` */
        useResource<Backfill>("/ops/backfill");
        request(`/coverage?${new URLSearchParams({ ...applied })}`, signal);
        request(`/services/${encodeURIComponent(t)}/files?${q}`, signal);
        const url = "https://example.com/x";
    """
    assert requested(text) == {"/ops/backfill", "/coverage", "/services/{}/files"}


def test_a_renamed_route_is_caught():
    assert _matches("/ops/backfill", "/ops/backfill")
    assert _matches("/services/{}/files", "/services/{target}/files")
    assert _matches("/ops/{}", "/ops/queue")
    assert not _matches("/ops/backfill", "/ops/backfills")
    assert not _matches("/services/{}/files", "/services/{target}/files/{name}")
