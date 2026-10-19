"""stp_register's in-process rollback undoes the FAILING step too (#1448).

Each registration step changes several tickerplant globals. Before #1448 the
undo loop started at the step BEFORE the one that threw, so the failing step's
partial effects stayed - and the answer said `"undone": i, "undo_failed": []`.

These run the operation's real lambda (`remote.source("stp_register")`) in a q
that has loaded TorQ's own stplog.q and stpmeta.q and holds a running tabperiod
tickerplant's state for one table, then make a step fail part way and check that
every global a step touches, the log directory and the stpmeta file are exactly
as they were - and that the answer counts the failing step as undone.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from uqs.attach import remote
from uqs.interpreter import q_interpreter

STP = Path(__file__).resolve().parents[3] / "lib" / "torq" / "code" / "segmentedtickerplant"
TABLE = "fx_new"
REQUEST = {
    "table": TABLE,
    "columns": [["time", "p"], ["sym", "s"], ["px", "f"]],
    "token": "tok1",
    "attachment": "att1",
}

#: A running tabperiod tickerplant with one table, `trade`, as .stplg.init leaves it.
HARNESS = """.lg.o:{[x;y]}; .lg.e:{[x;y]}; .dotz.set:{[x;y]};
.sctp.loggingmode:`create; .sctp.chainedtp:0b; .proc.procname:`stp1; .eodtime.dailyadj:0D;
\\l {stp}/stplog.q
\\l {stp}/stpmeta.q
trade:([]time:`timestamp$();sym:`symbol$();price:`float$());
.stpps.t:enlist`trade;
.stpps.schemas:.stpps.schemasnoattributes:(enlist`trade)!enlist trade;
.stpps.tabcols:(enlist`trade)!enlist cols trade;
.stplg.multilog:`tabperiod; .stplg.dldir:hsym`$"logs"; .stplg.i:1i;
.stplg.t:.stplg.logtabs:.stplg.rolltabs:enlist`trade;
.stplg.msgcount:.stplg.rowcount:(enlist`trade)!enlist 0;
.stplg.openlog[`tabperiod;.stplg.dldir;`trade;.z.p];
.stpm.updmeta[`tabperiod][`open;enlist`trade;.z.p];
names:`{table}`trade`.stpps.t`.stpps.schemas`.stpps.tabcols`.stpps.schemasnoattributes,
 `.stplg.updtab`.stplg.t`.stplg.logtabs`.stplg.rolltabs`.stplg.msgcount`.stplg.rowcount,
 `currlog`.stpm.metatable`.uqfattach.owned;
state:{{n!{.[{(1b;value x)};enlist x;{(0b;::)}]}each n:x}[names],
 `logfiles`stpmeta!(key .stplg.dldir;get `$":logs/stpmeta")};
{sabotage}
before:state[];
\\l op.q
-1 "ANSWER ",f raze read0 `:req.json;
-1 "SAME ",string before~state[];
-1 "HAS_TABLE ",string `{table} in tables[];
exit 0
"""


def run(tmp_path: Path, sabotage: str = "") -> tuple[dict, bool, bool]:
    q = q_interpreter(os.environ)
    if q is None:
        pytest.skip("no q interpreter - set $QCMD, or put q on PATH")
    (tmp_path / "logs").mkdir()
    (tmp_path / "op.q").write_text("f:" + remote.source("stp_register") + "\n")
    (tmp_path / "req.json").write_text(json.dumps(REQUEST))
    script = HARNESS.replace("{stp}", str(STP)).replace("{table}", TABLE)
    (tmp_path / "h.q").write_text(script.replace("{sabotage}", sabotage))
    env = {**os.environ, "QHOME": os.environ.get("QHOME", str(Path.home() / ".kx"))}
    proc = subprocess.run(
        [str(q), "h.q", "-q"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    out = {k: v for k, _, v in (line.partition(" ") for line in proc.stdout.splitlines())}
    assert "ANSWER" in out, proc.stdout + proc.stderr
    return json.loads(out["ANSWER"]), out["SAME"] == "1", out["HAS_TABLE"] == "1"


def test_the_issue_scenario_a_step_failing_on_its_last_assignment_is_undone(tmp_path):
    """Step 3 sets .stplg.t, logtabs, rolltabs and msgcount, then throws on
    `.stplg.rowcount[t]:0` because rowcount is a plain list. Before #1448 those
    four kept naming the new table while the answer said undone:3, undo_failed:[]."""
    answer, same, has_table = run(tmp_path, ".stplg.rowcount:enlist 0;")
    assert (answer["status"], answer["step"]) == ("failed", 3)
    assert (answer["undone"], answer["undo_failed"]) == (4, [])
    assert same, "every global a step touched is as it was before the call"
    assert not has_table


def test_an_openlog_failing_after_creating_the_log_is_undone_file_and_all(tmp_path):
    """The log file and handle the failing step created are closed and deleted,
    and currlog is restored."""
    sabotage = 'o:.stplg.openlog; .stplg.openlog:{[o;m;d;t;p] o[m;d;t;p]; \'"handle limit"}[o];'
    answer, same, _ = run(tmp_path, sabotage)
    assert (answer["status"], answer["step"], answer["detail"]) == ("failed", 4, "handle limit")
    assert (answer["undone"], answer["undo_failed"]) == (5, [])
    assert same


def test_a_metadata_step_failing_after_writing_is_undone_on_disk_too(tmp_path):
    """updmeta appends to .stpm.metatable and writes stpmeta, then throws: the row
    is gone from memory and from the file."""
    sabotage = (
        ".stpm.updmeta[`tabperiod]:{[x;t;p] .stpm.getmeta[x;p;;]'[enlist each t;"
        "`..currlog[([]tbl:t)]`logname]; .stpm.setmeta[.stplg.dldir;.stpm.metatable];"
        ' \'"meta write"};'
    )
    answer, same, _ = run(tmp_path, sabotage)
    assert (answer["status"], answer["step"]) == ("failed", 5)
    assert (answer["undone"], answer["undo_failed"]) == (6, [])
    assert same


def test_a_registration_that_succeeds_still_registers(tmp_path):
    answer, same, has_table = run(tmp_path)
    assert answer["status"] == "created", answer
    assert TABLE in answer["logs"][0]["name"]
    assert has_table and not same


def test_an_undo_that_throws_is_reported_not_counted(tmp_path):
    """The failing step's undo rewrites stpmeta; when that throws, the step is in
    undo_failed and not in undone - the answer never claims more than happened."""
    sabotage = (
        '.stpm.updmeta[`tabperiod]:{[x;t;p] \'"meta write"}; .stpm.setmeta:{[d;m] \'"disk gone"};'
    )
    answer, _, has_table = run(tmp_path, sabotage)
    assert (answer["status"], answer["step"]) == ("failed", 5)
    assert (answer["undone"], answer["undo_failed"]) == (5, [5])
    assert not has_table, "the steps before it were still undone"
