"""sources.csv (#718): which file is read, what it may hold, and the stub.

The rules mirror `.qetl.source.read_settings` and `.qtorq.load_source_settings`
in q; the last tests hold the Python constants to the q ones, so the two
cannot quietly disagree about a column, a secret key or a path variable.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from uqs import cli
from uqs.paths import UqsError, paths_for_root
from uqs.stack import source_settings as ss

UQF_ROOT = Path(__file__).resolve().parents[3]
SOURCE_CONTRACT_Q = UQF_ROOT / "src" / "etl" / "core" / "source_contract.q"
TORQ_PIPELINE_Q = UQF_ROOT / "scripts" / "processes" / "torq_pipeline.q"

TRANSPORTS = ("ipc", "odbc", "local")
HEADER = "source,transport,setting,secret_env\n"

runner = CliRunner()


def _layers(root: Path) -> tuple[Path, Path, Path]:
    """(application, service, base) sources.csv for a tree at `root`."""
    app, serv, base = ss.layers(paths_for_root(root, "uqf"))
    return app, serv, base


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --- which file ---------------------------------------------------------------


def test_the_layers_are_torqs_application_service_base(tmp_path):
    app, serv, base = _layers(tmp_path)
    assert app == tmp_path / "lib/torq-finance-starter-pack/appconfig/sources.csv"
    assert serv == tmp_path / "scripts/torqconfig/sources.csv"
    assert base == tmp_path / "lib/torq/config/sources.csv"


def test_the_most_specific_file_wins_whole(tmp_path):
    app, serv, base = _layers(tmp_path)
    paths = paths_for_root(tmp_path, "uqf")
    assert ss.selected(paths) is None
    _write(base, HEADER)
    assert ss.selected(paths) == base
    _write(serv, HEADER)
    assert ss.selected(paths) == serv
    _write(app, HEADER)
    assert ss.selected(paths) == app


def test_the_pure_torq_runtime_has_no_service_layer(tmp_path):
    assert len(ss.layers(paths_for_root(tmp_path, "torq"))) == 2


# --- what it may hold -----------------------------------------------------------


def test_a_header_alone_configures_nothing(tmp_path):
    assert ss.read(_write(tmp_path / "s.csv", HEADER), TRANSPORTS) == []


def test_a_row_is_read_with_its_line(tmp_path):
    f = _write(tmp_path / "s.csv", HEADER + "\nhdb_transfer,local,${KDBHDB},\n")
    (row,) = ss.read(f, TRANSPORTS)
    assert (row.source, row.transport, row.setting, row.secret_env) == (
        "hdb_transfer",
        "local",
        "${KDBHDB}",
        "",
    )


@pytest.mark.parametrize(
    ("text", "complaint"),
    [
        ("", "empty"),
        ("source,transport,setting\n", "lacks secret_env"),
        (HEADER.strip() + ",password\n", "password is not a column"),
        ("transport,source,setting,secret_env\n", "in the order"),
        (HEADER + "a,ipc,,\n", "line 2 needs a source, a transport and a setting"),
        (HEADER + "a,ftp,x,\n", "transport ftp is not one of"),
        (HEADER + "a,ipc,h:1,\na,ipc,h:2,\n", "a has more than one row - lines 2, 3"),
        (HEADER + "a,odbc,DRIVER=x;PWD=hunter2,\n", "secret inline"),
        (HEADER + "a,ipc,db1:5010:svc:hunter2,\n", "secret inline"),
    ],
)
def test_a_malformed_file_is_refused_naming_what_is_wrong(tmp_path, text, complaint):
    f = _write(tmp_path / "s.csv", text)
    with pytest.raises(UqsError, match=re.escape(complaint)):
        ss.read(f, TRANSPORTS)


def test_a_secret_referenced_through_its_variable_is_accepted(tmp_path):
    f = _write(tmp_path / "s.csv", HEADER + "a,odbc,DRIVER=x;PWD={secret},A_PWD\n")
    assert ss.read(f, TRANSPORTS)[0].secret_env == "A_PWD"


@pytest.mark.parametrize(
    ("setting", "secret_env", "environ", "complaint"),
    [
        ("SCAFFOLDED", "", {}, "still the SCAFFOLDED stub"),
        ("${HOME}/hdb", "", {}, "${HOME} is not a path it may use"),
        ("${KDBHDB}", "", {}, "${KDBHDB} is not set"),
        ("PWD={secret}", "", {}, "{secret} with no secret_env"),
        ("h:1", "A_PWD", {}, "secret_env with no {secret}"),
        ("PWD={secret}", "A_PWD", {}, "A_PWD is not set"),
    ],
)
def test_what_would_stop_a_row_resolving_is_named(setting, secret_env, environ, complaint):
    row = ss.Row("a", "odbc", setting, secret_env, 2)
    assert complaint in ss.problems(row, {"KDBHDB": ""}, environ)


def test_a_complete_row_has_no_problems():
    row = ss.Row("a", "odbc", "PWD={secret}", "A_PWD", 2)
    assert ss.problems(row, {}, {"A_PWD": "s3cret"}) == []


def test_describing_never_shows_a_secret(tmp_path, monkeypatch):
    _, serv, _ = _layers(tmp_path)
    _write(serv, HEADER + "a,odbc,DRIVER=x;PWD={secret},A_PWD\n")
    monkeypatch.setenv("A_PWD", "s3cret")
    monkeypatch.setenv("UQF_SOURCE_CRED_A", "override-value")
    path, rows = ss.describe(paths_for_root(tmp_path, "uqf"), TRANSPORTS)
    assert path == serv
    assert rows[0]["origin"] == "UQF_SOURCE_CRED_A", "the explicit override wins"
    assert "s3cret" not in repr(rows) and "override-value" not in repr(rows)


# --- the stub -----------------------------------------------------------------


def test_a_stub_with_no_file_anywhere_starts_one(tmp_path):
    app, _, _ = _layers(tmp_path)
    note = ss.add_stub(paths_for_root(tmp_path, "uqf"), "a", "odbc", TRANSPORTS)
    assert app.read_text() == HEADER + "a,odbc,SCAFFOLDED,\n"
    assert str(app) in note


def test_a_stub_keeps_every_row_the_stack_was_reading(tmp_path):
    app, serv, _ = _layers(tmp_path)
    _write(serv, HEADER + "b,local,${KDBHDB},\n")
    note = ss.add_stub(paths_for_root(tmp_path, "uqf"), "a", "odbc", TRANSPORTS)
    assert app.read_text() == HEADER + "b,local,${KDBHDB},\na,odbc,SCAFFOLDED,\n"
    assert serv.read_text() == HEADER + "b,local,${KDBHDB},\n", "the tree's file is untouched"
    assert f"a copy of {serv}" in note


def test_a_stub_never_overwrites_an_operators_row(tmp_path):
    app, _, _ = _layers(tmp_path)
    _write(app, HEADER + "a,odbc,DRIVER=x;Database=/live.duckdb,\n")
    before = app.read_text()
    note = ss.add_stub(paths_for_root(tmp_path, "uqf"), "a", "odbc", TRANSPORTS)
    assert app.read_text() == before
    assert "left as it is" in note


def test_a_stub_refuses_to_build_on_a_malformed_file(tmp_path):
    app, _, _ = _layers(tmp_path)
    _write(app, HEADER + "a,odbc,PWD=hunter2,\n")
    with pytest.raises(UqsError, match="secret inline"):
        ss.add_stub(paths_for_root(tmp_path, "uqf"), "b", "odbc", TRANSPORTS)


def test_a_sources_declared_transport_is_read_from_its_file():
    assert ss.declared_transport(UQF_ROOT, "duckdb_deals", "ipc") == "odbc"
    assert ss.declared_transport(UQF_ROOT, "hdb_transfer", "ipc") == "local"
    assert ss.declared_transport(UQF_ROOT, "demo_deals", "ipc") == "ipc"
    with pytest.raises(UqsError, match="no source 'nope'"):
        ss.declared_transport(UQF_ROOT, "nope", "ipc")


def test_the_cli_shows_the_selected_file(monkeypatch, tmp_path):
    monkeypatch.setattr("uqs.cli.sources._paths", lambda: paths_for_root(tmp_path, "uqf"))
    _, serv, _ = _layers(tmp_path)
    _write(serv, HEADER + "a,odbc,PWD={secret},A_PWD\n")
    monkeypatch.delenv("A_PWD", raising=False)
    result = runner.invoke(cli.app, ["config", "sources"])
    assert result.exit_code == 0, result.output
    assert str(serv) in result.output.replace("\n", "")
    assert "A_PWD is not set" in result.output


# --- held to the q ------------------------------------------------------------


def _q_value(path: Path, name: str) -> str:
    found = re.search(rf"^{name}:(.*)$", path.read_text(), re.MULTILINE)
    assert found, f"{name} is no longer defined in {path.name}"
    return found.group(1).strip()


def test_the_columns_are_the_ones_q_reads():
    assert _q_value(SOURCE_CONTRACT_Q, "settings_cols") == "`" + "`".join(ss.COLUMNS)


def test_the_secret_keys_and_markers_are_the_ones_q_uses():
    keys = re.findall(r'"([^"]*)"', _q_value(SOURCE_CONTRACT_Q, "secret_keys"))
    assert frozenset(keys) == ss.SECRET_KEYS
    assert _q_value(SOURCE_CONTRACT_Q, "settings_stub") == f'upper "{ss.STUB.lower()}"'
    assert _q_value(SOURCE_CONTRACT_Q, "secret_placeholder") == f'"{ss.SECRET_PLACEHOLDER}"'


def test_the_path_variables_are_the_ones_the_torq_adapter_allows():
    assert _q_value(TORQ_PIPELINE_Q, "source_settings_path_vars") == "`" + "`".join(ss.PATH_VARS)


def test_the_trees_own_file_is_a_header_and_nothing_else():
    """Demo sources stay on their fixtures until an operator configures one."""
    assert (UQF_ROOT / "scripts/torqconfig/sources.csv").read_text() == HEADER
