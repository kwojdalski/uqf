"""`uqs logs --attach NAME`: an attached run's log, read like a process's (#1725)."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest
from loguru import logger
from typer.testing import CliRunner

from uqs import cli
from uqs.cli import backfill_attached, shared
from uqs.paths import UqsError, UqsPaths
from uqs.stack import attached_logs, logs

WORKER = "piggybank_ledger_backfill"
INFO = f"2026.10.27D13:04:26.688546389|INFO|{WORKER}|window 1 of 3 fetched rows=12"
DEBUG = "2026.10.27D13:04:26.700000000|DEBUG|attached_backfill|flags parsed"
ERROR = '2026.10.27D13:04:27.100000000|ERROR|attached_backfill|run failed error="\'type"'
RESULT = 'RESULT {"worker":"piggybank_ledger_backfill","state":"failed","outcome":"failed"}'


@pytest.fixture(autouse=True)
def _restore_loguru():
    """`uqs logs` replaces the shared sink with one on this test's captured stream, which
    closes with the test: a later test's log line would land on it and fail there."""
    yield
    logger.remove()
    logger.add(sys.stderr)


@pytest.fixture
def paths(tmp_path, monkeypatch) -> UqsPaths:
    monkeypatch.setenv("UQF_STATUS_DIR", str(tmp_path / "status"))
    return UqsPaths(
        repo_root=tmp_path,
        torqhome=tmp_path / "torq",
        torqapphome=tmp_path / "app",
        torqdata=tmp_path,
        scripts_dir=tmp_path / "scripts",
        orchestrator_dir=tmp_path / "orch",
    )


def write_run(
    tmp_path: Path,
    lines: list[str],
    *,
    attachment: str = "pb-uat",
    identity: str = "0123456789ab",
    worker: str = WORKER,
    stamp: str = "20261027T130426000000",
) -> Path:
    folder = tmp_path / "status" / "attached" / f"{attachment}-{identity}" / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{worker}-{stamp}.log"
    path.write_text("".join(f"{line}\n" for line in lines))
    return path


class _Sink:
    """Stands in for the loguru sink: what the real `emit` printed, with the fields it bound."""

    def __init__(self) -> None:
        self.printed: list[dict[str, str]] = []
        self._bound: dict[str, str] = {}

    def bind(self, **kw):
        self._bound = kw
        return self

    def log(self, level: str, message: str) -> None:
        self.printed.append({**self._bound, "loglevel": level, "message": message})


def shown(paths, monkeypatch, **kw) -> list[dict[str, str]]:
    sink = _Sink()
    monkeypatch.setattr(logs, "log_sink", lambda: sink)
    attached_logs.show(paths, "pb-uat", **kw)
    return sink.printed


# ------------------------------------------------------------------ selection


def test_the_latest_run_is_chosen_across_the_attachments_destinations(paths, tmp_path):
    write_run(tmp_path, [INFO], stamp="20261027T120000000000")
    newest = write_run(tmp_path, [INFO], identity="ba9876543210", stamp="20261027T130000000000")
    write_run(tmp_path, [INFO], attachment="pb-uat-2", stamp="20261027T140000000000")
    chosen = attached_logs.select(paths, "pb-uat")
    assert chosen.path == newest, "a forced and a checked destination are both looked through"
    assert chosen.destination == "ba9876543210"


def test_an_older_run_is_chosen_by_its_file_name_or_stamp_prefix(paths, tmp_path):
    old = write_run(tmp_path, [INFO], stamp="20261026T090000000000")
    write_run(tmp_path, [INFO], stamp="20261027T090000000000")
    assert attached_logs.select(paths, "pb-uat", run=old.name).path == old
    assert attached_logs.select(paths, "pb-uat", run="20261026").path == old
    with pytest.raises(UqsError, match="more than one run .* the latest are"):
        attached_logs.select(paths, "pb-uat", run="2026102")
    with pytest.raises(UqsError, match="names no run"):
        attached_logs.select(paths, "pb-uat", run="2025")


def test_two_workers_need_worker_to_say_which(paths, tmp_path):
    write_run(tmp_path, [INFO])
    receiver = write_run(tmp_path, [INFO], worker="ledger.receiver")
    with pytest.raises(UqsError, match=f"ledger.receiver, {WORKER}: say which with --worker"):
        attached_logs.select(paths, "pb-uat")
    assert attached_logs.select(paths, "pb-uat", worker="ledger.receiver").path == receiver
    with pytest.raises(UqsError, match="no run of other_backfill .* it has logs of"):
        attached_logs.select(paths, "pb-uat", worker="other_backfill")


def test_an_attachment_with_no_logs_says_where_it_looked(paths):
    with pytest.raises(UqsError, match="no attached run has logged for attachment pb-uat under"):
        attached_logs.select(paths, "pb-uat")


# -------------------------------------------------------------------- reading


def test_four_field_lines_are_shown_as_the_workers_and_other_lines_are_kept(
    paths, tmp_path, monkeypatch
):
    write_run(tmp_path, ["q)'type", INFO, ERROR, "", RESULT])
    seen = shown(paths, monkeypatch)
    assert [r["message"] for r in seen] == [
        "q)'type", "window 1 of 3 fetched rows=12", "run failed error=\"'type\"", RESULT,
    ], "in the file's order: lines with no time are not sorted to the top"  # fmt: skip
    assert seen[1]["procname"] == WORKER and seen[2]["proctype"] == "attached_backfill"
    assert seen[1]["loglevel"] == "INFO" and seen[2]["loglevel"] == "ERROR"


def test_level_filters_log_lines_but_never_the_result_or_a_q_error(paths, tmp_path, monkeypatch):
    write_run(tmp_path, ["q)'type", DEBUG, INFO, ERROR, RESULT])
    seen = shown(paths, monkeypatch, min_level="WARNING")
    assert [r["message"][:10] for r in seen] == ["q)'type", "run failed", RESULT[:10]]


def test_lines_limits_the_history(paths, tmp_path, monkeypatch):
    write_run(tmp_path, [DEBUG, INFO, ERROR, RESULT])
    assert [r["message"] for r in shown(paths, monkeypatch, lines=1)] == [RESULT]


def test_a_receivers_lines_with_no_id_are_labelled_receiver(paths, tmp_path, monkeypatch):
    write_run(tmp_path, ["2026.10.27D13:04:26.000000000|INFO||listening"], worker="ledger.receiver")
    (rec,) = shown(paths, monkeypatch)
    assert (rec["procname"], rec["proctype"]) == ("ledger.receiver", "receiver")


def test_the_colored_output_carries_the_kdb_time_and_worker(paths, tmp_path, capsys):
    write_run(tmp_path, [INFO, RESULT])
    attached_logs.show(paths, "pb-uat")
    out = capsys.readouterr().out
    assert "2026.10.27D13:04:26.688" in out and WORKER in out and "window 1 of 3" in out
    assert '"state":"failed"' in out


def test_follow_shows_the_history_then_what_a_running_backfill_appends(
    paths, tmp_path, monkeypatch
):
    log = write_run(tmp_path, [INFO])
    seen: list[dict[str, str]] = []

    def stop_after_two(sink, rec, min_level):
        seen.append(rec)
        if len(seen) == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(logs, "emit", stop_after_two)

    def append_later():
        time.sleep(0.5)
        with log.open("a") as f:
            f.write(RESULT + "\n")

    threading.Thread(target=append_later, daemon=True).start()
    attached_logs.show(paths, "pb-uat", follow=True)
    assert [r["message"] for r in seen] == ["window 1 of 3 fetched rows=12", RESULT]


# ------------------------------------------------------------------------ CLI


class _ErrorLog:
    """What `_die` hands the log: loguru does not write into CliRunner's output."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def error(self, message: str, *args) -> None:
        self.messages.append(str(message).format(*args))

    def __getattr__(self, _name: str):
        return lambda *a, **kw: None


def _errors(monkeypatch) -> list[str]:
    log = _ErrorLog()
    monkeypatch.setattr(shared, "log", log)
    return log.messages


@pytest.mark.parametrize(
    ("argv", "said"),
    [
        (["--worker", WORKER], "--worker needs --attach"),
        (["--run", "2026"], "--run needs --attach"),
        (["rdb1", "--attach", "pb-uat"], "a process name does not apply with --attach"),
        (["--attach", "pb-uat", "--channel"], "--channel does not apply with --attach"),
    ],
)
def test_the_cli_refuses_options_that_do_not_go_together(monkeypatch, argv, said):
    errors = _errors(monkeypatch)
    result = CliRunner().invoke(cli.app, ["logs", *argv], catch_exceptions=False)
    assert result.exit_code != 0
    assert any(said in m for m in errors), errors


def test_the_cli_reads_the_attached_run(paths, tmp_path, monkeypatch):
    write_run(tmp_path, [INFO])
    monkeypatch.setattr("uqs.cli.config._paths", lambda: paths)
    seen: list[dict[str, str]] = []
    monkeypatch.setattr(logs, "emit", lambda log, rec, min_level: seen.append(rec))
    result = CliRunner().invoke(
        cli.app, ["logs", "--attach", "pb-uat", "--worker", WORKER], catch_exceptions=False
    )
    assert result.exit_code == 0, result.output
    assert [r["message"] for r in seen] == ["window 1 of 3 fetched rows=12"]


def test_a_finished_run_names_the_command_that_reads_its_log():
    class Run:
        log = Path("/status/attached/pb-uat-0123456789ab/logs/x.log")

        class destination:  # noqa: N801 - stands in for a Destination
            attachment = "pb-uat"

    text = backfill_attached._outcome_text(WORKER, Run, 0, {"outcome": "queryable"})
    assert f"uqs logs --attach pb-uat --worker {WORKER}" in text
