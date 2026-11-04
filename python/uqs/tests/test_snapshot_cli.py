"""`uqs data snapshot list` and `show`: reading the configuration snapshot store (#1999).

The store is q's (src/etl/core/config_snapshot.q), so the fixtures are written by q -
through the interface that writes it, `record_as` - and the commands are held to what
a q reads back: the same ids, the same digests, the same refusal when a digest does not
match. Python keeps no copy of the store, which is why a damaged file is made by q too.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from uqs import cli
from uqs.cli import snapshot as snapshot_cli
from uqs.paths import UqsPaths

ROOT = Path(__file__).resolve().parents[3]

#: Two markout snapshots and one of another kind. argv[0] is the status directory.
SEED = r"""
\l src/init.q
\l src/etl/core/status.q
\l src/etl/core/backfill_state.q
\l src/etl/core/config_snapshot.q
c:`contract_version`code_revision`domain`domain_revision`datasets`horizons,
    `max_age`report_ccy`rate_timing`rates!(1;1;`fx;`fx_r1;`fx_markout`fx_markout_wide;
    0D00:00:01 0D00:00:10;0D00:00:05;`USD;`execution;(enlist `USDT)!enlist 1f);
.qetl.snapshot.attach[];
.qetl.snapshot.record_as[`markout_aaaa;`markout;c;`alice;"first configuration"];
d:@[c;`report_ccy`datasets;:;(`EUR;enlist `crypto_markout)];
.qetl.snapshot.record_as[`markout_bbbb;`markout;d;`bob;"a reporting currency change"];
.qetl.snapshot.record_as[`other_cccc;`other;enlist[`a]!enlist 1;`carol;"another kind"];
exit 0
"""

#: Change one snapshot's digest in the file, behind the interface's back.
TAMPER = r"""
\l src/init.q
\l src/etl/core/status.q
\l src/etl/core/backfill_state.q
\l src/etl/core/config_snapshot.q
p:.qetl.snapshot.path[];
t:.qetl.job.bounded.state.durable_get p;
.qetl.job.bounded.state.durable_set[p;update digest:`deadbeefdeadbeefdeadbeefdeadbeef
    from t where snapshot_id=`markout_bbbb];
exit 0
"""


def _q(q_binary, script: str, status: Path, tmp: Path) -> None:
    q, env = q_binary
    file = tmp / "script.q"
    file.write_text(script)
    result = subprocess.run(
        [q, str(file), "-q"],
        capture_output=True,
        text=True,
        env={**env, "UQF_STATUS_DIR": str(status)},
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture
def store(tmp_path: Path, q_binary, monkeypatch) -> Path:
    """A status directory holding three snapshots, and the CLI pointed at it."""
    status = tmp_path / "status"
    _q(q_binary, SEED, status, tmp_path)
    paths = UqsPaths(
        repo_root=ROOT,
        torqhome=tmp_path / "torq",
        torqapphome=tmp_path / "app",
        torqdata=tmp_path / "data",
        scripts_dir=ROOT / "scripts",
        orchestrator_dir=tmp_path / "orchestrator",
    )
    monkeypatch.setattr(snapshot_cli, "_paths", lambda: paths)
    q, env = q_binary
    monkeypatch.setenv("QCMD", q)
    monkeypatch.setenv("QHOME", env["QHOME"])
    monkeypatch.setenv("UQF_STATUS_DIR", str(status))
    return status


def _run(monkeypatch, log_text, *argv: str) -> tuple[int, str, str]:
    """(exit code, stdout, what it logged): a refusal is logged, and --json also prints it."""
    monkeypatch.setattr(sys, "argv", ["uqs", *argv])  # what json_requested reads
    before = len(log_text())
    result = CliRunner().invoke(cli.app, list(argv), env={"COLUMNS": "300"})
    return result.exit_code, result.stdout, log_text()[before:]


def _json(out: str) -> dict:
    return json.loads(out[out.index("{") :])


def test_list_shows_every_snapshot_with_its_record(store, monkeypatch, log_text):
    code, out, _ = _run(monkeypatch, log_text, "data", "snapshot", "list")
    assert code == 0, out
    for needle in ("markout_aaaa", "markout_bbbb", "other_cccc", "alice", "first configuration"):
        assert needle in out
    assert "created (UTC)" in out and "digest_ok" in out


def test_list_json_is_one_document_of_typed_records(store, monkeypatch, log_text):
    code, out, _ = _run(monkeypatch, log_text, "data", "snapshot", "list", "--json")
    assert code == 0, out
    doc = _json(out)
    assert doc["ok"] is True
    assert [s["id"] for s in doc["snapshots"]] == ["markout_aaaa", "markout_bbbb", "other_cccc"]
    first = doc["snapshots"][0]
    assert set(first) == {
        "id", "kind", "datasets", "digest", "created_at", "actor", "reason", "digest_ok"
    }  # fmt: skip
    assert first["datasets"] == ["fx_markout", "fx_markout_wide"]
    assert first["digest_ok"] is True and len(first["digest"]) == 32
    assert first["actor"] == "alice" and first["reason"] == "first configuration"
    # an ISO-8601 time with an explicit offset, like every other document's
    assert first["created_at"].endswith("+00:00") and "T" in first["created_at"]


def test_list_can_be_limited_to_one_dataset(store, monkeypatch, log_text):
    code, out, _ = _run(
        monkeypatch, log_text, "data", "snapshot", "list", "--dataset", "crypto_markout", "--json"
    )
    assert code == 0, out
    assert [s["id"] for s in _json(out)["snapshots"]] == ["markout_bbbb"]
    code, out, _ = _run(
        monkeypatch, log_text, "data", "snapshot", "list", "--dataset", "fx_markout_wide", "--json"
    )
    assert [s["id"] for s in _json(out)["snapshots"]] == ["markout_aaaa"]


def test_list_of_a_directory_with_no_store_is_empty_not_an_error(
    tmp_path, monkeypatch, q_binary, log_text
):
    paths = UqsPaths(
        repo_root=ROOT,
        torqhome=tmp_path / "torq",
        torqapphome=tmp_path / "app",
        torqdata=tmp_path / "data",
        scripts_dir=ROOT / "scripts",
        orchestrator_dir=tmp_path / "orchestrator",
    )
    monkeypatch.setattr(snapshot_cli, "_paths", lambda: paths)
    q, env = q_binary
    monkeypatch.setenv("QCMD", q)
    monkeypatch.setenv("QHOME", env["QHOME"])
    monkeypatch.setenv("UQF_STATUS_DIR", str(tmp_path / "nowhere"))
    code, out, _ = _run(monkeypatch, log_text, "data", "snapshot", "list", "--json")
    assert code == 0, out
    assert _json(out) == {"snapshots": [], "ok": True}


def test_show_prints_every_recorded_field_and_parameter(store, monkeypatch, log_text):
    code, out, _ = _run(monkeypatch, log_text, "data", "snapshot", "show", "markout_aaaa")
    assert code == 0, out
    for needle in (
        "snapshot markout_aaaa", "alice", "first configuration", "fx_r1", "execution", "USD",
        "USDT", "contract_version", "code_revision", "max_age", "horizons", "rates",
    ):  # fmt: skip
        assert needle in out


def test_show_json_has_the_record_and_the_parameters_as_q_text(store, monkeypatch, log_text):
    code, out, _ = _run(monkeypatch, log_text, "data", "snapshot", "show", "markout_aaaa", "--json")
    assert code == 0, out
    doc = _json(out)
    assert doc["snapshot"]["id"] == "markout_aaaa"
    assert doc["snapshot"]["digest_ok"] is True
    params = doc["parameters"]
    assert params["report_ccy"] == "USD" and params["rate_timing"] == "execution"
    assert params["domain_revision"] == "fx_r1" and params["contract_version"] == "1"
    assert "0D00:00:01" in params["horizons"] and "USDT" in params["rates"]
    assert (
        "digest" not in params and "reason" not in params
    )  # those are the record's, not parameters


def test_an_unknown_snapshot_is_a_usage_error_naming_the_id(store, monkeypatch, log_text):
    code, out, err = _run(monkeypatch, log_text, "data", "snapshot", "show", "markout_nope")
    assert code == 2
    assert "markout_nope" in out + err
    assert "uqs data snapshot list" in out + err
    code, out, _ = _run(monkeypatch, log_text, "data", "snapshot", "show", "markout_nope", "--json")
    assert code == 2
    doc = _json(out)
    assert doc["ok"] is False and "markout_nope" in doc["error"]


def test_a_badly_spelt_id_never_reaches_q(store, monkeypatch, log_text):
    code, out, err = _run(monkeypatch, log_text, "data", "snapshot", "show", 'x; system "id"')
    assert code == 2
    assert "is not a snapshot id" in out + err


def test_a_digest_mismatch_is_reported_loudly_by_both_commands(
    store, monkeypatch, q_binary, tmp_path, log_text
):
    _q(q_binary, TAMPER, store, tmp_path)
    code, out, _ = _run(monkeypatch, log_text, "data", "snapshot", "list", "--json")
    assert code == 1
    doc = _json(out)
    assert doc["ok"] is False
    by_id = {s["id"]: s["digest_ok"] for s in doc["snapshots"]}
    assert by_id == {"markout_aaaa": True, "markout_bbbb": False, "other_cccc": True}
    code, out, err = _run(monkeypatch, log_text, "data", "snapshot", "list")
    assert code == 1
    assert "digest mismatch: markout_bbbb" in out
    code, out, err = _run(monkeypatch, log_text, "data", "snapshot", "show", "markout_bbbb")
    assert code == 1
    assert "the digest of markout_bbbb does not match its content" in out + err
    # a snapshot that is fine is still readable beside the damaged one
    code, out, _ = _run(monkeypatch, log_text, "data", "snapshot", "show", "markout_aaaa", "--json")
    assert code == 0, out
