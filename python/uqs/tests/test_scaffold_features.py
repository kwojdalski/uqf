"""`uqs new-job`'s timer, partition, quality check and profile options.

As in test_scaffold_options.py, each option is read back the way the tree
reads it where there is a reader, and the profiles.py edits are applied to
the real file's text and compiled, so an edit that broke the module fails here
rather than on the next import.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from uqs import cli
from uqs.cli import create
from uqs.model.declarations import declaration_calls, symbols
from uqs.paths import UqsError
from uqs.scaffold import jobs, profile
from uqs.scaffold import worker as backfill
from uqs.scaffold.normalizer import definition_columns, normalizer

UQF_ROOT = Path(__file__).resolve().parents[3]
PROFILES_TEXT = (UQF_ROOT / profile.PROFILES_FILE).read_text()
KNOWN = ("default", "fx", "crypto", "essential")

runner = CliRunner()


def _body(plan: jobs.ScaffoldPlan, suffix: str) -> str:
    return next(a.body for a in plan.actions if str(a.path).endswith(suffix))


def _fields(plan: jobs.ScaffoldPlan, suffix: str) -> dict[str, str]:
    (_, _, fields), *_ = declaration_calls(_body(plan, suffix))
    return fields


# ------------------------------------------------------------------ --period


def test_a_feed_ticks_at_the_period_it_is_given():
    fields = _fields(jobs.streaming_job("f", [], "fo", "px:float", period="0D00:00:00.500"), "f.q")
    assert fields["period"] == "0D00:00:00.500"


def test_a_feed_ticks_every_second_by_default():
    assert _fields(jobs.streaming_job("f", [], "fo", "px:float"), "f.q")["period"] == "0D00:00:01"


def test_an_etl_given_a_period_gets_a_timer_beside_its_batch_handler():
    plan = jobs.streaming_job("m", ["trades"], "mo", "px:float", period="0D00:00:05")
    fields = _fields(plan, "m.q")
    assert list(fields)[3:6] == ["on_batch", "period", "on_timer"], "define's key order"
    body = _body(plan, "m.q")
    assert '\'"m.on_batch: not implemented"' in body
    assert '\'"m.on_timer: not implemented"' in body


def test_an_etl_without_a_period_has_no_timer():
    assert "on_timer" not in _fields(jobs.streaming_job("m", ["trades"], "mo", "px:float"), "m.q")


@pytest.mark.parametrize(
    ("period", "message"),
    [("5s", "must be a q timespan"), ("0D00:00:00", "must be positive")],
)
def test_a_period_define_would_refuse_is_refused_here(period, message):
    with pytest.raises(UqsError, match=message):
        jobs.streaming_job("f", [], "fo", "px:float", period=period)


# -------------------------------------------------------- --partition, --check


def test_a_partitioned_worker_declares_its_partition():
    plan = backfill.bounded_worker("fx", "fx_rates", "px:float", partition="EURUSD")
    assert symbols(_fields(plan, "fx_backfill.q")["partition"]) == ("EURUSD",)


def test_a_partition_that_is_not_a_symbol_is_refused():
    with pytest.raises(UqsError, match="must be a q symbol"):
        backfill.bounded_worker("fx", "fx_rates", "px:float", partition="EUR/USD")


def test_check_scaffolds_a_throwing_quality_check_and_declares_it():
    plan = backfill.bounded_worker("fx", "fx_rates", "px:float", check=True)
    assert _fields(plan, "fx_backfill.q")["check"] == ".qpipe.job.fx_backfill.quality_check"
    assert '\'"fx_backfill.quality_check: not implemented"' in _body(plan, "fx_backfill.q")
    assert any("quality_check" in n and "every window fails" in n for n in plan.notes)


def test_without_check_there_is_no_check():
    plan = backfill.bounded_worker("fx", "fx_rates", "px:float")
    assert "check" not in _fields(plan, "fx_backfill.q")
    assert any("--check" in n for n in plan.notes)


def test_a_second_worker_needs_another_partition(tmp_path):
    """Coverage is kept per (dataset, partition): the same pair twice does not load."""
    workers = tmp_path / "src" / "etl" / "workers"
    workers.mkdir(parents=True)
    (workers / "w.q").write_text(
        ".qetl.job.bounded.define[`w;`source`dataset`width`transform`partition!"
        "(`s;`fx;1D;`s_passthrough;`EURUSD)];\n"
    )
    assert create._workers_filling(tmp_path, "fx", "EURUSD") == ["w"]
    assert create._workers_filling(tmp_path, "fx", "GBPUSD") == []
    assert create._workers_filling(tmp_path, "fx") == []


# ------------------------------------------------------------------ profiles


def _applied(actions) -> str:
    text = PROFILES_TEXT
    for action in actions:
        text = profile.apply(text, action)
    compile(text, str(profile.PROFILES_FILE), "exec")
    return text


def test_a_profile_takes_the_process_and_the_module_still_compiles():
    actions, notes = profile.membership(
        "pulse1", profile="crypto", unprofiled=None, start_with_all=False, known_profiles=KNOWN
    )
    assert not notes
    assert '"crypto": ("cryptomock1", "pulse1"),' in _applied(actions)


def test_start_with_all_puts_it_in_default_and_says_what_else_to_check():
    actions, notes = profile.membership(
        "pulse1", profile=None, unprofiled=None, start_with_all=True, known_profiles=KNOWN
    )
    default = next(ln for ln in _applied(actions).splitlines() if ln.startswith('    "default": ('))
    assert '"pulse1"),' in default
    assert any("always_on" in n for n in notes)


def test_an_exemption_is_written_with_its_reason():
    actions, _ = profile.membership(
        "tapx1",
        profile=None,
        unprofiled="a diagnostic started by hand against whichever table is being looked at",
        start_with_all=False,
        known_profiles=KNOWN,
    )
    text = _applied(actions)
    assert '    "tapx1": (\n' in text
    assert "a diagnostic started by hand" in text


def test_no_choice_leaves_a_note_and_writes_nothing():
    actions, notes = profile.membership(
        "p1", profile=None, unprofiled=None, start_with_all=False, known_profiles=KNOWN
    )
    assert not actions
    assert any("--profile" in n and "--unprofiled" in n for n in notes)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"profile": "nope"}, "is not a profile"),
        ({"profile": "fx", "unprofiled": "why"}, "pick one"),
        ({"profile": "default"}, "needs --start-with-all"),
        ({"unprofiled": "  "}, "needs the reason"),
    ],
)
def test_a_profile_choice_that_cannot_stand_is_refused(kwargs, message):
    args = {"profile": None, "unprofiled": None, **kwargs}
    with pytest.raises(UqsError, match=message):
        profile.membership("p1", start_with_all=False, known_profiles=KNOWN, **args)


def test_a_process_already_in_the_profile_is_refused():
    actions, _ = profile.membership(
        "cryptomock1", profile="crypto", unprofiled=None, start_with_all=False, known_profiles=KNOWN
    )
    with pytest.raises(UqsError, match="already in the crypto profile"):
        _applied(actions)


def test_a_normalizer_takes_a_profile_too():
    quote = "quote:([]time:`timestamp$(); sym:`g#`symbol$(); bid:`float$())"
    plan = normalizer(
        "ticks",
        ["quote"],
        jobs.parse_columns("sym:symbol"),
        {"quote": definition_columns(quote)},
        known_tables={"quote"},
        profile="fx",
        known_profiles=KNOWN,
    )
    assert any(a.anchor == "profile:fx" and a.body == "ticks1" for a in plan.actions)


# ----------------------------------------------------------------- the command


@pytest.mark.parametrize(
    ("argv", "option"),
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
                "--period",
                "0D00:00:01",
            ],
            "--period",
        ),
        (["x", "--publishes", "x", "--columns", "a:float", "--partition", "EURUSD"], "--partition"),
        (["x", "--publishes", "x", "--columns", "a:float", "--check"], "--check"),
        (
            [
                "x",
                "--kind",
                "backfill",
                "--dataset",
                "x",
                "--columns",
                "a:float",
                "--profile",
                "fx",
            ],
            "--profile",
        ),
    ],
)
def test_an_option_for_another_kind_is_refused_by_name(argv, option, monkeypatch):
    refused: list[str] = []

    def record(exc: Exception) -> None:
        refused.append(str(exc))
        raise SystemExit(1)

    monkeypatch.setattr(create, "_die", record)
    result = runner.invoke(cli.app, ["new-job", *argv, "--dry-run"])
    assert result.exit_code == 1
    assert refused and refused[0].startswith(f"{option} does not apply to --kind")
