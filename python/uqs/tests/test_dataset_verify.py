"""`uqs job check`: the q it runs, and what it prints of the report (#1851).

The verification itself is q's and is held by tests/q/test_verify_*.q. These hold the
Python half: the script it sends, the scope it will and will not splice, the report it
prints, the exit code - and, against a real q, the demo_deals pair end to end.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from plain_cli import PlainRunner

from uqs import cli, timerange
from uqs.interpreter import q_interpreter
from uqs.paths import UqsError, default_paths
from uqs.stack import dataset_verify

runner = PlainRunner()

CHECK = {
    "dataset": "demo_deals",
    "scope": "",
    "range_from": "2026-09-11D00:00:00.000000000",
    "range_to": "2026-09-14D00:00:00.000000000",
    "basis": "the basis",
    "reason": "",
    "findings": [],
}


def report(statuses: dict[str, str], readonly: bool = True) -> dict:
    return {
        "dataset": "demo_deals",
        "range_from": CHECK["range_from"],
        "range_to": CHECK["range_to"],
        "producers": [{"producer": "demo_deals_feed", "mode": "stream", "source": "demo_deals"}],
        "readonly": readonly,
        "checks": [{**CHECK, "category": c, "status": s} for c, s in statuses.items()],
        "behaviours": [],
    }


ALL_PASS = dict.fromkeys(("schema", "behaviour", "cross_mode", "completeness"), "PASS")


def fake_q(monkeypatch, answer: dict) -> dict:
    sent: dict = {}
    monkeypatch.setattr(dataset_verify, "q_interpreter", lambda: Path("/usr/bin/q"))

    def run(cmd, **kw):
        sent["script"] = Path(cmd[1]).read_text()
        out = f"loading\nUQS_VERIFY {json.dumps(answer)}\n"
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    monkeypatch.setattr(dataset_verify.subprocess, "run", run)
    return sent


def window():
    return timerange.resolve("2026-09-11", "2026-09-14")


def test_the_script_calls_the_one_q_function_with_the_scope_and_window(monkeypatch):
    sent = fake_q(monkeypatch, report(ALL_PASS))
    dataset_verify.verify(
        default_paths(), "demo_deals", {"sym": "EURUSD", "book": "7"}, window(), behaviours=False
    )
    assert (
        '.qetl.verify.run[`demo_deals;`sym`book!("EURUSD";"7");2026.09.11D00:00:00.000000000;'
        "2026.09.14D00:00:00.000000000;(::);0b]" in sent["script"]
    )


def test_one_scope_column_is_a_one_entry_dictionary(monkeypatch):
    sent = fake_q(monkeypatch, report(ALL_PASS))
    dataset_verify.verify(default_paths(), "demo_deals", {"sym": "EURUSD"}, window())
    assert 'enlist[`sym]!enlist "EURUSD";' in sent["script"]


def test_a_scope_value_goes_to_q_as_text_for_its_column_to_type(monkeypatch):
    """Not typed from how the text looks: `0005` was sent as the long 5, a date
    or a float as a symbol, and each matched no row (#2473)."""
    sent = fake_q(monkeypatch, report(ALL_PASS))
    scope = {"sym": "0005", "day": "2026.09.13", "px": "1.5"}
    dataset_verify.verify(default_paths(), "demo_deals", scope, window())
    assert '`sym`day`px!("0005";"2026.09.13";"1.5")' in sent["script"]
    assert "0005j" not in sent["script"]


def test_a_gateway_becomes_the_getdata_reader(monkeypatch):
    sent = fake_q(monkeypatch, report(ALL_PASS))
    dataset_verify.verify(default_paths(), "demo_deals", {}, window(), gateway="gw:6060")
    assert '.qetl.verify.getdata_reader[hopen `$":gw:6060";1D]' in sent["script"]


@pytest.mark.parametrize("bad", ["sym", "sym=", "1sym=A", "sym=a b", "sym=a;b", "sym=`x"])
def test_a_scope_q_could_misread_is_refused(bad):
    with pytest.raises(UqsError, match="expected column=value"):
        dataset_verify.parse_scope([bad])


def test_a_column_given_twice_is_refused():
    with pytest.raises(UqsError, match="names sym twice"):
        dataset_verify.parse_scope(["sym=A", "sym=B"])


def test_a_dataset_name_is_not_spliced_into_q_unchecked(monkeypatch):
    fake_q(monkeypatch, report(ALL_PASS))
    with pytest.raises(UqsError, match="is not a dataset name"):
        dataset_verify.verify(default_paths(), 'x;system "rm -rf /"', {}, window())


def test_a_q_refusal_is_the_commands_error(monkeypatch):
    fake_q(monkeypatch, {"error": "capture: nothing publishes or backfills nope"})
    with pytest.raises(UqsError, match="capture: nothing publishes or backfills nope"):
        dataset_verify.verify(default_paths(), "nope", {}, window())


def test_worst_orders_fail_over_unverified_over_pass():
    assert dataset_verify.worst(report(ALL_PASS)) == "PASS"
    assert dataset_verify.worst(report({**ALL_PASS, "behaviour": "UNVERIFIED"})) == "UNVERIFIED"
    assert (
        dataset_verify.worst(report({**ALL_PASS, "behaviour": "UNVERIFIED", "schema": "FAIL"}))
        == "FAIL"
    )


def invoke(monkeypatch, statuses, *extra, readonly=True):
    fake_q(monkeypatch, report(statuses, readonly))
    return runner.invoke(
        cli.app,
        ["job", "check", "demo_deals", "--from", "2026-09-11", "--to", "2026-09-14", *extra],
    )


def test_the_text_names_each_category_with_its_verdict(monkeypatch):
    r = invoke(monkeypatch, {**ALL_PASS, "completeness": "UNVERIFIED"})
    assert r.exit_code == 0, r.output
    for line in ("PASS  schema", "UNVERIFIED  completeness", "PASS  cross_mode", "PASS  behaviour"):
        assert line in r.output
    assert "producer  demo_deals_feed (stream)" in r.output


def test_a_failed_category_exits_1(monkeypatch):
    assert invoke(monkeypatch, {**ALL_PASS, "cross_mode": "FAIL"}).exit_code == 1


def test_unverified_exits_0_unless_asked(monkeypatch):
    unverified = {**ALL_PASS, "behaviour": "UNVERIFIED"}
    assert invoke(monkeypatch, unverified).exit_code == 0
    assert invoke(monkeypatch, unverified, "--fail-unverified").exit_code == 1


def test_a_changed_snapshot_exits_1(monkeypatch):
    assert invoke(monkeypatch, ALL_PASS, readonly=False).exit_code == 1


def test_json_is_the_report_with_its_worst_verdict(monkeypatch):
    r = invoke(monkeypatch, {**ALL_PASS, "completeness": "UNVERIFIED"}, "--json")
    doc = json.loads(r.stdout)
    assert doc["worst"] == "UNVERIFIED"
    assert [c["category"] for c in doc["checks"]] == [
        "schema",
        "behaviour",
        "cross_mode",
        "completeness",
    ]
    assert {c["status"] for c in doc["checks"]} == {"PASS", "UNVERIFIED"}
    assert set(doc["checks"][0]) >= {
        "dataset",
        "scope",
        "range_from",
        "range_to",
        "basis",
        "reason",
        "findings",
    }


@pytest.mark.skipif(q_interpreter() is None, reason="no q interpreter")
def test_demo_deals_end_to_end_against_a_real_q():
    r = runner.invoke(
        cli.app,
        [
            "job",
            "check",
            "demo_deals",
            "--from",
            "2026-09-11",
            "--to",
            "2026-09-14",
            "--scope",
            "sym=EURUSD",
            "--json",
        ],
    )
    doc = json.loads(r.stdout)
    by = {c["category"]: c["status"] for c in doc["checks"]}
    assert by["schema"] == "PASS" and by["cross_mode"] == "PASS"
    assert by["completeness"] == "UNVERIFIED", "no gateway: the destination was not read"
    assert by["behaviour"] == "UNVERIFIED", (
        "slow queries, startup failure and late arrivals have no harness"
    )
    assert doc["readonly"] is True
    assert [p["producer"] for p in doc["producers"]] == ["demo_deals_feed", "demo_deals_backfill"]
    assert doc["worst"] == "UNVERIFIED"
    assert r.exit_code == 0
