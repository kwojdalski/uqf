"""`uqs run`: the run ledger read back (#530).

Built with the real q writer into a temporary status directory - two finished
runs that published the same window with different facts, and one begun and
never finished, as a crashed worker leaves it - then read through
stack/runs.py, which is what every `uqs run` command calls. Needs q, so it
skips without one.
"""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from uqs.interpreter import q_interpreter
from uqs.paths import UqsError, paths_for_root
from uqs.stack import runs

UQF_ROOT = Path(__file__).resolve().parents[3]

_WRITE = """
\\l src/init.q
\\l src/etl/core/backfill_state.q
\\l src/etl/core/materialisation.q
\\l src/etl/core/run.q
\\l src/etl/core/status.q
.qetl.run.attach[];
w:(2026.09.13D00:00;2026.09.14D00:00);
.qetl.run.begin[`deals_a];
.qetl.run.record[`demo_deals;w 0;w 1;`rows`source_version!(5;`v1)];
.qetl.run.finish[`completed];
.qetl.run.begin[`deals_b];
.qetl.run.record[`demo_deals;w 0;w 1;`rows`source_version!(4;`v2)];
.qetl.run.finish[`completed];
.qetl.run.begin[`interrupted_worker];
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
