"""The EOD-reload markers, held to the gateway that writes the messages.

`_classify` turns q's error text into what the browser does next, and a
reload is the one error it retries: GatewayReloading is a transient 503 that
the page polls through. So a marker matching too much turns a permanent error
into a poll that never ends, and one matching too little shows a reload as a
failure. The authority is the vendored TorQ gateway's own text, read here -
not strings invented to agree with the marker list (#1794). `lib/` is read,
never edited.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from uqf_frontend.errors import GatewayReloading, QueryRejected
from uqf_frontend.gateway import _RELOADING_MARKERS, _classify
from uqs import paths as stack_paths

GATEWAY_Q = stack_paths.repo_root() / "lib" / "torq" / "code" / "processes" / "gateway.q"

#: What TorQ prefixes every error it sends a client with (`.gw.errorprefix`).
ERROR_PREFIX = "error: "


def _source() -> str:
    return Path(GATEWAY_Q).read_text()


def _literals(text: str) -> list[str]:
    return re.findall(r'"((?:[^"\\]|\\.)*)"', text)


def _reload_path() -> str:
    """The gateway's code that refuses or fails a query because of a reload:
    every line that asks `checkeod`, and the whole of `reloadstart`."""
    src = _source()
    eod_checks = [line for line in src.splitlines() if "checkeod[" in line]
    start = src.index("reloadstart:{")
    end = src.index("\n };", start)
    return "\n".join([*eod_checks, src[start:end]])


def _reload_messages() -> set[str]:
    """The reload path's client-facing messages, logs left out."""
    return {
        s
        for s in _literals(_reload_path())
        if s not in ("reload start called",)  # .lg.o, never sent to a client
    }


def test_the_reload_path_is_where_it_is_expected():
    """If the vendored gateway is re-pinned and this finds nothing, the golden
    tests below would pass vacuously - so they are anchored here."""
    assert _reload_messages() == {
        "unable to query multiple servers during eod reload",
        "query did not return prior to eod reload",
    }


@pytest.mark.parametrize("message", sorted(_reload_messages()))
def test_every_reload_message_the_gateway_sends_is_reloading(message):
    """Missing direction: a reload message no marker matches."""
    assert isinstance(_classify(Exception(ERROR_PREFIX + message)), GatewayReloading)


@pytest.mark.parametrize("marker", _RELOADING_MARKERS)
def test_every_marker_is_text_the_gateway_sends_on_a_reload(marker):
    """Stale direction: a marker that is not in any reload message matches
    only what the gateway never says - "not available" was one."""
    assert any(marker in m for m in _reload_messages()), marker


def test_no_other_gateway_message_is_taken_for_a_reload():
    """Every other string the gateway holds - its other errors among them -
    is not a reload, so none of them may become a transient 503."""
    others = set(_literals(_source())) - _reload_messages()
    wrongly = [s for s in others if isinstance(_classify(Exception(s)), GatewayReloading)]
    assert wrongly == []


@pytest.mark.parametrize(
    "text",
    [
        "service not available",  # matched "not available"
        "hdb reload failed",  # matched "reload"; uqf's own, permanent
        "unknown period: geod",  # matched bare "eod" inside a word
    ],
)
def test_text_that_only_resembles_a_reload_is_a_rejection(text):
    assert isinstance(_classify(Exception(text)), QueryRejected)
