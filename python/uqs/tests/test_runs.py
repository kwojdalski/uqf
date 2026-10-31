"""`uqs job runs`: the run ledger read back (#530).

Built with the real q writer into a temporary status directory - two finished
runs that published the same window with different facts, and one begun and
never finished, as a crashed worker leaves it - then read through
stack/runs.py, which is what every `uqs job runs` command calls. Needs q, so it
skips without one.
"""

from __future__ import annotations

import os
import re
import subprocess
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from uqs.interpreter import q_interpreter
from uqs.paths import UqsError, paths_for_root
from uqs.stack import runs

UQF_ROOT = Path(__file__).resolve().parents[3]


def q_loads(script: Path) -> tuple[str, ...]:
    """The files a q script loads, in order: its `system"l ..."` and `\\l ...`
    lines, read as q reads them - one load per line, at its start."""
    loads = []
    for line in script.read_text().splitlines():
        if m := re.match(r'^\s*(?:system\s*"l\s+([^"]+)"|\\l\s+(\S+))', line):
            loads.append((m.group(1) or m.group(2)).strip())
    return tuple(loads)


def test_the_ledgers_load_list_is_the_one_read_runs_q_proves():
    """read_runs.q is the proof that this subset is enough to read the ledger
    from a fresh process (#1795): a file one side loads and the other does
    not is either a `uqs job runs` that fails with a q name error, or a proof
    that no longer proves what ships."""
    proof = q_loads(UQF_ROOT / "tests" / "q" / "read_runs.q")
    assert proof, "read_runs.q loads nothing this test can read"
    assert runs._LOADS == proof


_WRITE = """
\\l src/init.q
\\l src/etl/core/backfill_state.q
\\l src/etl/core/intervals.q
\\l src/etl/core/materialisation.q
\\l src/etl/core/run.q
\\l src/etl/core/status.q
.qetl.run.attach[];
w:(2026.09.13D00:00;2026.09.14D00:00);
s:`dataset`source_version`range_from`range_to`width!(`demo_deals;`v1;w 0;w 1;1D);
.qetl.run.begin[`deals_a;s];
.qetl.run.record[`demo_deals;w 0;w 1;`rows`source_version!(5;`v1)];
n:`windows_planned`windows_completed`windows_failed`rows_published!1 1 0 5;
.qetl.run.finish[`completed;n];
.qetl.run.begin[`deals_b;()!()];
.qetl.run.record[`demo_deals;w 0;w 1;`rows`source_version!(4;`v2)];
.qetl.run.finish[`completed;()!()];
.qetl.run.begin[`interrupted_worker;()!()];
exit 0
"""


@pytest.fixture(scope="module")
def ledger(tmp_path_factory) -> Path:
    q = q_interpreter(os.environ)
    if q is None:
        pytest.skip("no q interpreter - set $QCMD, or put q on PATH")
    directory = tmp_path_factory.mktemp("status")
    script = directory / "write.q"
    script.write_text(_WRITE)
    result = subprocess.run(
        [str(q), str(script), "-q"],
        cwd=UQF_ROOT,
        env={**os.environ, "UQF_STATUS_DIR": str(directory)},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return directory


@pytest.fixture
def paths():
    return paths_for_root(UQF_ROOT)


def test_status_finds_the_run_that_never_finished(ledger, paths):
    rows = runs.unfinished(paths, directory=ledger)
    assert [(r["worker"], r["status"]) for r in rows] == [("interrupted_worker", "running")]


def test_list_is_every_run_newest_first(ledger, paths):
    workers = [r["worker"] for r in runs.history(paths, directory=ledger)]
    assert workers == ["interrupted_worker", "deals_b", "deals_a"]


def test_show_gives_one_run_and_its_facts(ledger, paths):
    first = next(r for r in runs.history(paths, directory=ledger) if r["worker"] == "deals_a")
    run, facts = runs.show(paths, first["run_id"], directory=ledger)
    assert [r["worker"] for r in run] == ["deals_a"]
    assert {(f["label"], f["text"]) for f in facts} == {("rows", "5"), ("source_version", "v1")}


def test_audit_puts_both_runs_facts_about_one_window_side_by_side(ledger, paths):
    rows = runs.audit(
        paths,
        "demo_deals",
        datetime(2026, 9, 13, tzinfo=UTC),
        datetime(2026, 9, 14, tzinfo=UTC),
        directory=ledger,
    )
    by_label = sorted((f["label"], f["text"]) for f in rows)
    assert by_label == [
        ("rows", "4"),
        ("rows", "5"),
        ("source_version", "v1"),
        ("source_version", "v2"),
    ]
    assert len({f["run_id"] for f in rows}) == 2, "two runs published this window"


def test_audit_of_a_window_nothing_published_is_empty(ledger, paths):
    rows = runs.audit(
        paths,
        "demo_deals",
        datetime(2026, 9, 1, tzinfo=UTC),
        datetime(2026, 9, 2, tzinfo=UTC),
        directory=ledger,
    )
    assert rows == []


def test_a_run_records_what_it_was_asked_and_what_it_did(ledger, paths):
    row = next(r for r in runs.history(paths, directory=ledger) if r["worker"] == "deals_a")
    assert (row["dataset"], row["source_version"]) == ("demo_deals", "v1")
    assert row["range_from"].startswith("2026-09-13") and row["range_to"].startswith("2026-09-14")
    counts = [row[c] for c in ("windows_planned", "windows_completed", "windows_failed")]
    assert counts == [1, 1, 0]
    assert row["rows_published"] == 5


def test_a_run_that_never_finished_has_no_counts(ledger, paths):
    (row,) = runs.unfinished(paths, directory=ledger)
    assert row["windows_completed"] is None and row["rows_published"] is None


_OLD_LEDGER = """
\\l src/init.q
\\l src/etl/core/backfill_state.q
\\l src/etl/core/intervals.q
\\l src/etl/core/materialisation.q
\\l src/etl/core/run.q
\\l src/etl/core/status.q
(hsym `$.qetl.run.table_path `etl_runs) set ([] run_id:enlist 0Ng; worker:enlist `old_worker;
    process:enlist `p; host:enlist `h; pid:enlist 1i; started_at:enlist .z.p;
    ended_at:enlist .z.p; status:enlist `completed);
exit 0
"""


def test_migrate_upgrades_a_ledger_from_before_the_range_and_counts(tmp_path, paths):
    q = q_interpreter(os.environ)
    if q is None:
        pytest.skip("no q interpreter - set $QCMD, or put q on PATH")
    script = tmp_path / "old.q"
    script.write_text(_OLD_LEDGER)
    result = subprocess.run(
        [str(q), str(script), "-q"],
        cwd=UQF_ROOT,
        env={**os.environ, "UQF_STATUS_DIR": str(tmp_path)},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    with pytest.raises(UqsError, match="uqs job runs upgrade"):
        runs.history(paths, directory=tmp_path)
    assert runs.migrate(paths, dry_run=True, directory=tmp_path) == 1, "counted, not written"
    with pytest.raises(UqsError, match="uqs job runs upgrade"):
        runs.history(paths, directory=tmp_path)
    assert runs.migrate(paths, directory=tmp_path) == 1
    (row,) = runs.history(paths, directory=tmp_path)
    assert (row["worker"], row["dataset"], row["windows_completed"]) == ("old_worker", "", None)
    assert runs.migrate(paths, directory=tmp_path) == 0, "a second migrate has nothing to do"
    assert runs.migrate(paths, dry_run=True, directory=tmp_path) == 0


@pytest.mark.parametrize(
    ("call", "message"),
    [
        (lambda p, d: runs.show(p, "not-a-guid", directory=d), "not a run id"),
        (
            lambda p, d: runs.audit(
                p, "x;exit 1", datetime.now(UTC), datetime.now(UTC), directory=d
            ),
            "not a dataset name",
        ),
        (lambda p, d: runs.unfinished(p, directory=d / "missing"), "no status directory"),
    ],
)
def test_bad_arguments_are_refused_before_q_sees_them(ledger, paths, call, message):
    with pytest.raises(UqsError, match=message):
        call(paths, ledger)


def test_a_bound_reaches_q_as_the_same_instant_in_utc():
    """The one q-timestamp formatter. Its predecessor here formatted the wall
    time it was handed, so a +02:00 bound was spliced two hours late."""
    warsaw = datetime(2026, 9, 13, 2, 0, tzinfo=timezone(timedelta(hours=2)))
    assert runs.to_q_timestamp(warsaw) == "2026.09.13D00:00:00.000000000"
    assert runs.to_q_timestamp(datetime(2026, 9, 13, tzinfo=UTC)) == "2026.09.13D00:00:00.000000000"
    assert runs.to_q_timestamp(datetime(2026, 9, 13)) == "2026.09.13D00:00:00.000000000", (
        "a naive datetime is UTC here, not this machine's local time"
    )


# --- the migration plan: q decides, Python shows it (#1782) -------------------

_LOAD_RUN = "".join(
    f"\\l src/{f}\n"
    for f in (
        "init.q",
        "etl/core/backfill_state.q",
        "etl/core/intervals.q",
        "etl/core/materialisation.q",
        "etl/core/run.q",
        "etl/core/status.q",
    )
)

_PREVIOUS = (
    "([] run_id:enlist 0Ng; worker:enlist `old_worker; process:enlist `p; host:enlist `h; "
    "pid:enlist 1i; started_at:enlist .z.p; ended_at:enlist .z.p; status:enlist `completed)"
)
_STATES = {
    "missing": None,
    "current": ".qetl.run.attach[]; .qetl.run.begin[`w;()!()]",
    "previous": f'(hsym `$"%s/etl_runs") set {_PREVIOUS}',
    "unsupported": '(hsym `$"%s/etl_runs") set ([] run_id:enlist 0Ng; worker:enlist `w)',
}


def _ledger_in(directory: Path, state: str) -> None:
    body = _STATES[state]
    if body is None:
        return
    q = q_interpreter(os.environ)
    if q is None:
        pytest.skip("no q interpreter - set $QCMD, or put q on PATH")
    script = directory.parent / f"{directory.name}.q"
    script.write_text(_LOAD_RUN + (body % directory if "%s" in body else body) + "\nexit 0\n")
    result = subprocess.run(
        [str(q), str(script), "-q"],
        cwd=UQF_ROOT,
        env={**os.environ, "UQF_STATUS_DIR": str(directory)},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _snapshot(directory: Path) -> dict[str, bytes | None]:
    """Every path under `directory` and every file's bytes: what a preview must not change."""
    return {
        str(p.relative_to(directory)): p.read_bytes() if p.is_file() else None
        for p in sorted(directory.rglob("*"))
    }


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("missing", ("absent", False, "none", 0)),
        ("current", ("current", False, "none", 0)),
        ("previous", ("previous", True, "upgrade", 1)),
        ("unsupported", ("unsupported", False, "refuse", 0)),
    ],
)
def test_the_plan_is_q_s_classification_of_each_state(tmp_path, paths, state, expected):
    d = tmp_path / "status"
    d.mkdir()
    _ledger_in(d, state)
    plan = runs.migration_plan(paths, directory=d)
    assert (plan["state"], plan["needed"], plan["action"], plan["rows"]) == expected
    assert bool(plan["refusal"]) == (state == "unsupported")


@pytest.mark.parametrize("state", ["missing", "current", "previous", "unsupported"])
def test_a_dry_run_touches_no_file(tmp_path, paths, state):
    d = tmp_path / "status"
    d.mkdir()
    _ledger_in(d, state)
    before = _snapshot(d)
    try:
        runs.migrate(paths, dry_run=True, directory=d)
    except UqsError:
        assert state == "unsupported"
    assert _snapshot(d) == before, "a preview must not create, change or delete anything"


@pytest.mark.parametrize("state", ["missing", "current", "previous"])
def test_preview_and_apply_agree_on_the_row_count(tmp_path, paths, state):
    d = tmp_path / "status"
    d.mkdir()
    _ledger_in(d, state)
    previewed = runs.migrate(paths, dry_run=True, directory=d)
    assert runs.migrate(paths, directory=d) == previewed


def test_an_unsupported_ledger_is_refused_by_preview_and_apply_with_q_s_text(tmp_path, paths):
    d = tmp_path / "status"
    d.mkdir()
    _ledger_in(d, "unsupported")
    expected = "migrate: etl_runs is neither the old shape nor the current one"
    with pytest.raises(UqsError, match=expected):
        runs.migrate(paths, dry_run=True, directory=d)
    with pytest.raises(UqsError, match=expected):
        runs.migrate(paths, directory=d)


def test_a_q_failure_is_a_failure_not_nothing_to_migrate(tmp_path, paths):
    """A ledger file q cannot read must not preview as 0 rows."""
    d = tmp_path / "status"
    d.mkdir()
    (d / "etl_runs").write_bytes(b"not a q file")
    with pytest.raises(UqsError, match="reading the run ledger failed"):
        runs.migrate(paths, dry_run=True, directory=d)
