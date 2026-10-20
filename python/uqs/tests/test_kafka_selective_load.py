"""A Kafka decoder job loads its own topic mappings (#1481).

TorQ processes set `.qetl.load.only`, so kafka_flow1 loads only the closure of
its job file. The mapping once lived in a transform file nothing in that
closure named, and the process came up with an empty topic registry: every
record was dead-lettered as `no_mapping`. These hold the closure and the load.

The q half needs an interpreter and skips without one: $QCMD (or $UQF_PEACHQ).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from uqs.model import load_plan
from uqs.paths import UqsError

REPO = Path(__file__).resolve().parents[3]
FLOW = "src/etl/streaming/kafka_flow.q"
MAPPING = "src/etl/transforms/kafka_client_flow.q"

#: A decoder file as a sidecar writes it: the topic declaration, the one thing
#: that names the mapping, sits in the job's own file.
SIDE_MAP = ".qetl.transform.define[`zz_map;`inputs`output`fn!(()!();()!();{[m] m})];\n"
SIDE_JOB = (
    ".qetl.kafka.topic.define[`zz.topic;`mapping`output`job!(`zz_map;`zz_out;`zz_decoder)];\n"
    ".qetl.job.stream.define[`zz_decoder;`procname`subscribe_to`publishes`note!"
    '(`zzdecoder1;enlist `kafka_message;`zz_out`kafka_dead_letter;"a sidecar decoder")];\n'
)


@pytest.fixture(scope="module")
def plan() -> load_plan.LoadPlan:
    return load_plan.load_plan(REPO)


def test_the_decoder_process_loads_the_mapping_its_topic_names(plan):
    assert MAPPING in plan.for_proc("kafka_flow1")
    assert plan.for_proc("kafka_flow1").index(MAPPING) < plan.for_proc("kafka_flow1").index(FLOW)


def test_the_feed_process_loads_no_decoder(plan):
    assert plan.for_proc("kafka_feed1") == ["src/etl/streaming/kafka_feed.q"]


def test_a_topic_declaration_pulls_in_the_transform_it_maps_through():
    extra = {"src/etl/transforms/zz_map.q": SIDE_MAP, "src/etl/streaming/zz_decoder.q": SIDE_JOB}
    sel = load_plan.load_plan(REPO, extra).select(["zzdecoder1"])
    assert sel == ["src/etl/transforms/zz_map.q", "src/etl/streaming/zz_decoder.q"]


def test_every_topic_is_claimed_by_the_job_whose_file_declares_it(plan):
    assert plan.topics == {
        "uqf.client.flow": "kafka_flow",
        "cryptorust.book_snapshots": "crypto_book_decoder",
    }
    extra = {"src/etl/transforms/zz_map.q": SIDE_MAP, "src/etl/streaming/zz_decoder.q": SIDE_JOB}
    assert load_plan.load_plan(REPO, extra).topics["zz.topic"] == "zz_decoder"
    assert '.qetl.load.topics[`$"uqf.client.flow"]:`kafka_flow' in load_plan.render(plan)


def test_two_jobs_claiming_one_topic_are_refused_at_generation():
    twin = SIDE_JOB.replace("zz_decoder", "zz_twin").replace("zzdecoder1", "zztwin1")
    twin = twin.replace("zz.topic", "uqf.client.flow")
    extra = {"src/etl/streaming/zz_twin.q": twin}
    with pytest.raises(UqsError, match="uqf.client.flow.*kafka_flow.*zz_twin"):
        load_plan.render(load_plan.load_plan(REPO, extra))


def test_the_example_bundle_adds_a_topic_and_a_process_without_touching_the_tree(plan):
    rel = "src/etl/streaming/example_quotes_decoder.q"
    text = (REPO / "sidecars" / "kafka_example" / "example_quotes_decoder.q").read_text()
    with_bundle = load_plan.load_plan(REPO, {rel: text})
    # its mapping, topic and job are one file, so its process loads that file alone
    assert with_bundle.select(["examplequotes1"]) == [rel]
    assert with_bundle.topics == {
        "uqf.client.flow": "kafka_flow",
        "cryptorust.book_snapshots": "crypto_book_decoder",
        "example.quotes": "example_quotes_decoder",
    }
    # and no other process's closure changes
    for proc in plan.procs:
        assert with_bundle.for_proc(proc) == plan.for_proc(proc), proc


def _q() -> str:
    q = os.environ.get("QCMD") or os.environ.get("UQF_PEACHQ")
    if not q or not shutil.which(q):
        pytest.skip("no q interpreter - set $QCMD or $UQF_PEACHQ")
    return q


#: kafka_flow1's selected load, then one record through it.
_SELECTED = r"""
.qetl.load.only:enlist `kafka_flow1;
\l src/init.q
\l src/etl/init.q
pub:();
.qetl.job.stream.wire[`kafka_flow;{[t;x] `pub set pub,enlist (t;x); count x}];
p:"{\"sym\":\"EURUSD\",\"side\":\"buy\",\"qty\":1000000,\"price\":1.1,\"client\":\"ACME\",\"trade_id\":100}";
b:([] time:2#2026.10.20D09:00:00; topic:`uqf.client.flow`unknown.topic; partition:2#0j; offset:2#5j;
    msg_key:2#enlist ""; broker_time:2#2026.10.20D09:00:00; payload:(p;"{}"));
.qpipe.job.kafka_flow.on_batch[`kafka_message;b];
-1 "TOPICS:",", " sv string .qetl.kafka.topic.defined[];
-1 "PUBLISHED:",", " sv string first each pub;
-1 "REASONS:",", " sv string (pub[0;1])`reason;
-1 "TRADES:",", " sv string (pub[1;1])`trade_id;
exit 0
"""


def test_the_selected_load_decodes_a_record(tmp_path):
    q = _q()
    script = tmp_path / "selected.q"
    script.write_text(_SELECTED)
    done = subprocess.run(
        [q, str(script), "-q"], cwd=REPO, capture_output=True, text=True, timeout=180, check=False
    )
    out = done.stdout
    assert "TOPICS:uqf.client.flow" in out, out + done.stderr
    assert "PUBLISHED:kafka_dead_letter, client_flow" in out, out
    assert "REASONS:no_mapping\n" in out, "the unclaimed topic is dead-lettered, once: " + out
    assert "TRADES:100\n" in out, "and the mapped record decoded: " + out
