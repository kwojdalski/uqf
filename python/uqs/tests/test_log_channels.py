"""Log channels (#2274): a record's `id` is the subsystem it concerns, and `uqs logs` shows,
filters and reports it - for recent lines and followed ones, with the level and the process,
without ever dropping a line whose id is not a channel.

The q side's half (process identity, the channel at each call site, the channel surviving
TorQ's text, JSON and published paths) is tests/q/test_log.q and the suites beside the
paths it names; this is the reader's, and the gates that keep the two lists one.
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path

import pytest

from uqs.cli.logs import lines_document
from uqs.generated import q_facts
from uqs.paths import UqsPaths, UqsUsageError
from uqs.stack import log_follow, logs

REPO = Path(__file__).resolve().parents[3]


def _line(second: int, process: str, level: str, channel: str, message: str) -> str:
    return f"2026.08.22D14:21:{second:02d}.000000000|host|etl|{process}|{level}|{channel}|{message}"


#: rdb1 and hdb1 each write lines on several channels, at several levels, plus the two kinds a
#: reader must not lose: a native TorQ category and an older line that names a worker.
RDB1 = [
    _line(1, "rdb1", "INFO", "lifecycle", "initialised"),
    _line(2, "rdb1", "DEBUG", "source", "fetching"),
    _line(3, "rdb1", "TRACE", "ipc", "query sent"),
    _line(4, "rdb1", "WARNING", "storage", "repaired partitions"),
    _line(5, "rdb1", "INFO", "main", "a native TorQ category"),
    _line(6, "rdb1", "INFO", "demo_deals_backfill", "an older line naming a worker"),
]
HDB1 = [
    _line(7, "hdb1", "ERROR", "source", "fetch failed"),
    _line(8, "hdb1", "DEBUG", "coverage", "coverage recorded"),
]


@pytest.fixture
def paths(tmp_path, monkeypatch) -> UqsPaths:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    for name, content in {"out_rdb1.log": RDB1, "out_hdb1.log": HDB1}.items():
        (log_dir / name).write_text("".join(f"{line}\n" for line in content))
    monkeypatch.setattr(logs, "list_process_names", lambda paths: ["rdb1", "hdb1"])
    return UqsPaths(
        repo_root=tmp_path,
        torqhome=tmp_path / "torq",
        torqapphome=tmp_path / "app",
        torqdata=tmp_path,
        scripts_dir=tmp_path / "scripts",
        orchestrator_dir=tmp_path / "orch",
    )


def _messages(records: list[dict[str, str]]) -> list[str]:
    return [r["message"] for r in records]


# ---------------------------------------------------------------- the channel list


def test_the_cli_takes_exactly_the_channels_q_declares():
    assert logs.CHANNELS == q_facts.LOG_CHANNELS
    assert set(q_facts.LOG_CHANNELS) == {
        "lifecycle", "ipc", "source", "transform", "storage", "coverage", "eod", "config",
    }  # fmt: skip


def test_the_docs_table_lists_every_channel_and_only_those():
    text = (REPO / "docs/guides/logs.md").read_text()
    section = text.split("### Channels: which subsystem a line concerns", 1)[1].split("\n### ", 1)[
        0
    ]
    rows = re.findall(r"^\s*\| `(\w+)`\s+\|", section, re.M)
    assert rows == list(q_facts.LOG_CHANNELS)


def test_the_docs_say_a_channel_filters_the_view_and_does_not_stop_generation():
    text = (REPO / "docs/guides/logs.md").read_text()
    assert "`--channel` filters what you view. It does not stop a record being generated" in text


def _q_call_sites() -> list[tuple[Path, str]]:
    """Every `.qetl.log.ch.<level>` call under src/ and scripts/, with the channel it names."""
    found = []
    for root in ("src", "scripts"):
        for path in (REPO / root).rglob("*.q"):
            text = path.read_text()
            for match in re.finditer(
                r"\.qetl\.log\.ch\.(?:trc|dbg|info|warn|err)(?=\[)([^\n]*)", text
            ):
                tail = match.group(1)
                named = re.search(r"\[(`\w+)\s*;", tail) or re.search(r"\(?(`\w+)\s*;\s*\"", tail)
                found.append((path, named.group(1)[1:] if named else ""))
    return found


def test_every_channel_call_site_names_a_declared_channel():
    """The q call refuses an undeclared channel when it runs; this refuses it before any
    test has to reach the line, and refuses a call whose channel is not a literal."""
    sites = _q_call_sites()
    assert len(sites) >= 10, "the scan found the representative call sites"
    bad = [(p.relative_to(REPO).as_posix(), c) for p, c in sites if c not in q_facts.LOG_CHANNELS]
    assert not bad, bad


def test_the_representative_paths_use_the_channels_the_issue_names():
    wanted = {
        "src/etl/core/source_fetch.q": "source",
        "src/etl/core/io_remote.q": "ipc",
        "src/etl/core/io_hdb.q": "storage",
        "src/etl/core/compression.q": "storage",
        "src/etl/core/materialisation.q": "coverage",
        "src/etl/core/io_hdb_eod.q": "eod",
        "src/etl/core/source_registry.q": "config",
        "src/etl/core/bounded_worker.q": "lifecycle",
        "src/etl/core/stream_job.q": "lifecycle",
    }
    by_file: dict[str, set[str]] = {}
    for path, channel in _q_call_sites():
        by_file.setdefault(path.relative_to(REPO).as_posix(), set()).add(channel)
    missing = {f: c for f, c in wanted.items() if c not in by_file.get(f, set())}
    assert not missing, missing


# ------------------------------------------------------------------- recent lines


def test_the_channel_is_the_id_column_of_a_parsed_line():
    rec = logs.parse_log_line(RDB1[1])
    assert rec is not None and rec["id"] == "source" and rec["procname"] == "rdb1"


def test_one_channel_keeps_only_its_lines(paths):
    got = logs.get_recent_logs(paths, "all", channels=["source"])
    assert _messages(got) == ["fetching", "fetch failed"]


def test_several_channels_match_any_of_them(paths):
    got = logs.get_recent_logs(paths, "all", channels=["source", "coverage"])
    assert _messages(got) == ["fetching", "fetch failed", "coverage recorded"]


def test_channel_level_and_process_combine(paths):
    assert (
        _messages(logs.get_recent_logs(paths, "rdb1", channels=["source"], min_level="INFO")) == []
    )
    got = logs.get_recent_logs(paths, "all", channels=["source", "storage"], min_level="WARNING")
    assert _messages(got) == ["repaired partitions", "fetch failed"]
    only_rdb1 = logs.get_recent_logs(
        paths, "rdb1", channels=["source", "storage"], min_level="DEBUG"
    )
    assert _messages(only_rdb1) == ["fetching", "repaired partitions"]


def test_no_channel_filter_shows_every_line_including_unfamiliar_ids(paths):
    got = logs.get_recent_logs(paths, "rdb1")
    assert [r["id"] for r in got] == [
        "lifecycle", "source", "ipc", "storage", "main", "demo_deals_backfill",
    ]  # fmt: skip


def test_a_channel_that_is_not_declared_is_refused_naming_them_all(paths):
    with pytest.raises(UqsUsageError) as err:
        logs.get_recent_logs(paths, "all", channels=["sourse"])
    assert "'sourse' is not a channel" in str(err.value)
    assert all(c in str(err.value) for c in q_facts.LOG_CHANNELS)


@pytest.mark.parametrize("lines", [-1, -27])
def test_a_negative_line_count_is_refused_not_read_as_skip_the_first(paths, lines):
    """`splitlines()[-lines:]` with a negative count skipped the first lines
    and returned the rest of the log - from the CLI and from MCP (#2468)."""
    with pytest.raises(UqsUsageError, match="lines"):
        logs.get_recent_logs(paths, "rdb1", lines)


def test_the_cli_refuses_a_negative_lines_as_a_usage_error(paths):
    from plain_cli import PlainRunner

    from uqs import cli

    result = PlainRunner().invoke(cli.app, ["logs", "rdb1", "-l", "-1"])
    assert result.exit_code == 2
    assert "--lines" in result.output


# ----------------------------------------------------------------------- display


def test_the_channel_is_a_column_and_an_unfamiliar_id_is_shown_as_is(paths, capsys):
    logs.print_recent_logs(paths, "rdb1")
    out = capsys.readouterr().out
    assert "rdb1/etl | lifecycle - initialised" in out
    assert "rdb1/etl | main - a native TorQ category" in out
    assert "rdb1/etl | demo_deals_backfill - an older line naming a worker" in out


def test_a_line_with_no_id_shows_a_dash_in_the_column(tmp_path, monkeypatch, capsys):
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "out_rdb1.log").write_text(_line(1, "rdb1", "INFO", "", "no id") + "\n")
    monkeypatch.setattr(logs, "list_process_names", lambda paths: ["rdb1"])
    p = UqsPaths(
        repo_root=tmp_path,
        torqhome=tmp_path / "t",
        torqapphome=tmp_path / "a",
        torqdata=tmp_path,
        scripts_dir=tmp_path / "s",
        orchestrator_dir=tmp_path / "o",
    )
    logs.print_recent_logs(p, "rdb1")
    assert "rdb1/etl | - - no id" in capsys.readouterr().out


def test_an_old_line_of_the_pre_rename_vocabulary_is_still_read(paths, tmp_path):
    """A log written before levels were renamed and before channels existed."""
    old = "2026.08.22D14:21:09.000000000|host|rdb|rdb1|WARN|init|old style line"
    (tmp_path / "logs" / "out_rdb1.log").write_text(old + "\n")
    (rec,) = logs.get_recent_logs(paths, "rdb1")
    assert (rec["loglevel"], rec["id"], rec["message"]) == ("WARNING", "init", "old style line")


# --------------------------------------------------------------------------- JSON


def test_json_records_carry_channel_not_id(paths):
    doc = lines_document(logs.get_recent_logs(paths, "all", channels=["source"]))
    assert [(line["channel"], line["process"], line["level"]) for line in doc["lines"]] == [
        ("source", "rdb1", "DEBUG"),
        ("source", "hdb1", "ERROR"),
    ]
    assert all("id" not in line and "loglevel" not in line for line in doc["lines"])


def test_json_keeps_an_unfamiliar_id_as_its_channel(paths):
    doc = lines_document(logs.get_recent_logs(paths, "rdb1"))
    assert [line["channel"] for line in doc["lines"]][-2:] == ["main", "demo_deals_backfill"]


# ------------------------------------------------------------------------ following


class _Sink:
    """The log loguru would print to: what `emit` shows, after its own level filter."""

    def __init__(self) -> None:
        self.shown: list[str] = []

    def bind(self, **_extra):
        return self

    def log(self, _level: str, message: str) -> None:
        self.shown.append(message)


def _follow(paths, monkeypatch, appended: list[str], count: int, **kw) -> list[str]:
    """What `follow_logs` shows (through the real `emit`, so the level applies) until
    `count` lines are shown."""
    sink = _Sink()
    real_emit = logs.emit

    def stop_after(_log, rec, min_level):
        real_emit(sink, rec, min_level)
        if len(sink.shown) == count:
            raise KeyboardInterrupt

    monkeypatch.setattr(logs, "emit", stop_after)

    def append_later():
        time.sleep(0.5)
        with (paths.log_dir / "out_rdb1.log").open("a") as f:
            f.write("".join(f"{line}\n" for line in appended))

    threading.Thread(target=append_later, daemon=True).start()
    log_follow.follow_logs(paths, "rdb1", **kw)
    return sink.shown


def test_following_filters_the_history_and_the_appended_lines_alike(paths, monkeypatch):
    appended = [
        _line(20, "rdb1", "WARNING", "storage", "appended storage"),
        _line(21, "rdb1", "WARNING", "ipc", "appended ipc"),
        _line(22, "rdb1", "DEBUG", "source", "appended source"),
    ]
    seen = _follow(
        paths, monkeypatch, appended, 3, channels=["source", "storage"], min_level="DEBUG"
    )
    assert seen == ["fetching", "repaired partitions", "appended storage"]


def test_following_combines_level_and_channel(paths, monkeypatch):
    appended = [
        _line(20, "rdb1", "DEBUG", "storage", "appended debug storage"),
        _line(21, "rdb1", "WARNING", "storage", "appended warning storage"),
    ]
    seen = _follow(paths, monkeypatch, appended, 2, channels=["storage"], min_level="WARNING")
    assert seen == ["repaired partitions", "appended warning storage"]


def test_following_refuses_an_undeclared_channel_before_following(paths):
    with pytest.raises(UqsUsageError):
        log_follow.follow_logs(paths, "rdb1", channels=["nope"])
