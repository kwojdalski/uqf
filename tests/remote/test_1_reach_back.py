"""Whether the stack can reach a publisher back, both ways (#1651).

TorQ registers the publisher with discovery under the name of the machine it
runs on, and discovery connects back to that name. These run on uat-laptop, a
machine the stack cannot resolve, before any publisher is attached: the one that
passes, from uat-client, is test_2_publisher's.
"""

from __future__ import annotations

import socket
import threading

import pytest
from remote_world import (
    PUBLISHER,
    TABLE,
    World,
    attach_args,
    blockers,
    listed,
    owned,
    stages,
    stp_tables,
)


def test_the_stacks_machine_reaches_this_host_through_host_docker_internal(world: World):
    """Colima's host.docker.internal is this host's loopback: the route the
    laptop takes to the stack's published ports. Checked, not assumed."""
    banner = b"uqf-remote-lane\n"
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        server.settimeout(30)

        def answer() -> None:
            conn, _ = server.accept()
            with conn:
                conn.sendall(banner)

        thread = threading.Thread(target=answer, daemon=True)
        thread.start()
        port = server.getsockname()[1]
        got = world.stack.sh(f"nc -w 5 host.docker.internal {port}", timeout=30)
        thread.join(10)
    assert got.stdout.encode() == banner, got.stdout + got.stderr
    resolved = world.laptop.sh("getent hosts uat-stack").stdout.split()
    assert (
        resolved
        and resolved[0] == world.stack.sh("getent hosts host.docker.internal").stdout.split()[0]
    ), "the laptop reaches uat-stack through this host"


def test_a_name_the_stack_cannot_resolve_is_refused_and_nothing_changes(world: World):
    tables, holders = stp_tables(world), owned(world)
    code, doc = world.laptop.uqs_json(*attach_args(world, "laptop-uat"))
    assert code == 1, doc
    assert set(blockers(doc)) == {"ADVERTISED_UNRESOLVABLE"}, blockers(doc)
    assert "uat-laptop" in blockers(doc)["ADVERTISED_UNRESOLVABLE"]
    assert stp_tables(world) == tables and TABLE not in tables, "nothing was registered"
    assert owned(world) == holders
    assert listed(world, "uat-laptop") == [], "no publisher started"


@pytest.fixture
def unreachable_laptop(world: World):
    """The stack's machine resolves uat-laptop - to an address nothing answers
    on (TEST-NET-1), as a name in DNS behind a firewall does."""
    world.stack.sh("echo '192.0.2.10 uat-laptop' >> /etc/hosts")
    try:
        yield
    finally:
        # /etc/hosts is bound into the container: rewrite it, never rename onto it
        world.stack.sh("grep -v uat-laptop /etc/hosts > /tmp/hosts && cat /tmp/hosts > /etc/hosts")


@pytest.mark.usefixtures("unreachable_laptop")
def test_a_publisher_the_stack_cannot_open_is_stopped_and_not_attached(world: World):
    code, doc = world.laptop.uqs_json(*attach_args(world, "laptop-uat"), timeout=400)
    assert code == 1, doc
    reach = stages(doc).get("reach", {})
    assert reach.get("status") == "failed", stages(doc)
    assert "PUBLISHER_UNREACHABLE_FROM_STACK" in reach["detail"], reach
    assert "uat-laptop:" in reach["detail"], reach
    port = doc["plan"]["publisher_port"]
    assert not world.laptop.listening(port), "the publisher this run started is stopped"
    manifest = world.laptop.sh("cat /data/attachments/laptop-uat/manifest.json").stdout
    assert '"state": "applied"' in manifest, "back to applied: the gate is closed"
    # The stack's own record: the publisher registered, and discovery holds no
    # handle to it - listed and unusable, which is what the reach stage refuses.
    rows = [r for r in listed(world, "uat-laptop") if r[0] == PUBLISHER]
    assert rows and not any(live for _, _, live in rows), rows
    assert rows[0][1] == f":uat-laptop:{port}"
