"""`uqs job new`'s process options, source transport and q-held spellings.

Each option is read back the way the tree reads it - through the reader the
process registry is built from - rather than by searching the generated text,
so an option that wrote something the registry does not see fails here.

The two Python spellings of q facts in scaffold/templates.py (the transports
and the credential variable) are held to the q file they mirror, the same way
test_q_names.py holds every q name the Python sends.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from uqs import cli
from uqs.model.declarations import read_file_text
from uqs.paths import UqsError
from uqs.scaffold import jobs
from uqs.scaffold import worker as backfill
from uqs.scaffold.columns import definition_columns, parse_columns
from uqs.scaffold.normalizer import normalizer
from uqs.scaffold.templates import TRANSPORTS, credential_var

UQF_ROOT = Path(__file__).resolve().parents[3]
SOURCE_CONTRACT_Q = UQF_ROOT / "src" / "etl" / "core" / "source_contract.q"

runner = CliRunner()


def _body(plan: jobs.ScaffoldPlan, suffix: str) -> str:
    return next(a.body for a in plan.actions if str(a.path).endswith(suffix))


def _declared(plan: jobs.ScaffoldPlan, suffix: str):
    (d,) = read_file_text(_body(plan, suffix), Path(suffix))
    return d


# ------------------------------------------------------ procname, start_with_all


def test_a_given_procname_is_the_process_that_runs_it():
    plan = jobs.streaming_job("pulse", [], "pulse", "px:float", "pulsefeed1")
    assert _declared(plan, "pulse.q").procname == "pulsefeed1"


def test_start_with_all_reads_back_as_starting_with_the_stack():
    plan = jobs.streaming_job("pulse", [], "pulse", "px:float", start_with_all=True)
    assert _declared(plan, "pulse.q").start_with_all


def test_without_start_with_all_the_key_is_not_written():
    """Absent means on demand. Writing `0b` would restate the default."""
    body = _body(jobs.streaming_job("pulse", [], "pulse", "px:float"), "pulse.q")
    assert "start_with_all`" not in body.split("define[", 1)[1]


def test_a_normalizer_can_start_with_the_stack_too():
    quote = "quote:([]time:`timestamp$(); sym:`g#`symbol$(); bid:`float$(); ask:`float$())"
    plan = normalizer(
        "ticks",
        ["quote"],
        parse_columns("sym:symbol"),
        {"quote": definition_columns(quote)},
        known_tables={"quote"},
        procname="tick1",
        start_with_all=True,
    )
    d = _declared(plan, "ticks.q")
    assert (d.procname, d.start_with_all) == ("tick1", True)


def test_a_backfill_takes_a_procname():
    plan = backfill.bounded_worker("fx", "fx_rates", "px:float", procname="fxbf1")
    assert "`fxbf1;" in _body(plan, "fx_backfill.q")


# ------------------------------------------------------------- one subscription


def test_one_subscription_is_written_as_a_list_like_the_tree_writes_it():
    """`enlist `trades`, as `publishes` and the tree's own jobs spell it - not
    the atom `trades`, which define accepts but no other declaration uses."""
    plan = jobs.streaming_job("m", ["trades"], "m_out", "px:float")
    body = _body(plan, "m.q")
    assert "enlist `trades;" in body
    assert _declared(plan, "m.q").subscribe_to == ("trades",)


# ------------------------------------------------------------------- transport


def test_an_ipc_source_declares_no_transport():
    """IPC is `.qetl.source.define`'s default, so saying so adds nothing."""
    body = _body(backfill.bounded_worker("fx", "fx_rates", "px:float"), "sources/fx.q")
    assert "transport" not in body.split(".qetl.source.define", 1)[1]


def test_an_odbc_source_declares_its_transport_and_a_credential_example():
    body = _body(
        backfill.bounded_worker("fx", "fx_rates", "px:float", transport="odbc"), "sources/fx.q"
    )
    define = body.split(".qetl.source.define", 1)[1]
    assert "`tz`transport`credential_example!" in define
    assert ";tz;transport;credential_example)" in define
    assert "transport:`odbc" in body
    assert 'credential_example:"SCAFFOLDED' in body, "a placeholder test_no_scaffold_left will flag"
    assert ".qetl.io.odbc.run_sql" in body, "the query comment is the ODBC one"


def test_an_unknown_transport_is_refused():
    with pytest.raises(UqsError, match="--transport must be one of"):
        backfill.bounded_worker("fx", "fx_rates", "px:float", transport="http")


def test_a_transport_for_a_source_that_already_exists_is_refused():
    """The transport shapes the source file, and a reused one is not written."""
    with pytest.raises(UqsError, match="drop --transport"):
        backfill.bounded_worker(
            "fx", "fx_rates", None, transport="odbc", reuse_source=True, define_table=False
        )


def test_a_new_source_names_the_variable_its_credential_is_read_from():
    notes = backfill.bounded_worker("fx", "fx_rates", "px:float", transport="odbc").notes
    assert any("UQF_SOURCE_CRED_FX" in n and "fixture" in n for n in notes)


def test_a_worker_is_pointed_at_a_quality_check():
    notes = backfill.bounded_worker("fx", "fx_rates", "px:float").notes
    assert any("quality_check" in n for n in notes)


# ------------------------------------------------- held to the q they restate


def test_the_transports_are_the_ones_q_accepts():
    line = next(
        ln for ln in SOURCE_CONTRACT_Q.read_text().splitlines() if ln.startswith("transports:")
    )
    assert tuple(re.findall(r"`(\w+)", line)) == TRANSPORTS


def test_the_credential_variable_is_spelled_as_q_spells_it():
    """q: `credential_var:{[source] "UQF_SOURCE_CRED_",upper string source}`."""
    line = next(
        ln for ln in SOURCE_CONTRACT_Q.read_text().splitlines() if ln.startswith("credential_var:")
    )
    prefix = re.search(r'"(\w+)",upper string source', line)
    assert prefix, "credential_var no longer reads the way this test expects"
    assert credential_var("duckdb_deals") == prefix.group(1) + "DUCKDB_DEALS"


# ----------------------------------------------------------------- the command


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (
            [
                "x",
                "--kind",
                "backfill",
                "--dataset",
                "x",
                "--columns",
                "a:float",
                "--start-with-all",
            ],
            "standing jobs, not a backfill",
        ),
        (
            ["x", "--publishes", "x", "--columns", "a:float", "--transport", "odbc"],
            "--transport does not apply to --kind streaming",
        ),
    ],
)
def test_an_option_that_does_not_apply_to_the_kind_is_refused(argv, message, monkeypatch):
    """The message, not only the exit code: `_die` logs through loguru, which
    CliRunner cannot capture (see test_cli.py), so it is recorded here."""
    from uqs.cli import create

    refused: list[str] = []

    def record(exc: Exception) -> None:
        refused.append(str(exc))
        raise SystemExit(1)

    monkeypatch.setattr(create, "_die", record)
    result = runner.invoke(cli.app, ["job", "new", *argv, "--dry-run"])
    assert result.exit_code == 1
    assert refused and message in refused[0]


def test_without_q_the_contract_surface_step_is_named(tmp_path, monkeypatch):
    """No q, no export: the command says what to run instead of leaving the
    contract-surface hook to fail on the next commit unexplained."""
    from uqs.cli import regenerate

    monkeypatch.setattr(regenerate, "q_interpreter", lambda: None)
    assert regenerate._export_contract_surface(tmp_path) is None
