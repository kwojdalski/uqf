"""#2192 on a REAL TorQ segmented tickerplant with error mode on (its default).

errmode turns a failing `.u.upd` into a synchronous success: the batch goes to the STP's
error log and the publisher is told nothing. A feed that commits a Kafka offset, or saves a
polling cursor, after such a publish loses the batch for good.

A publisher process (q, loading the real `.qtorq.publish`) is driven against `stp1` of a
disposable stack, once with a good batch and once with a malformed one, through the three
paths that advance a position after a publish: a bare publish, a Kafka cycle and a polling
tick. A batch is malformed by dropping the envelope's last column, the state a plant and a
feed are in when a deploy restarted one onto a new `plant_tables.q` and not the other.

Opt-in like the other stack tests (`-m live_stack`).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from attach_stack_support import IsolatedStack, requires_stack

from uqs.paths import repo_root

pytestmark = requires_stack()
ROOT = repo_root()
#: Base port of the scratch stack; None picks a free span.
BASE_PORT = int(os.environ["UQS_TEST_BASE_PORT"]) if "UQS_TEST_BASE_PORT" in os.environ else None
KAFKA_MESSAGE = (
    "kafka_message:([]time:`timestamp$(); topic:`g#`symbol$(); partition:`long$(); "
    "offset:`long$(); msg_key:(); broker_time:`timestamp$(); payload:())\n"
)

PUBLISHER = r"""
\l src/init.q
\l scripts/processes/torq_pipeline.q
\l src/etl/init.q
h:hopen `$":127.0.0.1:",getenv[`STP_PORT],":admin:admin";
\c 400 4000
out:{[k;v] -1 k,"=",-3!v;};
msgs:{[offs] n:count offs;
    flip `mtype`topic`client`partition`offset`msgtime`data`key!(
    n#`;n#`uqf.t;n#0i;n#0i;`long$offs;
    2026.10.20D09:00:00.000000000+1000000000*til n;("x"$) each n#enlist "{}";n#enlist `byte$())};
/ the transport: poll hands the inbox once, commit records what it was asked to
inbox:enlist msgs 100 101 102;
committed:();
mk:{[drop] `poll`publish`commit`assigned!(
    {[n] b:inbox; if[0=count b; :msgs `long$()]; `inbox set 1_b; first b};
    {[drop;t;x] .qtorq.publish[h;t;$[drop; ![x;();0b;enlist `payload]; x]]}[drop];
    {[t;o] `committed set committed,enlist (t;o); 1b};
    {[u] ::})};
stp_count:{[] h"count kafka_message"};

/ a bare publish: the good batch, then the malformed one
trade:{[] (enlist `AAA;enlist 1.0;enlist 1i;enlist 0b;enlist "N";enlist "X";enlist `buy)};
out["good_publish";.qtorq.publish[h;`trade;trade[]]];
out["bad_publish";@[.qtorq.publish[h;`trade;];1#trade[];{x}]];

/ kafka: a malformed batch, then (plant unchanged) the same records published properly
st:.qetl.kafka.cycle[mk 1b;.qetl.kafka.state_new[]];
out["kafka_bad_commits";count committed];
out["kafka_bad_stats";(st[`stats]`published;st[`stats]`publish_errors;count st`backlog)];
out["kafka_bad_stored";stp_count[]];
st:.qetl.kafka.cycle[mk 0b;st];
out["kafka_good_commits";committed];
out["kafka_good_stats";(st[`stats]`published;st[`stats]`publish_errors;count st`backlog)];
out["kafka_good_stored";stp_count[]];

/ a polling feed whose cursor must not pass a refused page
setenv[`UQF_STATUS_DIR;getenv`SCRATCH];
upstream:([] ts:2026.09.10D00:00:00.000000000+1D*1 2 3; topic:`a`b`c;
    partition:1 2 3j; offset:7 8 9j;
    msg_key:3#enlist "key"; broker_time:3#2026.09.10D00:00:00.000000000; payload:3#enlist "pay");
.qetl.job.stream.define[`refuse_feed;`procname`subscribe_to`outputs`period`poll!(
    `refuse_feed1;`symbol$();enlist `kafka_message;0D00:00:01;
    `fetch`normalize`next_cursor!(
        {[c] select from upstream where ts>c};
        {[page] select topic,partition,offset,msg_key,broker_time,payload from page};
        {[page] last page`ts}))];
pub:{[drop;t;rows] .qtorq.publish[h;t;$[drop; ![rows;();0b;enlist `payload]; rows]]};
(` sv .qetl.job.stream.namespace[`refuse_feed],`publish) set pub[1b];
r:@[.qetl.job.stream.tick;`refuse_feed;{(`threw;x)}];
out["poll_bad_result";first r];
out["poll_bad_cursor";.qetl.job.continuous.load_cursor`refuse_feed];
(` sv .qetl.job.stream.namespace[`refuse_feed],`publish) set pub[0b];
.qetl.job.stream.tick `refuse_feed;
out["poll_good_cursor";.qetl.job.continuous.load_cursor`refuse_feed];
out["poll_stored";stp_count[]];
out["error_log_bytes";h"hcount first exec logname from `..currlog where tbl=.stplg.errorlogname"];
exit 0
"""


@pytest.fixture(scope="module")
def stack(tmp_path_factory: pytest.TempPathFactory):
    s = IsolatedStack(tmp_path_factory.mktemp("stack"), base_port=BASE_PORT)
    s.schema_file.write_text(s.schema_file.read_text() + "\n" + KAFKA_MESSAGE)
    try:
        s.start("discovery1", "stp1")
        yield s
    finally:
        s.teardown()


@pytest.fixture(scope="module")
def run(stack: IsolatedStack, tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    scratch = tmp_path_factory.mktemp("publisher")
    script = scratch / "publisher.q"
    script.write_text(PUBLISHER)
    env: dict[str, str] = {**stack.env, "STP_PORT": str(stack.port), "SCRATCH": str(scratch)}
    for var in ("UQF_PEACHQ", "UQF_Q_IMPL", "UQF_PEACHQ_REASON"):
        env.pop(var, None)
    q = os.environ.get("QCMD", str(Path.home() / ".kx/bin/q"))
    done = subprocess.run(
        [q, str(script), "-q"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120
    )
    lines = dict(line.split("=", 1) for line in done.stdout.splitlines() if "=" in line)
    assert "error_log_bytes" in lines, done.stdout + done.stderr
    return lines


def test_the_stp_really_swallows_a_refusal(stack, run):
    """The premise: error mode is on and the plant itself reports success."""
    assert stack.q("stp1", "?[.stplg.errmode;1b;0b]")
    assert int(run["error_log_bytes"]) > 0, "the refused batches are in the STP's error log"


def test_a_good_batch_publishes_and_a_malformed_one_is_a_failed_publish(run):
    assert run["good_publish"] == "1"
    assert "qtorq.publish: the tickerplant refused a batch of 1 trade rows" in run["bad_publish"]


def test_a_refused_kafka_batch_is_not_committed_and_a_good_one_is(run):
    assert run["kafka_bad_commits"] == "0"
    assert run["kafka_bad_stats"] == "0 1 3", (
        "published 0, one publish error, all three records still held"
    )
    assert run["kafka_bad_stored"] == "0", "the plant stored nothing of the refused batch"
    assert run["kafka_good_stats"] == "3 1 0"
    assert "103" in run["kafka_good_commits"], "the retry of the same records commits past them"
    assert run["kafka_good_stored"] == "3"


def test_a_refused_poll_page_does_not_move_the_cursor(run):
    assert run["poll_bad_result"] == "`threw"
    assert run["poll_bad_cursor"] in ("0Np", "::"), run["poll_bad_cursor"]
    assert run["poll_good_cursor"].startswith("2026.09.13"), run["poll_good_cursor"]
    assert run["poll_stored"] == "6", "three kafka records and the three polled rows"
