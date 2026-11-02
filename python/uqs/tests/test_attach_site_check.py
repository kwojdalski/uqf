"""The controlled check against a real site (#1847, acceptance item 7): opt-in.

Nothing here runs by default. It runs with `-m external` AND
`UQF_SITE_ATTACHMENT` naming an attachment that is saved and verified on this
machine, under the site's own environment - TORQHOME, TORQAPPHOME and
KDBSECURE as the site's processes see them, and the password variable the
attachment names. It starts that attachment's publisher the way an operator
does, then asks the live site three things:

  discovery        the stack's discovery lists a tickerplant that holds every
                   attached table among its tables and pub/sub tables
                   (live.missing, the start gate's own question);
  authentication   the publisher opens a handle to discovery and to that
                   tickerplant with its OWN credential - the site's loader's,
                   never TorQ's demo files (reach.outbound);
  publication      the tickerplant's message count for an attached table grows
                   while the publisher runs.

It stops the publisher it started, and only that one.

    UQF_SITE_ATTACHMENT=piggybank-uat uv run pytest -m external \\
        python/uqs/tests/test_attach_site_check.py
"""

from __future__ import annotations

import os
import time

import pytest

from uqs.attach import config, live, plan, reach, service, topology
from uqs.attach.publisher import UqsPublisher, stop_publisher
from uqs.paths import default_paths, repo_root
from uqs.stack import runtime

pytestmark = pytest.mark.external

#: How long the publisher has to start, and then to publish.
START_S, PUBLISH_S = 60.0, 120.0


def _wait(what: str, ready, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while not ready():
        if time.monotonic() > deadline:
            pytest.fail(f"{what} within {seconds:g}s")
        time.sleep(2.0)


def test_a_site_publisher_discovers_authenticates_and_publishes():
    name = os.environ.get("UQF_SITE_ATTACHMENT", "").strip()
    if not name:
        pytest.skip("UQF_SITE_ATTACHMENT names no attachment: the site check is opt-in")
    att = config.load(name)
    paths, ipc = default_paths(), service.ipc_for(att)
    publisher = UqsPublisher(paths)
    names = [t.name for t in att.tables]
    gone, where = live.missing(att, ipc)
    assert not gone, f"{where} does not hold {gone}: verify the attachment first"
    st = att.stack
    disc = topology.ask(ipc, st.discovery_host, "discovery", "discovery", st.discovery_port, names)
    tp, found = topology.tickerplant(st, list((disc.probe or {}).get("services", [])), ipc, names)
    assert tp is not None and tp.up, found
    started = not publisher.running(att)
    if started:
        done = runtime.start(paths, att.publisher.procname, base_port=st.id, capture=True)
        assert done.returncode == 0, (done.stdout or "") + (done.stderr or "")
    try:
        _wait(f"{att.publisher.procname} did not listen", lambda: publisher.running(att), START_S)
        port = plan.publisher_port(att, repo_root()).port
        assert port is not None
        refused = reach.outbound(att, ipc, port, tp.endpoint)
        assert refused is None, f"{refused.code}: {refused.message}"

        def count() -> int:
            probe = topology.ask(ipc, tp.endpoint.host, tp.procname, tp.proctype,
                                 tp.endpoint.port, names).probe or {}  # fmt: skip
            return sum(int(probe.get("msgcount", {}).get(t, 0)) for t in names)

        before = count()
        _wait(f"no message for {names} reached {tp.procname}", lambda: count() > before, PUBLISH_S)
    finally:
        if started:
            stop_publisher(att, publisher)
