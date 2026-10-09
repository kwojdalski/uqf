"""An outside real-time subscriber (#984): it subscribes on sctp1, the
chained tickerplant, as its own login, and snapshots through the gateway's
getdata under a query policy - never on stp1 or a data tier."""

from __future__ import annotations

import csv
from dataclasses import replace
from pathlib import Path

import pytest

from uqs import paths as stack_paths
from uqs.stack import gateway_access
from uqs.stack.procs import effective_process_rows

REPO = Path(stack_paths.__file__).resolve().parents[4]
CONFIG = REPO / "scripts" / "torqconfig"


def _rows(runtime: str) -> dict[str, dict[str, str]]:
    return {
        r["procname"]: r for r in effective_process_rows(stack_paths.paths_for_root(REPO, runtime))
    }


@pytest.mark.parametrize("runtime", ["uqf", "crypto"])
def test_sctp1_takes_the_subscriber_access_list(runtime):
    rows = _rows(runtime)
    assert rows["sctp1"]["U"] == "${TORQDATA}/subscriber_accesslist.txt"
    vendored = rows["rdb1"]["U"]
    assert rows["stp1"]["U"] == vendored, "an outside subscriber never logs in on stp1"
    assert "subscriber" not in vendored


def test_the_torq_runtime_keeps_sctp1_as_shipped():
    assert "subscriber" not in _rows("torq")["sctp1"]["U"]


def test_the_subscriber_list_is_the_vendored_logins_plus_the_subscribers(tmp_path):
    paths = replace(stack_paths.paths_for_root(REPO, "crypto"), torqdata=tmp_path)
    lines = gateway_access.subscriber_access_lines(paths)
    vendored = (paths.torqapphome / "appconfig/passwords/accesslist.txt").read_text().split()
    assert lines[: len(vendored)] == vendored
    assert lines[len(vendored) :] == ["rts:rts"]
    gateway_access.write(paths)
    assert (tmp_path / "subscriber_accesslist.txt").read_text().split() == lines


def test_the_subscriber_can_snapshot_through_getdata_and_nothing_more():
    with (CONFIG / "permissions" / "gateway_users.csv").open(newline="") as f:
        roles = {row["user"]: row["role"] for row in csv.DictReader(f)}
    assert roles["rts"] == "analyst", "analyst is granted getdata and querypolicyfor only"


def test_the_first_subscribable_table_is_catalogued_and_has_a_policy():
    with (CONFIG / "dataaccess" / "querypolicy.csv").open(newline="") as f:
        policies = {row["tablename"] for row in csv.DictReader(f) if not row["role"]}
    assert "crypto_execution_quality" in policies
    catalog = (REPO / "scripts" / "processes" / "uqs_catalog.q").read_text()
    assert (
        ".qcat.describe[`crypto_execution_quality]" in catalog
        or "describe[`crypto_execution_quality]" in catalog
    )
