"""Receiver contracts: exported from a worker's declarations, approved for an attachment (#1717).

Export runs a real q over this tree's declarations; approval runs against the attachment
world of the attached-backfill tests (a verified attachment, a native-shaped HDB on disk).
What is under test: the contract is derived, not copied; a malformed, unsupported or
altered one is refused before anything is stored; an approval is bound to the
attachment's destination, and stops counting the moment that destination changes.
"""

from __future__ import annotations

import json
import os

import pytest
from attach_backfill_support import DATASET, UQF_ROOT, WORKER, make_world, save_attachment
from attach_backfill_support import tree_table as plant_table
from typer.testing import CliRunner

from uqs import cli
from uqs.attach import backfill as attached
from uqs.attach import receiver_contract as rc
from uqs.attach.schema import TableSpec
from uqs.interpreter import q_interpreter
from uqs.paths import UqsError, paths_for_root

runner = CliRunner()


@pytest.fixture(scope="module")
def exported() -> rc.Contract:
    if q_interpreter(os.environ) is None:
        pytest.skip("no q interpreter - set $QCMD, or put q on PATH")
    return rc.export(paths_for_root(UQF_ROOT), WORKER)


@pytest.fixture
def world(monkeypatch, tmp_path):
    return make_world(monkeypatch, tmp_path)


def _body(contract: rc.Contract, **change) -> dict:
    """The contract's JSON with `change` applied and its identity recomputed - a
    well-formed contract that says something else."""
    body = {k: v for k, v in contract.body.items() if k != "identity"}
    body.update(change)
    return {**body, "identity": rc.identity_of(body)}


# ------------------------------------------------------------------ export


def test_the_contract_is_derived_from_the_workers_declarations(exported):
    assert exported.dataset == DATASET
    assert exported.body["time_column"] == "source_time", "the source's time column"
    assert exported.body["row_key"] == ["venue", "sym", "source_time", "latency_count"]
    assert exported.body["origin"] == {
        "worker": WORKER,
        "source": "crypto_market_data",
        "transform": "crypto_market_data",
    }
    # what the HDB stores, read independently from the plant table's text
    assert exported.stored_columns == plant_table().as_pairs()
    nested = dict(exported.columns)
    assert nested["bid_prices"] == "F", "a () column carries the element type the plant declares"


def test_the_identity_is_a_digest_of_the_contents(exported):
    again = rc.export(paths_for_root(UQF_ROOT), WORKER)
    assert again.identity == exported.identity and len(exported.identity) == 16
    other = rc.parse(_body(exported, conflict_policies=["upsert"]))
    assert other.identity != exported.identity, "any change is another contract"


def test_an_unknown_worker_is_refused():
    with pytest.raises(UqsError, match="no_such_worker"):
        rc.export(paths_for_root(UQF_ROOT), "no_such_worker")


# ------------------------------------------------------------------ validation


@pytest.mark.parametrize(
    ("change", "said"),
    [
        ({"format": "something-else"}, "not a receiver contract"),
        ({"version": 2}, "version 2 is not supported"),
        ({"version": "1"}, "version must be a whole number"),
        ({"columns": []}, "columns must be a non-empty array"),
        ({"columns": [["time", "p"], ["time", "p"]]}, "named twice"),
        ({"columns": [["time", " "]]}, "not a concrete type"),
        ({"columns": [["time", "pp"]]}, "not a concrete type"),
        ({"columns": [["a b", "p"]]}, "must be a q name"),
        ({"time_column": "nope"}, "time_column nope is not a column"),
        ({"window_column": "nope"}, "window_column nope is not a column"),
        ({"row_key": ["nope"]}, "row_key must be an array of the contract's columns"),
        ({"partition": "../etc"}, "partition must be a partition name"),
        ({"conflict_policies": ["overwrite"]}, "conflict_policies must name strategies"),
        ({"conflict_policies": []}, "conflict_policies must name strategies"),
        ({"hdb_dir": "/tmp/x"}, "unknown key"),
    ],
)
def test_a_malformed_or_unsupported_contract_is_refused(exported, change, said):
    with pytest.raises(rc.ContractError, match=said):
        rc.parse(_body(exported, **change))


def test_a_contract_missing_a_key_is_refused(exported):
    body = _body(exported)
    del body["row_key"]
    with pytest.raises(rc.ContractError, match="missing row_key"):
        rc.parse(body)


def test_a_contract_altered_after_export_is_refused(exported):
    body = {**exported.body, "time_column": "local_time"}
    with pytest.raises(rc.ContractError, match="altered after it was exported"):
        rc.parse(body)


def test_a_file_that_is_not_json_is_refused(tmp_path):
    bad = tmp_path / "c.json"
    bad.write_text("{not json")
    with pytest.raises(rc.ContractError, match="is not JSON"):
        rc.load_file(bad)


# ------------------------------------------------------------------ approval


def test_an_approval_binds_the_contract_to_the_attachments_destination(world, exported):
    record = rc.approve(world.paths, exported, "pb-uat", max_rows=500)
    b = record["binding"]
    dest = attached.resolve(world.paths, "pb-uat", WORKER)
    assert b["destination"] == dest.identity and b["hdb_dir"] == str(dest.hdb_dir)
    assert b["users"] == ["admin"] and b["max_rows"] == 500
    assert b["conflict_policies"] == exported.conflict_policies
    contract, binding, found, path = rc.approved(world.paths, "pb-uat", exported.identity[:8])
    assert contract == exported and binding == b and found == dest
    assert path.parent == rc.approved_dir("pb-uat")
    assert [r["contract"]["identity"] for r in rc.records("pb-uat")] == [exported.identity]


def test_an_approval_may_narrow_the_conflict_policies_never_widen_them(world, exported):
    narrow = rc.approve(world.paths, exported, "pb-uat", conflict_policies=["upsert"])
    assert narrow["binding"]["conflict_policies"] == ["upsert"]
    only_upsert = rc.parse(_body(exported, conflict_policies=["upsert"]))
    with pytest.raises(UqsError, match="never widen"):
        rc.approve(world.paths, only_upsert, "pb-uat", conflict_policies=["append"])


@pytest.mark.parametrize("state", ["applied", "detached"])
def test_an_attachment_that_is_not_verified_approves_nothing(world, exported, state):
    save_attachment("pb-uat", world.hdb, state=state)
    with pytest.raises(UqsError, match=f"attachment pb-uat is {state}, not verified"):
        rc.approve(world.paths, exported, "pb-uat")
    assert not rc.approved_dir("pb-uat").exists(), "nothing was stored"


def test_a_dataset_the_attachment_does_not_publish_is_refused(world, exported):
    other = TableSpec("something_else", (("time", "p"), ("x", "f")))
    save_attachment("pb-uat", world.hdb, table=other)
    with pytest.raises(UqsError, match="which attachment pb-uat does not publish"):
        rc.approve(world.paths, exported, "pb-uat")
    assert not rc.approved_dir("pb-uat").exists()


def test_a_contract_whose_shape_the_attachment_does_not_have_is_refused(world, exported):
    cols = [[n, "j" if n == "latency_ms" else t] for n, t in exported.columns]
    drifted = rc.parse(_body(exported, columns=cols))
    with pytest.raises(UqsError, match="column latency_ms is type 'j', expected 'f'"):
        rc.approve(world.paths, drifted, "pb-uat")


def test_an_attachment_with_no_hdb_here_approves_nothing(world, exported):
    save_attachment("pb-uat", world.tmp / "not-here")
    with pytest.raises(UqsError, match="is not a directory on this machine"):
        rc.approve(world.paths, exported, "pb-uat")


def test_an_approval_does_not_follow_the_attachment_to_another_hdb(world, exported):
    rc.approve(world.paths, exported, "pb-uat")
    moved = world.tmp / "other-hdb"
    moved.mkdir()
    save_attachment("pb-uat", moved)
    with pytest.raises(UqsError, match="approve it again"):
        rc.approved(world.paths, "pb-uat", exported.identity)


def test_an_unapproved_or_ambiguous_identity_is_refused(world, exported):
    with pytest.raises(UqsError, match="no contract 0123456789abcdef is approved"):
        rc.approved(world.paths, "pb-uat", "0123456789abcdef")
    with pytest.raises(UqsError, match="not a contract identity"):
        rc.approved(world.paths, "pb-uat", "../x")


def test_a_stored_approval_that_was_edited_is_refused(world, exported):
    path = rc.approve(world.paths, exported, "pb-uat")["path"]
    record = json.loads(open(path).read())
    record["contract"]["dataset"] = "imported_trades"
    with open(path, "w") as fh:
        fh.write(json.dumps(record))
    with pytest.raises(rc.ContractError, match="altered after it was exported"):
        rc.approved(world.paths, "pb-uat", exported.identity)


# ------------------------------------------------------------------ the CLI


def test_export_approve_and_list_from_the_command_line(world, exported, tmp_path):
    out = tmp_path / "contract.json"
    done = runner.invoke(cli.app, ["job", "contract", "export", WORKER, "--output", str(out)])
    assert done.exit_code == 0, done.output
    assert json.loads(out.read_text())["identity"] == exported.identity
    done = runner.invoke(
        cli.app,
        ["job", "contract", "approve", str(out), "--attach", "pb-uat", "--conflict", "upsert"],
    )
    assert done.exit_code == 0, done.output
    assert f"approved contract {exported.identity}" in done.output
    done = runner.invoke(cli.app, ["job", "contract", "list", "--attach", "pb-uat", "--json"])
    assert done.exit_code == 0, done.output
    [record] = json.loads(done.output)
    assert record["binding"]["conflict_policies"] == ["upsert"]


def test_approving_a_malformed_file_fails_and_stores_nothing(world, tmp_path):
    bad = tmp_path / "contract.json"
    bad.write_text(json.dumps({"format": rc.FORMAT, "version": 9}))
    done = runner.invoke(cli.app, ["job", "contract", "approve", str(bad), "--attach", "pb-uat"])
    assert done.exit_code == 1
    assert not rc.approved_dir("pb-uat").exists()
