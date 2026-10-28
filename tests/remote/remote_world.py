"""The machines of the remote-stack lane, as Docker containers (scripts/test.py remote-stack).

Three Linux x86_64 machines from one image (tests/remote/Dockerfile), each with
its own hostname, filesystem and process table:

  uat-stack   the TorQ stack, on PeachQ: discovery, tickerplant, rdb, hdb and wdb
              (which runs on PeachQ through scripts/peachq_init.q's .Q.chk,
              #1650). Its ports are published to this
              host's 127.0.0.1, so the tests can look at it directly.
  uat-client  the machine `uqs` runs on: the attachment, the publisher, the
              extractor. On the stack's network, so each resolves the other.
  uat-laptop  a machine the stack cannot resolve: on a network of its own, it
              reaches the stack only through this host's published ports
              (`--add-host uat-stack:host-gateway`), as a laptop behind NAT does.

Why the machine `uqs` runs on is a container and not this host: from this host
the stack is reachable only through ports forwarded to an address this host
owns, and `uqs` rightly calls such a stack local (attach/facts.py, Host.is_local),
which turns off every check this lane exists to exercise. Colima gives the VM
no address this host can route to unless it is started with --network-address.

The tests drive `uqs` with `docker exec` and check its effects on the stack
themselves, through the published ports, never through the code under test.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

ENABLED = os.environ.get("UQF_REMOTE_STACK_TEST") == "1"
IMAGE = os.environ.get("UQF_REMOTE_IMAGE", "")
RUN = os.environ.get("UQF_REMOTE_RUN") or str(os.getpid())
LABEL = f"uqf-remote-run={RUN}"
#: Ports published from the stack: its processes (offsets 0-6) and a receiver's.
SPAN = 41
RECEIVER_OFFSET = 40
OFFSETS = {"stp1": 0, "discovery1": 1, "rdb1": 2, "hdb1": 3, "wdb1": 5}
#: The stack's own processes. wdb1 is the writer an attached backfill is
#: coordinated with, and what pins the HDB its receiver writes (test_4).
STACK_PROCS = ("discovery1", "stp1", "rdb1", "hdb1", "wdb1")
BUNDLE = "tests/remote/bundle"
CLIENT_RUNTIME = "peachq-etl"
STACK_RUNTIME = "peachq"
REPO_IN_IMAGE = Path("/uqf")


def docker(*args: str, timeout: float = 120, check: bool = True) -> subprocess.CompletedProcess:
    done = subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=timeout, check=False
    )
    if check and done.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args)}: {done.stdout}{done.stderr}")
    return done


@dataclass
class Machine:
    """One container, run as a machine: commands in its checkout, as root."""

    name: str
    hostname: str

    def sh(
        self, script: str, *, env: dict[str, str] | None = None, timeout: float = 300
    ) -> subprocess.CompletedProcess:
        flags = [f"--env={k}={v}" for k, v in (env or {}).items()]
        return docker(
            "exec", "-w", str(REPO_IN_IMAGE), *flags, self.name, "bash", "-c", script,
            timeout=timeout, check=False,
        )  # fmt: skip

    def uqs(
        self,
        *args: str,
        runtime: str = CLIENT_RUNTIME,
        env: dict[str, str] | None = None,
        timeout: float = 300,
    ) -> subprocess.CompletedProcess:
        flags = [f"--env={k}={v}" for k, v in {"COLUMNS": "200", **(env or {})}.items()]
        return docker(
            "exec", "-w", str(REPO_IN_IMAGE), *flags, self.name, "uqs", "--runtime", runtime,
            *args, timeout=timeout, check=False,
        )  # fmt: skip

    def uqs_json(self, *args: str, **kw: Any) -> tuple[int, dict[str, Any]]:
        """`uqs ... --json`: the exit code and the JSON document it prints.

        From the first line that opens one: a refusal logs its ERROR line to
        stdout ahead of the document."""
        done = self.uqs(*args, "--json", **kw)
        lines = done.stdout.splitlines(keepends=True)
        start = next((i for i, line in enumerate(lines) if line.startswith("{")), None)
        try:
            assert start is not None
            return done.returncode, json.loads("".join(lines[start:]))
        except AssertionError, ValueError:
            pytest.fail(f"uqs {' '.join(args)} printed no JSON:\n{done.stdout}\n{done.stderr}")

    def listening(self, port: int) -> bool:
        return self.sh(f"ss -ltnH 'sport = :{port}' | grep -q LISTEN").returncode == 0


@dataclass
class World:
    base: int
    stack: Machine
    client: Machine
    laptop: Machine
    networks: list[str] = field(default_factory=list)
    #: the receiver contract test_4 approved on the stack's machine
    contract: str = ""

    @property
    def receiver_port(self) -> int:
        return self.base + RECEIVER_OFFSET

    def q(self, proc: str, expr: str, *args: Any, timeout_ms: int = 10000) -> Any:
        """Evaluate `expr` on one of the stack's processes, from this host, through
        its published port - the tests' own view of the stack, not uqs's."""
        import kola

        conn = kola.Q(
            "127.0.0.1", self.base + OFFSETS[proc], user="admin", passwd="admin",
            timeout=timeout_ms,
        )  # fmt: skip
        try:
            return conn.sync(expr, *args)
        finally:
            conn.disconnect()

    def answers(self, proc: str) -> bool:
        try:
            self.q(proc, "1b", timeout_ms=2000)
            return True
        except Exception:  # noqa: BLE001 - any failure is "not answering yet"
            return False

    def wait_answering(self, *procs: str, seconds: float = 120) -> None:
        deadline = time.monotonic() + seconds
        for proc in procs:
            while not self.answers(proc):
                if time.monotonic() > deadline:
                    log = self.stack.sh(f"tail -5 /data/uqs-peachq/logs/err_{proc}.log")
                    pytest.fail(f"{proc} did not answer in {seconds:g}s: {log.stdout}")
                time.sleep(1)

    def stack_uqs(self, *args: str, **kw: Any) -> subprocess.CompletedProcess:
        return self.stack.uqs(*args, "--port", str(self.base), runtime=STACK_RUNTIME, **kw)


def _free_span(start: int = 17400, stop: int = 19400) -> int:
    for base in range(start, stop, 100):
        if all(_free(base + i) for i in range(SPAN)):
            return base
    pytest.fail(f"no free {SPAN}-port span on this host between {start} and {stop}")


def _free(port: int) -> bool:
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _run(name: str, hostname: str, network: str, *extra: str) -> Machine:
    docker(
        "run", "-d", "--init", "--platform", "linux/amd64", "--label", LABEL,
        "--name", name, "--hostname", hostname, "--network", network, *extra, IMAGE,
    )  # fmt: skip
    return Machine(name, hostname)


def build() -> World:
    """Start the three machines and the stack; prepare the fixture bundle."""
    base = _free_span()
    net, nat = f"uqf-remote-{RUN}", f"uqf-remote-nat-{RUN}"
    for network in (net, nat):
        docker("network", "create", "--label", LABEL, network)
    publish = f"127.0.0.1:{base}-{base + SPAN - 1}:{base}-{base + SPAN - 1}"
    world = World(
        base,
        _run(f"uqf-remote-stack-{RUN}", "uat-stack", net, "-p", publish),
        _run(f"uqf-remote-client-{RUN}", "uat-client", net),
        _run(f"uqf-remote-laptop-{RUN}", "uat-laptop", nat, "--add-host", "uat-stack:host-gateway"),
        [net, nat],
    )
    started = world.stack_uqs("start", *STACK_PROCS)
    assert started.returncode == 0, started.stdout + started.stderr
    world.wait_answering("discovery1", "stp1", "rdb1", "hdb1")
    for machine in (world.client, world.laptop):
        prepared = machine.uqs("runtime", "prepare", "--bundle", BUNDLE)
        assert prepared.returncode == 0, prepared.stdout + prepared.stderr
    # The stack's machine never has the client's job bundle (#1717): its receiver
    # writes the backfill from an approved receiver contract, not from the job's code.
    gone = world.stack.sh(f"rm -rf {BUNDLE}")
    assert gone.returncode == 0, gone.stdout + gone.stderr
    return world


def copy_between(src: Machine, dst: Machine, directory: str, name: str) -> None:
    """Carry `directory/name` from one machine to another, as an operator would by
    hand: a tar stream through this host, the two machines sharing no filesystem."""
    out = subprocess.Popen(
        ["docker", "exec", src.name, "tar", "-C", directory, "--exclude=backfills",
         "-cf", "-", name],
        stdout=subprocess.PIPE,
    )  # fmt: skip
    done = subprocess.run(
        ["docker", "exec", "-i", dst.name, "sh", "-c",
         f"mkdir -p {directory} && tar -C {directory} -xf -"],
        stdin=out.stdout, capture_output=True, text=True, timeout=120, check=False,
    )  # fmt: skip
    assert out.wait(timeout=120) == 0 and done.returncode == 0, done.stdout + done.stderr


def teardown(world: World | None) -> None:
    """Remove what this run created, by its label; never anything else."""
    ids = docker("ps", "-aq", "--filter", f"label={LABEL}", check=False).stdout.split()
    if ids:
        docker("rm", "-f", *ids, check=False)
    nets = docker("network", "ls", "-q", "--filter", f"label={LABEL}", check=False).stdout.split()
    if nets:
        docker("network", "rm", *nets, check=False)


# ------------------------------------------------------------- the scenarios' shared steps

FEED_JOB = "remote_trades_feed"
PUBLISHER = "remote_trades_feed1"
TABLE = "remote_trades"


def attach_args(world: World, name: str, *extra: str) -> list[str]:
    """`uqs job attachment add NAME` for the fixture feed, against the stack on uat-stack.

    wdb1 is a consumer so that the attachment pins the stack's HDB: the same
    attachment names the destination of test_4's remote writer (#1650)."""
    return [
        "job", "attachment", "add", name, "--job", FEED_JOB, "--bundle", BUNDLE,
        "--stack-id", str(world.base), "--host", "uat-stack", "--consumer", "rdb1",
        "--consumer", "wdb1", "--runtime-only", "--yes", *extra,
    ]  # fmt: skip


def blockers(doc: dict[str, Any]) -> dict[str, str]:
    """code -> message of every blocker in an attach document's plan."""
    found = (doc.get("plan") or {}).get("findings", [])
    return {f["code"]: f["message"] for f in found if f["level"] == "blocker"}


def stages(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {s["stage"]: s for s in doc.get("stages", [])}


def live_rows(world: World) -> int:
    """The `live rows rdb1 holds: only the fixture feed writes side `live."""
    return int(world.q("rdb1", f"exec count i from {TABLE} where side=`live"))


def wait_for(predicate, seconds: float, what: str) -> None:
    deadline = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() > deadline:
            pytest.fail(f"{what}: not within {seconds:g}s")
        time.sleep(1)


def stp_tables(world: World) -> list[str]:
    return [str(t) for t in world.q("stp1", "tables[]")]


def owned(world: World) -> list[str]:
    """The attachments that hold an ownership record on the tickerplant."""
    return [str(k) for k in world.q("stp1", "key @[value;`.uqfattach.owned;()!()]")]


def listed(world: World, host: str) -> list[tuple[str, str, bool]]:
    """(procname, hpup, whether discovery holds a handle) for every process
    discovery lists on `host`."""
    rows = world.q(
        "discovery1", "select procname, hpup, live:not null w from .servers.SERVERS"
    ).to_dicts()
    return [
        (str(r["procname"]), str(r["hpup"]), bool(r["live"]))
        for r in rows
        if str(r["hpup"]).startswith(f":{host}:")
    ]
