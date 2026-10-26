"""A stack "on another machine", as close as one machine gets (#1651).

Real KDB-X processes stand in for the stack's discovery and tickerplant, and
for the publisher. What makes it two hosts as far as this code can tell:

  * the stack's processes are asked at this machine's host name - a name that
    is not `localhost` and resolves to a non-loopback address - never at a
    loopback one, and the attachment calls that host remote;
  * the publisher is advertised under this machine's IPv4 address, another
    name than the stack host, and the stack connects back to THAT;
  * every process enforces its own access list (`-U`), with a different
    credential for each direction: the attachment's (uqs to the stack), the
    publisher's own (it to the stack) and the stack's (it to the publisher).

What one machine cannot show - a firewall, routing, a name that resolves
differently on each side, a clock or file system the stack does not share -
is listed in docs/guides/attach-existing-torq.md. These are plain q processes
running the attach operations, not TorQ: a whole stack is the attach-stack
suite's (attach_stack_support.py).
"""

from __future__ import annotations

import socket
import stat
from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path

import pytest
from attach_support import make_bundle, q_load

from uqs.attach import facts, persist, reach, service, topology
from uqs.attach.config import Attachment, AttachmentError
from uqs.attach.facts import Host
from uqs.attach.remote import Endpoint, IpcError, KolaIpc
from uqs.attach.schema_files import RemoteFile
from uqs.paths import repo_root

ROOT = repo_root()
ATTACH, PUBLISHER, STACK = ("ops", "opspw"), ("feed", "feedpw"), ("torq", "torqpw")
UQS_LOCAL = ("admin", "admin")
SCHEMA = "quote:([] time:`timestamp$(); sym:`symbol$())\n"


def _two_names() -> tuple[str, str]:
    """This machine's host name and one of its IPv4 addresses, both not loopback."""
    name = socket.gethostname()
    try:
        found = {str(i[4][0]) for i in socket.getaddrinfo(name, None, socket.AF_INET)}
    except OSError:
        pytest.skip(f"{name}, this machine's name, does not resolve here")
    outside = sorted(a for a in found if not a.startswith("127."))
    if name.lower() in facts.LOCAL_HOSTS or not outside:
        pytest.skip(f"{name} resolves only to loopback: no second name for this machine")
    return name, outside[0]


def _users(path: Path, *creds: tuple[str, str]) -> str:
    path.write_text("".join(f"{u}:{p}\n" for u, p in creds))
    return str(path)


class Hosts:
    """The three processes, and the attachment that names them."""

    def __init__(self, start_q, tmp: Path, stack_creds: bool = True, pub_cred=PUBLISHER):
        self.stack_host, self.advertised = _two_names()
        self.schema = tmp / "stack" / "database.q"
        self.schema.parent.mkdir()
        self.schema.write_text(SCHEMA)
        self.schema.chmod(0o640)
        self._exit = ExitStack()

        def proc(role: str, script: str, *creds: tuple[str, str]) -> int:
            (tmp / f"{role}.q").write_text(script)
            users = _users(tmp / f"{role}.users", *creds)
            return self._exit.enter_context(start_q(str(tmp / f"{role}.q"), "-U", users))

        user, password = STACK
        self.discovery = proc(
            "discovery", f".servers.USERPASS:`{user}:{password};\n", ATTACH, pub_cred
        )
        self.tickerplant = proc(
            "stp1",
            f'.proc.params:enlist[`schemafile]!enlist enlist "{self.schema}";\n',
            ATTACH,
            PUBLISHER,
        )
        self.port = proc(
            "publisher",
            ".servers.USERPASS:`{}:{};\n".format(*PUBLISHER),
            UQS_LOCAL,
            *([STACK] if stack_creds else []),
        )
        self.ipc = KolaIpc(*ATTACH)

    def att(self, bundle: Path) -> Attachment:
        att = service.create(
            "pb-uat", "pb_general_ledger", bundle, 6000, ROOT, host=self.stack_host,
            discovery=f"{self.stack_host}:{self.discovery}", remote_schema_file=True,
        )  # fmt: skip
        return replace(att, publisher=replace(att.publisher, advertised_host=self.advertised))

    @property
    def stp(self) -> Endpoint:
        return Endpoint("stp1", self.stack_host, self.tickerplant)

    def close(self) -> None:
        self._exit.close()


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    return make_bundle(tmp_path / "piggybank")


@pytest.fixture
def hosts(start_q, tmp_path, q_binary) -> Iterator[Hosts]:
    h = Hosts(start_q, tmp_path)
    yield h
    h.close()


def test_the_stack_is_another_host_to_the_attachment(hosts, bundle, monkeypatch):
    # what makes this machine's own name "local" is only that it owns the address
    monkeypatch.setattr(facts, "_owned", lambda address: address.startswith("127."))
    att, host = hosts.att(bundle), Host(None, ("localhost",))
    assert not host.is_local(att.stack.host) and reach.applies(att, host)
    assert att.publisher.advertised_host != att.stack.host
    assert att.stack.discovery_host == hosts.stack_host != "localhost"


def test_both_directions_reach_with_the_right_credentials(hosts, bundle):
    att = hosts.att(bundle)
    assert reach.outbound(att, hosts.ipc, hosts.port, hosts.stp) is None
    assert reach.check(att, hosts.ipc, hosts.port) is None


def test_the_stack_refusing_the_publishers_credential(start_q, tmp_path, bundle):
    h = Hosts(start_q, tmp_path, pub_cred=("someone", "else"))
    try:
        found = reach.outbound(h.att(bundle), h.ipc, h.port, h.stp)
    finally:
        h.close()
    assert found is not None and found.code == "STACK_REFUSES_PUBLISHER_CREDENTIALS"
    assert found.facts == {"address": f"{h.stack_host}:{h.discovery}", "process": "discovery"}


def test_the_publisher_refusing_the_stacks_credential(start_q, tmp_path, bundle):
    h = Hosts(start_q, tmp_path, stack_creds=False)
    try:
        att = h.att(bundle)
        found = reach.check(att, h.ipc, h.port)
        out = reach.outbound(att, h.ipc, h.port, h.stp)
    finally:
        h.close()
    assert found is not None and found.code == "PUBLISHER_REFUSES_STACK_CREDENTIALS"
    assert f"{h.advertised}:{h.port}" in found.message
    assert out is None, "the other direction is not affected"


def test_the_stack_refusing_the_attachments_credential(hosts, bundle):
    att = hosts.att(bundle)
    with pytest.raises(IpcError) as refused:
        KolaIpc("ops", "wrong").call(hosts.stp, "schema_file", {"action": "read"})
    assert refused.value.refused and "was refused" in refused.value.reason
    proc = topology.ask(
        KolaIpc("ops", "wrong"), hosts.stack_host, "stp1", "x", hosts.tickerplant, []
    )
    finding = topology.refused(att, proc)
    assert proc.refused and finding.code == "STACK_REFUSES_ATTACH_CREDENTIALS"


# ----------------------------------------------- the schema file, by its tickerplant


def test_the_tickerplant_appends_and_removes_the_block_in_its_own_file(
    hosts, bundle, q_binary, tmp_path
):
    att, before = hosts.att(bundle), hosts.schema.read_bytes()
    inode = hosts.schema.stat().st_ino
    file = RemoteFile(hosts.ipc, hosts.stp)
    record = persist.write(att, "tok1", att.tables, file)
    assert record.process == "stp1" and record.path == str(hosts.schema)
    assert persist.problems(record, att, "tok1", att.tables, file) == []
    loaded = q_load(q_binary, hosts.schema, tmp_path)
    assert loaded["loaded"] and "piggybank_general_ledger" in loaded["tables"]
    assert loaded["owned"]["piggybank_general_ledger"]["token"] == "tok1"

    persist.remove(record, att, "tok1", att.tables, file)
    assert hosts.schema.read_bytes() == before
    assert stat.S_IMODE(hosts.schema.stat().st_mode) == 0o640
    assert hosts.schema.stat().st_ino == inode, "the file was changed, not replaced"
    assert sorted(p.name for p in hosts.schema.parent.iterdir()) == ["database.q"]


def _raw(hosts: Hosts, request: dict) -> dict:
    return hosts.ipc.call(hosts.stp, "schema_file", request)


def test_the_tickerplant_refuses_what_is_not_exactly_one_block(hosts):
    read = _raw(hosts, {"action": "read"})
    assert read["status"] == "read" and read["writable"] is True
    assert bytes.fromhex(read["bytes"]).decode() == SCHEMA
    block = "/ BEGIN uqs-attach a t\nx:1;\n/ END uqs-attach a\n"
    cases = {
        "the file changed since it was read": {"expect": "0" * 32, "bytes": block.encode().hex()},
        "not exactly one whole uqs-attach block": {"bytes": b"x:1;\n".hex()},
        "the block name or token is not one uqs writes": {
            "token": "t t", "bytes": block.encode().hex()
        },
    }  # fmt: skip
    for said, extra in cases.items():
        request = {"action": "append", "expect": read["md5"], "block": "a", "token": "t", **extra}
        answer = _raw(hosts, request)
        assert (answer["status"], answer["detail"]) == ("refused", said)
    cut = {"action": "cut", "expect": read["md5"], "block": "a", "token": "t",
           "start": 0, "end": 10, "trim": False}  # fmt: skip
    assert _raw(hosts, cut)["detail"] == "the span is not exactly this block"
    assert hosts.schema.read_text() == SCHEMA


def test_a_schema_file_that_is_gone_is_absent(hosts, bundle):
    hosts.schema.unlink()
    assert _raw(hosts, {"action": "read"})["status"] == "absent"
    att = hosts.att(bundle)
    with pytest.raises(AttachmentError, match=f"{hosts.schema} on stp1 does not exist"):
        persist.write(att, "tok1", att.tables, RemoteFile(hosts.ipc, hosts.stp))


def test_the_tickerplant_reads_the_stacks_disk_for_publication(hosts):
    logs = hosts.schema.parent / "logs" / "stp1_2026.10.15"
    logs.mkdir(parents=True)
    (logs / "stp1_quote20261015T00000").write_bytes(b"x" * 400)
    (hosts.schema.parent / "hdb" / "2026.10.15" / "quote").mkdir(parents=True)
    root = hosts.schema.parent
    request = {"logdir": str(logs.parent), "plant": "stp1", "table": "quote",
               "dirs": [str(root / "wdb"), str(root / "hdb")]}  # fmt: skip
    answer = hosts.ipc.call(hosts.stp, "disk_marks", request)
    assert answer == {
        "logs": [{"name": "stp1_quote20261015T00000", "size": 400}],
        "partitions": [False, True],
    }
