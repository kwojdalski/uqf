"""`uqs cron` (#2987): the cron matcher and its zone guard, the crontab block and its
annotations, and what `add` refuses. The crontab is always a fake (conftest's
`no_real_crontab`, and `fake_crontab` here): never this machine's."""

from __future__ import annotations

import json
import sys
import textwrap
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from typer.testing import CliRunner

from uqs import cli
from uqs.cron import check, crontab, entries, guard, runs, schedule
from uqs.paths import UqsError

runner = CliRunner()
LONDON = "Europe/London"


def at(text: str, tz: str = "UTC") -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=ZoneInfo(tz))


# ---------------------------------------------------------------- the matcher


def test_fields_steps_ranges_lists_and_names():
    c = schedule.parse("*/15 6-8 * jan-mar mon-fri")
    assert c.minutes == {0, 15, 30, 45} and c.hours == {6, 7, 8}
    assert c.months == {1, 2, 3} and c.weekdays == {1, 2, 3, 4, 5}
    assert schedule.parse("0 0 * * 7").weekdays == {0}  # 7 is Sunday
    assert schedule.parse("5-20/5 * * * *").minutes == {5, 10, 15, 20}
    assert schedule.parse("1,2,40 * * * *").minutes == {1, 2, 40}


@pytest.mark.parametrize(
    "text",
    ["* * * *", "60 * * * *", "* 24 * * *", "* * 0 * *", "*/0 * * * *", "5-1 * * * *", "@daily"],
)
def test_a_bad_expression_is_refused(text):
    with pytest.raises(schedule.CronError):
        schedule.parse(text)


def test_day_of_month_and_day_of_week_are_ored_when_both_are_restricted():
    c = schedule.parse("0 9 1 * mon")
    assert c.matches(at("2026-12-01T09:00"))  # a Tuesday, the 1st
    assert c.matches(at("2026-12-07T09:00"))  # a Monday, the 7th
    assert not c.matches(at("2026-12-08T09:00"))  # neither


def test_a_star_day_field_makes_the_days_anded():
    c = schedule.parse("0 9 * * mon")
    assert c.matches(at("2026-12-07T09:00")) and not c.matches(at("2026-12-01T09:00"))
    c = schedule.parse("0 9 1 * *")
    assert c.matches(at("2026-12-01T09:00")) and not c.matches(at("2026-12-07T09:00"))


def test_next_runs_in_the_entrys_zone_and_never_for_an_impossible_date():
    c = schedule.parse("10 6 * * 1-5")
    nxt = schedule.next_runs(c, "UTC", at("2026-11-27T07:00"), 2)  # a Friday
    assert [t.isoformat() for t in nxt] == [
        "2026-11-30T06:10:00+00:00",
        "2026-12-01T06:10:00+00:00",
    ]
    assert schedule.next_runs(schedule.parse("0 0 30 2 *"), "UTC", at("2026-01-01T00:00")) == []


def test_the_zone_guard_across_a_dst_change():
    """06:10 London is 06:10 UTC in winter and 05:10 UTC in summer: due follows the clock."""
    c = schedule.parse("10 6 * * *")
    winter, summer = at("2026-03-28T06:10"), at("2026-03-30T05:10")  # the clocks go on the 29th
    assert schedule.due(c, LONDON, winter) and schedule.due(c, LONDON, summer)
    assert not schedule.due(c, LONDON, at("2026-03-30T06:10"))
    runs_ = schedule.next_runs(c, LONDON, at("2026-03-28T07:00"), 2)
    assert [t.astimezone(UTC).hour for t in runs_] == [5, 5]
    # a wall time the clocks skip never fires that day
    skipped = schedule.parse("30 1 * * *")
    days = [t.day for t in schedule.next_runs(skipped, LONDON, at("2026-03-28T02:00"), 2)]
    assert days == [30, 31]


def test_the_crontab_line_is_the_entrys_own_only_in_the_hosts_zone():
    c, now = schedule.parse("10 6 * * 1-5"), at("2026-11-27T00:00")
    assert schedule.line_schedule(c, "UTC", "Etc/UTC", now) == "10 6 * * 1-5"
    assert schedule.line_schedule(c, LONDON, "UTC", now) == "10 * * * *"
    assert schedule.line_schedule(c, "Asia/Kolkata", "UTC", now) == "* * * * *"
    assert schedule.line_schedule(c, "UTC", None, now) == "* * * * *"  # host zone unknown


def test_host_zone_reads_tz():
    assert schedule.host_zone({"TZ": ":Europe/London"}) == LONDON
    assert schedule.host_zone({"TZ": "Not/AZone"}) is None


# ------------------------------------------------------------ the entries file


@pytest.fixture
def cron_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("UQS_ATTACH_DIR", str(tmp_path / "attachments"))
    return tmp_path / "cron"


def test_entries_live_beside_the_attachments_and_round_trip(cron_dir):
    assert entries.directory() == cron_dir
    e = entries.entry("deals-daily", "10  6 * * 1-5", "job backfill x --last 3d")
    entries.save({e.name: e})
    text = entries.path().read_text()
    assert 'tz = "UTC"' in text  # always written
    assert entries.load() == {"deals-daily": e}


def test_an_entry_is_refused_with_why(cron_dir):
    for bad in [("Bad", "* * * * *", "job"), ("ok", "* * *", "job"), ("ok", "* * * * *", "")]:
        with pytest.raises(UqsError):
            entries.entry(*bad)
    with pytest.raises(UqsError, match="time zone"):
        entries.entry("ok", "* * * * *", "job run x --once", "Mars/Olympus")
    entries.path().parent.mkdir(parents=True)
    entries.path().write_text('[x]\ncron = "* * * * *"\nrun = "job"\nextra = 1\n')
    with pytest.raises(UqsError, match="nothing else"):
        entries.load()


# --------------------------------------------------------------- the crontab


def ctx(host="UTC") -> crontab.Context:
    return crontab.Context(
        repo=Path("/abs/repo"),
        uqs="/abs/bin/uqs",
        schedules=Path("/abs/data/cron/cron.toml"),
        log_dir=Path("/abs/data/cron"),
        host_tz=host,
        now=at("2026-11-27T00:00"),
    )


DEALS = entries.entry(
    "deals-daily", "10 6 * * 1-5", "job backfill demo_deals_backfill --last 3d --source-version v1"
)
OTHER = "# my own\n0 3 * * * /usr/bin/backup --all\nMAILTO=me\n"


def test_the_block_annotates_each_line_and_keeps_the_rest():
    planned = crontab.plan(OTHER, {DEALS.name: DEALS}, ctx())
    lines = planned.after.splitlines()
    assert lines[:3] == OTHER.splitlines()
    assert lines[3] == crontab.BEGIN and lines[-1] == crontab.END
    note = next(x for x in lines if x.startswith(crontab.ANNOTATION))
    assert note == (
        "# uqs-cron: entry=deals-daily managed-by=uqs schedules=/abs/data/cron/cron.toml "
        f"repo=/abs/repo tz=UTC sha={crontab.entry_sha(DEALS)}"
    )
    assert (
        "#   10 6 * * 1-5 UTC (06:10, Mon-Fri, UTC); log: /abs/data/cron/deals-daily.log" in lines
    )
    assert lines[-2] == (
        "10 6 * * 1-5 cd /abs/repo && UQS_CRON_ENTRY=deals-daily /abs/bin/uqs cron run deals-daily"
    )
    [a] = crontab.annotations_of(lines)
    assert a.entry == "deals-daily" and a.fields["repo"] == "/abs/repo" and a.line == lines[-2]


def test_another_zone_fires_hourly_and_says_so():
    e = entries.entry("ldn", "10 6 * * *", "job run x --once", LONDON)
    block = crontab.render({e.name: e}, ctx("UTC"))
    assert block[-2].startswith("10 * * * * cd /abs/repo")
    assert "fires 10 * * * * on this host" in block[-3]


def test_install_round_trips_and_a_reinstall_writes_nothing():
    first = crontab.plan(OTHER, {DEALS.name: DEALS}, ctx())
    assert first.changed
    again = crontab.plan(first.after, {DEALS.name: DEALS}, ctx())
    assert not again.changed
    gone = crontab.plan(first.after, {}, ctx())
    assert gone.after == OTHER  # removing the last entry removes the block, nothing else
    assert not crontab.plan(OTHER, {}, ctx()).changed


def test_a_hand_edit_inside_the_block_is_refused():
    installed = crontab.plan(OTHER, {DEALS.name: DEALS}, ctx()).after
    edited = installed.replace("10 6 * * 1-5 cd", "15 6 * * 1-5 cd")
    with pytest.raises(crontab.CrontabError, match="edited by hand"):
        crontab.plan(edited, {DEALS.name: DEALS}, ctx())
    assert [f.kind for f in crontab.findings(edited, {DEALS.name: DEALS})] == ["edited"]


def test_findings_report_unknown_stale_missing_and_orphan_lines():
    installed = crontab.plan(OTHER, {DEALS.name: DEALS}, ctx()).after
    changed = entries.entry(DEALS.name, "20 6 * * 1-5", DEALS.run)
    new = entries.entry("new-one", "0 1 * * *", "job run x --once")
    orphan = (
        "5 5 * * * cd /old && /old/uqs cron run old-entry\n# 5 5 * * * uqs cron run commented\n"
    )
    found = crontab.findings(orphan + installed, {changed.name: changed, new.name: new})
    assert {(f.kind, f.entry) for f in found} == {
        ("stale", "deals-daily"),
        ("missing", "new-one"),
        ("orphan", ""),
    }
    assert [f.line for f in found if f.kind == "orphan"] == [orphan.splitlines()[0]]
    unknown = crontab.findings(installed, {})
    assert [(f.kind, f.entry) for f in unknown] == [("unknown", "deals-daily")]


@pytest.fixture
def fake_crontab(tmp_path, monkeypatch):
    """A crontab program that keeps the table in a file: `-l` prints it, `-` stores stdin."""
    store = tmp_path / "crontab.txt"
    script = tmp_path / "fake_crontab.py"
    script.write_text(
        textwrap.dedent(f"""\
        import pathlib, sys
        store = pathlib.Path({str(store)!r})
        if sys.argv[1] == "-l":
            if not store.exists():
                sys.stderr.write("crontab: no crontab for you\\n"); sys.exit(1)
            sys.stdout.write(store.read_text())
        else:
            store.write_text(sys.stdin.read())
        """)
    )
    monkeypatch.setattr(crontab, "COMMAND", (sys.executable, str(script)))
    return store


def test_the_crontab_program_is_injected(fake_crontab):
    assert crontab.read() == ""  # no crontab yet
    crontab.write(OTHER)
    assert crontab.read() == OTHER == fake_crontab.read_text()


def test_cli_install_dry_run_then_install_then_nothing(fake_crontab, cron_dir):
    fake_crontab.write_text(OTHER)
    entries.save({DEALS.name: DEALS})
    dry = runner.invoke(cli.app, ["cron", "install", "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert "+" + crontab.BEGIN in dry.output and fake_crontab.read_text() == OTHER
    done = runner.invoke(cli.app, ["cron", "install"])
    assert done.exit_code == 0, done.output
    assert fake_crontab.read_text().startswith(OTHER) and "cron run deals-daily" in (
        fake_crontab.read_text()
    )
    again = runner.invoke(cli.app, ["cron", "install", "--json"])
    assert json.loads(again.output)["changed"] is False


def test_the_test_suite_never_reaches_the_real_crontab():
    assert crontab.COMMAND == ("false",)
    with pytest.raises(crontab.CrontabError):
        crontab.read()


# --------------------------------------------------------------- what add refuses

ROWS = [
    {"job": "deals_backfill", "kind": "backfill", "procname": "deals_bf1"},
    {"job": "deals_feed", "kind": "streaming", "procname": "deals1"},
    {"job": "rebuild", "kind": "reaction", "procname": "deals_bf1"},
]


@pytest.mark.parametrize(
    ("parsed", "said"),
    [
        (check.Parsed(("start",)), "cannot be scheduled"),
        (check.Parsed(check.BACKFILL, "deals_feed"), "streaming job"),
        (check.Parsed(check.RUN, "rebuild", given=frozenset({"--once"})), "reaction"),
        (check.Parsed(check.RUN, "deals_feed"), "--once"),
        (check.Parsed(check.BACKFILL, "rebuild"), "reaction"),
        (check.Parsed(check.BACKFILL, "nope"), "not a job"),
        (check.Parsed(check.BACKFILL, "deals_backfill", "ghost"), "not an attachment"),
        (check.Parsed(check.BACKFILL, "deals_backfill", given=frozenset({"--follow"})), "--follow"),
    ],
)
def test_add_refuses_what_cron_must_not_run(parsed, said):
    with pytest.raises(UqsError, match=said):
        check.refuse_unschedulable(parsed, ROWS, ["att-one"])


def test_add_admits_a_backfill_and_one_unit():
    check.refuse_unschedulable(
        check.Parsed(check.BACKFILL, "deals_backfill", "att-one"), ROWS, ["att-one"]
    )
    check.refuse_unschedulable(
        check.Parsed(check.RUN, "deals1", given=frozenset({"--once"})), ROWS, []
    )


def test_add_refuses_a_time_inside_the_end_of_day_fence():
    e = entries.entry("eod", "55 23 * * *", "job backfill deals_backfill --attachment a --last 1d")
    plan = {"coordination": {"next_roll": "2026-11-28T00:00:00+00:00", "eod_guard_minutes": 30}}
    with pytest.raises(UqsError, match="end of day"):
        check.refuse_in_fence(e, plan, at("2026-11-27T12:00"))
    ok = entries.entry("ok", "0 6 * * *", e.run)
    check.refuse_in_fence(ok, plan, at("2026-11-27T12:00"))
    after = entries.entry("after", "10 0 * * *", e.run)  # 10 minutes after the roll
    with pytest.raises(UqsError, match="end of day"):
        check.refuse_in_fence(after, plan, at("2026-11-27T12:00"))


def test_add_runs_the_plan_and_refuses_when_it_is_refused():
    e = entries.entry("d", "0 6 * * *", "job backfill deals_backfill --last 1d")
    seen = []
    check.run_plan(e, check.Parsed(check.BACKFILL, "deals_backfill"),
                   lambda a: (seen.append(a), (0, "", ""))[1])  # fmt: skip
    assert seen == [[*e.args, "--mode", "plan"]]
    with pytest.raises(UqsError, match="refused"):
        check.run_plan(e, check.Parsed(check.BACKFILL, "deals_backfill"), lambda a: (1, "", "no"))


def test_run_is_parsed_by_the_real_cli():
    from uqs.cli.cron.cron import parse_run

    p = parse_run("job backfill demo_deals_backfill --last 3d --source-version v1".split())
    assert p.path == check.BACKFILL and p.job == "demo_deals_backfill"
    assert {"--last", "--source-version"} <= p.given
    for bad in ["job backfill x --bogus", "job", "job backfill", "nosuch"]:
        with pytest.raises(UqsError):
            parse_run(bad.split())


def test_cli_add_refuses_a_reaction_and_saves_nothing(cron_dir, monkeypatch):
    from uqs.cli.common import shared

    said = []
    errors = type("Errors", (), {"error": staticmethod(lambda f, *a: said.append(f.format(*a)))})
    monkeypatch.setattr(shared, "log", errors())
    argv = ["cron", "add", "r", "--cron", "0 6 * * *", "--", "job", "run", "rebuild_positions"]
    result = runner.invoke(cli.app, [*argv, "--once"])
    assert result.exit_code == 1 and "is a reaction" in said[0]
    assert not entries.path().exists()


# ------------------------------------------------------------------ run and status


def test_the_guard_says_not_due_only_for_a_guarded_entry_off_its_time(cron_dir):
    e = entries.entry("ldn", "10 6 * * *", "job run x --once", LONDON)
    entries.save({e.name: e})
    assert guard.not_due("ldn", at("2026-11-27T07:10"), host="UTC")
    assert not guard.not_due("ldn", at("2026-11-27T06:10"), host="UTC")  # 06:10 London, winter
    assert not guard.not_due("ldn", at("2026-11-27T07:10"), host=LONDON)  # cron decided
    assert not guard.not_due("absent", at("2026-11-27T07:10"), host="UTC")


def test_run_adds_wait_logs_and_records_the_outcome(cron_dir, tmp_path, monkeypatch):
    import subprocess

    e = entries.entry("d", "0 6 * * *", "job backfill deals_backfill --last 1d")
    assert runs.command(e, check.BACKFILL)[-1] == "--wait"
    assert "--wait" not in runs.command(
        entries.entry("u", "0 6 * * *", "job run x --once"), check.RUN
    )
    seen = {}

    def fake_run(argv, **kw):
        seen.update(argv=argv, env=kw["env"])
        kw["stdout"].write("worker said hello\n")
        return subprocess.CompletedProcess(argv, 4)

    monkeypatch.setattr(runs.subprocess, "run", fake_run)
    assert runs.run(e, check.BACKFILL, tmp_path) == 4
    assert seen["env"]["UQS_CRON_ENTRY"] == "d" and seen["argv"][-1] == "--wait"
    log = entries.log_path("d").read_text()
    assert "uqs cron run d" in log and "worker said hello" in log and "exit 4" in log
    record = runs.last_outcome("d")
    assert record is not None and record["entry"] == "d" and record["exit_code"] == 4


def test_status_flags_failing_overdue_and_never_run(cron_dir):
    e = entries.entry("d", "0 6 * * *", "job run x --once")
    assert runs.status(e, at("2026-11-27T12:00"))["state"] == "never run"
    out = entries.outcomes_path("d")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"entry": "d", "started": "2026-11-27T06:00:00+00:00",
                               "finished": "x", "command": [], "exit_code": 1}) + "\n")  # fmt: skip
    assert runs.status(e, at("2026-11-27T12:00"))["state"] == "failing"
    assert runs.status(e, at("2026-11-28T07:30"))["state"] == "overdue"
    assert runs.status(e, at("2026-11-28T06:30") + timedelta(minutes=1))["state"] == "failing"


def test_cli_status_reports_entries_and_crontab_findings(fake_crontab, cron_dir):
    fake_crontab.write_text("5 5 * * * /old/uqs cron run gone\n")
    entries.save({DEALS.name: DEALS})
    result = runner.invoke(cli.app, ["cron", "status", "--json"])
    assert result.exit_code == 0, result.output
    doc = json.loads(result.output)
    assert [r["state"] for r in doc["entries"]] == ["never run"]
    assert {f["kind"] for f in doc["findings"]} == {"missing", "orphan"}
