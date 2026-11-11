"""Tests for `uqs data replay` and how it aims TorQ's tickerlogreplay.

The point of the command is that it reads the RUNNING processes rather than
the configuration that describes them, so most of what is asserted here is
what it takes off a command line and what it refuses to guess. The flags it
builds reach q on a start line torq.sh `eval`s, so what is refused there is as
much the point as what is built - the same reasoning as test_backfill.py.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from uqs import cli
from uqs import paths as stack_paths
from uqs.cli import replay as cli_replay
from uqs.paths import UqsError
from uqs.stack import replay, runtime

runner = CliRunner()

TPLOGS = "/data/uqf/tplogs"
SCHEMA = "/data/uqf/database.q"
HDB = "/data/uqf/hdb"

#: A real start line, shortened: the switches this module reads, in the order
#: torq.sh writes them.
STP1 = (
    "q /repo/lib/torq/torq.q -stackid 6050 -proctype segmentedtickerplant -procname stp1 "
    f"-localtime 1 -g 0 -load /repo/lib/torq/code/processes/segmentedtickerplant.q "
    f"-schemafile {SCHEMA} -tplogdir {TPLOGS} -procfile /data/uqf/process.csv"
)

#: The chained plant as this tree actually runs it: no log of its own.
SCTP1 = (
    "q /repo/lib/torq/torq.q -stackid 6050 -proctype segmentedchainedtickerplant "
    "-procname sctp1 -localtime 1 -g 0 -parentproctype segmentedtickerplant"
)

HDB1 = f"q /repo/lib/torq/torq.q -stackid 6050 -proctype hdb -procname hdb1 -T 60 -load {HDB}"


@pytest.fixture
def fleet(monkeypatch):
    """Put a fleet on the machine, as `ps` would report it."""

    def running(*commands):
        monkeypatch.setattr(
            replay, "_command_lines", lambda timeout=None: list(enumerate(commands, start=100))
        )

    return running


def test_a_switch_is_the_one_word_after_it():
    assert replay._switch(STP1, "tplogdir") == TPLOGS
    assert replay._switch(STP1, "procname") == "stp1"


def test_a_switch_that_is_not_there_is_none_not_an_error():
    """A chained plant has no -tplogdir at all, and that is the normal case
    rather than a malformed command line."""
    assert replay._switch(SCTP1, "tplogdir") is None


def test_a_flag_with_no_value_does_not_swallow_the_next_switch():
    """`-g 0 -load x` must not make `-g`'s value `-load`. q's own .Q.opt
    stops at the next switch too."""
    assert replay._switch("q -debug -procname stp1", "debug") is None


def test_a_running_plant_is_read_off_its_own_start_line(fleet):
    fleet(STP1, HDB1)
    (plant,) = replay.running_plants()
    assert plant.procname == "stp1"
    assert plant.base_port == 6050
    assert plant.tplogdir == Path(TPLOGS)
    assert plant.schemafile == Path(SCHEMA)


def test_a_plant_writing_no_log_is_not_offered_as_a_choice(fleet):
    """sctp1 is a tickerplant by proctype and has nothing to replay from, so
    listing it would only produce a refusal one step later."""
    fleet(STP1, SCTP1, HDB1)
    assert [p.procname for p in replay.running_plants()] == ["stp1"]


def test_no_plant_running_is_refused_with_what_to_do_instead(fleet):
    fleet(HDB1)
    with pytest.raises(UqsError, match="no tickerplant is running"):
        replay.resolve_plant()


def test_two_plants_with_logs_are_refused_rather_than_ranked(fleet):
    """Two plants write two different logs. Picking one by sort order would
    replay the wrong day into the HDB as readily as the right one."""
    fleet(STP1, STP1.replace("stp1", "stp2"))
    with pytest.raises(UqsError, match="several tickerplants are running .stp1, stp2."):
        replay.resolve_plant()


def test_naming_a_plant_that_is_not_running_says_which_are(fleet):
    fleet(STP1, SCTP1)
    with pytest.raises(UqsError, match="named 'sctp1'.*running plants: stp1"):
        replay.resolve_plant("sctp1")


def test_the_hdb_is_the_one_on_this_stack(fleet):
    """A second fleet on another base port has its own database, and writing
    a replay into it would be the same wrong-target mistake."""
    fleet(STP1, HDB1.replace("6050", "7000").replace(HDB, "/other/hdb"), HDB1)
    assert replay.running_hdb_dir(6050) == Path(HDB)
    assert replay.running_hdb_dir(7000) == Path("/other/hdb")


def test_no_hdb_on_this_stack_is_refused(fleet):
    fleet(STP1)
    with pytest.raises(UqsError, match="no hdb process is running under stackid 6050"):
        replay.running_hdb_dir(6050)


@pytest.fixture
def logs(tmp_path):
    """A plant whose log directory holds three days, and another plant's."""
    tplogdir = tmp_path / "tplogs"
    for name in ("stp1_2026.09.23", "stp1_2026.09.24", "stp1_2026.09.25", "sctp1_2026.09.26"):
        (tplogdir / name).mkdir(parents=True)
    (tplogdir / "stp1_notadate").mkdir()
    return replay.RunningPlant(
        procname="stp1",
        proctype="segmentedtickerplant",
        pid=100,
        base_port=6050,
        tplogdir=tplogdir,
        schemafile=Path(SCHEMA),
    )


def test_the_default_day_is_the_plants_newest(logs):
    """Newest rather than today's: a stack started yesterday and left running
    has no directory for today until the first message of the day lands."""
    assert replay.resolve_log_dir(logs).name == "stp1_2026.09.25"


def test_another_plants_log_in_the_same_directory_is_not_a_candidate(logs):
    """One tplogdir holds every plant's logs side by side, and sctp1's sorts
    last."""
    assert [d.name for d in replay.log_dirs(logs)] == [
        "stp1_2026.09.23",
        "stp1_2026.09.24",
        "stp1_2026.09.25",
    ]


def test_a_day_that_was_never_logged_is_refused_with_the_days_that_were(logs):
    with pytest.raises(UqsError, match="no log for 2020.01.01 - it has: 2026.09.23"):
        replay.resolve_log_dir(logs, "2020.01.01")


def test_a_tplogdir_that_does_not_exist_names_the_plant_that_claims_it(logs):
    missing = replace(logs, tplogdir=Path("/nope"))
    with pytest.raises(UqsError, match="stp1 is running with -tplogdir /nope"):
        replay.log_dirs(missing)


@pytest.mark.parametrize(
    "given",
    [
        ["--date", "2026.09.23"],
        ["--date", "2026-09-23"],
        ["--from", "2026-09-23", "--to", "2026-09-24"],
        ["--from", "2026.09.23D00:00", "--to", "2026.09.24"],
    ],
)
def test_the_day_is_given_as_any_range_that_is_one_whole_day(monkeypatch, given):
    """The log directories are named the q way; the range is the one every
    command takes (#1735)."""
    seen = {}

    def plan(**kw):
        seen.update(kw)
        raise UqsError("stop here")

    monkeypatch.setattr(replay, "plan", plan)
    runner.invoke(cli.app, ["data", "replay", *given])
    assert seen["date"] == "2026.09.23"


def test_no_day_is_the_plants_newest(monkeypatch):
    seen = {}

    def plan(**kw):
        seen.update(kw)
        raise UqsError("stop here")

    monkeypatch.setattr(replay, "plan", plan)
    runner.invoke(cli.app, ["data", "replay"])
    assert seen["date"] is None


@pytest.mark.parametrize(
    "given",
    [
        ["--from", "2026-09-23", "--to", "2026-09-25"],
        ["--from", "2026-09-23T06:00", "--to", "2026-09-24T06:00"],
    ],
)
def test_a_range_that_is_not_one_whole_day_is_refused(monkeypatch, given):
    """`_die` logs through loguru, which CliRunner cannot capture: the refusal
    is read off the error it was handed."""
    refused = []
    monkeypatch.setattr(replay, "plan", lambda **kw: refused.append("planned"))

    def record(exc: Exception) -> None:
        refused.append(str(exc))
        raise SystemExit(1)

    monkeypatch.setattr(cli_replay, "_die", record)
    runner.invoke(cli.app, ["data", "replay", *given])
    assert refused and refused[0].startswith("data replay replays one whole day")


def test_the_flags_are_the_switches_tickerlogreplay_reads():
    """Plain paths, no leading colon: the script hsyms hdbdir and tplogdir
    itself, and loads schemafile with `system "l ",string ...`."""
    assert replay.replay_flags(Path(TPLOGS), Path(HDB), Path(SCHEMA)) == [
        "-.replay.tplogdir",
        TPLOGS,
        "-.replay.hdbdir",
        HDB,
        "-.replay.schemafile",
        SCHEMA,
    ]


def test_a_whole_log_replay_passes_no_tablelist_at_all():
    """TorQ's default IS `all`, and a switch that restates a default is one
    more thing that can disagree with it."""
    assert "-.replay.tablelist" not in replay.replay_flags(Path(TPLOGS), Path(HDB), Path(SCHEMA))


def test_named_tables_are_each_their_own_word():
    flags = replay.replay_flags(Path(TPLOGS), Path(HDB), Path(SCHEMA), ["quote", "trade"])
    assert flags[-3:] == ["-.replay.tablelist", "quote", "trade"]


@pytest.mark.parametrize("bad", ["/data/$(id)", "/data/a;rm -rf x"])
def test_a_path_a_shell_would_interpret_is_refused(bad):
    """torq.sh builds the start line into a string and `eval`s it."""
    with pytest.raises(UqsError, match="may contain only letters"):
        replay.replay_flags(Path(bad), Path(HDB), Path(SCHEMA))


def test_a_path_torq_sh_would_read_as_its_own_flag_is_refused():
    """torq.sh greps every argument for `csv` and `extras`, and a path is
    quite capable of holding either."""
    with pytest.raises(UqsError, match="'csv' or 'extras'"):
        replay.replay_flags(Path("/data/csvlogs"), Path(HDB), Path(SCHEMA))


def test_the_plan_comes_off_the_running_processes(fleet, logs, monkeypatch):
    fleet(STP1, HDB1)
    monkeypatch.setattr(replay, "resolve_plant", lambda *a, **k: logs)
    plan = replay.plan()
    assert plan.log_dir.name == "stp1_2026.09.25"
    assert (plan.hdb_dir, plan.schema_file, plan.base_port) == (Path(HDB), Path(SCHEMA), 6050)


def test_an_option_that_is_given_wins_over_what_is_running(fleet, logs, monkeypatch):
    """--port is the one that matters: it exists to be overridden, not to be
    the only way to say which stack this is."""
    fleet(STP1, HDB1)
    monkeypatch.setattr(replay, "resolve_plant", lambda *a, **k: logs)
    plan = replay.plan(hdb_dir=Path("/elsewhere/hdb"), base_port=6050)
    assert plan.hdb_dir == Path("/elsewhere/hdb")


def test_a_defaulted_base_port_must_be_the_running_plants(fleet, logs, monkeypatch):
    """`--base-port` defaults to the runtime's own, as on every command
    (#1855); a plant under another stackid is refused, naming the one to give,
    rather than replayed under a port nobody chose."""
    fleet(STP1, HDB1)
    monkeypatch.setattr(replay, "resolve_plant", lambda *a, **k: logs)
    assert replay.plan(base_port=6050, base_port_given=False).base_port == 6050
    with pytest.raises(UqsError, match="runs under base port 6050, not this runtime's 6150"):
        replay.plan(base_port=6150, base_port_given=False)


def test_supplying_everything_consults_no_process_at_all(monkeypatch):
    """Which is what makes the command usable on a stack that has already
    been stopped."""

    def explode(timeout=None):
        raise AssertionError("the plan asked the machine what is running")

    monkeypatch.setattr(replay, "_command_lines", explode)
    plan = replay.plan(
        log_dir=Path(TPLOGS), hdb_dir=Path(HDB), schema_file=Path(SCHEMA), base_port=6050
    )
    assert plan.plant is None
    assert plan.rows()[0] == ("log", TPLOGS, "--tplogdir")


def test_a_day_cannot_be_picked_out_of_a_directory_that_was_named_outright():
    with pytest.raises(UqsError, match="cannot be used with --tplogdir"):
        replay.plan(
            log_dir=Path(TPLOGS),
            hdb_dir=Path(HDB),
            schema_file=Path(SCHEMA),
            base_port=6050,
            date="2026.09.23",
        )


def test_a_plant_with_no_schemafile_is_refused_rather_than_replayed_blind(fleet):
    """The log's tables were declared somewhere. Replaying under a schema
    this command invented would write down a different shape."""
    fleet(STP1.replace(f"-schemafile {SCHEMA} ", ""), HDB1)
    with pytest.raises(UqsError, match="running without -schemafile"):
        replay.plan(log_dir=Path(TPLOGS))


def test_start_passes_the_flags_through_torq_sh_extras(monkeypatch):
    """`-extras` is torq.sh's own way to add to one process's start line, so
    tpreplay1 starts the way every other process does - the same channel
    `uqs job backfill` uses."""
    seen = {}
    monkeypatch.setattr(
        runtime,
        "run_torq_sh",
        lambda paths, args, **kw: seen.update(args=args, kw=kw),
    )
    plan = replay.ReplayPlan(
        log_dir=Path(TPLOGS),
        hdb_dir=Path(HDB),
        schema_file=Path(SCHEMA),
        base_port=6050,
        tables=(),
        plant=None,
    )
    replay.start(stack_paths.default_paths(), plan)
    assert seen["args"][:3] == ["start", "tpreplay1", "-extras"]
    assert seen["args"][3:] == plan.flags()
    assert seen["kw"]["base_port"] == 6050


AIMED = ["--base-port", "6050", "--tplogdir", TPLOGS, "--hdb", HDB, "--schema-file", SCHEMA]


def _explode(*a, **k):
    raise AssertionError("the replay was started")


def test_without_apply_it_prints_the_plan_and_starts_nothing(monkeypatch):
    """#2235: a replay empties what it writes, so it is a dry run by default."""
    monkeypatch.setattr(replay, "start", _explode)
    result = runner.invoke(cli.app, ["data", "replay", *AIMED])
    assert result.exit_code == 0, result.output
    said = result.output.replace("\n", "")
    assert "would start: tpreplay1 -extras" in said
    assert "dry run: nothing was changed. Run it again with --apply" in said


def test_dry_run_is_refused_naming_apply(monkeypatch, log_text):
    monkeypatch.setattr(replay, "start", _explode)
    result = runner.invoke(cli.app, ["data", "replay", "--dry-run", *AIMED])
    assert result.exit_code == 2
    assert "`data replay` is a dry run unless --apply: drop --dry-run" in log_text()


def test_apply_asks_and_without_a_terminal_refuses_naming_yes(monkeypatch, log_text):
    monkeypatch.setattr(replay, "start", _explode)
    result = runner.invoke(cli.app, ["data", "replay", *AIMED, "--apply"])
    assert result.exit_code == 1
    assert "emptying them first in the partitions it touches? - there is no terminal" in (
        log_text()
    )


def test_apply_a_no_starts_nothing(monkeypatch, terminal):
    monkeypatch.setattr(replay, "start", _explode)
    result = runner.invoke(cli.app, ["data", "replay", *AIMED, "--apply"], input="n\n")
    assert result.exit_code == 1 and "Replay every table in the log into" in result.output


def test_the_command_exits_with_what_the_replay_exited_with(monkeypatch):
    monkeypatch.setattr(replay, "start", lambda *a, **k: type("Completed", (), {"returncode": 3})())
    argv = ["data", "replay", *AIMED, "--apply", "--yes"]
    assert runner.invoke(cli.app, argv).exit_code == 3


def test_json_is_the_plan_and_without_yes_apply_is_refused(monkeypatch, terminal):
    """#2229: --json never asks, even at a terminal that would say y."""
    import json

    monkeypatch.setattr(replay, "start", _explode)
    plan = json.loads(runner.invoke(cli.app, ["data", "replay", *AIMED, "--json"]).stdout)
    assert plan["ok"] and not plan["applied"] and plan["start"].startswith("tpreplay1 -extras")
    argv = ["data", "replay", *AIMED, "--apply", "--json"]
    monkeypatch.setattr("sys.argv", ["uqs", *argv])  # json_requested reads it
    refused = runner.invoke(cli.app, argv, input="y\n")
    assert refused.exit_code == 1
    doc = json.loads(refused.stdout)
    assert doc["ok"] is False and "cannot ask for confirmation: add --yes to replay" in doc["error"]


def test_a_refusal_exits_one_rather_than_raising(monkeypatch):
    monkeypatch.setattr(replay, "_command_lines", lambda timeout=None: [])
    result = runner.invoke(cli.app, ["data", "replay"])
    assert result.exit_code == 1


# ---------------------------------------------------------------- the live day (#2196)


def _live_stack(tmp_path):
    """A plant mid-day: logs for two past days and today, a wdb whose save
    directory holds today only (end of day has moved the older days out and
    opened nothing later), and an HDB dir. Returns (plant, hdb, wdb, days)."""
    import datetime

    today = datetime.datetime.now(datetime.UTC).date()
    days = [(today - datetime.timedelta(days=n)).strftime("%Y.%m.%d") for n in (2, 1, 0)]
    tplogdir = tmp_path / "tplogs"
    for day in days:
        (tplogdir / f"stp1_{day}").mkdir(parents=True)
    wdb = tmp_path / "wdbhdb"
    (wdb / days[2]).mkdir(parents=True)
    hdb = tmp_path / "hdb"
    (hdb / days[0]).mkdir(parents=True)
    plant = replay.RunningPlant("stp1", "segmentedtickerplant", 100, 6050, tplogdir, Path(SCHEMA))
    return plant, hdb, wdb, days


def _plan(plant, hdb, wdb, monkeypatch, **kw):
    monkeypatch.setattr(replay, "resolve_plant", lambda *a, **k: plant)
    return replay.plan(hdb_dir=hdb, schema_file=Path(SCHEMA), base_port=6050, wdb_dir=wdb, **kw)


def test_the_default_day_is_never_the_live_day(tmp_path, monkeypatch):
    """With a wdb holding today, the newest log is today's; the default must be
    the newest day end of day has handed over - here, the day before."""
    plant, hdb, wdb, days = _live_stack(tmp_path)
    planned = _plan(plant, hdb, wdb, monkeypatch)
    assert planned.log_dir.name == f"stp1_{days[1]}"


def test_an_explicit_live_day_is_refused_with_the_fences_error(tmp_path, monkeypatch):
    plant, hdb, wdb, days = _live_stack(tmp_path)
    with pytest.raises(UqsError, match="today and later belong to the tickerplant and end-of-day"):
        _plan(plant, hdb, wdb, monkeypatch, date=days[2])


def test_a_day_the_wdb_still_holds_is_refused(tmp_path, monkeypatch):
    """Yesterday, with end of day not yet run: the wdb's directory still holds it."""
    plant, hdb, wdb, days = _live_stack(tmp_path)
    (wdb / days[1]).mkdir()
    with pytest.raises(UqsError, match="before TorQ's end of day has moved that date"):
        _plan(plant, hdb, wdb, monkeypatch, date=days[1])
    assert _plan(plant, hdb, wdb, monkeypatch).log_dir.name == f"stp1_{days[0]}"


def test_a_handed_over_day_replays(tmp_path, monkeypatch):
    plant, hdb, wdb, days = _live_stack(tmp_path)
    planned = _plan(plant, hdb, wdb, monkeypatch, date=days[0])
    assert planned.log_dir.name == f"stp1_{days[0]}"


def test_only_the_live_day_logged_has_no_default(tmp_path, monkeypatch):
    plant, hdb, wdb, days = _live_stack(tmp_path)
    for day in days[:2]:
        (plant.tplogdir / f"stp1_{day}").rmdir()
    with pytest.raises(UqsError, match="no log for a day end of day has handed over"):
        _plan(plant, hdb, wdb, monkeypatch)


def test_a_named_plant_log_directory_of_today_is_refused(tmp_path, monkeypatch):
    plant, hdb, wdb, days = _live_stack(tmp_path)
    with pytest.raises(UqsError, match="today and later belong"):
        replay.plan(
            log_dir=plant.tplogdir / f"stp1_{days[2]}",
            hdb_dir=hdb,
            schema_file=Path(SCHEMA),
            base_port=6050,
            wdb_dir=wdb,
        )
