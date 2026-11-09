"""q's stale-lock rule and Python's process_gone, over one table (#2652).

q decides whether a lock's holder is gone in
`.qetl.job.bounded.state.lock_is_stale`; Python's readers decide whether a
status file's writer is gone in `uqs.liveness.process_gone`. They had
diverged: #2497 taught q that a holder naming a live pid, recorded before
that live process started, is a dead predecessor whose number was recycled -
the usual case after a container restart, where the worker is pid 1 again -
and Python went on reading that predecessor as alive.

The two rules look from different places. q's live process is the one
asking, so it compares the record with its own start (`loaded_at`); a Python
reader is never the recorded process, so it compares the record with the
start of whatever process now has the number (`ps -o lstart`). The table
below makes them meet: q writes each lock owner and judges it, and while
that q is still running - so "this pid, alive" is true for both - Python
reads the same owner files and judges them too.

Not compared: a `started` between the q process's real start and the moment
it loaded backfill_state.q, where q's `loaded_at` and `ps`'s second-truncated
start disagree by design. No real record falls there: q writes `started`
after it has loaded the file.
"""

from __future__ import annotations

import itertools
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

from uqs import liveness
from uqs.interpreter import q_interpreter

UQF_ROOT = Path(__file__).resolve().parents[3]

HOSTS = ("same", "other")
PIDS = ("dead", "self")
STARTED = ("before", "after", "missing")

#: The verdict both rules must reach: gone on this host when the pid is dead,
#: or when it is alive but started after the record.
EXPECTED = {
    (host, pid, started): host == "same" and (pid == "dead" or started == "before")
    for host, pid, started in itertools.product(HOSTS, PIDS, STARTED)
}

#: q writes each owner as write_owner_as would, but with the case's host,
#: pid and `started` - `before` an hour before this process loaded the
#: rule, `after` now - judges every one, and then stays alive for Python.
_SCRIPT = """
\\l src/init.q
\\l src/etl/core/backfill_state.q
dir:"{dir}";
dead:{dead};
own:{{[name;host;pid;started]
    p:dir,"/",name,".lock";
    system "mkdir -p ",p;
    d:`pid`host!("j"$$[pid~`self;.z.i;dead];$[host~`same;string .z.h;"elsewhere.example"]);
    before:.qetl.job.bounded.state.loaded_at-0D01;
    if[not started~`missing; d[`started]:$[started~`before;before;.z.p]];
    (hsym `$.qetl.job.bounded.state.owner_file p) 0: enlist .j.j d;
    p}};
cases:{cases};
paths:own .' cases;
verdicts:(`$cases[;0])!.qetl.job.bounded.state.lock_is_stale each paths;
(hsym `$dir,"/verdicts.json") 0: enlist .j.j verdicts;
(hsym `$dir,"/ready") 0: enlist "ready";
system "sleep 300";
exit 0
"""


def _name(case: tuple[str, str, str]) -> str:
    return "_".join(case)


def _dead_pid() -> int:
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


def _q_cases() -> str:
    rows = [f'("{_name(c)}";`{c[0]};`{c[1]};`{c[2]})' for c in EXPECTED]
    return "(" + ";".join(rows) + ")"


def test_q_and_python_judge_one_table_of_owners_alike():
    q = q_interpreter(os.environ)
    if q is None:
        pytest.skip("no q interpreter - set $QCMD, or put q on PATH")
    env = {**os.environ, "QHOME": os.environ.get("QHOME", str(Path.home() / ".kx"))}
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "parity.q"
        script.write_text(_SCRIPT.format(dir=tmp, dead=_dead_pid(), cases=_q_cases()))
        proc = subprocess.Popen(
            [str(q), str(script), "-q"],
            cwd=UQF_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )
        try:
            deadline = time.monotonic() + 120
            while not (Path(tmp) / "ready").exists():
                if proc.poll() is not None or time.monotonic() > deadline:
                    proc.kill()
                    out = proc.communicate()[0]
                    pytest.fail(f"q never judged the owners:\n{out[-2000:]}")
                time.sleep(0.1)
            q_verdicts = json.loads((Path(tmp) / "verdicts.json").read_text())
            python_verdicts = {}
            for case in EXPECTED:
                lock = Path(tmp) / f"{_name(case)}.lock" / "owner"
                owner = json.loads(lock.read_text())
                python_verdicts[_name(case)] = liveness.process_gone(
                    owner["host"], int(owner["pid"]), owner.get("started")
                )
        finally:
            proc.kill()
            proc.wait()
    expected = {_name(c): gone for c, gone in EXPECTED.items()}
    assert q_verdicts == expected, "q's rule is not the one this table states"
    assert python_verdicts == expected, "Python's rule is not the one this table states"
