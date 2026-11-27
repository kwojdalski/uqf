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
start of whatever process now has the number (`ps -o lstart`). q reads that
too when the live pid is another process's (#2692): a sibling or a shell
given a dead holder's number. The table below makes them meet: q writes
each lock owner and judges it, and while that q is still running - so "this
pid, alive" is true for both - Python reads the same owner files and judges
them too.

The pid space beside the host (#2986) is a dimension too: an owner recording
none (an older version, or macOS) is judged by host alone, one recording this
machine's like any other, and one recording another's by its time alone -
gone when written before this pid space's pid 1 began (a reboot, a container
restart), live otherwise. Both
sides are given the same fixed pid space for this machine, so the table reads
the same on macOS, which has no /proc to read a real one from.

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
PIDS = ("dead", "self", "other")
STARTED = ("before", "after", "missing", "ancient")
SPACES = ("missing", "same", "other")
#: The pid space both rules are told this machine has, and another machine's.
HERE, ELSEWHERE = "boot-a/pid:[1]", "boot-b/pid:[2]"


def _verdict(host: str, pid: str, started: str, space: str) -> bool | None:
    """The verdict both rules must reach. In this pid space (or with none
    recorded): gone on this host when the pid is dead, or when it is alive but
    started after the record - this q's own pid or another process's. In
    another pid space on this host, only the record's time can say: `ancient`
    (2000) is before this pid space's pid 1 began, so this machine before a
    reboot or a container before its restart - gone (#2497); `after` (now) is
    another machine's live holder, and `missing` cannot be judged - live
    (#2986). None: `before`, an hour ago, falls either side of pid 1's start
    (a container started minutes ago), so q and Python need only agree."""
    if host != "same":
        return False
    if space == "other":
        return None if started == "before" else started == "ancient"
    return pid == "dead" or started in ("before", "ancient")


EXPECTED = {case: _verdict(*case) for case in itertools.product(HOSTS, PIDS, STARTED, SPACES)}

#: q writes each owner as write_owner_as would, but with the case's host,
#: pid (`other` a process started before q was) and `started` - `before` an
#: hour before this process loaded the rule, `after` now - judges every one,
#: and then stays alive for Python.
_SCRIPT = """
\\l src/init.q
\\l src/etl/core/backfill_state.q
.qetl.job.bounded.state.pid_space:"{here}";
dir:"{dir}";
dead:{dead};
other:{other};
own:{{[name;host;pid;started;space]
    p:dir,"/",name,".lock";
    system "mkdir -p ",p;
    num:$[pid~`self;.z.i;pid~`other;other;dead];
    d:`pid`host!("j"$num;$[host~`same;string .z.h;"elsewhere.example"]);
    at:`before`after`ancient!(.qetl.job.bounded.state.loaded_at-0D01;.z.p;2000.01.01D);
    if[not started~`missing; d[`started]:at started];
    if[not space~`missing; d[`pid_space]:$[space~`same;"{here}";"{elsewhere}"]];
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


def _name(case: tuple[str, ...]) -> str:
    return "_".join(case)


def _dead_pid() -> int:
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


def _q_cases() -> str:
    rows = [f'("{_name(c)}";`{c[0]};`{c[1]};`{c[2]};`{c[3]})' for c in EXPECTED]
    return "(" + ";".join(rows) + ")"


def test_q_and_python_judge_one_table_of_owners_alike(monkeypatch):
    q = q_interpreter(os.environ)
    if q is None:
        pytest.skip("no q interpreter - set $QCMD, or put q on PATH")
    monkeypatch.setattr(liveness, "local_pid_space", lambda: HERE)
    env = {**os.environ, "QHOME": os.environ.get("QHOME", str(Path.home() / ".kx"))}
    other = subprocess.Popen(["sleep", "300"])
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "parity.q"
        script.write_text(
            _SCRIPT.format(
                dir=tmp,
                dead=_dead_pid(),
                other=other.pid,
                cases=_q_cases(),
                here=HERE,
                elsewhere=ELSEWHERE,
            )
        )
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
                    owner["host"], int(owner["pid"]), owner.get("started"), owner.get("pid_space")
                )
        finally:
            proc.kill()
            proc.wait()
            other.kill()
            other.wait()
    assert q_verdicts == python_verdicts, "q and Python judge the same owners differently"
    expected = {_name(c): gone for c, gone in EXPECTED.items() if gone is not None}
    assert {k: q_verdicts[k] for k in expected} == expected, (
        "q's rule is not the one this table states"
    )
