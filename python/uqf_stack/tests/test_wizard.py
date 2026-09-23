from pathlib import Path

import pytest

from uqf_stack.paths import UqfStackPaths
from uqf_stack.scaffold import wizard


@pytest.fixture
def fake_paths(tmp_path: Path) -> UqfStackPaths:
    """Minimal paths for wizard tests - only scripts_dir/orchestrator_dir
    are actually touched by the pure-logic pieces under test here (skeleton
    writers, template formatting); unlike test_core.py's own fake_paths,
    no vendored torq/torq-finance-starter-pack stand-in is needed since
    nothing here calls bootstrap()/start().
    """
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    return UqfStackPaths(
        repo_root=tmp_path,
        torqhome=tmp_path / "lib" / "torq",
        torqapphome=tmp_path / "lib" / "torq-finance-starter-pack",
        torqdata=tmp_path / "scripts" / "output" / "uqf-stack",
        scripts_dir=scripts_dir,
        orchestrator_dir=tmp_path / "python" / "uqf_stack",
    )


def test_parse_pairs_normalizes_case_and_separators():
    assert wizard._parse_pairs("eurusd, gbpusd  usdjpy") == ["EURUSD", "GBPUSD", "USDJPY"]


def test_parse_pairs_dedupes_preserving_first_occurrence():
    assert wizard._parse_pairs("EURUSD, EURUSD, GBPUSD") == ["EURUSD", "GBPUSD"]


def test_parse_pairs_rejects_a_malformed_pair():
    with pytest.raises(ValueError, match="EURUS"):
        wizard._parse_pairs("EURUS")  # 5 letters, not 6


def test_parse_pairs_empty_input_is_an_empty_list_not_an_error():
    # _prompt_pairs (the interactive wrapper) is what re-prompts on an
    # empty result - _parse_pairs itself just reports what it found.
    assert wizard._parse_pairs("   ") == []


def test_pip_size_is_0_01_for_jpy_crosses_0_0001_otherwise():
    assert wizard._pip_size("USDJPY") == 0.01
    assert wizard._pip_size("EURJPY") == 0.01
    assert wizard._pip_size("EURUSD") == 0.0001


def test_default_table_name_strips_a_trailing_process_number():
    assert wizard._default_table_name("myquotes1") == "myquotes"
    assert wizard._default_table_name("fxfeed42") == "fxfeed"


def test_default_table_name_falls_back_when_stripping_leaves_nothing():
    assert wizard._default_table_name("1") == "1_quotes"


def test_quotes_table_schema_matches_the_shape_forwards_q_requires():
    schema = wizard._quotes_table_schema("myquotes")
    assert schema == (
        "myquotes:([]time:`timestamp$(); sym:`g#`symbol$(); "
        "bid_prices:(); bid_sizes:(); ask_prices:(); ask_sizes:())"
    )


def test_write_quotes_feed_skeleton_produces_valid_q_literals(fake_paths: UqfStackPaths):
    dest = wizard._write_quotes_feed_skeleton(
        fake_paths,
        procname="myfeed1",
        pairs=["EURUSD", "USDJPY"],
        spots={"EURUSD": 1.085, "USDJPY": 150.0},
        table="myquotes",
        port_offset=29,
    )
    content = dest.read_text()
    assert dest == fake_paths.scripts_dir / "processes" / "torq_myfeed1.q"
    assert "pairs:`EURUSD`USDJPY" in content
    assert "spot:1.085 150.0" in content
    # EURUSD -> 0.0001, USDJPY (a JPY cross) -> 0.01
    assert "pip:0.0001 0.01" in content
    assert "`myquotes;(pairs;bid_prices;bid_sizes;ask_prices;ask_sizes)" in content
    assert "{KDBBASEPORT}+29" in content
    # every q lambda's opening/closing brace survived .format()'s escaping
    # unbalanced - a stray single brace would desync this count.
    assert content.count("{") == content.count("}")


def test_write_cross_etl_skeleton_produces_valid_q_literals(fake_paths: UqfStackPaths):
    dest = wizard._write_cross_etl_skeleton(
        fake_paths,
        procname="myetl1",
        source_table="quotes",
        cross_pairs=["EURJPY", "GBPJPY"],
        cross_size=1_000_000.0,
        port_offset=30,
    )
    content = dest.read_text()
    assert dest == fake_paths.scripts_dir / "processes" / "torq_myetl1.q"
    # Under the .qsub root, like every subscriber process in scripts/ - a
    # generated process that named itself at the root would be invisible to
    # every tool that enumerates this tree's namespaces.
    assert "\\d .qsub.myetl1" in content
    assert "cross_pairs:`EURJPY`GBPJPY" in content
    assert "cross_size:1000000.0" in content
    assert "if[t=`quotes; `.qsub.myetl1.mirror insert x; .qsub.myetl1.reprice[]]" in content
    assert content.count("{") == content.count("}")


def test_build_row_publisher_has_no_credential_and_feed_proctype():
    row = wizard._build_row("myfeed1", "publisher", 29, "${UQFSCRIPTS}/torq_myfeed1.q")
    assert row["proctype"] == "feed"
    assert row["U"] == ""
    assert row["port"] == "{KDBBASEPORT}+29"


def test_build_row_subscriber_needs_accesslist_credential_and_metrics_proctype():
    row = wizard._build_row("myetl1", "subscriber", 30, "${UQFSCRIPTS}/torq_myetl1.q")
    assert row["proctype"] == "metrics"
    assert row["U"] == "${TORQAPPHOME}/appconfig/passwords/accesslist.txt"
