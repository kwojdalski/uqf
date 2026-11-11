"""A batch that cannot be applied while a stream job replays its log (#2195).

Both run the real thing, KDB-X only (skipping without it, for the reason
test_etl_standalone_load.py gives):

- TorQ's own `.sub.replay` over a log holding one poison batch, with the `upd`
  torq_stream.q installs. TorQ traps `-11!` once per log file, so before the
  fix one error dropped every later message; now the batch is quarantined and
  the following ones are applied. The log was written under a narrower schema
  than the plant has today, so the replayed columns must be named from the log.
- the standalone runner (run_stream.q) started twice against the same day's
  log holding a poison batch: both starts must come up.
"""

# ruff: noqa: E501 - the q scripts below are q source, kept as written
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from uqs.interpreter import q_interpreter

UQF_ROOT = Path(__file__).resolve().parents[3]
TIMEOUT_SECONDS = 120


def _kdbx() -> tuple[str, dict[str, str]]:
    env = os.environ.copy()
    q = q_interpreter(env)
    if q is None:
        pytest.skip("no q interpreter - set $QCMD, or put q on PATH")
    env.setdefault("QHOME", str(Path.home() / ".kx"))
    return str(q), env


def _run(script: Path, tmp_path: Path, *args: str) -> str:
    q, env = _kdbx()
    env["UQF_STATUS_DIR"] = str(tmp_path / "status")
    result = subprocess.run(
        [q, str(script), "-q", *args],
        cwd=UQF_ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
    )
    out = result.stdout + result.stderr
    assert result.returncode == 0, out
    return out


def _value(out: str, key: str) -> str:
    lines = [line for line in out.splitlines() if line.startswith(f"{key}:")]
    assert lines, out
    return lines[-1].split(":", 1)[1]


#: TorQ's replay (the vendored subscriptions.q) over four logged batches, the
#: second of which on_batch rejects, delivered through torq_stream.q's upd.
_POISON = """\
.timer.enabled:0b;.dotz.getcommand:{[x] {[x;y]}};.dotz.set:{[x;y]};.lg.o:{[a;b]};.lg.e:{[a;b] -1 "LGE ",b};
\\l src/init.q
\\l scripts/processes/torq_pipeline.q
\\l src/etl/init.q
\\l lib/torq/code/common/subscriptions.q
.qpipe.job.rp.book:([] sym:`symbol$(); qty:`float$());
.qpipe.job.rp.on_batch:{[t;x] if[any null x`qty; '"bad qty"]; `.qpipe.job.rp.book insert (`sym`qty#x);};
.qetl.job.stream.define[`rp;`procname`subscribe_to`outputs`on_batch`replay`state!(`rp1;enlist `fills;enlist `rp_out;.qpipe.job.rp.on_batch;1b;enlist `book)];
lf:`$":LOGFILE"; lf set (); h:hopen lf;
h enlist (`upd;`fills;(enlist .z.p;enlist `EURUSD;enlist 1f));
h enlist (`upd;`fills;(enlist .z.p;enlist `GBPUSD;enlist 0n));
h enlist (`upd;`fills;(enlist .z.p;enlist `USDJPY;enlist 3f));
h enlist (`upd;`fills;(enlist .z.p;enlist `AUDUSD;enlist 4f));
hclose h;
/ what stpmeta recorded for the log: three columns; the plant's table today has four
old:([] time:`timestamp$(); sym:`symbol$(); qty:`float$());
new:([] time:`timestamp$(); sym:`symbol$(); qty:`float$(); venue:`symbol$());
.qtorq.sub_cands[`fills]:enlist old;
.qtorq.sub_sch_at[`fills]:0;
`upd set .qtorq.sub_upd[`rp;.qetl.job.stream.tolerant[`rp;.qetl.job.stream.handler`rp]];
.qetl.job.stream.replaying:1b; .qetl.job.stream.catching_up[`rp;1b];
.sub.replay[`fills;`subtabs`errtabs`instrs!(enlist `fills;`symbol$();`);enlist (`fills;new);enlist (4;lf)];
-1 "BOOK:",", " sv string .qpipe.job.rp.book`sym;
-1 "QUARANTINED:",string count .qetl.job.stream.quarantined;
-1 "QUARANTINED_AT:",string first .qetl.job.stream.quarantined`position;
-1 "QUARANTINED_ROWS:",string first (first .qetl.job.stream.quarantined`rows)`sym;
-1 "FAILED:",string .qetl.stream_health.of[`rp]`failed;
exit 0
"""


def test_a_poison_batch_mid_log_is_quarantined_and_the_next_ones_are_applied(
    tmp_path: Path,
) -> None:
    script = tmp_path / "poison.q"
    script.write_text(_POISON.replace(":LOGFILE", f":{tmp_path / 'rp.log'}"))
    out = _run(script, tmp_path)
    assert "could not replay the log file" not in out, out
    assert _value(out, "BOOK") == "EURUSD, USDJPY, AUDUSD", out
    assert _value(out, "QUARANTINED") == "1", out
    assert _value(out, "QUARANTINED_AT") == "2", out
    assert _value(out, "QUARANTINED_ROWS") == "GBPUSD", out
    assert _value(out, "FAILED") == "1", out


#: Seeds today's plant log the way the standalone plant writes it: a fill, a
#: batch the job cannot apply (no book, product or side), and another fill.
_SEED = """\
\\l scripts/jobs/run_stream.q
.qproc.standalone.declare_schemas[];
.qetl.tick.open_log["LOGDIR";`uqflocal;.z.D];
fill:{[s;px] ([] source_time:enlist .z.p; sym:enlist s; venue:enlist `fx; side:enlist 1;
    size:enlist 1e6; price:enlist px; fee:enlist 0f; fee_ccy:enlist `USD; fill_id:enlist `f;
    book:enlist `desk; product:enlist `spot; clock:enlist `venue)};
.qetl.tick.publish[`executions;fill[`EURUSD;1.1]];
.qetl.tick.record[(`upd;`executions;([] time:enlist .z.p; sym:enlist `GBPUSD))];
.qetl.tick.publish[`executions;fill[`USDJPY;150f]];
exit 0
"""

_RESTART = """\
\\l scripts/jobs/run_stream.q
-1 "QUARANTINED:",string count .qetl.job.stream.quarantined;
-1 "BOOK:",string count .qpipe.job.fx_positions.positions;
-1 "RUNNING:",", " sv string .qetl.job.stream.running;
exit 0
"""


def test_the_standalone_runner_restarts_the_same_day_against_a_poison_batch(
    tmp_path: Path,
) -> None:
    logdir = tmp_path / "tplog"
    seed = tmp_path / "seed.q"
    seed.write_text(_SEED.replace("LOGDIR", str(logdir)))
    _run(seed, tmp_path)
    restart = tmp_path / "restart.q"
    restart.write_text(_RESTART)
    for _ in range(2):
        out = _run(restart, tmp_path, "-job", "fx_positions", "-logdir", str(logdir))
        assert _value(out, "RUNNING").endswith("fx_positions"), out
        assert _value(out, "QUARANTINED") == "1", out
        assert _value(out, "BOOK") == "2", out
