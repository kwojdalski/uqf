"""An isolated TorQ stack for the attach tests: real processes, nobody else's.

The `torq` runtime is the starter pack AS IT SHIPS - no uqf layers - which is
what "an existing TorQ stack" is. It runs on a base port nothing is using,
with its own data directory and its own COPY of the starter pack (so a test
may edit the stack's schema file; the vendored tree is never touched).

Opt-in, like the other tests that start a whole stack: UQS_ATTACH_STACK_TEST=1
(`python3 scripts/test.py attach-stack`). It needs a KDB-X licence and about
a minute per stack; hosted CI has neither, and the pre-commit hook must not
start processes on every commit.

Every process is started by name, with `--port`, so `uqs stop` and the final
sweep touch this stack's `-stackid` and no other.
"""

from __future__ import annotations

import os
import random
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from uqs.attach.remote import Endpoint, IpcError, KolaIpc

STACK_TEST = os.environ.get("UQS_ATTACH_STACK_TEST") == "1"
REPO = Path(__file__).resolve().parents[3]
#: discovery, stp, rdb, hdb, wdb, sort: the starter pack's own, minus the monitoring extras.
CORE = ("discovery1", "stp1", "rdb1", "hdb1", "wdb1", "sort1")
#: Ports a stack of this kind spans (starter pack offsets 0-28, plus a publisher's).
SPAN = 80


def requires_stack() -> list[pytest.MarkDecorator]:
    """A module's `pytestmark`: opt-in, and one stack at a time under xdist.

    pick_base_port shuffles per process, but between its check that a span is
    free and the stack binding it, another xdist worker's stack can take the
    same span. The group puts every stack test on one worker
    (`-n auto --dist loadgroup`), so they start one after another.
    """
    return [
        pytest.mark.skipif(
            not STACK_TEST, reason="UQS_ATTACH_STACK_TEST=1 starts an isolated TorQ stack"
        ),
        pytest.mark.xdist_group("torq-stack"),
    ]


def _free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # a TIME_WAIT is not a listener
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def pick_base_port(start: int = 7440, stop: int = 9040) -> int:
    """A base port whose whole SPAN is free and which no running stack claims as
    a -stackid. The candidates are tried in an order that differs per process,
    so two test runs starting together do not both go for the first span."""
    claimed = subprocess.run(["ps", "-axo", "command"], capture_output=True, text=True).stdout
    candidates = list(range(start, stop, SPAN))
    random.Random(os.getpid()).shuffle(candidates)
    for base in candidates:
        if f"-stackid {base} " in claimed:
            continue
        if all(_free(base + i) for i in range(SPAN)):
            return base
    pytest.fail(f"no free {SPAN}-port span between {start} and {stop}")


class IsolatedStack:
    def __init__(self, root: Path, base_port: int | None = None, settings: str = "") -> None:
        self.root = root
        self.port = base_port or pick_base_port()
        self.data = root / "data"
        self.pack = root / "pack"
        self.data.mkdir(parents=True, exist_ok=True)
        self._copy_pack(settings)
        self.env: dict[str, str] = {
            **os.environ,
            "UQS_DATA_ROOT": str(self.data),
            "TORQAPPHOME": str(self.pack),
            "UQS_RUNTIME": "torq",
            "UQS_ATTACH_DIR": str(root / "attachments"),
        }
        self.env.pop("UQS_TARGET", None)
        self.started: set[str] = set()

    def _copy_pack(self, settings: str) -> None:
        """The starter pack, symlinked, except the files a test may edit."""
        src = REPO / "lib" / "torq-finance-starter-pack"
        self.pack.mkdir(parents=True, exist_ok=True)
        for entry in src.iterdir():
            if entry.name in ("database.q", "appconfig"):
                continue
            (self.pack / entry.name).symlink_to(entry)
        shutil.copy2(src / "database.q", self.pack / "database.q")
        shutil.copytree(src / "appconfig", self.pack / "appconfig", symlinks=True)
        if settings:
            path = self.pack / "appconfig" / "settings" / "segmentedtickerplant.q"
            path.write_text(path.read_text() + "\n" + settings + "\n")

    @property
    def schema_file(self) -> Path:
        return self.pack / "database.q"

    @property
    def tplogs(self) -> Path:
        return self.data / "uqs-torq" / "tplogs"

    @property
    def wdb_dir(self) -> Path:
        return self.data / "uqs-torq" / "wdbhdb"

    @property
    def hdb_dir(self) -> Path:
        return self.data / "uqs-torq" / "hdb"

    def port_of(self, offset: int) -> int:
        return self.port + offset

    def uqs(self, *args: str, timeout: float = 180) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", "from uqs.cli import main; main()", *args],
            cwd=REPO,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    def ipc(self, user: str = "admin", password: str = "admin") -> KolaIpc:
        return KolaIpc(user, password, timeout_ms=10000)

    def endpoint(self, name: str) -> Endpoint:
        offsets = {"stp1": 0, "discovery1": 1, "rdb1": 2, "hdb1": 3, "wdb1": 5, "sort1": 6}
        return Endpoint(name, "127.0.0.1", self.port + offsets[name])

    def start(self, *names: str) -> None:
        names = names or CORE
        run = self.uqs("start", *names, "--port", str(self.port))
        assert run.returncode == 0, run.stdout + run.stderr
        self.started |= set(names)
        self.wait_ready(names)

    def stop(self, *names: str) -> None:
        names = names or tuple(self.started)
        if names:
            self.uqs("stop", *names, "--port", str(self.port))
        self.started -= set(names)

    def wait_ready(self, names: tuple[str, ...], seconds: float = 90) -> None:
        """Every named process answers, and rdb/wdb hold their subscriptions."""
        ipc, deadline, last = self.ipc(), time.monotonic() + seconds, ""
        for name in names:
            if name == "hdb1" or name == "sort1":
                continue
            while True:
                try:
                    answer = ipc.call(self.endpoint(name), "probe", {"tables": []})
                    if name in ("rdb1", "wdb1") and "trade" not in answer.get("subscribed", []):
                        raise IpcError(self.endpoint(name), "probe", "not subscribed yet")
                    break
                except IpcError as exc:
                    last = str(exc)
                    if time.monotonic() > deadline:
                        pytest.fail(f"{name} did not come up in {seconds}s: {last}")
                    time.sleep(0.5)

    def pids(self) -> list[int]:
        out = subprocess.run(["ps", "-axo", "pid,command"], capture_output=True, text=True).stdout
        needle = f"-stackid {self.port} "
        return [int(line.split()[0]) for line in out.splitlines() if needle in line + " "]

    def teardown(self) -> None:
        """Stop what this stack started, then kill any leftover carrying ITS
        -stackid (never a pattern wider than that)."""
        self.uqs("stop", "all", "--port", str(self.port))
        deadline = time.monotonic() + 15
        while self.pids() and time.monotonic() < deadline:
            time.sleep(0.5)
        for pid in self.pids():
            try:
                os.kill(pid, 15)
            except OSError:
                pass
        time.sleep(1)

    # ------------------------------------------------------------ raw access

    def q(self, name: str, expr: str, *args):
        """Evaluate `expr` on a process directly (kola, 127.0.0.1) - for the test to
        look at, or drive, the stack independently of the code under test."""
        import kola

        conn = kola.Q(
            "127.0.0.1", self.endpoint(name).port, user="admin", passwd="admin", timeout=15000
        )
        try:
            return conn.sync(expr, *args)
        finally:
            conn.disconnect()

    def publish(self, table: str, columns: list[list]) -> None:
        """What a publisher does: `.u.upd[table; columns]` on the tickerplant."""
        self.q("stp1", ".u.upd", table, columns)

    def flush(self, seconds: float = 3.0) -> None:
        """Let the tickerplant's 1s timer publish what it buffered."""
        time.sleep(seconds)

    def end_of_day(self, wait: float = 15.0) -> None:
        """Drive the tickerplant's own end of day, as its timer would at the roll."""
        self.q(
            "stp1",
            ".stplg.endofday[.eodtime.d; (.stplg.endofdaydata[]),(enlist `p)!enlist .z.p]",
        )
        time.sleep(wait)

    def rows(self, name: str, table: str) -> int:
        return int(self.q(name, f"count {table}"))
