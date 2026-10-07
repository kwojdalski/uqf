"""`uqs list runtimes` and `uqs runtime diff` (#763), and the guide's runtime
table held to the declarations it describes."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from uqs import cli
from uqs.paths import UqsError, paths_for_root
from uqs.runtimes import DEFAULT_RUNTIME, RUNTIMES, UQF_ONLY_COMMANDS
from uqs.stack import alive, runtime_report

ROOT = Path(__file__).resolve().parents[3]
runner = CliRunner()


@pytest.fixture(autouse=True)
def _nothing_running(monkeypatch):
    """The listing asks the OS what is up; nothing is, here."""
    monkeypatch.setattr(alive, "running", lambda paths, base_port=None: set())


def _rows(monkeypatch, selected: str | None = None):
    if selected is None:
        monkeypatch.delenv("UQS_RUNTIME", raising=False)
    else:
        monkeypatch.setenv("UQS_RUNTIME", selected)
    return {r["runtime"]: r for r in runtime_report.list_runtimes(paths_for_root(ROOT))}


def test_every_declared_runtime_is_listed_in_declaration_order(monkeypatch):
    assert list(_rows(monkeypatch)) == list(RUNTIMES)


def test_the_default_and_the_selected_runtime_are_marked(monkeypatch):
    rows = _rows(monkeypatch, "torq")
    assert [n for n, r in rows.items() if r["default"]] == [DEFAULT_RUNTIME]
    assert [n for n, r in rows.items() if r["selected"]] == ["torq"]
    assert [n for n, r in _rows(monkeypatch).items() if r["selected"]] == [DEFAULT_RUNTIME]


def test_torq_is_the_starter_pack_alone(monkeypatch):
    torq = _rows(monkeypatch)["torq"]
    assert (torq["tables"], torq["layers"]) == ("3", "TorQ, starter pack")
    assert torq["data_dir"] == "output/uqs-torq" and torq["base_port"] == "6150"


def test_running_counts_only_this_runtimes_processes(monkeypatch):
    monkeypatch.setattr(alive, "running", lambda paths, base_port=None: {"rdb1", "fxfeed1"})
    rows = _rows(monkeypatch)
    assert rows["uqf"]["running"].startswith("2/")
    assert rows["torq"]["running"].startswith("1/"), "fxfeed1 is not a torq process"


def test_the_diff_says_what_uqf_adds_to_the_starter_pack():
    d = runtime_report.diff(ROOT, "uqf", "torq")
    assert all(not side["only_torq"] for side in d.values()), "torq adds nothing of its own"
    assert "fx_orderbook" in d["tables"]["only_uqf"]
    assert "fxfeed1" in d["processes"]["only_uqf"]
    assert d["layers"]["only_uqf"] == ["uqf service layer"]
    assert d["commands"]["only_uqf"] == sorted(UQF_ONLY_COMMANDS)


def test_a_runtime_diffed_with_itself_differs_in_nothing():
    d = runtime_report.diff(ROOT, "fx", "fx")
    assert all(not v for side in d.values() for v in side.values())


def test_an_unknown_runtime_is_refused_naming_the_real_ones():
    with pytest.raises(UqsError, match="'nope' is not a runtime - choose from uqf, torq"):
        runtime_report.diff(ROOT, "uqf", "nope")


def test_both_commands_print_json(monkeypatch):
    monkeypatch.delenv("UQS_RUNTIME", raising=False)
    listed = runner.invoke(cli.app, ["list", "runtimes", "--json"])
    assert listed.exit_code == 0, listed.output
    assert [r["runtime"] for r in json.loads(listed.stdout)] == list(RUNTIMES)
    diffed = runner.invoke(cli.app, ["runtime", "diff", "uqf", "torq", "--json"])
    assert diffed.exit_code == 0, diffed.output
    assert set(json.loads(diffed.stdout)) == set(runtime_report.DIFF_SECTIONS)


def _guide_table() -> dict[str, list[str]]:
    """docs/guides/uqs.md's runtime table: row label -> cells, header first."""
    text = (ROOT / "docs" / "guides" / "uqs.md").read_text()
    section = text[text.index("### Runtimes") :]
    lines = [ln.strip() for ln in section.splitlines()]
    start = next(i for i, ln in enumerate(lines) if ln.startswith("|"))
    rows = []
    for ln in lines[start:]:
        if not ln.startswith("|"):
            break
        rows.append([c.strip() for c in ln.strip("|").split("|")])
    table = {"header": rows[0][1:]}
    table.update({r[0]: r[1:] for r in rows[2:]})
    return table


def test_the_guides_runtime_table_matches_the_declarations():
    """The prose rows are the doc's; the columns, data directories and base
    ports are facts the declarations hold, and are checked against them."""
    table = _guide_table()
    names = [re.sub(r"`|\s*\(default\)", "", h) for h in table["header"]]
    assert names == list(RUNTIMES), "one column per runtime, in declaration order"
    assert table["data directory"] == [f"`output/{r.data_dir}`" for r in RUNTIMES.values()]
    assert table["base port"] == [f"`{r.base_port}`" for r in RUNTIMES.values()]
