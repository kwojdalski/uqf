"""`run_stream.q`'s documented one-process command, run end to end (#927).

`docs/services/fx-positions.md` gives one command that runs the plant, the
orders feed, the executions normalizer and the positions service in a single
q process. When fx_positions moved onto `executions` (#885) that command
stopped producing a book, and nothing noticed: no test ran it. This one does,
with the command's own flags, and the runner now derives the normalizer from
the job graph rather than the command line naming it.

KDB-X only, skipping without it, for the reason test_etl_standalone_load.py
gives.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from uqs.interpreter import KDBX, identify, q_interpreter

UQF_ROOT = Path(__file__).resolve().parents[3]

#: Twenty forced ticks take well under a second; this only stops a hang.
TIMEOUT_SECONDS = 120


def _kdbx() -> tuple[str, dict[str, str]]:
    env = os.environ.copy()
    q = q_interpreter(env)
    if q is None:
        pytest.skip("no q interpreter - set $QCMD, or put q on PATH")
    env.setdefault("QHOME", str(Path.home() / ".kx"))
    return str(q), env


#: The command docs/services/fx-positions.md gives, minus the script name.
DOCUMENTED = ["-job", "fx_positions", "-feed", "fx_orders_feed"]

#: Loads the runner under the documented flags, which starts everything, then
#: fires every job's timer twenty times rather than waiting on q's clock.
_DRIVER = """\\l scripts/processes/run_stream.q
system "t 0";
due:{[] .qproc.standalone.timers:{x[3]:0Np; x} each .qproc.standalone.timers};
force:{[] due[]; .qproc.standalone.tick[]};
do[20; force[]];
-1 "BOOK:",string count .qpipe.job.fx_positions.positions;
-1 "UPSTREAM:",", " sv string .qproc.standalone.with_upstream `fx_positions`arbitrage;
exit 0
"""


def _run(tmp_path: Path, *args: str) -> str:
    q, env = _kdbx()
    driver = tmp_path / "driver.q"
    driver.write_text(_DRIVER)
    result = subprocess.run(
        [q, str(driver), "-q", *args, "-logdir", str(tmp_path / "tplog")],
        cwd=UQF_ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def _value(out: str, key: str) -> str:
    lines = [line for line in out.splitlines() if line.startswith(f"{key}:")]
    assert lines, out
    return lines[-1].split(":", 1)[1]


def test_the_documented_one_process_command_builds_a_book(tmp_path: Path) -> None:
    out = _run(tmp_path, *DOCUMENTED)
    assert int(_value(out, "BOOK")) > 0, out
    assert "executions" in out.split("run_stream: ")[-1].splitlines()[0]


def test_upstream_is_the_job_graph_and_never_a_feed(tmp_path: Path) -> None:
    upstream = _value(_run(tmp_path, *DOCUMENTED), "UPSTREAM").split(", ")
    # producers first, then the jobs asked for; no feed is ever added
    assert upstream[-2:] == ["fx_positions", "arbitrage"]
    assert {"executions", "market_data", "superbook"} <= set(upstream[:-2])
    assert not any(name.endswith("_feed") for name in upstream)


#: After the documented command has built a book: end the day in-process,
#: then do what a restart on the new day does - replay that day's log into a
#: flat book - and compare it with the book the running process holds (#943).
_DAY_CHANGE = """\\l scripts/processes/run_stream.q
system "t 0";
due:{[] .qproc.standalone.timers:{x[3]:0Np; x} each .qproc.standalone.timers};
do[20; due[]; .qproc.standalone.tick[]];
.qproc.standalone.end_day .z.D+1;
running:.qpipe.job.fx_positions.positions;
-1 "OPENING_LOG_MESSAGES:",string -11!(-2;.qetl.tick.log_path);
`.qpipe.job.fx_positions.positions set `sym`book`product xkey .qpipe.job.fx_positions.desk_book;
`.qetl.job.stream.replaying set 1b;
f:.qetl.job.stream.handler `fx_positions;
h:{[f;t;x] if[t in `fx_position_open`executions; f[t;x]]}[f];
.qetl.tick.replay[.qetl.tick.log_path;h];
.qetl.job.stream.replayed `fx_positions;
`.qetl.job.stream.replaying set 0b;
-1 "AGREE:",string running~.qpipe.job.fx_positions.positions;
-1 "BOOK:",string count running;
exit 0
"""


def test_a_restart_after_the_day_ends_rebuilds_the_carried_book(tmp_path: Path) -> None:
    q, env = _kdbx()
    driver = tmp_path / "day.q"
    driver.write_text(_DAY_CHANGE)
    result = subprocess.run(
        [q, str(driver), "-q", *DOCUMENTED, "-logdir", str(tmp_path / "tplog")],
        cwd=UQF_ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
    )
    out = result.stdout + result.stderr
    assert result.returncode == 0, out
    assert int(_value(out, "BOOK")) > 0, out
    # the new day's log opens with the carried book
    assert int(_value(out, "OPENING_LOG_MESSAGES")) >= 1, out
    assert _value(out, "AGREE") == "1", out


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("localhost", 0))
        return s.getsockname()[1]


_PUBLISH = """h:hopen {port};
f:{{[sz] ([] source_time:enlist .z.p; sym:enlist `EURUSD; venue:enlist `v; side:enlist 1;
    size:enlist sz; price:enlist 1.1; fee:enlist 0f; fee_ccy:enlist `USD;
    fill_id:enlist `$string sz; book:enlist `london; product:enlist `spot)}};
h(`.qetl.tick.publish;`executions;f 1e6);
h(`.qetl.tick.publish;`executions;f 5e5);
exit 0
"""

_READ = """h:@[hopen;{port};{{0N}}];
-1 "BOOK:",$[null h; "unreachable"; .Q.s1 h"exec base_qty from .qpipe.job.fx_positions.positions"];
exit 0
"""


def test_a_job_with_its_plant_in_another_process_replays_the_plants_log(tmp_path: Path) -> None:
    """#1071: with the plant in another process, a replay-dependent job was
    warned and started flat. It now asks the plant to replay its log and
    subscribe it in one call, so the book a restart builds is the log's."""
    import time

    q, env = _kdbx()
    if identify(Path(q), env) != KDBX:
        # three processes talking over ports; CI's PeachQ could not open a
        # handle to the plant ('io), and this module is KDB-X's to run
        pytest.skip("the multi-process run needs KDB-X")
    plant_port, job_port = _free_port(), _free_port()
    publish = tmp_path / "publish.q"
    publish.write_text(_PUBLISH.format(port=plant_port))
    read = tmp_path / "read.q"
    read.write_text(_READ.format(port=job_port))
    script = "scripts/processes/run_stream.q"

    def start(*args: str) -> subprocess.Popen[bytes]:
        return subprocess.Popen(
            [q, script, "-q", *args],
            cwd=UQF_ROOT,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def run(path: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [q, str(path), "-q"],
            cwd=UQF_ROOT,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    procs = [start("-plant", str(plant_port), "-logdir", str(tmp_path / "tplog"))]
    try:
        for _ in range(50):
            done = run(publish)
            if done.returncode == 0:
                break
            time.sleep(0.2)
        assert done.returncode == 0, done.stdout + done.stderr
        procs.append(start("-job", "fx_positions", "-tp", str(plant_port), "-port", str(job_port)))
        out = ""
        for _ in range(50):
            out = run(read).stdout
            if "unreachable" not in out:
                break
            time.sleep(0.2)
        assert _value(out, "BOOK") == ",1500000f", out
    finally:
        for proc in reversed(procs):
            proc.kill()
            proc.wait(timeout=10)
